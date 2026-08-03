from odoo import fields
from odoo.addons.point_of_sale.tests.common import TestPoSCommon
from odoo.exceptions import UserError
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestPosCreditReceivable(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref("odx_books.group_book_manager")

    def setUp(self):
        super().setUp()
        self.config = self.basic_config
        self.credit_pm = self.pay_later_pm
        self.credit_pm.auto_receivable_credit_sale = True
        self.customer_book = self.env["receivable.book"].create({
            "partner_id": self.customer.id,
            "company_id": self.company.id,
            "state": "confirm",
        })
        sales_head = self.env["book.head"].create({
            "head_name": "POS Test Sales",
            "cash": True,
            "bank": False,
            "expense": False,
        })
        self.env["cash.book"].create({
            "name": "POS Credit Test Cash Book",
            "start_date": fields.Date.today(),
            "end_date": fields.Date.today(),
            "company_id": self.company.id,
            "state": "confirm",
            "pos_payment_method_ids": self.cash_pm1.ids,
            "pos_sales_head_id": sales_head.id,
        })

    def _close_session(self):
        self.pos_session.post_closing_cash_details(0.0)
        self.pos_session.action_pos_session_validate()

    def test_credit_and_split_sales_post_only_credit_amount(self):
        self.open_new_session()
        product = self.create_product("Credit Product", self.categ_basic, 100.0)
        full_credit = self.create_ui_order_data(
            [(product, 1)], customer=self.customer,
            payments=[(self.credit_pm, 100.0)],
        )
        split_credit = self.create_ui_order_data(
            [(product, 1)], customer=self.customer,
            payments=[(self.cash_pm1, 60.0), (self.credit_pm, 40.0)],
        )
        result = self.env["pos.order"].sync_from_ui([full_credit, split_credit])
        orders = self.env["pos.order"].browse([item["id"] for item in result["pos.order"]])
        self._close_session()

        lines = self.env["receivable.book.line"].search([
            ("pos_order_id", "in", orders.ids),
        ])
        self.assertEqual(len(lines), 2)
        self.assertEqual(sorted(lines.mapped("amount")), [40.0, 100.0])
        self.assertEqual(lines.mapped("receivable_id"), self.customer_book)
        self.assertTrue(self.pos_session.credit_receivable_synced)

        self.pos_session.action_rebuild_pos_book_entries()
        self.assertEqual(self.env["receivable.book.line"].search_count([
            ("pos_order_id", "in", orders.ids),
        ]), 2)

    def test_credit_refund_posts_negative_amount(self):
        self.open_new_session()
        product = self.create_product("Refund Product", self.categ_basic, 50.0)
        refund = self.create_ui_order_data(
            [(product, -1)], customer=self.customer,
            payments=[(self.credit_pm, -50.0)],
        )
        result = self.env["pos.order"].sync_from_ui([refund])
        order = self.env["pos.order"].browse(result["pos.order"][0]["id"])
        self._close_session()
        self.assertEqual(order.credit_receivable_line_ids.amount, -50.0)

    def test_missing_customer_or_confirmed_book_blocks_closing(self):
        self.open_new_session()
        product = self.create_product("Missing Customer Product", self.categ_basic, 25.0)
        order_data = self.create_ui_order_data(
            [(product, 1)], payments=[(self.credit_pm, 25.0)],
        )
        self.env["pos.order"].sync_from_ui([order_data])
        self.pos_session.post_closing_cash_details(0.0)
        with self.assertRaisesRegex(UserError, "select a customer"):
            self.pos_session.action_pos_session_validate()

    def test_closed_order_payment_changes_resynchronize(self):
        self.open_new_session()
        product = self.create_product("Changed Credit Product", self.categ_basic, 30.0)
        order_data = self.create_ui_order_data(
            [(product, 1)], customer=self.customer,
            payments=[(self.credit_pm, 30.0)],
        )
        result = self.env["pos.order"].sync_from_ui([order_data])
        order = self.env["pos.order"].browse(result["pos.order"][0]["id"])
        self._close_session()
        self.assertEqual(order.credit_receivable_line_ids.amount, 30.0)

        order.payment_ids.write({"payment_method_id": self.cash_pm1.id})
        self.assertFalse(order.credit_receivable_line_ids)
        order.payment_ids.write({"payment_method_id": self.credit_pm.id})
        self.assertEqual(order.credit_receivable_line_ids.amount, 30.0)
