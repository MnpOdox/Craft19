from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPaymentTracker(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        today = fields.Date.context_today(cls.env.user)
        cls.today = today
        cls.cash_head = cls.env["book.head"].create({
            "head_name": "Tracker Test Cash", "cash": True, "bank": False,
        })
        cls.bank_head = cls.env["book.head"].create({
            "head_name": "Tracker Test Bank", "cash": False, "bank": True,
        })
        cls.cash_book = cls.env["cash.book"].create({
            "name": "Tracker Test Cash", "start_date": today - timedelta(days=10),
            "end_date": today + timedelta(days=40), "open_balance": 1000,
            "company_id": cls.company.id, "state": "confirm",
        })

    def _schedule(self, amount=500, days=10):
        schedule = self.env["payment.tracker.schedule"].create({
            "title": "Test supplier payment",
            "planned_amount": amount, "due_date": self.today + timedelta(days=days),
            "responsible_id": self.env.user.id, "company_id": self.company.id,
        })
        schedule.action_confirm()
        return schedule

    def test_dashboard_cumulative_allocation(self):
        available = self.env["payment.tracker.schedule"].get_dashboard_data()["available"]
        self._schedule(available / 2, 5)
        self._schedule((available / 2) + 300, 10)
        data = self.env["payment.tracker.schedule"].get_dashboard_data()
        self.assertEqual(len(data["groups"]), 2)
        self.assertEqual(data["groups"][0]["shortage"], 0)
        self.assertEqual(data["groups"][1]["shortage"], 300)
        self.assertEqual(data["groups"][1]["daily_target"], 30)

    def test_cash_payment_and_reversal(self):
        schedule = self._schedule(500)
        wizard = self.env["payment.tracker.payment.wizard"].create({
            "schedule_id": schedule.id, "payment_date": self.today, "amount": 200,
            "source_type": "cash", "cash_book_id": self.cash_book.id,
            "head_id": self.cash_head.id, "description": schedule.title,
        })
        wizard.sudo().action_confirm()
        payment = schedule.payment_ids
        self.assertEqual(payment.cash_book_line_id.amount, -200)
        self.assertEqual(schedule.state, "partial")
        self.assertEqual(schedule.remaining_amount, 300)
        payment.sudo().action_reverse()
        self.assertEqual(payment.state, "reversed")
        self.assertEqual(payment.reversed_by_payment_id.cash_book_line_id.amount, 200)
        self.assertEqual(schedule.state, "confirmed")
        self.assertEqual(schedule.remaining_amount, 500)

    def test_overpayment_and_confirmed_edit_are_blocked(self):
        schedule = self._schedule(100)
        with self.assertRaises(UserError):
            schedule.write({"planned_amount": 120})
        wizard = self.env["payment.tracker.payment.wizard"].create({
            "schedule_id": schedule.id, "payment_date": self.today, "amount": 101,
            "source_type": "cash", "cash_book_id": self.cash_book.id,
            "head_id": self.cash_head.id, "description": schedule.title,
        })
        with self.assertRaises(ValidationError):
            wizard.sudo().action_confirm()

    def test_simple_creation_generates_title_and_defaults(self):
        due_date = self.today + timedelta(days=15)
        schedule = self.env["payment.tracker.schedule"].create({
            "planned_amount": 750,
            "due_date": due_date,
        })
        self.assertIn(due_date.strftime("%d %b %Y"), schedule.title)
        self.assertEqual(schedule.responsible_id, self.env.user)
        self.assertEqual(schedule.company_id, self.env.company)
        self.assertEqual(schedule.state, "draft")

    def test_confirmed_receivable_fetches_partner_and_balance(self):
        partner = self.env["res.partner"].create({"name": "Tracker Supplier"})
        receivable = self.env["receivable.book"].create({
            "partner_id": partner.id, "company_id": self.company.id, "state": "confirm",
        })
        self.env["receivable.book.line"].create({
            "receivable_id": receivable.id, "company_id": self.company.id,
            "date": self.today, "description": "Outstanding", "amount": -1250,
        })
        schedule = self.env["payment.tracker.schedule"].create({
            "receivable_book_id": receivable.id,
            "planned_amount": 0, "due_date": self.today + timedelta(days=5),
        })
        self.assertEqual(schedule.partner_id, partner)
        self.assertEqual(schedule.planned_amount, 1250)
        self.assertEqual(schedule.current_receivable_amount, 1250)
