from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _


class DailyBookDashboard(models.TransientModel):
    _name = "daily.book.dashboard"
    _description = "Books Dashboard"

    company_id = fields.Many2one("res.company", required=True, readonly=True)
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", readonly=True,
    )
    cash_balance = fields.Monetary(readonly=True, currency_field="currency_id")
    month_label = fields.Char(readonly=True)
    bank_balance_line_ids = fields.One2many(
        "daily.book.dashboard.bank", "dashboard_id", readonly=True,
    )
    expense_summary_line_ids = fields.One2many(
        "daily.book.dashboard.expense", "dashboard_id", readonly=True,
    )
    receivable_summary_line_ids = fields.One2many(
        "daily.book.dashboard.receivable", "dashboard_id", readonly=True,
    )

    @api.model
    def action_open_dashboard(self):
        company = self.env.company
        today = fields.Date.context_today(self)
        month_start = today.replace(day=1)
        month_end = month_start + relativedelta(months=1, days=-1)
        cash_books = self.env["cash.book"].sudo().search([
            ("company_id", "=", company.id),
            ("state", "=", "confirm"),
        ])
        dashboard = self.create({
            "company_id": company.id,
            "cash_balance": sum(cash_books.mapped("cur_balance")),
            "month_label": month_start.strftime("%B %Y"),
        })
        bank_books = self.env["bank.book"].sudo().search([
            ("company_id", "=", company.id),
            ("state", "=", "confirm"),
        ], order="name, id")
        self.env["daily.book.dashboard.bank"].create([{
            "dashboard_id": dashboard.id,
            "bank_book_id": book.id,
            "name": book.name,
            "balance": book.cur_balance,
            "currency_id": company.currency_id.id,
        } for book in bank_books])

        expense_groups = self.env["expense.book"].sudo()._read_group(
            domain=[
                ("company_id", "=", company.id),
                ("date", ">=", month_start),
                ("date", "<=", month_end),
            ],
            groupby=["head_id"],
            aggregates=["amount:sum"],
        )
        expense_values = sorted(
            ((head, amount) for head, amount in expense_groups if head),
            key=lambda item: item[1], reverse=True,
        )
        self.env["daily.book.dashboard.expense"].create([{
            "dashboard_id": dashboard.id,
            "head_id": head.id,
            "amount": amount,
            "currency_id": company.currency_id.id,
        } for head, amount in expense_values])

        receivable_books = self.env["receivable.book"].sudo().search([
            ("company_id", "=", company.id),
            ("state", "=", "confirm"),
        ])
        receivable_books = receivable_books.filtered(
            lambda book: book.balance < 0
        ).sorted(key=lambda book: book.balance)
        self.env["daily.book.dashboard.receivable"].create([{
            "dashboard_id": dashboard.id,
            "receivable_book_id": book.id,
            "partner_id": book.partner_id.id,
            "amount": book.balance,
            "currency_id": company.currency_id.id,
        } for book in receivable_books])
        return {
            "type": "ir.actions.act_window",
            "name": _("Books Dashboard"),
            "res_model": self._name,
            "view_mode": "form",
            "view_id": self.env.ref(
                "odx_receivable_automation.view_daily_book_dashboard_form"
            ).id,
            "res_id": dashboard.id,
            "target": "current",
        }

    def action_refresh(self):
        self.ensure_one()
        return self.action_open_dashboard()

    def action_new_expense(self):
        return self.env["ir.actions.actions"]._for_xml_id(
            "odx_receivable_automation.action_daily_expense"
        )

    def action_cash_in_out(self):
        return self.env["ir.actions.actions"]._for_xml_id(
            "odx_receivable_automation.action_daily_cash_transaction"
        )

    def action_bank_in_out(self):
        return self.env["ir.actions.actions"]._for_xml_id(
            "odx_receivable_automation.action_daily_bank_transaction"
        )


class DailyBookDashboardBank(models.TransientModel):
    _name = "daily.book.dashboard.bank"
    _description = "Dashboard Bank Balance"
    _order = "name, id"

    dashboard_id = fields.Many2one("daily.book.dashboard", required=True, ondelete="cascade")
    bank_book_id = fields.Many2one("bank.book", readonly=True)
    name = fields.Char(readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    balance = fields.Monetary(readonly=True, currency_field="currency_id")


class DailyBookDashboardExpense(models.TransientModel):
    _name = "daily.book.dashboard.expense"
    _description = "Dashboard Monthly Expense"
    _order = "amount desc, id"

    dashboard_id = fields.Many2one("daily.book.dashboard", required=True, ondelete="cascade")
    head_id = fields.Many2one("book.head", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    amount = fields.Monetary(readonly=True, currency_field="currency_id")


class DailyBookDashboardReceivable(models.TransientModel):
    _name = "daily.book.dashboard.receivable"
    _description = "Dashboard Receivable Balance"
    _order = "amount, id"

    dashboard_id = fields.Many2one("daily.book.dashboard", required=True, ondelete="cascade")
    receivable_book_id = fields.Many2one("receivable.book", readonly=True)
    partner_id = fields.Many2one("res.partner", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    amount = fields.Monetary(readonly=True, currency_field="currency_id")
