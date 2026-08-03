from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = "res.partner"

    receivable_book_ids = fields.One2many(
        "receivable.book", "partner_id", string="Receivable Books",
    )
    has_confirmed_receivable_book = fields.Boolean(
        string="Has Confirmed Receivable Book",
        compute="_compute_has_confirmed_receivable_book", store=True, index=True,
    )

    @api.depends("receivable_book_ids", "receivable_book_ids.state")
    def _compute_has_confirmed_receivable_book(self):
        for partner in self:
            partner.has_confirmed_receivable_book = any(
                book.state == "confirm" for book in partner.receivable_book_ids
            )


class ReceivableBook(models.Model):
    _inherit = "receivable.book"

    @api.depends("receivable_line_ids.amount")
    def _compute_amount(self):
        for record in self:
            record.balance = sum(record.receivable_line_ids.mapped("amount"))

    @api.model
    def _get_or_create_automation_book(self, partner, company):
        book = self.sudo().search([
            ("partner_id", "=", partner.id),
            ("company_id", "=", company.id),
            ("state", "=", "confirm"),
        ], order="id desc", limit=1)
        if not book:
            book = self.sudo().create({
                "partner_id": partner.id,
                "company_id": company.id,
                "state": "confirm",
            })
        return book

    @api.model
    def _get_confirmed_automation_book(self, partner, company):
        book = self.sudo().search([
            ("partner_id", "=", partner.id),
            ("company_id", "=", company.id),
            ("state", "=", "confirm"),
        ], order="id desc", limit=1)
        if not book:
            raise ValidationError(_(
                "Partner %(partner)s does not have a Confirmed Receivable Book for %(company)s.",
                partner=partner.display_name,
                company=company.display_name,
            ))
        return book


class ReceivableBookLine(models.Model):
    _inherit = "receivable.book.line"

    partner_id = fields.Many2one(
        "res.partner", string="Partner",
        related="receivable_id.partner_id", store=True, readonly=True, index=True,
    )
    source_type = fields.Selection([
        ("purchase", "Purchase"),
        ("cash_payment", "Cash Payment"),
        ("bank_payment", "Bank Payment"),
        ("salary_expense", "Salary Expense"),
        ("credit_sale", "Credit Sale"),
    ], string="Source", readonly=True, copy=False)
    purchase_order_id = fields.Many2one(
        "purchase.order", string="Purchase Order", readonly=True, copy=False,
        ondelete="cascade", index=True,
    )
    cash_book_line_id = fields.Many2one(
        "cash.book.line", string="Cash Book Line", readonly=True, copy=False,
        ondelete="cascade", index=True,
    )
    bank_book_line_id = fields.Many2one(
        "bank.book.line", string="Bank Book Line", readonly=True, copy=False,
        ondelete="cascade", index=True,
    )
    expense_book_id = fields.Many2one(
        "expense.book", string="Expense", readonly=True, copy=False,
        ondelete="cascade", index=True,
    )
    pos_order_id = fields.Many2one(
        "pos.order", string="POS Order", readonly=True, copy=False,
        ondelete="cascade", index=True,
    )
    pos_session_id = fields.Many2one(
        "pos.session", string="POS Session",
        related="pos_order_id.session_id", store=True, readonly=True, index=True,
    )

    _purchase_source_unique = models.Constraint(
        "UNIQUE(purchase_order_id)",
        "A Purchase Order can create only one Receivable line.",
    )
    _cash_source_unique = models.Constraint(
        "UNIQUE(cash_book_line_id)",
        "A Cash Book line can create only one Receivable line.",
    )
    _bank_source_unique = models.Constraint(
        "UNIQUE(bank_book_line_id)",
        "A Bank Book line can create only one Receivable line.",
    )
    _expense_source_unique = models.Constraint(
        "UNIQUE(expense_book_id)",
        "An Expense can create only one Receivable line.",
    )
    _pos_order_source_unique = models.Constraint(
        "UNIQUE(pos_order_id)",
        "A POS Order can create only one Credit Sale Receivable line.",
    )
