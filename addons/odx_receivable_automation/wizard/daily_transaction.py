from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class DailyBookTransaction(models.TransientModel):
    _name = "daily.book.transaction"
    _description = "Daily Transaction Entry"

    transaction_type = fields.Selection([
        ("expense", "Expense"),
        ("cash_in", "Cash In"),
        ("cash_out", "Cash Out"),
        ("bank_in", "Bank In"),
        ("bank_out", "Bank Out"),
    ], string="Transaction Type", required=True, default="expense")
    date = fields.Date(required=True, default=fields.Date.context_today)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company,
    )
    available_head_ids = fields.Many2many(
        "book.head", compute="_compute_available_head_ids",
    )
    head_id = fields.Many2one(
        "book.head", string="Head", required=True,
        domain="[('id', 'in', available_head_ids)]",
    )
    partner_required = fields.Boolean(compute="_compute_partner_required")
    partner_id = fields.Many2one(
        "res.partner", string="Partner",
        domain="[('has_confirmed_receivable_book', '=', True)]",
    )
    amount = fields.Float(string="Amount", required=True)
    description = fields.Char(string="Description")
    cash_book_id = fields.Many2one(
        "cash.book", string="Cash Book",
        domain="[('company_id', '=', company_id), ('state', '=', 'confirm'), ('start_date', '<=', date), ('end_date', '>=', date)]",
    )
    bank_book_id = fields.Many2one(
        "bank.book", string="Bank Book",
        domain="[('company_id', '=', company_id), ('state', '=', 'confirm'), ('start_date', '<=', date), ('end_date', '>=', date)]",
    )

    @api.model
    def default_get(self, field_list):
        values = super().default_get(field_list)
        transaction_type = values.get("transaction_type", "expense")
        transaction_date = values.get("date") or fields.Date.context_today(self)
        company_id = values.get("company_id") or self.env.company.id
        domain = [
            ("company_id", "=", company_id),
            ("state", "=", "confirm"),
            ("start_date", "<=", transaction_date),
            ("end_date", ">=", transaction_date),
        ]
        if transaction_type.startswith("cash_") and "cash_book_id" in field_list:
            books = self.env["cash.book"].search(domain)
            if len(books) == 1:
                values["cash_book_id"] = books.id
        elif transaction_type.startswith("bank_") and "bank_book_id" in field_list:
            books = self.env["bank.book"].search(domain)
            if len(books) == 1:
                values["bank_book_id"] = books.id
        return values

    @api.depends("transaction_type")
    def _compute_available_head_ids(self):
        head_model = self.env["book.head"]
        for wizard in self:
            if wizard.transaction_type == "expense":
                domain = [("expense", "=", True)]
            elif wizard.transaction_type.startswith("cash_"):
                domain = [("cash", "=", True)]
            else:
                domain = [("bank", "=", True)]
            wizard.available_head_ids = head_model.search(domain)

    @api.depends("head_id", "transaction_type")
    def _compute_partner_required(self):
        for wizard in self:
            wizard.partner_required = bool(
                wizard.head_id
                and (
                    wizard.head_id.receivable_payment_effect != "none"
                    or (
                        wizard.transaction_type == "expense"
                        and wizard.head_id.auto_receivable_expense
                    )
                )
            )

    @api.onchange("transaction_type", "date", "company_id")
    def _onchange_transaction_context(self):
        self.head_id = False
        self.partner_id = False
        self.cash_book_id = False
        self.bank_book_id = False
        if not self.date or not self.company_id:
            return
        date_domain = [
            ("company_id", "=", self.company_id.id),
            ("state", "=", "confirm"),
            ("start_date", "<=", self.date),
            ("end_date", ">=", self.date),
        ]
        if self.transaction_type.startswith("cash_"):
            books = self.env["cash.book"].search(date_domain)
            if len(books) == 1:
                self.cash_book_id = books
        elif self.transaction_type.startswith("bank_"):
            books = self.env["bank.book"].search(date_domain)
            if len(books) == 1:
                self.bank_book_id = books

    @api.onchange("head_id")
    def _onchange_head_id(self):
        self.partner_id = False
        if self.head_id and not self.description:
            self.description = self.head_id.head_name

    def _validate_entry(self):
        self.ensure_one()
        if self.amount <= 0:
            raise ValidationError(_("Enter an amount greater than zero."))
        if self.partner_required and not self.partner_id:
            raise ValidationError(_("Select a Partner with a Confirmed Receivable Book."))
        if self.transaction_type.startswith("cash_") and not self.cash_book_id:
            raise ValidationError(_("Select an active Cash Book covering the transaction date."))
        if self.transaction_type.startswith("bank_") and not self.bank_book_id:
            raise ValidationError(_("Select an active Bank Book covering the transaction date."))

    def _create_transaction(self):
        self.ensure_one()
        self._validate_entry()
        common_vals = {
            "date": self.date,
            "head_id": self.head_id.id,
            "partner_id": self.partner_id.id,
            "description": self.description or self.head_id.head_name,
            "company_id": self.company_id.id,
        }
        if self.transaction_type == "expense":
            return self.env["expense.book"].create({
                **common_vals,
                "amount": self.amount,
            })
        is_in = self.transaction_type.endswith("_in")
        line_amount = self.amount if is_in else -self.amount
        if self.transaction_type.startswith("cash_"):
            return self.env["cash.book.line"].create({
                **common_vals,
                "name_id": self.cash_book_id.id,
                "amount": line_amount,
            })
        return self.env["bank.book.line"].create({
            **common_vals,
            "name_id": self.bank_book_id.id,
            "amount": line_amount,
        })

    def action_save(self):
        self._create_transaction()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Transaction Saved"),
                "message": _("The entry was added to the correct Book."),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def action_save_and_new(self):
        self._create_transaction()
        action_by_type = {
            "expense": "odx_receivable_automation.action_daily_expense",
            "cash_in": "odx_receivable_automation.action_daily_cash_transaction",
            "cash_out": "odx_receivable_automation.action_daily_cash_transaction",
            "bank_in": "odx_receivable_automation.action_daily_bank_transaction",
            "bank_out": "odx_receivable_automation.action_daily_bank_transaction",
        }
        return self.env["ir.actions.actions"]._for_xml_id(
            action_by_type[self.transaction_type]
        )
