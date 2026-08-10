from odoo import fields, models


class CashBookLine(models.Model):
    _inherit = "cash.book.line"

    payment_tracker_payment_id = fields.Many2one(
        "payment.tracker.payment", string="Tracker Payment", readonly=True,
        copy=False, ondelete="restrict", index=True,
    )

    _tracker_payment_unique = models.Constraint(
        "UNIQUE(payment_tracker_payment_id)",
        "A tracker payment can create only one Cash Book line.",
    )


class BankBookLine(models.Model):
    _inherit = "bank.book.line"

    payment_tracker_payment_id = fields.Many2one(
        "payment.tracker.payment", string="Tracker Payment", readonly=True,
        copy=False, ondelete="restrict", index=True,
    )

    _tracker_payment_unique = models.Constraint(
        "UNIQUE(payment_tracker_payment_id)",
        "A tracker payment can create only one Bank Book line.",
    )
