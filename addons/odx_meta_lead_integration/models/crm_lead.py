import secrets

from odoo import fields, models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    meta_lead_id = fields.Char(index=True, copy=False, readonly=True)
    meta_page_id = fields.Many2one("odx.meta.page", copy=False, readonly=True)
    meta_form_id = fields.Many2one("odx.meta.form", copy=False, readonly=True)
    meta_ad_route_id = fields.Many2one("odx.meta.ad.route", copy=False, readonly=True)
    meta_created_time = fields.Datetime(copy=False, readonly=True)
    meta_campaign_ref = fields.Char(string="Meta Campaign ID", copy=False, readonly=True)
    meta_adset_ref = fields.Char(string="Meta Ad Set ID", copy=False, readonly=True)
    meta_ad_ref = fields.Char(string="Meta Ad ID", copy=False, readonly=True, index=True)
    meta_campaign_name = fields.Char(copy=False, readonly=True)
    meta_adset_name = fields.Char(copy=False, readonly=True)
    meta_ad_name = fields.Char(copy=False, readonly=True)
    meta_location = fields.Char(
        string="Enquiry State / Location",
        copy=False,
        help="State or location submitted through the Meta Instant Form.",
    )
    meta_status_sync_state = fields.Selection([
        ("pending", "Pending"),
        ("done", "Sent"),
        ("failed", "Failed"),
    ], string="Meta Status Sync", readonly=True, copy=False)
    meta_status_sync_event_id = fields.Many2one(
        "odx.meta.status.event", string="Latest Meta Status Event", readonly=True, copy=False,
        ondelete="set null",
    )
    meta_status_synced_name = fields.Char(string="Last Status Sent", readonly=True, copy=False)
    meta_status_synced_at = fields.Datetime(string="Last Status Sent At", readonly=True, copy=False)
    meta_status_sync_error = fields.Text(string="Meta Status Error", readonly=True, copy=False)

    _meta_lead_unique = models.Constraint(
        "UNIQUE(meta_lead_id)", "A Meta lead submission can only be imported once."
    )

    def write(self, vals):
        previous = {
            lead.id: {"active": lead.active, "won": bool(lead.stage_id.is_won)}
            for lead in self
        }
        result = super().write(vals)
        if self.env.context.get("odx_meta_status_sync_write"):
            return result
        for lead in self.filtered("meta_lead_id"):
            old = previous[lead.id]
            event_name = False
            if vals.get("active") is False and old["active"] and not lead.active:
                event_name = "Lost"
            elif "stage_id" in vals and not old["won"] and lead.stage_id.is_won:
                event_name = "Won"
            if event_name:
                lead._send_meta_status_feedback(event_name)
        return result

    def _send_meta_status_feedback(self, event_name):
        """Queue and immediately attempt an idempotent Meta Conversions API event."""
        for lead in self.sudo():
            account = lead.meta_form_id.account_id.sudo()
            if not account or not account.conversion_sync_enabled:
                continue
            event = self.env["odx.meta.status.event"].sudo().create({
                "account_id": account.id,
                "lead_id": lead.id,
                "meta_lead_ref": lead.meta_lead_id,
                "event_name": event_name,
                "event_id": "odoo-%s-%s-%s" % (
                    lead.id,
                    event_name.lower(),
                    secrets.token_hex(8),
                ),
            })
            lead.with_context(odx_meta_status_sync_write=True).write({
                "meta_status_sync_state": "pending",
                "meta_status_sync_event_id": event.id,
                "meta_status_sync_error": False,
            })
            event._send()
