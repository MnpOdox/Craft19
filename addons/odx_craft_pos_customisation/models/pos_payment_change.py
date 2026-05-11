from odoo import _, models
from odoo.exceptions import UserError


class PosOrder(models.Model):
    _inherit = "pos.order"

    def _ensure_payment_method_change_allowed(self):
        self.ensure_one()
        if self.state != "paid":
            raise UserError(_("Payment method can only be changed on paid POS orders."))
        if self.account_move:
            raise UserError(_("You cannot change the payment method once the POS order has been invoiced or posted."))
        if self.session_id.state in ("closing_control", "closed"):
            raise UserError(_("You cannot change the payment method after the POS session has started closing or is already closed."))
        if not self.payment_ids.filtered(lambda p: not p.is_change):
            raise UserError(_("There is no editable payment line on this order."))

    def action_open_payment_method_change_wizard(self):
        self.ensure_one()
        self._ensure_payment_method_change_allowed()
        return {
            "name": _("Change Payment Method"),
            "type": "ir.actions.act_window",
            "res_model": "pos.payment.method.change.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_pos_order_id": self.id,
            },
        }
