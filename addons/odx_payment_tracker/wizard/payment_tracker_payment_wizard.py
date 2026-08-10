from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare


class PaymentTrackerPaymentWizard(models.TransientModel):
    _name = "payment.tracker.payment.wizard"
    _description = "Record Tracker Payment"

    schedule_id = fields.Many2one("payment.tracker.schedule", required=True, readonly=True)
    company_id = fields.Many2one(related="schedule_id.company_id", readonly=True)
    currency_id = fields.Many2one(related="schedule_id.currency_id", readonly=True)
    payment_date = fields.Date(required=True, default=fields.Date.context_today)
    amount = fields.Monetary(required=True, currency_field="currency_id")
    source_type = fields.Selection([("cash", "Cash"), ("bank", "Bank")], required=True, default="cash")
    cash_book_id = fields.Many2one("cash.book", domain="[('company_id', '=', company_id), ('state', '=', 'confirm')]")
    bank_book_id = fields.Many2one("bank.book", domain="[('company_id', '=', company_id), ('state', '=', 'confirm')]")
    allowed_head_ids = fields.Many2many("book.head", compute="_compute_allowed_head_ids")
    head_id = fields.Many2one("book.head", required=True, domain="[('id', 'in', allowed_head_ids)]")
    partner_id = fields.Many2one("res.partner", string="Partner / Payee")
    description = fields.Char(required=True)
    reference = fields.Char()
    note = fields.Text()

    @api.onchange("schedule_id")
    def _onchange_schedule_id(self):
        if self.schedule_id:
            self.description = self.schedule_id.title
            self.partner_id = self.schedule_id.partner_id

    @api.onchange("source_type")
    def _onchange_source_type(self):
        self.cash_book_id = False
        self.bank_book_id = False
        self.head_id = False

    @api.depends("source_type")
    def _compute_allowed_head_ids(self):
        head_model = self.env["book.head"]
        for wizard in self:
            wizard.allowed_head_ids = head_model.search([
                ("cash" if wizard.source_type == "cash" else "bank", "=", True)
            ]) if wizard.source_type else head_model.browse()

    def action_confirm(self):
        self.ensure_one()
        self.schedule_id._check_manager()
        if self.schedule_id.state not in ("confirmed", "partial"):
            raise UserError(_("This schedule is no longer available for payment."))
        if self.amount <= 0:
            raise ValidationError(_("Enter a payment amount greater than zero."))
        if float_compare(self.amount, self.schedule_id.remaining_amount,
                         precision_rounding=self.currency_id.rounding) > 0:
            raise ValidationError(_("The payment cannot exceed the remaining scheduled amount."))
        book = self.cash_book_id if self.source_type == "cash" else self.bank_book_id
        if not book:
            raise ValidationError(_("Select the active %s Book.", self.source_type.title()))
        if book.company_id != self.company_id or book.state != "confirm":
            raise ValidationError(_("The selected Book must be active for the schedule company."))
        if not (book.start_date <= self.payment_date <= book.end_date):
            raise ValidationError(_("Payment date must fall within the selected Book period."))
        if not self.head_id[self.source_type]:
            raise ValidationError(_("The selected head is not available for this Book type."))
        payment = self.env["payment.tracker.payment"].create({
            "schedule_id": self.schedule_id.id, "payment_date": self.payment_date,
            "amount": self.amount, "source_type": self.source_type,
            "cash_book_id": self.cash_book_id.id, "bank_book_id": self.bank_book_id.id,
            "head_id": self.head_id.id, "partner_id": self.partner_id.id,
            "description": self.description, "reference": self.reference, "note": self.note,
        })
        line_model = "cash.book.line" if self.source_type == "cash" else "bank.book.line"
        line = self.env[line_model].create({
            "date": self.payment_date, "head_id": self.head_id.id,
            "description": self.description, "amount": -self.amount,
            "name_id": book.id, "company_id": self.company_id.id,
            "partner_id": self.partner_id.id,
            "payment_tracker_payment_id": payment.id,
        })
        payment.write({("cash_book_line_id" if self.source_type == "cash" else "bank_book_line_id"): line.id})
        self.schedule_id._compute_amounts()
        self.schedule_id._refresh_state()
        return {"type": "ir.actions.act_window_close"}
