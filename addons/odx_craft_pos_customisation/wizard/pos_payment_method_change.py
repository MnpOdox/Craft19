from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PosPaymentMethodChangeWizard(models.TransientModel):
    _name = "pos.payment.method.change.wizard"
    _description = "POS Payment Method Change"

    pos_order_id = fields.Many2one("pos.order", string="POS Order", required=True, readonly=True)
    payment_id = fields.Many2one(
        "pos.payment",
        string="Payment Line",
        required=True,
        domain="[('id', 'in', available_payment_ids)]",
    )
    available_payment_ids = fields.Many2many(
        "pos.payment",
        compute="_compute_available_payment_ids",
    )
    amount = fields.Monetary(string="Amount", related="payment_id.amount", readonly=True)
    currency_id = fields.Many2one("res.currency", related="pos_order_id.currency_id", readonly=True)
    old_payment_method_id = fields.Many2one(
        "pos.payment.method",
        string="Current Payment Method",
        related="payment_id.payment_method_id",
        readonly=True,
    )
    new_payment_method_id = fields.Many2one(
        "pos.payment.method",
        string="New Payment Method",
        required=True,
        domain="[('id', 'in', available_payment_method_ids)]",
    )
    available_payment_method_ids = fields.Many2many(
        "pos.payment.method",
        compute="_compute_available_payment_method_ids",
    )
    reason = fields.Text(string="Reason", required=True)

    @api.depends("pos_order_id")
    def _compute_available_payment_ids(self):
        for wizard in self:
            wizard.available_payment_ids = wizard.pos_order_id.payment_ids.filtered(lambda p: not p.is_change)

    @api.depends("pos_order_id")
    def _compute_available_payment_method_ids(self):
        for wizard in self:
            wizard.available_payment_method_ids = wizard.pos_order_id.available_payment_method_ids

    @api.onchange("payment_id")
    def _onchange_payment_id(self):
        if self.payment_id:
            self.new_payment_method_id = self.payment_id.payment_method_id

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        order = self.env["pos.order"].browse(vals.get("pos_order_id"))
        if order:
            order._ensure_payment_method_change_allowed()
            payment = order.payment_ids.filtered(lambda p: not p.is_change)[:1]
            if payment and "payment_id" in fields_list:
                vals.setdefault("payment_id", payment.id)
                vals.setdefault("new_payment_method_id", payment.payment_method_id.id)
        return vals

    def action_apply(self):
        self.ensure_one()
        order = self.pos_order_id
        order._ensure_payment_method_change_allowed()
        payment = self.payment_id
        if payment.pos_order_id != order:
            raise UserError(_("The selected payment line does not belong to this order."))
        if payment.is_change:
            raise UserError(_("Change lines cannot be edited."))
        if self.new_payment_method_id not in order.available_payment_method_ids:
            raise UserError(_("The selected payment method is not allowed for this POS configuration."))
        if payment.payment_method_id == self.new_payment_method_id:
            raise UserError(_("Please choose a different payment method."))

        old_method = payment.payment_method_id
        payment.write({"payment_method_id": self.new_payment_method_id.id})
        order.message_post(
            body=_(
                "Payment method changed on payment line %(payment)s: %(old)s → %(new)s.<br/>Reason: %(reason)s",
                payment=payment.display_name,
                old=old_method.display_name,
                new=self.new_payment_method_id.display_name,
                reason=self.reason,
            )
        )
        return {"type": "ir.actions.act_window_close"}
