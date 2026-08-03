from odoo.tests import TransactionCase, tagged
from odoo.exceptions import ValidationError


@tagged("post_install", "-at_install")
class TestReceivableAutomation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor = cls.env["res.partner"].create({"name": "Automation Vendor"})
        cls.product = cls.env["product.product"].create({
            "name": "Automation Product",
            "is_storable": True,
        })
        cls.purchase = cls.env["purchase.order"].create({
            "partner_id": cls.vendor.id,
            "order_line": [(0, 0, {
                "product_id": cls.product.id,
                "product_qty": 2,
                "price_unit": 50,
            })],
        })
        cls.payment_head = cls.env["book.head"].create({
            "head_name": "Vendor Payment",
            "cash": True,
            "bank": True,
            "expense": False,
            "receivable_payment_effect": "payable_settlement",
        })
        cls.salary_head = cls.env["book.head"].create({
            "head_name": "SALARY",
            "cash": True,
            "bank": True,
            "expense": True,
            "auto_expense": False,
            "auto_receivable_expense": True,
            "receivable_payment_effect": "payable_settlement",
        })
        cls.collection_head = cls.env["book.head"].create({
            "head_name": "CASH COLLECTED",
            "cash": True,
            "bank": True,
            "expense": False,
            "receivable_payment_effect": "customer_receipt",
        })
        cls.cash_book = cls.env["cash.book"].create({
            "name": "Automation Cash Book",
            "state": "confirm",
        })
        cls.bank_book = cls.env["bank.book"].create({
            "name": "Automation Bank Book",
            "state": "confirm",
        })
        cls.vendor_receivable = cls.env["receivable.book"].create({
            "partner_id": cls.vendor.id,
            "company_id": cls.env.company.id,
            "state": "confirm",
        })
        cls.cash_user = cls.env["res.users"].create({
            "name": "Automation Cash User",
            "login": "automation_cash_user",
            "company_id": cls.env.company.id,
            "company_ids": [(6, 0, cls.env.company.ids)],
            "group_ids": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("odx_books.group_cash_book_user").id,
            ])],
        })

    def test_purchase_and_payments_sync_without_duplicates(self):
        self.purchase.button_confirm()
        purchase_line = self.env["receivable.book.line"].search([
            ("purchase_order_id", "=", self.purchase.id)
        ])
        self.assertEqual(len(purchase_line), 1)
        purchase_total = self.purchase.amount_total
        self.assertEqual(purchase_line.amount, -purchase_total)
        self.assertEqual(purchase_line.partner_id, self.vendor)
        self.assertEqual(purchase_line.receivable_id.partner_id, self.vendor)

        self.purchase._sync_receivable_purchase()
        self.assertEqual(self.env["receivable.book.line"].search_count([
            ("purchase_order_id", "=", self.purchase.id)
        ]), 1)

        cash_line = self.env["cash.book.line"].create({
            "name_id": self.cash_book.id,
            "head_id": self.payment_head.id,
            "partner_id": self.vendor.id,
            "description": "Part payment cash",
            "amount": -30,
        })
        bank_line = self.env["bank.book.line"].create({
            "name_id": self.bank_book.id,
            "head_id": self.payment_head.id,
            "partner_id": self.vendor.id,
            "description": "Part payment bank",
            "amount": -20,
        })
        self.assertEqual(cash_line.receivable_line_id.amount, 30)
        self.assertEqual(bank_line.receivable_line_id.amount, 20)
        self.assertEqual(purchase_line.receivable_id.balance, -purchase_total + 50)

        cash_line.write({"amount": -40})
        self.assertEqual(cash_line.receivable_line_id.amount, 40)
        self.assertEqual(purchase_line.receivable_id.balance, -purchase_total + 60)

        self.purchase.order_line.write({"price_unit": 60})
        self.assertEqual(purchase_line.amount, -self.purchase.amount_total)

    def test_payment_requires_partner_and_negative_amount(self):
        with self.assertRaises(ValidationError):
            self.env["cash.book.line"].create({
                "name_id": self.cash_book.id,
                "head_id": self.payment_head.id,
                "amount": -10,
            })
        with self.assertRaises(ValidationError):
            self.env["bank.book.line"].create({
                "name_id": self.bank_book.id,
                "head_id": self.payment_head.id,
                "partner_id": self.vendor.id,
                "amount": 10,
            })

    def test_salary_expense_and_payments(self):
        employee = self.env["res.partner"].create({"name": "Salary Employee"})
        employee_book = self.env["receivable.book"].create({
            "partner_id": employee.id,
            "company_id": self.env.company.id,
            "state": "confirm",
        })
        expense = self.env["expense.book"].create({
            "head_id": self.salary_head.id,
            "partner_id": employee.id,
            "description": "July salary",
            "amount": 1000,
        })
        self.assertEqual(expense.receivable_line_id.amount, -1000)
        self.assertEqual(expense.receivable_line_id.receivable_id, employee_book)
        cash_line = self.env["cash.book.line"].create({
            "name_id": self.cash_book.id,
            "head_id": self.salary_head.id,
            "partner_id": employee.id,
            "amount": -600,
        })
        bank_line = self.env["bank.book.line"].create({
            "name_id": self.bank_book.id,
            "head_id": self.salary_head.id,
            "partner_id": employee.id,
            "amount": -400,
        })
        self.assertEqual(cash_line.receivable_line_id.amount, 600)
        self.assertEqual(bank_line.receivable_line_id.amount, 400)
        self.assertEqual(employee_book.balance, 0)

    def test_customer_collection_creates_negative_receivable(self):
        line = self.env["cash.book.line"].create({
            "name_id": self.cash_book.id,
            "head_id": self.collection_head.id,
            "partner_id": self.vendor.id,
            "amount": 75,
        })
        self.assertEqual(line.receivable_line_id.amount, -75)
        with self.assertRaises(ValidationError):
            self.env["bank.book.line"].create({
                "name_id": self.bank_book.id,
                "head_id": self.collection_head.id,
                "partner_id": self.vendor.id,
                "amount": -10,
            })

    def test_daily_transaction_cash_out_uses_positive_staff_amount(self):
        wizard = self.env["daily.book.transaction"].create({
            "transaction_type": "cash_out",
            "head_id": self.payment_head.id,
            "partner_id": self.vendor.id,
            "amount": 25,
            "cash_book_id": self.cash_book.id,
            "description": "Easy cash payment",
        })
        line = wizard._create_transaction()
        self.assertEqual(line.amount, -25)
        self.assertEqual(line.receivable_line_id.amount, 25)
        self.assertEqual(line.name_id, self.cash_book)

    def test_daily_transaction_salary_expense(self):
        wizard = self.env["daily.book.transaction"].create({
            "transaction_type": "expense",
            "head_id": self.salary_head.id,
            "partner_id": self.vendor.id,
            "amount": 500,
        })
        expense = wizard._create_transaction()
        self.assertEqual(expense.amount, 500)
        self.assertEqual(expense.receivable_line_id.amount, -500)

    def test_books_dashboard_summaries_and_ordering(self):
        self.env["bank.book.line"].create({
            "name_id": self.bank_book.id,
            "head_id": self.collection_head.id,
            "partner_id": self.vendor.id,
            "amount": 75,
        })
        self.env["expense.book"].create({
            "head_id": self.salary_head.id,
            "partner_id": self.vendor.id,
            "amount": 250,
        })
        action = self.env["daily.book.dashboard"].action_open_dashboard()
        dashboard = self.env["daily.book.dashboard"].browse(action["res_id"])
        self.assertTrue(dashboard.exists())
        self.assertIn(self.bank_book, dashboard.bank_balance_line_ids.mapped("bank_book_id"))
        salary_summary = dashboard.expense_summary_line_ids.filtered(
            lambda line: line.head_id == self.salary_head
        )
        self.assertTrue(salary_summary)
        self.assertGreaterEqual(salary_summary.amount, 250)
        amounts = dashboard.receivable_summary_line_ids.mapped("amount")
        self.assertEqual(amounts, sorted(amounts))

    def test_cash_user_without_receivable_access_can_create_payment(self):
        cash_line = self.env["cash.book.line"].with_user(self.cash_user).create({
            "name_id": self.cash_book.id,
            "head_id": self.payment_head.id,
            "partner_id": self.vendor.id,
            "description": "Cash user vendor payment",
            "amount": -10,
        })
        self.assertEqual(cash_line.sudo().receivable_line_id.amount, 10)

    def test_payment_partner_domain_only_includes_receivable_partners(self):
        partner_without_book = self.env["res.partner"].create({"name": "No Receivable Partner"})
        domain_partner = self.env["res.partner"].create({"name": "Domain Partner"})
        confirmed_book = self.env["receivable.book"].create({
            "partner_id": domain_partner.id,
            "company_id": self.env.company.id,
            "state": "confirm",
        })
        self.assertTrue(domain_partner.has_confirmed_receivable_book)
        self.assertFalse(partner_without_book.has_confirmed_receivable_book)
        available = self.env["res.partner"].search([
            ("has_confirmed_receivable_book", "=", True),
            ("id", "in", (domain_partner | partner_without_book).ids),
        ])
        self.assertEqual(available, domain_partner)

        confirmed_book.action_done()
        self.assertFalse(domain_partner.has_confirmed_receivable_book)
        self.assertFalse(self.env["res.partner"].search([
            ("has_confirmed_receivable_book", "=", True),
            ("id", "=", domain_partner.id),
        ]))

    def test_purchase_cancel_removes_automatic_line(self):
        self.purchase.button_confirm()
        self.purchase.button_cancel()
        self.assertFalse(self.env["receivable.book.line"].search([
            ("purchase_order_id", "=", self.purchase.id)
        ]))
