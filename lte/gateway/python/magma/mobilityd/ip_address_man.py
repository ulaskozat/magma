"""
Copyright 2020 The Magma Authors.

This source code is licensed under the BSD-style license found in the
LICENSE file in the root directory of this source tree.

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

The IP allocator maintains the life cycle of assigned IP addresses.

The IP address manager supports allocating and releasing IP addresses
Note that an IP address is not immediately made available for allocation right
after release: it is "reserved" for the same client for a certain period of
time to ensure that 1) an observer, e.g. pipelined, that caches IP states has
enough time to pull the updated IP states; 2) IP packets intended for the
old client will not be unintentionally routed to a new client until the old
TCP connection expires.

We have plug in design for IP address allocator. today we have two allocator
1. IP_POOL allocator, which allocates IP address from block of IP address
2. DHCP IP allocator. This allocates IP address assigned by DHCP server
   in network. This is typically used in Non NAT GW environment.

IP allocator supports static IP allocation for sub set of subscribers.
When static Ip mode is enabled, mobilityD would check subscriberDB for
assigned IP address before allocating Ip from configured allocator.

To support this semantic, an IP address can have the following states
during it's life cycle in the IP allocator:
    FREE: IP is available for allocation
    ALLOCATED: IP is allocated for a client.
    RELEASED: IP is released, but still reserved for the client
    REAPED: IPs are periodically reaped from the RELEASED state to the
        REAPED state, and at the same time a timer is set. All REAPED state
        IPs are freed once the time goes off. The purpose of this state is
        to age IPs for a certain period of time before freeing.
"""

from __future__ import absolute_import, division, print_function, \
    unicode_literals

import ipaddress
import logging
import threading
from collections import defaultdict
from ipaddress import ip_address, ip_network
from typing import List, Optional, Tuple
from lte.protos.mconfig.mconfigs_pb2 import MobilityD
from lte.protos.mobilityd_pb2 import GWInfo

from magma.mobilityd import mobility_store as store

from magma.mobilityd.ip_descriptor import IPDesc, IPState, IPType
from magma.mobilityd.metrics import (IP_ALLOCATED_TOTAL, IP_RELEASED_TOTAL)
from magma.common.redis.client import get_default_client

from .ip_allocator_dhcp import IPAllocatorDHCP
from .ip_allocator_pool import IpAllocatorPool
from .ip_allocator_static import IPAllocatorStaticWrapper
from .ip_allocator_multi_apn import IPAllocatorMultiAPNWrapper
from .ip_allocator_base import DuplicateIPAssignmentError

from .ip_descriptor_map import IpDescriptorMap
from .uplink_gw import UplinkGatewayInfo

DEFAULT_IP_RECYCLE_INTERVAL = 15


