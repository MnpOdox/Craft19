from odoo import api, exceptions, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    @api.constrains("phone")
    def _check_unique_non_empty_phone(self):
        for partner in self:
            phone = (partner.phone or "").strip()
            if not phone:
                continue
            duplicate = self.search(
                [
                    ("id", "!=", partner.id),
                    ("phone", "=", phone),
                ],
                limit=1,
            )
            if duplicate:
                raise exceptions.ValidationError("Customer Phone must be unique")
