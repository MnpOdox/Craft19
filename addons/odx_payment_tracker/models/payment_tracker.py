from collections import defaultdict
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare


class PaymentTrackerSchedule(models.Model):
    _name = "payment.tracker.schedule"
    _description = "Payment Schedule"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "due_date, priority, id"

    name = fields.Char(string="Reference", default="New", readonly=True, copy=False)
    title = fields.Char(tracking=True)
    description = fields.Text()
    partner_id = fields.Many2one("res.partner", string="Partner / Payee", tracking=True)
    receivable_book_id = fields.Many2one(
        "receivable.book", string="Receivable",
        domain="[('state', '=', 'confirm'), ('company_id', '=', company_id)]",
        tracking=True,
    )
    current_receivable_amount = fields.Monetary(
        string="Current Receivable", compute="_compute_current_receivable_amount",
        currency_field="currency_id",
    )
    planned_amount = fields.Monetary(required=True, currency_field="currency_id", tracking=True)
    due_date = fields.Date(required=True, tracking=True, index=True)
    responsible_id = fields.Many2one(
        "res.users", required=True, default=lambda self: self.env.user,
        domain="[('share', '=', False)]", tracking=True,
    )
    priority = fields.Selection([
        ("0", "Normal"), ("1", "High"), ("2", "Urgent"),
    ], default="0", required=True, tracking=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company,
        index=True, tracking=True,
    )
    currency_id = fields.Many2one(related="company_id.currency_id", readonly=True)
    payment_ids = fields.One2many("payment.tracker.payment", "schedule_id", string="Payments")
    paid_amount = fields.Monetary(compute="_compute_amounts", currency_field="currency_id", store=True)
    remaining_amount = fields.Monetary(compute="_compute_amounts", currency_field="currency_id", store=True)
    state = fields.Selection([
        ("draft", "Draft"),
        ("confirmed", "Confirmed"),
        ("partial", "Partially Paid"),
        ("paid", "Paid"),
        ("cancelled", "Cancelled"),
    ], default="draft", required=True, tracking=True, index=True)
    reminder_sent_keys = fields.Char(copy=False, readonly=True)

    _positive_amount = models.Constraint(
        "CHECK(planned_amount > 0)", "The planned amount must be greater than zero.",
    )

    @api.depends("planned_amount", "payment_ids.amount", "payment_ids.state")
    def _compute_amounts(self):
        for record in self:
            paid = sum(record.payment_ids.filtered(lambda p: p.state == "posted").mapped("amount"))
            record.paid_amount = paid
            record.remaining_amount = max(record.planned_amount - paid, 0.0)

    @api.depends("receivable_book_id", "receivable_book_id.receivable_line_ids.amount")
    def _compute_current_receivable_amount(self):
        for record in self:
            record.current_receivable_amount = abs(record.receivable_book_id.balance or 0.0)

    @api.onchange("receivable_book_id")
    def _onchange_receivable_book_id(self):
        if self.receivable_book_id:
            self.partner_id = self.receivable_book_id.partner_id
            self.planned_amount = abs(self.receivable_book_id.balance or 0.0)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            receivable = self.env["receivable.book"].browse(vals.get("receivable_book_id")).exists()
            if receivable:
                vals.setdefault("partner_id", receivable.partner_id.id)
                if not vals.get("planned_amount"):
                    vals["planned_amount"] = abs(receivable.balance or 0.0)
            if not vals.get("title"):
                due_date = fields.Date.to_date(vals.get("due_date"))
                parts = []
                if receivable:
                    parts.append(receivable.partner_id.display_name)
                elif vals.get("partner_id"):
                    partner = self.env["res.partner"].browse(vals.get("partner_id")).exists()
                    if partner:
                        parts.append(partner.display_name)
                if due_date:
                    parts.append(due_date.strftime("%d %b %Y"))
                else:
                    parts.append(_("Scheduled Payment"))
                vals["title"] = " - ".join(parts)
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("payment.tracker.schedule") or "New"
        return super().create(vals_list)

    def write(self, vals):
        protected = {"title", "partner_id", "receivable_book_id", "planned_amount", "due_date", "company_id"}
        if protected.intersection(vals):
            locked = self.filtered(lambda r: r.state not in ("draft", "cancelled"))
            if locked:
                raise UserError(_("Reset confirmed payments to Draft before changing their details."))
        return super().write(vals)

    def unlink(self):
        if self.filtered(lambda r: r.state != "draft" or r.payment_ids):
            raise UserError(_("Only Draft schedules without payment history can be deleted."))
        return super().unlink()

    def action_confirm(self):
        for record in self:
            if record.planned_amount <= 0:
                raise ValidationError(_("The planned amount must be greater than zero."))
            if record.receivable_book_id and (
                record.receivable_book_id.state != "confirm"
                or record.receivable_book_id.company_id != record.company_id
            ):
                raise ValidationError(_("Select a Confirmed Receivable for the schedule company."))
            record.state = "confirmed"

    def action_reset_draft(self):
        self._check_manager()
        if self.filtered("payment_ids"):
            raise UserError(_("A schedule with payment history cannot be reset to Draft."))
        self.write({"state": "draft", "reminder_sent_keys": False})

    def action_cancel(self):
        self._check_manager()
        if self.filtered(lambda r: r.payment_ids.filtered(lambda p: p.state == "posted")):
            raise UserError(_("Reverse posted payments before cancelling the schedule."))
        self.write({"state": "cancelled"})
        self._close_reminder_activities()

    def action_record_payment(self):
        self.ensure_one()
        self._check_manager()
        if self.state not in ("confirmed", "partial"):
            raise UserError(_("Payments can be recorded only for Confirmed or Partially Paid schedules."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Record Payment"),
            "res_model": "payment.tracker.payment.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_schedule_id": self.id, "default_amount": self.remaining_amount},
        }

    def _check_manager(self):
        if not self.env.su and not self.env.user.has_group("odx_payment_tracker.group_payment_tracker_manager"):
            raise AccessError(_("Only a Payment Tracker Manager can perform this action."))

    def _refresh_state(self):
        for record in self:
            if record.state in ("draft", "cancelled"):
                continue
            comparison = float_compare(
                record.paid_amount, record.planned_amount,
                precision_rounding=record.currency_id.rounding,
            )
            record.with_context(tracker_state_refresh=True).state = (
                "paid" if comparison >= 0 else "partial" if record.paid_amount else "confirmed"
            )
            if record.state == "paid":
                record._close_reminder_activities()

    def _close_reminder_activities(self):
        activity_type = self.env.ref("mail.mail_activity_data_todo")
        self.activity_ids.filtered(lambda a: a.activity_type_id == activity_type).action_done()

    @api.model
    def _cron_create_due_activities(self):
        today = fields.Date.context_today(self)
        schedules = self.search([("state", "in", ("confirmed", "partial"))])
        activity_type = self.env.ref("mail.mail_activity_data_todo")
        for schedule in schedules:
            days = (schedule.due_date - today).days
            configured = schedule._get_reminder_days()
            marker = "before_%s" % days if days in configured else False
            if days < 0:
                marker = "overdue"
            sent_keys = set(filter(None, (schedule.reminder_sent_keys or "").split(",")))
            if not marker or marker in sent_keys:
                continue
            label = _("Payment overdue") if days < 0 else _("Payment due in %s day(s)", days)
            schedule.activity_schedule(
                activity_type_id=activity_type.id,
                date_deadline=schedule.due_date if days >= 0 else today,
                user_id=schedule.responsible_id.id,
                summary=label,
                note=_("%(payment)s: %(amount)s due on %(date)s", payment=schedule.title,
                       amount=schedule.remaining_amount, date=schedule.due_date),
            )
            sent_keys.add(marker)
            schedule.reminder_sent_keys = ",".join(sorted(sent_keys))

    def _get_reminder_days(self):
        self.ensure_one()
        try:
            return {int(value.strip()) for value in (self.company_id.payment_tracker_reminder_days or "").split(",") if value.strip()}
        except ValueError:
            return {7, 3, 1}

    @api.model
    def get_dashboard_data(self):
        company = self.env.company
        currency = company.currency_id
        today = fields.Date.context_today(self)
        cash_books = self.env["cash.book"].sudo().search([
            ("company_id", "=", company.id), ("state", "=", "confirm"),
        ])
        bank_books = self.env["bank.book"].sudo().search([
            ("company_id", "=", company.id), ("state", "=", "confirm"),
        ])
        cash_balance = sum(cash_books.mapped("cur_balance"))
        bank_balance = sum(bank_books.mapped("cur_balance"))
        available = max(cash_balance + bank_balance, 0.0)
        schedules = self.sudo().search([
            ("company_id", "=", company.id), ("state", "in", ("confirmed", "partial")),
        ], order="due_date, priority desc, id")
        grouped = defaultdict(list)
        for schedule in schedules:
            grouped[schedule.due_date].append(schedule)
        groups = []
        cumulative = 0.0
        for due_date in sorted(grouped):
            records = grouped[due_date]
            due_amount = sum(record.remaining_amount for record in records)
            cumulative += due_amount
            shortage = max(cumulative - available, 0.0)
            days = (due_date - today).days
            groups.append({
                "date": fields.Date.to_string(due_date),
                "date_label": due_date.strftime("%d %b %Y"),
                "amount": due_amount,
                "cumulative": cumulative,
                "allocated": min(available, cumulative),
                "shortage": shortage,
                "days": days,
                "daily_target": shortage / max(days, 1) if days >= 0 else shortage,
                "status": "overdue" if days < 0 else "shortage" if shortage else "funded",
                "items": [{
                    "id": rec.id, "reference": rec.name, "title": rec.title,
                    "remaining": rec.remaining_amount,
                    "responsible": rec.responsible_id.display_name,
                } for rec in records],
            })
        total_unpaid = sum(schedules.mapped("remaining_amount"))
        return {
            "company": company.display_name,
            "currency": {"symbol": currency.symbol or currency.name, "position": currency.position,
                         "digits": currency.decimal_places},
            "cash_balance": cash_balance, "bank_balance": bank_balance,
            "available": available, "total_unpaid": total_unpaid,
            "due_7": sum(s.remaining_amount for s in schedules if s.due_date <= today + timedelta(days=7)),
            "due_30": sum(s.remaining_amount for s in schedules if s.due_date <= today + timedelta(days=30)),
            "shortage": max(total_unpaid - available, 0.0),
            "groups": groups,
        }