class IPAddressManager:
    """ A thread-safe IP allocator: all mutating functions are protected by a
    re-entrant lock.


    The IPAllocator maintains IP life cycle, as well as the mapping between
    SubscriberID (SID) and IPs. For now, only one-to-one mapping is supported
    between SID and IP.

    IP Recycling:
        An IP address is periodically recycled for allocation after releasing.

        Constraints:
        1. Define a maturity time T, in seconds. Any IPs that were released
           within the past T seconds cannot be freed.
        2. All released IPs must eventually be freed.

        To achieve these constraints, whenever we release an IP address we try
        to set a recycle timer if it's not already set. When setting a timer,
        we move all IPs in the RELEASED state to the REAPED state. Those IPs
        will be freed once the timer goes off, at which time those IPs are
        guaranteed to be "matured". Concurrently, newly released IPs are added
        to the RELEASED state.

        The time between when an IP is released and when it is freed is
        guaranteed to be between T and 2T seconds. The worst-case occurs when
        the release happens right after the previous timer has been initiated.
        This leads to an additional T seconds wait for the existing timer to
        time out and trigger a callback which initiate a new timer, on top of
        the T seconds of timeout for the next timer.

    Persistence to Redis:
        The IP allocator now by default persists its state to a redis-server
        process denoted in mobilityd.yml. The allocator's persisted properties
        and associated structure:
            - self._assigned_ip_blocks: {ip_block}
            - self.ip_state_map: {state=>{ip=>ip_desc}}
            - self.sid_ips_map: {SID=>[IPDesc]}

        The utilized redis_containers store a cache of state in local memory,
        so reads are the same speed as without persistence. For writes, state
        is serialized with provided serializers and written through to the
        Redis process. Used as expected, writes will be small (a few bytes).
        Redis's performance can be measured with the redis-benchmark tool,
        but we expect almost 100% of writes to take less than 1 millisecond.

    """

    def __init__(self,
                 *,
                 config,
                 mconfig,
                 subscriberdb_rpc_stub=None,
                 recycling_interval: int = DEFAULT_IP_RECYCLE_INTERVAL):
        """ Initializes a new IP allocator

        Args:
            recycling_interval (number): minimum time, in seconds, before a
                released IP is recycled and freed into the pool of available
                IPs.

                Default: None, no recycling will occur automatically.
        """

        persist_to_redis = config.get('persist_to_redis', True)
        redis_port = config.get('redis_port', 6379)

        self.multi_apn = config.get('multi_apn', mconfig.multi_apn_ip_alloc)
        self.static_ip_enabled = config.get('static_ip', mconfig.static_ip_enabled)

        self.allocator_type = mconfig.ip_allocator_type
        logging.debug('Persist to Redis: %s', persist_to_redis)
        self._lock = threading.RLock()  # re-entrant locks

        self._recycle_timer = None  # reference to recycle timer
        self._recycling_interval_seconds = recycling_interval

        if not persist_to_redis:
            self._assigned_ip_blocks = set()  # {ip_block}
            self.sid_ips_map = defaultdict(IPDesc)  # {SID=>IPDesc}
            self._dhcp_gw_info = UplinkGatewayInfo(defaultdict(str))
            self._dhcp_store = {}  # mac => DHCP_State
        else:
            if not redis_port:
                raise ValueError(
                    'Must specify a redis_port in mobilityd config.')
            client = get_default_client()
            self._assigned_ip_blocks = store.AssignedIpBlocksSet(client)
            self.sid_ips_map = store.IPDescDict(client)
            self._dhcp_gw_info = UplinkGatewayInfo(store.GatewayInfoMap())
            self._dhcp_store = store.MacToIP()  # mac => DHCP_State

        self.ip_state_map = IpDescriptorMap(persist_to_redis, redis_port)
        logging.info("Using allocator: %s static ip: %s multi_apn %s",
                     self.allocator_type,
                     self.static_ip_enabled,
                     self.multi_apn)

        if self.allocator_type == MobilityD.IP_POOL:
            self._dhcp_gw_info.read_default_gw()
            ip_allocator = IpAllocatorPool(self._assigned_ip_blocks,
                                           self.ip_state_map,
                                           self.sid_ips_map)
        elif self.allocator_type == MobilityD.DHCP:
            iface = config.get('dhcp_iface', 'dhcp0')
            retry_limit = config.get('retry_limit', 300)
            ip_allocator = IPAllocatorDHCP(assigned_ip_blocks=self._assigned_ip_blocks,
                                           ip_state_map=self.ip_state_map,
                                           iface=iface,
                                           retry_limit=retry_limit,
                                           dhcp_store=self._dhcp_store,
                                           gw_info=self._dhcp_gw_info)
        else:
            raise ValueError("Unknown IP allocator type: %s" % self.allocator_type)

        if self.static_ip_enabled:
            ip_allocator = IPAllocatorStaticWrapper(subscriberdb_rpc_stub=subscriberdb_rpc_stub,
                                                    ip_allocator=ip_allocator,
                                                    gw_info=self._dhcp_gw_info,
                                                    assigned_ip_blocks=self._assigned_ip_blocks,
                                                    ip_state_map=self.ip_state_map)

        if self.multi_apn:
            self.ip_allocator = IPAllocatorMultiAPNWrapper(subscriberdb_rpc_stub=subscriberdb_rpc_stub,
                                                           ip_allocator=ip_allocator)
        else:
            self.ip_allocator = ip_allocator

    def add_ip_block(self, ipblock: ip_network):
        """ Add a block of IP addresses to the free IP list

        IP blocks should not overlap.

        Args:
            ipblock (ipaddress.ip_network): ip network to add
            e.g. ipaddress.ip_network("10.0.0.0/24")

        Raises:
            OverlappedIPBlocksError: if the given IP block overlaps with
            existing ones
        """
        with self._lock:
            self.ip_allocator.add_ip_block(ipblock)

    def remove_ip_blocks(self, *_ipblocks: List[ip_network],
                         force: bool = False) -> List[ip_network]:
        """ Makes the indicated block(s) unavailable for allocation

        If force is False, blocks that have any addresses currently allocated
        will not be removed. Otherwise, if force is True, the indicated blocks
        will be removed regardless of whether any addresses have been allocated
        and any allocated addresses will no longer be served.

        Removing a block entails removing the IP addresses within that block
        from the internal state machine.

        Args:
            _ipblocks (ipaddress.ip_network): variable number of objects of type
                ipaddress.ip_network, representing the blocks that are intended
                to be removed. The blocks should have been explicitly added and
                not yet removed. Any blocks that are not active in the IP
                allocator will be ignored with a warning.
            force (bool): whether to forcibly remove the blocks indicated. If
                False, will only remove a block if no addresses from within the
                block have been allocated. If True, will remove all blocks
                regardless of whether any addresses have been allocated from
                them.

        Returns a set of the blocks that have been successfully removed.
        """

        with self._lock:
            ip_blocks_deleted = self.ip_allocator.remove_ip_blocks(_ipblocks, _force=force)

        return ip_blocks_deleted

    def list_added_ip_blocks(self) -> List[ip_network]:
        """ List IP blocks added to the IP allocator

        Return:
             copy of the list of assigned IP blocks
        """
        with self._lock:
            ip_blocks = self.ip_allocator.list_added_ip_blocks();
        return ip_blocks

    def list_allocated_ips(self, ipblock: ip_network) -> List[ip_address]:
        """ List IP addresses allocated from a given IP block

        Args:
            ipblock (ipaddress.ip_network): ip network to add
            e.g. ipaddress.ip_network("10.0.0.0/24")

        Return:
            list of IP addresses (ipaddress.ip_address)

        Raises:
          IPBlockNotFoundError: if the given IP block is not found in the
          internal list
        """

        with self._lock:
            res = self.ip_allocator.list_allocated_ips(ipblock)
        return res

    def alloc_ip_address(self, sid: str) -> Tuple[ip_address, int]:
        """ Allocate an IP address from the free list

        Assumption: one-to-one mappings between SID and IP.

        Args:
            sid (string): universal subscriber id

        Returns:
            ipaddress.ip_address: IP address allocated

        Raises:
            NoAvailableIPError: if run out of available IP addresses
            DuplicatedIPAllocationError: if an IP has been allocated to a UE
                with the same IMSI
        """

        with self._lock:
            # if an IP is reserved for the UE, this IP could be in the state of
            # ALLOCATED, RELEASED or REAPED.
            if sid in self.sid_ips_map:
                old_ip_desc = self.sid_ips_map[sid]
                if self.ip_state_map.test_ip_state(old_ip_desc.ip, IPState.ALLOCATED):
                    # MME state went out of sync with mobilityd!
                    # Recover gracefully by allocating the same IP
                    logging.warning("Re-allocate IP %s for sid %s without "
                                    "MME releasing it first ip-state-map",
                                    old_ip_desc.ip,
                                    sid)

                    # TODO: enable strict checking after root causing the
                    # issue in MME
                    # raise DuplicatedIPAllocationError(
                    #     "An IP has been allocated for this IMSI")
                elif self.ip_state_map.test_ip_state(old_ip_desc.ip, IPState.RELEASED):
                    ip_desc = self.ip_state_map.mark_ip_state(old_ip_desc.ip,
                                                              IPState.ALLOCATED)
                    ip_desc.sid = sid
                    logging.debug("SID %s IP %s RELEASED => ALLOCATED",
                                  sid, old_ip_desc.ip)
                elif self.ip_state_map.test_ip_state(old_ip_desc.ip, IPState.REAPED):
                    ip_desc = self.ip_state_map.mark_ip_state(old_ip_desc.ip,
                                                              IPState.ALLOCATED)
                    ip_desc.sid = sid
                    logging.debug("SID %s IP %s REAPED => ALLOCATED",
                                  sid, old_ip_desc.ip)
                else:
                    raise AssertionError("Unexpected internal state")

                logging.info("Allocating the same IP %s for sid %s",
                             old_ip_desc.ip, sid)
                IP_ALLOCATED_TOTAL.inc()
                return old_ip_desc.ip, old_ip_desc.vlan_id

            # Now try to allocate it from underlying allocator.
            ip_desc = self.ip_allocator.alloc_ip_address(sid, 0)
            existing_sid = self.get_sid_for_ip(ip_desc.ip)
            if existing_sid:
                error_msg = "Dup IP: {} for SID: {}, which already is " \
                            "assigned to SID: {}".format(ip_desc.ip,
                                                         sid,
                                                         existing_sid)
                logging.error(error_msg)
                raise DuplicateIPAssignmentError(error_msg)

            self.ip_state_map.add_ip_to_state(ip_desc.ip, ip_desc, IPState.ALLOCATED)
            self.sid_ips_map[sid] = ip_desc

            logging.debug("Allocating New IP: %s", str(ip_desc))
            IP_ALLOCATED_TOTAL.inc()
            return ip_desc.ip, ip_desc.vlan_id

    def get_sid_ip_table(self) -> List[Tuple[str, ip_address]]:
        """ Return list of tuples (sid, ip) """
        with self._lock:
            res = [(sid, ip_desc.ip) for sid, ip_desc in
                   self.sid_ips_map.items()]
            return res

    def get_ip_for_sid(self, sid: str) -> Optional[ip_address]:
        """ if ip is mapped to sid, return it, else return None """
        with self._lock:
            if sid in self.sid_ips_map:
                if not self.sid_ips_map[sid]:
                    raise AssertionError("Unexpected internal state")
                else:
                    return self.sid_ips_map[sid].ip
            return None

    def get_sid_for_ip(self, requested_ip: ip_address) -> Optional[str]:
        """ If ip is associated with an sid, return the sid, else None """
        with self._lock:
            for sid, ip_desc in self.sid_ips_map.items():
                if requested_ip == ip_desc.ip:
                    return sid
            return None

    def release_ip_address(self, sid: str, ip: ip_address):
        """ Release an IP address.

        A released IP is moved to a released list. Released IPs are recycled
        periodically to the free list. SID IP mappings are removed at the
        recycling time.

        Args:
            sid (string): universal subscriber id
            ip (ipaddress.ip_address): IP address to release

        Raises:
            MappingNotFoundError: if the given sid-ip mapping is not found
            IPNotInUseError: if the given IP is not found in the used list
        """
        with self._lock:
            if not (sid in self.sid_ips_map and ip ==
                    self.sid_ips_map[sid].ip):
                logging.error(
                    "Releasing unknown <SID, IP> pair: <%s, %s> "
                    "sid_ips_map[%s]: %s",
                    sid, ip, sid, self.sid_ips_map.get(sid, ""))
                raise MappingNotFoundError(
                    "(%s, %s) pair is not found", sid, str(ip))
            if not self.ip_state_map.test_ip_state(ip, IPState.ALLOCATED):
                logging.error("IP not found in used list, check if IP is "
                              "already released: <%s, %s>", sid, ip)
                raise IPNotInUseError("IP not found in used list: %s", str(ip))

            self.ip_state_map.mark_ip_state(ip, IPState.RELEASED)
            IP_RELEASED_TOTAL.inc()

            self._try_set_recycle_timer()  # start the timer to recycle

    def list_gateway_info(self) -> List[GWInfo]:
        with self._lock:
            return self._dhcp_gw_info.get_all_router_ips()

    def set_gateway_info(self, info: GWInfo):
        ip = str(ipaddress.ip_address(info.ip.address))
        with self._lock:
            self._dhcp_gw_info.update_mac(ip, info.mac, info.vlan)

    def _recycle_reaped_ips(self):
        """ Periodically called to recycle the given IPs

        *** It is highly not recommended to call this function directly, even
        in tests. ***

        Recycling depends on the period, T = self._recycling_interval_seconds,
        which is set at construction time.

        """
        with self._lock:
            for ip in self.ip_state_map.list_ips(IPState.REAPED):
                ip_desc = self.ip_state_map.mark_ip_state(ip, IPState.FREE)
                logging.debug("Release Reaped IP: %s", ip_desc)

                self.ip_allocator.release_ip(ip_desc)
                # update SID-IP map
                del self.sid_ips_map[ip_desc.sid]

            # Set timer for the next round of recycling
            self._recycle_timer = None
            if self.ip_state_map.get_ip_count(IPState.RELEASED):
                self._try_set_recycle_timer()

    def _try_set_recycle_timer(self):
        """ Try set the recycle timer and move RELEASED IPs to the REAPED state

        self._try_set_recycle_timer is called in two places:
            1) at the end of self.release_ip_address, we are guaranteed that
            some IPs exist in RELEASED state, so we attempt to initiate a timer
            then.
            2) at the end of self._recycle_reaped_ips, the call to
            self._try_set_recycle_timer serves as a callback for setting the
            next timer, if any IPs have been released since the current timer
            was initiated.
        """
        with self._lock:
            # check if auto recycling is enabled and no timer has been set
            if self._recycling_interval_seconds is not None \
                    and not self._recycle_timer:
                for ip in self.ip_state_map.list_ips(IPState.RELEASED):
                    self.ip_state_map.mark_ip_state(ip, IPState.REAPED)
                if self._recycling_interval_seconds:
                    self._recycle_timer = threading.Timer(
                        self._recycling_interval_seconds,
                        self._recycle_reaped_ips)
                    self._recycle_timer.start()
                else:
                    self._recycle_reaped_ips()


class IPNotInUseError(Exception):
    """ Exception thrown when releasing an IP address that is not found in the
    used list
    """
    pass


class MappingNotFoundError(Exception):
    """ Exception thrown when releasing a non-exising SID-IP mapping """
    pass

