from odoo import _, models
from odoo.exceptions import UserError

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
        self.ensure_one()
        data = {}
        report_action = self.env["ir.actions.report"].search(
            [
                ("report_name", "=", "odx_pos_receipt_custom.pos_order_template"),
                ("model", "=", "pos.order"),
            ],
            limit=1,
        )
        if not report_action:
            report_action = self.env.ref(
                "odx_pos_receipt_custom.pos_order_receipt",
                raise_if_not_found=False,
            )
        if not report_action:
            raise UserError(
                _("POS receipt report action is missing. Please upgrade the POS Custom module.")
            )
        return report_action.with_context(landscape=False).report_action(
            self, data=data, config=False
        )

# class Poscontact(models.Model):
#     _inherit = 'res.partner'
#
# class Poscontacts(models.Model):
#     _inherit = 'res.users'
