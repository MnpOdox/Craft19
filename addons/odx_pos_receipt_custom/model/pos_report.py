from odoo import models

class PosReport(models.Model):
    _inherit = 'pos.order'

    def _get_sale_type_label(self):
        self.ensure_one()
        if getattr(self, "crm_sale", False):
            return "CRM Sale"
        if getattr(self, "online_order", False):
            return "Online Sale"
        return "Walking Customer"

    def print_order(self):
            data = {}

            return self.env.ref('odx_pos_receipt_custom.pos_order_receipt').with_context(landscape=False).report_action(self, data=data, config=False)

# class Poscontact(models.Model):
#     _inherit = 'res.partner'
#
# class Poscontacts(models.Model):
#     _inherit = 'res.users'
