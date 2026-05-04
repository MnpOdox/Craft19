from odoo import api, models


class AccountMove(models.Model):
    _inherit = "account.move"

    @api.model_create_multi
    def create(self, vals_list):
        """POS safety fallback: ensure journal_id exists for entry moves.

        Some migrated POS flows can call account.move.create() without journal_id,
        which crashes with "Missing required value for Journal". For entry moves,
        pick a deterministic company journal to keep POS validation unblocked.
        """
        Journal = self.env["account.journal"].sudo()
        for vals in vals_list:
            if vals.get("journal_id"):
                continue
            if vals.get("move_type") and vals.get("move_type") != "entry":
                continue

            company_id = vals.get("company_id") or self.env.company.id
            journal = Journal.search(
                [
                    ("company_id", "=", company_id),
                    ("type", "=", "general"),
                    ("name", "ilike", "Point of Sale"),
                    ("active", "=", True),
                ],
                limit=1,
            )
            if not journal:
                journal = Journal.search(
                    [
                        ("company_id", "=", company_id),
                        ("type", "=", "general"),
                        ("active", "=", True),
                    ],
                    order="id",
                    limit=1,
                )
            if journal:
                vals["journal_id"] = journal.id

        return super().create(vals_list)

