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
            "auto_receivable_payment": True,
        })
        cls.cash_book = cls.env["cash.book"].create({"name": "Automation Cash Book"})
        cls.bank_book = cls.env["bank.book"].create({"name": "Automation Bank Book"})
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

    def test_cash_user_without_receivable_access_can_create_payment(self):
        cash_line = self.env["cash.book.line"].with_user(self.cash_user).create({
            "name_id": self.cash_book.id,
            "head_id": self.payment_head.id,
            "partner_id": self.vendor.id,
            "description": "Cash user vendor payment",
            "amount": -10,
        })
        self.assertEqual(cash_line.sudo().receivable_line_id.amount, 10)

    def test_purchase_cancel_removes_automatic_line(self):
        self.purchase.button_confirm()
        self.purchase.button_cancel()
        self.assertFalse(self.env["receivable.book.line"].search([
            ("purchase_order_id", "=", self.purchase.id)
        ]))
