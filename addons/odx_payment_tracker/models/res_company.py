from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = "res.company"

    payment_tracker_reminder_days = fields.Char(
        string="Payment Reminder Days",
        default="7,3,1",
        help="Comma-separated days before the due date when activities are created.",
    )

    @api.constrains("payment_tracker_reminder_days")
    def _check_payment_tracker_reminder_days(self):
        for company in self:
            try:
                days = [int(value.strip()) for value in (company.payment_tracker_reminder_days or "").split(",") if value.strip()]
            except ValueError as error:
                raise ValidationError(_("Reminder days must be comma-separated whole numbers, for example 7,3,1.")) from error
            if any(day < 0 for day in days):
                raise ValidationError(_("Reminder days cannot be negative."))
