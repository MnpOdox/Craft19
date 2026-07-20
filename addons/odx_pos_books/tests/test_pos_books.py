from odoo import fields
from odoo.addons.point_of_sale.tests.common import TestPoSCommon
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestPosBooks(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref("odx_books.group_book_manager")

    def setUp(self):
        super().setUp()
        self.config = self.basic_config
        today = fields.Date.today()
        self.sales_head = self.env["book.head"].create({
            "head_name": "POS Sales",
            "cash": True,
            "bank": True,
            "expense": False,
        })
        self.expense_head = self.env["book.head"].create({
            "head_name": "POS Expense",
            "cash": True,
            "bank": False,
            "expense": True,
            "auto_expense": True,
        })
        self.transfer_head = self.env["book.head"].create({
            "head_name": "Manager Drawer Transfer",
            "cash": True,
            "bank": False,
            "expense": False,
            "pos_drawer_transfer": True,
        })
        self.config.pos_cash_move_head_ids = self.expense_head | self.transfer_head
        self.cash_book = self.env["cash.book"].create({
            "name": "POS Cash Book",
            "start_date": today,
            "end_date": today,
            "company_id": self.company.id,
            "state": "confirm",
            "pos_payment_method_ids": self.cash_pm1.ids,
            "pos_sales_head_id": self.sales_head.id,
        })
        self.bank_book = self.env["bank.book"].create({
            "name": "POS Bank Book",
            "start_date": today,
            "end_date": today,
            "company_id": self.company.id,
            "state": "confirm",
            "pos_payment_method_ids": self.bank_pm1.ids,
            "pos_sales_head_id": self.sales_head.id,
        })

    def test_session_close_posts_sales_and_cash_move(self):
        self.open_new_session()
        product = self.create_product("Book Test Product", self.categ_basic, 30.0)
        order_data = self.create_ui_order_data(
            [(product, 1)],
            payments=[(self.cash_pm1, 10.0), (self.bank_pm1, 20.0)],
        )
        self.env["pos.order"].sync_from_ui([order_data])
        self.pos_session.try_cash_in_out(
            "out", 5.0, "Courier", self.env.user.partner_id.id,
            {"translatedType": "out", "pos_book_head_id": self.expense_head.id},
        )
        self.pos_session.try_cash_in_out(
            "out", 5.0, "Ignored manual reason", self.env.user.partner_id.id,
            {"translatedType": "out", "pos_book_head_id": self.transfer_head.id},
        )
        self.pos_session.try_cash_in_out(
            "in", 2.0, "Ignored manual reason", self.env.user.partner_id.id,
            {"translatedType": "in", "pos_book_head_id": self.transfer_head.id},
        )

        self.pos_session.post_closing_cash_details(0.0)
        self.pos_session.action_pos_session_validate()

        self.assertTrue(self.pos_session.pos_book_synced)
        cash_sale = self.pos_session.pos_cash_book_line_ids.filtered(
            lambda line: line.pos_source_type == "sale"
        )
        bank_sale = self.pos_session.pos_bank_book_line_ids.filtered(
            lambda line: line.pos_source_type == "sale"
        )
        cash_out = self.pos_session.pos_cash_book_line_ids.filtered(
            lambda line: line.pos_source_type == "cash_out"
        )
        self.assertEqual(cash_sale.amount, 10.0)
        self.assertEqual(bank_sale.amount, 20.0)
        self.assertEqual(cash_out.amount, -5.0)
        self.assertEqual(cash_out.expense_id.amount, 5.0)
        transfer_move = self.pos_session.statement_line_ids.filtered(
            lambda line: line.pos_book_head_id == self.transfer_head
        )
        self.assertEqual(len(transfer_move), 2)
        self.assertTrue(all(
            move.payment_ref.endswith("Ignored manual reason")
            for move in transfer_move
        ))
        self.assertFalse(self.pos_session.pos_cash_book_line_ids.filtered(
            lambda line: line.pos_statement_line_id == transfer_move
        ))

        self.pos_session._sync_pos_book_entries()
        self.assertEqual(len(self.pos_session.pos_cash_book_line_ids), 2)
        self.assertEqual(len(self.pos_session.pos_bank_book_line_ids), 1)

    def test_cash_move_requires_head(self):
        self.open_new_session()
        with self.assertRaises(UserError):
            self.pos_session.try_cash_in_out(
                "in", 5.0, "Change", self.env.user.partner_id.id,
                {"translatedType": "in"},
            )

    def test_overlapping_payment_mapping_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.env["cash.book"].create({
                "name": "Overlapping Cash Book",
                "start_date": fields.Date.today(),
                "end_date": fields.Date.today(),
                "company_id": self.company.id,
                "state": "confirm",
                "pos_payment_method_ids": self.cash_pm1.ids,
                "pos_sales_head_id": self.sales_head.id,
            })
