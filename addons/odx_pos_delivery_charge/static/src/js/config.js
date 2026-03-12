odoo.define('odx_pos_delivery_charge.NumpadWidget', function (require) {
    'use strict';

    const NumpadWidget = require('point_of_sale.NumpadWidget');
    const Registries = require('point_of_sale.Registries');
    const models = require('point_of_sale.models');


    models.load_fields('pos.config', ['disc_button', 'price_button']);

    const PosFrNumpadWidget = NumpadWidget => class extends NumpadWidget {
        mounted() {

        if (this.env.pos.config.disc_button){
        $('.discbutton').hide()

        }

        if (this.env.pos.config.price_button) {

        $('.Pricebutton').hide()

            }
        }
    };

    Registries.Component.extend(NumpadWidget, PosFrNumpadWidget);

    return NumpadWidget;


});






