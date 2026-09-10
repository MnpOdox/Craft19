from odoo import models, fields, api, _
from datetime import date
import logging

_logger = logging.getLogger(__name__)

class Pos(models.Model):
    _inherit = 'pos.order'

    courier_id = fields.Many2one('shipment.ship',string="Courier", readonly=True)
    tracking_number = fields.Char(string = "Tracking Number", readonly=True)

    online_order = fields.Boolean(string = 'Online order')
    crm_sale = fields.Boolean(string="CRM Sale")
    other_state_sale = fields.Boolean(string="Other State Sale")

    @api.model
    def order_online(self,check_value,name):
        orders = self.env['pos.order'].search([('pos_reference','=',name)],limit=1)
        orders.write({'online_order':check_value})

    @api.onchange("online_order", "crm_sale")
    def _onchange_sale_type_other_state(self):
        if not self.online_order and not self.crm_sale:
            self.other_state_sale = False

    @api.model
    def _load_pos_data_fields(self, config):
        result = super()._load_pos_data_fields(config)
        # In Odoo 19 an empty list means "load all fields".
        # Keep that behavior to avoid dropping required core fields.
        if not result:
            return result
        for name in (
            "online_order",
            "crm_sale",
            "other_state_sale",
            "courier_id",
            "tracking_number",
        ):
            if name not in result:
                result.append(name)
        return result
