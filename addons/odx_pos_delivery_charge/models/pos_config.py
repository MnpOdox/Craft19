from odoo import api, fields, models, _, tools
from odoo.exceptions import ValidationError

class PosConfigInherit(models.Model):

    _inherit = 'pos.config'


    disc_button = fields.Boolean("Disc Button", default=True)
    price_button = fields.Boolean("Price Button", default=True)
    show_product_price = fields.Boolean("Show Product Price", default=True)

    # delivery charge boolean

    del_charge = fields.Boolean("Deliver Charge", default=True)
    del_charge_pro_id = fields.Many2one('product.product', string='Charge Product',
                                          domain="[('sale_ok', '=', True)]",
                                          help='The product used to model the discount.')

    @api.model
    def _load_pos_data_fields(self, config):
        result = super()._load_pos_data_fields(config)
        # In Odoo 19, an empty list means "load all fields".
        # Keep that behavior to avoid dropping required core keys
        # (e.g. use_pricelist) and only append when a concrete list exists.
        if not result:
            return result
        for name in ("disc_button", "price_button", "show_product_price", "del_charge", "del_charge_pro_id"):
            if name not in result:
                result.append(name)
        return result

    def _get_special_products(self):
        result = super()._get_special_products()
        return result | self.search([]).mapped("del_charge_pro_id")
