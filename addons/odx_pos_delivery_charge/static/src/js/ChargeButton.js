odoo.define('odx_pos_delivery_charge.chargebutton', function(require) {
'use strict';

   const { Gui } = require('point_of_sale.Gui');
   const PosComponent = require('point_of_sale.PosComponent');
   const { posbus } = require('point_of_sale.utils');
   const ProductScreen = require('point_of_sale.ProductScreen');
   const { useListener } = require('web.custom_hooks');
   const Registries = require('point_of_sale.Registries');
   const PaymentScreen = require('point_of_sale.PaymentScreen');
   const models = require('point_of_sale.models');

   models.load_fields('pos.config', ['del_charge','del_charge_pro_id']);

   class PosChargeButtons extends PosComponent {

       constructor() {

           super(...arguments);
           useListener('click', this.onClick);
       }
       is_available() {
           const order = this.env.pos.get_order();
           return order
       }
       async onClick() {
            var self = this;
            const { confirmed, payload } = await this.showPopup('NumberPopup',{
                title: this.env._t('Charge'),

            });
            if (confirmed) {
        const val = parseFloat(payload);
        await self.apply_charge(val);
    }
        }

        async apply_charge(pc) {
            var order    = this.env.pos.get_order();
            var lines    = order.get_orderlines();
            var product  = this.env.pos.db.get_product_by_id(this.env.pos.config.del_charge_pro_id[0]);
            if (product === undefined) {
                await this.showPopup('ErrorPopup', {
                    title : this.env._t("No charge product found"),
                    body  : this.env._t("Please Select the Product  first"),
                });
                return;
            }

            // Remove existing discounts
//            var i = 0;
//            while ( i < lines.length ) {
//                if (lines[i].get_product() === product) {
//                    order.remove_orderline(lines[i]);
//                } else {
//                    i++;
//                }
//            }

            var charge = pc


            if( charge > 0 ){
                order.add_product(product, {
                    price: charge,
                    lst_price: charge,
                    extras: {
                        price_manually_set: true,
                    },
                });
            }
        }





   }
   PosChargeButtons.template = 'PosChargeButtons';
   ProductScreen.addControlButton({
       component: PosChargeButtons,
       condition: function() {

            // to hide when boolean false

               const config = this.env.pos.config;
        return config && config.del_charge !== false;

       },


   });
   Registries.Component.add(PosChargeButtons);
   return PosChargeButtons;
});