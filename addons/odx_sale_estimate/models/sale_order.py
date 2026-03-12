from odoo import _, api, fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    # payment_method = fields.Selection([
    #     ('cash', 'Cash'),
    #     ('bank', 'Bank'),
    #     ('credit', 'Credit'),
    #     ], default='cash',copy=False)

    def action_confirm(self):
        result = super(SaleOrder, self).action_confirm()
        for order in self:
            if order.company_id.automatic_delivery_validation:
                if order.picking_ids:
                    for picking in order.picking_ids:
                        if picking.state == 'assigned':
                            for picking_line in picking.move_ids_without_package:
                                picking_line.quantity_done = picking_line.product_uom_qty
                                picking_line.forecast_availability = picking_line.product_uom_qty
                            picking.button_validate()
        return result
