"""
Copyright 2020 The Magma Authors.

This source code is licensed under the BSD-style license found in the
LICENSE file in the root directory of this source tree.

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
import subprocess
import threading
from collections import namedtuple

from magma.pipelined.app.base import MagmaController, ControllerType
from magma.pipelined.bridge_util import BridgeTools
from magma.pipelined.openflow import flows


class UplinkBridgeController(MagmaController):
    """
    This controller manages uplink bridge flows
    These flows are used in Non NAT configuration.
    """

    APP_NAME = "uplink_bridge"
    APP_TYPE = ControllerType.SPECIAL
    UPLINK_DHCP_PORT_NAME = 'dhcp0'
    UPLINK_PATCH_PORT_NAME = 'patch-agw'
    UPLINK_OVS_BRIDGE_NAME = 'uplink_br0'
    DEFAULT_UPLINK_PORT_NANE = 'eth3'
    DEFAULT_UPLINK_MAC = '11:22:33:44:55:66'
    DEFAULT_DEV_VLAN_IN = 'vlan_pop_in'
    DEFAULT_DEV_VLAN_OUT = 'vlan_pop_out'

    UplinkConfig = namedtuple(
        'UplinkBridgeConfig',
        ['uplink_bridge', 'uplink_eth_port_name', 'uplink_patch',
         'enable_nat', 'virtual_mac', 'dhcp_port',
         'sgi_management_iface_vlan', 'sgi_management_iface_ip_addr',
         'dev_vlan_in', 'dev_vlan_out', 'ovs_vlan_workaround'],
    )

    def __init__(self, *args, **kwargs):
        super(UplinkBridgeController, self).__init__(*args, **kwargs)

        self.config = self._get_config(kwargs['config'])
        self.logger.info("uplink bridge app config: %s", self.config)

    def _get_config(self, config_dict) -> namedtuple:

        enable_nat = config_dict.get('enable_nat', True)
        bridge_name = config_dict.get('uplink_bridge',
                                      self.UPLINK_OVS_BRIDGE_NAME)
        dhcp_port = config_dict.get('uplink_dhcp_port',
                                    self.UPLINK_DHCP_PORT_NAME)
        uplink_patch = config_dict.get('uplink_patch',
                                       self.UPLINK_PATCH_PORT_NAME)

        uplink_eth_port_name = config_dict.get('uplink_eth_port_name',
                                               self.DEFAULT_UPLINK_PORT_NANE)
        virtual_mac = config_dict.get('virtual_mac',
                                      self.DEFAULT_UPLINK_MAC)
        sgi_management_iface_vlan = config_dict.get('sgi_management_iface_vlan', "")
        sgi_management_iface_ip_addr = config_dict.get('sgi_management_iface_ip_addr', "")
        dev_vlan_in = config_dict.get('dev_vlan_in', self.DEFAULT_DEV_VLAN_IN)
        dev_vlan_out = config_dict.get('dev_vlan_out', self.DEFAULT_DEV_VLAN_OUT)
        ovs_vlan_workaround = config_dict.get('ovs_vlan_workaround', True)
        return self.UplinkConfig(
            enable_nat=enable_nat,
            uplink_bridge=bridge_name,
            uplink_eth_port_name=uplink_eth_port_name,
            virtual_mac=virtual_mac,
            uplink_patch=uplink_patch,
            dhcp_port=dhcp_port,
            sgi_management_iface_vlan=sgi_management_iface_vlan,
            sgi_management_iface_ip_addr=sgi_management_iface_ip_addr,
            dev_vlan_in=dev_vlan_in,
            dev_vlan_out=dev_vlan_out,
            ovs_vlan_workaround=ovs_vlan_workaround
        )

    def initialize_on_connect(self, datapath):
        if self.config.enable_nat is True:
            self._delete_all_flows()
            self._del_eth_port()
            return

        self._delete_all_flows()
        self._add_eth_port()
        self._set_vlan_eth_port()
        self._setup_vlan_pop_dev()
        # flows to forward traffic between patch port to eth port

        # 1. DHCP traffic
        match = "in_port=%s,ip,udp,tp_dst=68" % self.config.uplink_eth_port_name
        actions = "output:%s,output:%s,output:LOCAL" % (self.config.dhcp_port,
                                                        self.config.uplink_patch)
        self._install_flow(flows.MAXIMUM_PRIORITY, match, actions)

        # 2.a. all egress traffic
        match = "in_port=%s,ip" % self.config.uplink_patch
        actions = "mod_dl_src=%s, output:%s" % (self.config.virtual_mac,
                                                self.config.uplink_eth_port_name)
        self._install_flow(flows.MEDIUM_PRIORITY, match, actions)

        if self.config.ovs_vlan_workaround:
            # 2.b. All ingress IP traffic for UE mac
            match = "in_port=%s,ip, dl_dst=%s, vlan_tci=0x0000/0x1000" % \
                    (self.config.uplink_eth_port_name,
                     self.config.virtual_mac)
            actions = "output:%s" % self.config.uplink_patch
            self._install_flow(flows.MEDIUM_PRIORITY, match, actions)

            match = "in_port=%s,ip, dl_dst=%s, vlan_tci=0x1000/0x1000" % \
                    (self.config.uplink_eth_port_name,
                     self.config.virtual_mac)
            actions = "strip_vlan,output:%s" % self.config.dev_vlan_in
            self._install_flow(flows.MEDIUM_PRIORITY, match, actions)

            # 2.c. redirect all vlan-out traffic to patch port
            match = "in_port=%s, dl_dst=%s, ip" % \
                    (self.config.dev_vlan_out,
                     self.config.virtual_mac)
            actions = "output:%s" % self.config.uplink_patch
            self._install_flow(flows.MEDIUM_PRIORITY, match, actions)
        else:
            # 2.b. All ingress IP traffic for UE mac
            match = "in_port=%s,ip, dl_dst=%s" % \
                    (self.config.uplink_eth_port_name,
                     self.config.virtual_mac)
            actions = "output:%s" % self.config.uplink_patch
            self._install_flow(flows.MEDIUM_PRIORITY, match, actions)

        # 3.a. drop all packets from vlan_in
        match = "in_port=%s" % self.config.dev_vlan_in
        actions = "drop"
        self._install_flow(flows.MEDIUM_PRIORITY, match, actions)

        # 3.b. drop all remaining packets form vlan_out
        match = "in_port=%s" % self.config.dev_vlan_out
        actions = "drop"
        self._install_flow(flows.MINIMUM_PRIORITY + 1, match, actions)

        # everything else:
        self._install_flow(flows.MINIMUM_PRIORITY, "", "NORMAL")
        self._set_sgi_ip_addr(self.config.uplink_bridge)

    def cleanup_on_disconnect(self, datapath):
        self._del_eth_port()
        self._delete_all_flows()

    def delete_all_flows(self, datapath):
        self._delete_all_flows()

    def _delete_all_flows(self):
        if self.config.uplink_bridge is None:
            return
        del_flows = "ovs-ofctl del-flows %s" % self.config.uplink_bridge
        self.logger.info("Delete all flows: %s", del_flows)
        try:
            subprocess.Popen(del_flows, shell=True).wait()
        except subprocess.CalledProcessError as ex:
            raise Exception('Error: %s failed with: %s' % (del_flows, ex))

    def _install_flow(self, priority: int, flow_match: str, flow_action: str):
        if self.config.enable_nat is True:
            return
        flow_cmd = "ovs-ofctl add-flow %s \"priority=%s,%s, actions=%s\"" % (
            self.config.uplink_bridge, priority,
            flow_match, flow_action)

        self.logger.info("Create flow %s", flow_cmd)

        try:
            subprocess.Popen(flow_cmd, shell=True).wait()
        except subprocess.CalledProcessError as ex:
            raise Exception('Error: %s failed with: %s' % (flow_cmd, ex))

    def _add_eth_port(self):
        if self.config.enable_nat is True or \
                self.config.uplink_eth_port_name is None:
            return
        self._cleanup_if(self.config.uplink_eth_port_name, True)
        # Add eth interface to OVS.
        ovs_add_port = "ovs-vsctl --may-exist add-port %s %s" \
                       % (self.config.uplink_bridge, self.config.uplink_eth_port_name)
        try:
            subprocess.Popen(ovs_add_port, shell=True).wait()
        except subprocess.CalledProcessError as ex:
            raise Exception('Error: %s failed with: %s' % (ovs_add_port, ex))

        self.logger.info("Add uplink port: %s", ovs_add_port)

    def _set_vlan_eth_port(self):
        if self.config.uplink_bridge is None:
            return

        if self.config.sgi_management_iface_vlan == '':
            vlan_cmd = "ovs-vsctl clear port %s tag" \
                       % self.config.uplink_bridge
        else:
            vlan_cmd = "ovs-vsctl set port %s tag=%s" \
                       % (self.config.uplink_bridge,
                          self.config.sgi_management_iface_vlan)

        self.logger.info("Vlan set port: %s", vlan_cmd)
        try:
            subprocess.Popen(vlan_cmd, shell=True).wait()
        except subprocess.CalledProcessError as ex:
            raise Exception('Error: %s failed with: %s' % (vlan_cmd, ex))

    def _del_eth_port(self):
        self._cleanup_if(self.config.uplink_bridge, True)

        ovs_rem_port = "ovs-vsctl --if-exists del-port %s %s" \
                       % (self.config.uplink_bridge, self.config.uplink_eth_port_name)
        try:
            subprocess.Popen(ovs_rem_port, shell=True).wait()
            self.logger.info("Remove ovs uplink port: %s", ovs_rem_port)
        except subprocess.CalledProcessError as ex:
            self.logger.debug("ignore port del error: %s ", ex)

        self._set_sgi_ip_addr(self.config.uplink_eth_port_name)

    def _set_sgi_ip_addr(self, if_name: str):
        self.logger.debug("self.config.sgi_management_iface_ip_addr %s",
                          self.config.sgi_management_iface_ip_addr)
        if self.config.sgi_management_iface_ip_addr is None or \
                self.config.sgi_management_iface_ip_addr == "":
            if if_name == self.config.uplink_bridge:
                self._restart_dhclient(if_name)
            else:
                # for system port, use networking config
                if_up_cmd = ["ifup", if_name]
                try:
                    subprocess.check_call(if_up_cmd)
                except subprocess.CalledProcessError as ex:
                    self.logger.info("could not bring up if: %s, %s",
                                     if_up_cmd, ex)
            return

        try:
            # Kill dhclient if running.
            pgrep_out = subprocess.Popen(["pgrep", "-f",
                                          "dhclient.*" + if_name],
                                         stdout=subprocess.PIPE)
            for pid in pgrep_out.stdout.readlines():
                subprocess.check_call(["kill", pid.strip()])

            flush_ip = ["ip", "addr", "flush",
                        "dev", if_name]
            subprocess.check_call(flush_ip)

            set_ip_cmd = ["ip",
                          "addr", "add",
                          self.config.sgi_management_iface_ip_addr,
                          "dev",
                          if_name]
            subprocess.check_call(set_ip_cmd)
            self.logger.debug("SGi ip address config: [%s]", set_ip_cmd)
        except subprocess.SubprocessError as e:
            self.logger.warning("Error while setting SGi IP: %s", e)

    def _restart_dhclient(self, if_name):
        # restart DHCP client can take loooong time, process it in separate thread:
        threading.Thread(target=self._restart_dhclient_if(if_name))

    def _setup_vlan_pop_dev(self):
        if self.config.ovs_vlan_workaround:
            # Create device
            BridgeTools.create_veth_pair(self.config.dev_vlan_in,
                                         self.config.dev_vlan_out)
            # Add to OVS,
            # OFP requested port (70 and 71) no are for test validation,
            # its not used anywhere else.
            BridgeTools.add_ovs_port(self.config.uplink_bridge,
                                     self.config.dev_vlan_in, "70")
            BridgeTools.add_ovs_port(self.config.uplink_bridge,
                                     self.config.dev_vlan_out, "71")

    def _cleanup_if(self, if_name, flush: bool):
        # Release eth IP first.
        release_eth_ip = ["dhclient", "-r", if_name]
        try:
            subprocess.check_call(release_eth_ip)
        except subprocess.CalledProcessError as ex:
            self.logger.info("could not release dhcp lease: %s, %s",
                             release_eth_ip, ex)

        if not flush:
            return
        flush_eth_ip = ["ip", "addr", "flush", "dev", if_name]
        try:
            subprocess.check_call(flush_eth_ip)
        except subprocess.CalledProcessError as ex:
            self.logger.info("could not flush ip addr: %s, %s",
                             flush_eth_ip, ex)

        self.logger.info("SGi DHCP: port [%s] ip removed", if_name)

    def _restart_dhclient_if(self, if_name):
        self._cleanup_if(if_name, False)

        setup_dhclient = ["dhclient", if_name]
        try:
            subprocess.check_call(setup_dhclient)
        except subprocess.CalledProcessError as ex:
            self.logger.info("could not release dhcp lease: %s, %s",
                             setup_dhclient, ex)

        self.logger.info("SGi DHCP: restart for %s done", if_name)