class PaymentTrackerPayment(models.Model):
    _name = "payment.tracker.payment"
    _description = "Payment Tracker Payment"
    _order = "payment_date desc, id desc"

    schedule_id = fields.Many2one("payment.tracker.schedule", required=True, ondelete="restrict", index=True)
    payment_date = fields.Date(required=True, default=fields.Date.context_today)
    amount = fields.Monetary(required=True, currency_field="currency_id")
    currency_id = fields.Many2one(related="schedule_id.currency_id", readonly=True, store=True)
    company_id = fields.Many2one(related="schedule_id.company_id", readonly=True, store=True, index=True)
    source_type = fields.Selection([("cash", "Cash"), ("bank", "Bank")], required=True)
    cash_book_id = fields.Many2one("cash.book", readonly=True)
    bank_book_id = fields.Many2one("bank.book", readonly=True)
    head_id = fields.Many2one("book.head", required=True, readonly=True)
    partner_id = fields.Many2one("res.partner", readonly=True)
    description = fields.Char(required=True, readonly=True)
    reference = fields.Char(readonly=True)
    note = fields.Text(readonly=True)
    user_id = fields.Many2one("res.users", readonly=True, default=lambda self: self.env.user)
    cash_book_line_id = fields.Many2one("cash.book.line", readonly=True, copy=False, ondelete="restrict")
    bank_book_line_id = fields.Many2one("bank.book.line", readonly=True, copy=False, ondelete="restrict")
    reversal_payment_id = fields.Many2one("payment.tracker.payment", readonly=True, copy=False)
    reversed_by_payment_id = fields.Many2one("payment.tracker.payment", readonly=True, copy=False)
    state = fields.Selection([("posted", "Posted"), ("reversed", "Reversed"), ("reversal", "Reversal")],
                             default="posted", required=True, readonly=True)

    _positive_amount = models.Constraint("CHECK(amount > 0)", "Payment amount must be greater than zero.")
    _cash_line_unique = models.Constraint("UNIQUE(cash_book_line_id)", "A Cash Book line can be linked only once.")
    _bank_line_unique = models.Constraint("UNIQUE(bank_book_line_id)", "A Bank Book line can be linked only once.")
    _reversal_unique = models.Constraint("UNIQUE(reversal_payment_id)", "A payment can be reversed only once.")

    def write(self, vals):
        allowed = {"state", "reversed_by_payment_id", "cash_book_line_id", "bank_book_line_id"}
        if set(vals) - allowed:
            raise UserError(_("Posted tracker payments cannot be edited."))
        return super().write(vals)

    def unlink(self):
        raise UserError(_("Tracker payment history cannot be deleted. Use Reverse Payment."))

    def action_reverse(self):
        self.ensure_one()
        self.schedule_id._check_manager()
        if self.state != "posted" or self.reversed_by_payment_id:
            raise UserError(_("This payment is already reversed or cannot be reversed."))
        line = self.cash_book_line_id or self.bank_book_line_id
        if not line:
            raise UserError(_("The linked Book line was not found."))
        vals = {
            "date": fields.Date.context_today(self), "head_id": self.head_id.id,
            "description": _("Reversal: %s", self.description), "amount": self.amount,
            "name_id": line.name_id.id, "company_id": self.company_id.id,
            "partner_id": self.partner_id.id,
        }
        line_model = "cash.book.line" if self.source_type == "cash" else "bank.book.line"
        reversal = self.create({
            "schedule_id": self.schedule_id.id, "payment_date": vals["date"], "amount": self.amount,
            "source_type": self.source_type, "cash_book_id": self.cash_book_id.id,
            "bank_book_id": self.bank_book_id.id, "head_id": self.head_id.id,
            "partner_id": self.partner_id.id, "description": vals["description"],
            "reference": self.reference, "note": _("Reversal of %s", self.display_name),
            "state": "reversal", "reversal_payment_id": self.id,
        })
        vals["payment_tracker_payment_id"] = reversal.id
        reverse_line = self.env[line_model].with_context(skip_receivable_payment_sync=True).create(vals)
        reversal.write({("cash_book_line_id" if self.source_type == "cash" else "bank_book_line_id"): reverse_line.id})
        original_receivable = line.receivable_line_id
        if original_receivable:
            receivable_vals = {
                "receivable_id": original_receivable.receivable_id.id,
                "date": vals["date"], "description": vals["description"],
                "amount": -original_receivable.amount, "company_id": self.company_id.id,
                "source_type": "cash_payment" if self.source_type == "cash" else "bank_payment",
                "cash_book_line_id" if self.source_type == "cash" else "bank_book_line_id": reverse_line.id,
            }
            receivable_line = self.env["receivable.book.line"].sudo().create(receivable_vals)
            reverse_line.sudo().with_context(skip_receivable_payment_sync=True).write({"receivable_line_id": receivable_line.id})
        self.write({"state": "reversed", "reversed_by_payment_id": reversal.id})
        self.schedule_id._compute_amounts()
        self.schedule_id._refresh_state()
        return True
