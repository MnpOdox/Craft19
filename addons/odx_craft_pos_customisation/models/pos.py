from odoo import models, fields, api, _
from datetime import date
import logging

_logger = logging.getLogger(__name__)

class Pos(models.Model):
    _inherit = 'pos.order'


    order_status = fields.Selection([
        ('Billing', 'Billing'),
        ('Picking','Picking'),
        ('Packing', 'Packing'),
        ('Ready To Ship', 'Ready To Ship'),
        ('Shipped', 'Shipped')
        # ('Delivery', 'Delivery'),

    ], string='Order Status',required = True, default = 'Billing',
    )


    courier_id = fields.Many2one('shipment.ship',string="Courier", readonly=True)
    tracking_number = fields.Char(string = "Tracking Number", readonly=True)

    online_order = fields.Boolean(string = 'Online order')


    # def batch_order_status_change(self):
    #     for order in self:
    #         if order.order_status == 'Billing':
    #             self.write({'order_status': 'Shipped'})



    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('order_status'):
                vals['order_status'] = 'Billing'
        return super().create(vals_list)

    def write(self, vals):
        if 'order_status' in vals and not vals.get('order_status'):
            vals['order_status'] = 'Billing'
        return super().write(vals)

    @api.model
    def order_online(self,check_value,name):
        orders = self.env['pos.order'].search([('pos_reference','=',name)],limit=1)
        orders.write({'online_order':check_value})

    @api.model
    def _load_pos_data_fields(self, config):
        result = super()._load_pos_data_fields(config)
        # In Odoo 19 an empty list means "load all fields".
        # Keep that behavior to avoid dropping required core fields.
        if not result:
            return result
        for name in ("online_order", "order_status", "courier_id", "tracking_number"):
            if name not in result:
                result.append(name)
        return result
