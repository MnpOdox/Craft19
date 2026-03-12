from odoo import fields, models,api

class PosReport(models.Model):
    _inherit = 'pos.order'
    def print_order(self):
            data = {}

            return self.env.ref('odx_pos_receipt_custom.pos_order_receipt').with_context(landscape=False).report_action(self, data=data, config=False)

# class Poscontact(models.Model):
#     _inherit = 'res.partner'
#
# class Poscontacts(models.Model):
#     _inherit = 'res.users'
