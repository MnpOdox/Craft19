odoo.define('odx_craft_pos_customisation.PhoneRequired', function(require) {
    'use strict';

    const { _t } = require('web.core');
    const { getDataURLFromFile } = require('web.utils');
    const PosComponent = require('point_of_sale.PosComponent');
    const Registries = require('point_of_sale.Registries');
    var PhoneRequired = require('point_of_sale.ClientDetailsEdit');

    const MyPhoneRequired = (PhoneRequired) =>
    class ClientDetailsEdit extends PosComponent{
//    template: 'ClientDetailsEdit',


//        events: {
//
//            'change .detail client-address-country needsclick': 'onclick_pos_country',
///
//        },


    constructor() {
            super(...arguments);
            this.intFields = ['country_id', 'state_id', 'property_product_pricelist'];
            const partner = this.props.partner;
            this.changes = {
                'country_id': partner.country_id && partner.country_id[0],
                'state_id': partner.state_id && partner.state_id[0],

            };
        }
        mounted() {
            this.env.bus.on('save-customer', this, this.saveChanges);
        }

    captureChange(event) {
                this.changes[event.target.name] = event.target.value;
            }
    saveChanges() {

            let processedChanges = {};
            for (let [key, value] of Object.entries(this.changes)) {
                if (this.intFields.includes(key)) {
                    processedChanges[key] = parseInt(value) || false;

                } else {
                    processedChanges[key] = value;
                }
            }
            if ((!this.props.partner.name && !processedChanges.name) ||
                processedChanges.name === '' ){
                return this.showPopup('ErrorPopup', {
                  title: _t('A Customer Name Is Required'),
                });
            }
            if ((!this.props.partner.phone && !processedChanges.phone) ||
                processedChanges.phone === '' ){
                return this.showPopup('ErrorPopup', {
                  title: _t('A Customer Phone Is Required'),
                });
            }

             if ((!this.props.partner.country_id && !processedChanges.country_id) ||
                processedChanges.country_id === '' ){
                return this.showPopup('ErrorPopup', {
                  title: _t('A Customer Country Is Required'),
                });
            }

            processedChanges.id = this.props.partner.id || false;
            this.trigger('save-changes', { processedChanges });

        }
        }
        Registries.Component.extend(PhoneRequired, MyPhoneRequired);

});
