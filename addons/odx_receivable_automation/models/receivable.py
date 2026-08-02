from odoo import api, fields, models


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


class ReceivableBookLine(models.Model):
    _inherit = "receivable.book.line"

    source_type = fields.Selection([
        ("purchase", "Purchase"),
        ("cash_payment", "Cash Payment"),
        ("bank_payment", "Bank Payment"),
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
