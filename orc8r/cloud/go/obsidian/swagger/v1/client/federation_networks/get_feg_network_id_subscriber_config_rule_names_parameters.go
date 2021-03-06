// Code generated by go-swagger; DO NOT EDIT.

package federation_networks

// This file was generated by the swagger tool.
// Editing this file might prove futile when you re-run the swagger generate command

import (
	"context"
	"net/http"
	"time"

	"github.com/go-openapi/errors"
	"github.com/go-openapi/runtime"
	cr "github.com/go-openapi/runtime/client"

	strfmt "github.com/go-openapi/strfmt"
)

// NewGetFegNetworkIDSubscriberConfigRuleNamesParams creates a new GetFegNetworkIDSubscriberConfigRuleNamesParams object
// with the default values initialized.
func NewGetFegNetworkIDSubscriberConfigRuleNamesParams() *GetFegNetworkIDSubscriberConfigRuleNamesParams {
	var ()
	return &GetFegNetworkIDSubscriberConfigRuleNamesParams{

		timeout: cr.DefaultTimeout,
	}
}

// NewGetFegNetworkIDSubscriberConfigRuleNamesParamsWithTimeout creates a new GetFegNetworkIDSubscriberConfigRuleNamesParams object
// with the default values initialized, and the ability to set a timeout on a request
func NewGetFegNetworkIDSubscriberConfigRuleNamesParamsWithTimeout(timeout time.Duration) *GetFegNetworkIDSubscriberConfigRuleNamesParams {
	var ()
	return &GetFegNetworkIDSubscriberConfigRuleNamesParams{

		timeout: timeout,
	}
}

// NewGetFegNetworkIDSubscriberConfigRuleNamesParamsWithContext creates a new GetFegNetworkIDSubscriberConfigRuleNamesParams object
// with the default values initialized, and the ability to set a context for a request
func NewGetFegNetworkIDSubscriberConfigRuleNamesParamsWithContext(ctx context.Context) *GetFegNetworkIDSubscriberConfigRuleNamesParams {
	var ()
	return &GetFegNetworkIDSubscriberConfigRuleNamesParams{

		Context: ctx,
	}
}

// NewGetFegNetworkIDSubscriberConfigRuleNamesParamsWithHTTPClient creates a new GetFegNetworkIDSubscriberConfigRuleNamesParams object
// with the default values initialized, and the ability to set a custom HTTPClient for a request
func NewGetFegNetworkIDSubscriberConfigRuleNamesParamsWithHTTPClient(client *http.Client) *GetFegNetworkIDSubscriberConfigRuleNamesParams {
	var ()
	return &GetFegNetworkIDSubscriberConfigRuleNamesParams{
		HTTPClient: client,
	}
}

/*GetFegNetworkIDSubscriberConfigRuleNamesParams contains all the parameters to send to the API endpoint
for the get feg network ID subscriber config rule names operation typically these are written to a http.Request
*/
type GetFegNetworkIDSubscriberConfigRuleNamesParams struct {

	/*NetworkID
	  Network ID

	*/
	NetworkID string

	timeout    time.Duration
	Context    context.Context
	HTTPClient *http.Client
}

// WithTimeout adds the timeout to the get feg network ID subscriber config rule names params
func (o *GetFegNetworkIDSubscriberConfigRuleNamesParams) WithTimeout(timeout time.Duration) *GetFegNetworkIDSubscriberConfigRuleNamesParams {
	o.SetTimeout(timeout)
	return o
}

// SetTimeout adds the timeout to the get feg network ID subscriber config rule names params
func (o *GetFegNetworkIDSubscriberConfigRuleNamesParams) SetTimeout(timeout time.Duration) {
	o.timeout = timeout
}

// WithContext adds the context to the get feg network ID subscriber config rule names params
func (o *GetFegNetworkIDSubscriberConfigRuleNamesParams) WithContext(ctx context.Context) *GetFegNetworkIDSubscriberConfigRuleNamesParams {
	o.SetContext(ctx)
	return o
}

// SetContext adds the context to the get feg network ID subscriber config rule names params
func (o *GetFegNetworkIDSubscriberConfigRuleNamesParams) SetContext(ctx context.Context) {
	o.Context = ctx
}

// WithHTTPClient adds the HTTPClient to the get feg network ID subscriber config rule names params
func (o *GetFegNetworkIDSubscriberConfigRuleNamesParams) WithHTTPClient(client *http.Client) *GetFegNetworkIDSubscriberConfigRuleNamesParams {
	o.SetHTTPClient(client)
	return o
}

// SetHTTPClient adds the HTTPClient to the get feg network ID subscriber config rule names params
func (o *GetFegNetworkIDSubscriberConfigRuleNamesParams) SetHTTPClient(client *http.Client) {
	o.HTTPClient = client
}

// WithNetworkID adds the networkID to the get feg network ID subscriber config rule names params
func (o *GetFegNetworkIDSubscriberConfigRuleNamesParams) WithNetworkID(networkID string) *GetFegNetworkIDSubscriberConfigRuleNamesParams {
	o.SetNetworkID(networkID)
	return o
}

// SetNetworkID adds the networkId to the get feg network ID subscriber config rule names params
func (o *GetFegNetworkIDSubscriberConfigRuleNamesParams) SetNetworkID(networkID string) {
	o.NetworkID = networkID
}

// WriteToRequest writes these params to a swagger request
func (o *GetFegNetworkIDSubscriberConfigRuleNamesParams) WriteToRequest(r runtime.ClientRequest, reg strfmt.Registry) error {

	if err := r.SetTimeout(o.timeout); err != nil {
		return err
	}
	var res []error

	// path param network_id
	if err := r.SetPathParam("network_id", o.NetworkID); err != nil {
		return err
	}

	if len(res) > 0 {
		return errors.CompositeValidationError(res...)
	}
	return nil
}
