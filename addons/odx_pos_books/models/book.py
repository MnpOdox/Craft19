from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class PosBookMixin(models.AbstractModel):
    _name = "pos.book.mixin"
    _description = "POS Book Mapping Mixin"

    pos_payment_method_ids = fields.Many2many(
        "pos.payment.method",
        string="POS Payment Methods",
        check_company=True,
        help="Session payments using these methods are posted to this book at closing.",
    )
    pos_sales_head_id = fields.Many2one(
        "book.head",
        string="POS Sales Head",
        help="Head used for automatically generated POS sales lines.",
    )

    def _validate_pos_mapping(self, book_type):
        for book in self:
            if book.pos_payment_method_ids and not book.pos_sales_head_id:
                raise ValidationError(_("Configure a POS Sales Head when assigning POS payment methods."))
            if book.pos_sales_head_id and not book.pos_sales_head_id[book_type]:
                raise ValidationError(
                    _("The POS Sales Head on %(book)s is not enabled for the %(type)s Book.",
                      book=book.display_name, type=book_type.title())
                )
            wrong_methods = book.pos_payment_method_ids.filtered(
                lambda method: method.type != book_type
            )
            if wrong_methods:
                raise ValidationError(
                    _("These payment methods do not belong in the %(type)s Book: %(methods)s",
                      type=book_type.title(), methods=", ".join(wrong_methods.mapped("name")))
                )
            excluded_methods = book.pos_payment_method_ids.filtered("pos_book_excluded")
            if excluded_methods:
                raise ValidationError(
                    _("Excluded payment methods cannot be assigned to a POS Book: %s",
                      ", ".join(excluded_methods.mapped("name")))
                )
            wrong_company = book.pos_payment_method_ids.filtered(
                lambda method: method.company_id != book.company_id
            )
            if wrong_company:
                raise ValidationError(_("POS payment methods and their book must use the same company."))

    def _check_overlapping_pos_mappings(self):
        for book in self.filtered(lambda record: record.state == "confirm" and record.pos_payment_method_ids):
            overlapping = self.search([
                ("id", "!=", book.id),
                ("company_id", "=", book.company_id.id),
                ("state", "=", "confirm"),
                ("start_date", "<=", book.end_date),
                ("end_date", ">=", book.start_date),
                ("pos_payment_method_ids", "in", book.pos_payment_method_ids.ids),
            ], limit=1)
            if overlapping:
                shared = book.pos_payment_method_ids & overlapping.pos_payment_method_ids
                raise ValidationError(
                    _("Payment method %(method)s is assigned to overlapping active books %(first)s and %(second)s.",
                      method=", ".join(shared.mapped("name")), first=book.display_name,
                      second=overlapping.display_name)
                )


class CashBook(models.Model):
    _name = "cash.book"
    _inherit = ["cash.book", "pos.book.mixin"]

    @api.constrains("pos_payment_method_ids", "pos_sales_head_id", "company_id")
    def _check_pos_mapping_values(self):
        self._validate_pos_mapping("cash")

    @api.constrains("pos_payment_method_ids", "state", "start_date", "end_date", "company_id")
    def _check_pos_mapping_overlap(self):
        self._check_overlapping_pos_mappings()


class BankBook(models.Model):
    _name = "bank.book"
    _inherit = ["bank.book", "pos.book.mixin"]

    @api.constrains("pos_payment_method_ids", "pos_sales_head_id", "company_id")
    def _check_pos_mapping_values(self):
        self._validate_pos_mapping("bank")

    @api.constrains("pos_payment_method_ids", "state", "start_date", "end_date", "company_id")
    def _check_pos_mapping_overlap(self):
        self._check_overlapping_pos_mappings()


class PosBookLineMixin(models.AbstractModel):
    _name = "pos.book.line.mixin"
    _description = "POS Generated Book Line Mixin"

    pos_session_id = fields.Many2one("pos.session", string="POS Session", readonly=True, copy=False, index=True)
    pos_payment_method_id = fields.Many2one(
        "pos.payment.method", string="POS Payment Method", readonly=True, copy=False
    )
    pos_statement_line_id = fields.Many2one(
        "account.bank.statement.line", string="POS Cash Movement", readonly=True, copy=False
    )
    pos_source_type = fields.Selection([
        ("sale", "POS Sale"),
        ("cash_in", "Cash In"),
        ("cash_out", "Cash Out"),
    ], string="POS Source", readonly=True, copy=False, index=True)

    def unlink(self):
        generated = self.filtered("pos_session_id")
        if generated and not self.env.context.get("pos_book_rebuild"):
            raise UserError(_("Generated POS book lines can only be removed using Rebuild POS Book Entries."))
        return super().unlink()

    def write(self, vals):
        generated = self.filtered("pos_session_id")
        protected_fields = set(vals) - {"expense_id"}
        if (
            generated
            and protected_fields
            and not self.env.context.get("pos_book_sync")
            and not self.env.user.has_group("odx_books.group_book_manager")
        ):
            raise UserError(_("Only a Book Manager can modify generated POS book lines."))
        return super().write(vals)


class CashBookLine(models.Model):
    _name = "cash.book.line"
    _inherit = ["cash.book.line", "pos.book.line.mixin"]

    _pos_sale_unique = models.Constraint(
        "UNIQUE(pos_session_id, pos_payment_method_id, pos_source_type)",
        "This POS session payment method has already been posted to the Cash Book.",
    )
    _pos_cash_move_unique = models.Constraint(
        "UNIQUE(pos_statement_line_id)",
        "This POS cash movement has already been posted to the Cash Book.",
    )


class BankBookLine(models.Model):
    _name = "bank.book.line"
    _inherit = ["bank.book.line", "pos.book.line.mixin"]

    _pos_sale_unique = models.Constraint(
        "UNIQUE(pos_session_id, pos_payment_method_id, pos_source_type)",
        "This POS session payment method has already been posted to the Bank Book.",
    )
