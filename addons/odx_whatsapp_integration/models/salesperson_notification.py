import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .crm_lead import is_valid_whatsapp_phone, normalize_phone


_logger = logging.getLogger(__name__)
_ACTION_PREFIX = "odxlead"
_ACTION_RE = re.compile(r"^odxlead:(\d+):([A-Za-z0-9_-]+):(won|closed)$")


class WhatsAppSalespersonNotification(models.Model):
    _name = "odx.whatsapp.salesperson.notification"
    _description = "WhatsApp Salesperson Lead Notification"
    _order = "create_date desc, id desc"

    lead_id = fields.Many2one("crm.lead", required=True, ondelete="cascade", index=True)
    account_id = fields.Many2one("odx.whatsapp.account", required=True, ondelete="restrict", index=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True, index=True)
    salesperson_id = fields.Many2one("res.users", required=True, ondelete="restrict", index=True)
    recipient_phone = fields.Char(required=True, index=True)
    template_id = fields.Many2one("odx.whatsapp.template", required=True, ondelete="restrict")
    template_parameters_json = fields.Text(readonly=True)
    rendered_body = fields.Text(readonly=True)
    action_token = fields.Char(required=True, copy=False, groups="base.group_system")
    meta_message_id = fields.Char(index=True, copy=False)
    send_mode = fields.Selection([
        ("template", "Approved Template"),
        ("session", "24-Hour Session Message"),
    ], readonly=True)
    state = fields.Selection([
        ("pending", "Pending"),
        ("sent", "Sent"),
        ("delivered", "Delivered"),
        ("read", "Read"),
        ("failed", "Failed"),
    ], required=True, default="pending", index=True)
    sent_at = fields.Datetime(readonly=True)
    delivered_at = fields.Datetime(readonly=True)
    read_at = fields.Datetime(readonly=True)
    last_recipient_message_at = fields.Datetime(readonly=True, index=True)
    last_recipient_message = fields.Char(readonly=True)
    action = fields.Selection([
        ("pending", "Awaiting Salesperson"),
        ("won", "Won"),
        ("closed", "Closed"),
        ("rejected", "Rejected as Stale"),
    ], required=True, default="pending", readonly=True, index=True)
    action_at = fields.Datetime(readonly=True)
    reply_meta_message_id = fields.Char(readonly=True, copy=False)
    retry_count = fields.Integer(readonly=True, copy=False)
    next_retry_at = fields.Datetime(readonly=True, copy=False, index=True)
    error_message = fields.Text(readonly=True, copy=False)

    _meta_message_unique = models.Constraint(
        "UNIQUE(meta_message_id)", "This salesperson notification was already recorded."
    )
    _reply_message_unique = models.Constraint(
        "UNIQUE(reply_meta_message_id)", "This salesperson response was already processed."
    )

    @api.model
    def _recipient_phone(self, salesperson, account):
        return normalize_phone(
            salesperson.partner_id.phone or "",
            account.default_country_code,
        )

    @api.model
    def _session_is_open(self, account, recipient_phone):
        cutoff = fields.Datetime.now() - timedelta(hours=24)
        return bool(self.sudo().search_count([
            ("account_id", "=", account.id),
            ("recipient_phone", "=", recipient_phone),
            ("last_recipient_message_at", ">=", cutoff),
        ]))

    def _button_id(self, action):
        self.ensure_one()
        return "%s:%s:%s:%s" % (_ACTION_PREFIX, self.id, self.action_token, action)

    def _template_components(self):
        self.ensure_one()
        parameters = json.loads(self.template_parameters_json or "[]")
        components = []
        if parameters:
            components.append({
                "type": "body",
                "parameters": [{"type": "text", "text": value} for value in parameters],
            })
        for index, action in enumerate(("won", "closed")):
            components.append({
                "type": "button",
                "sub_type": "quick_reply",
                "index": str(index),
                "parameters": [{"type": "payload", "payload": self._button_id(action)}],
            })
        return components

    def _session_payload(self):
        self.ensure_one()
        buttons = [
            {"type": "reply", "reply": {"id": self._button_id("won"), "title": "Won"}},
            {"type": "reply", "reply": {"id": self._button_id("closed"), "title": "Closed"}},
        ]
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self.recipient_phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": self.rendered_body},
                "action": {"buttons": buttons},
            },
        }

    def _template_payload(self):
        self.ensure_one()
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self.recipient_phone,
            "type": "template",
            "template": {
                "name": self.template_id.name,
                "language": {"code": self.template_id.language},
                "components": self._template_components(),
            },
        }

    def _send(self):
        for notification in self:
            if notification.state in ("sent", "delivered", "read"):
                continue
            if notification.salesperson_id != notification.lead_id.user_id:
                notification.write({
                    "action": "rejected",
                    "action_at": fields.Datetime.now(),
                    "error_message": _("The lead was reassigned before this notification could be sent."),
                    "next_retry_at": False,
                })
                continue
            mode = "session" if notification._session_is_open(
                notification.account_id, notification.recipient_phone
            ) else "template"
            payload = notification._session_payload() if mode == "session" else notification._template_payload()
            try:
                result = notification.account_id._api(
                    notification.account_id,
                    "POST",
                    "%s/messages" % notification.account_id.phone_number_id,
                    json=payload,
                )
                meta_id = (result.get("messages") or [{}])[0].get("id")
                if not meta_id:
                    raise ValidationError(_("Meta accepted no WhatsApp message ID."))
                now = fields.Datetime.now()
                notification.write({
                    "meta_message_id": meta_id,
                    "send_mode": mode,
                    "state": "sent",
                    "sent_at": now,
                    "error_message": False,
                    "next_retry_at": False,
                })
                notification.lead_id.message_post(body=_(
                    "Lead details sent to salesperson %(salesperson)s on WhatsApp (%(mode)s).",
                    salesperson=notification.salesperson_id.display_name,
                    mode=_("approved template") if mode == "template" else _("open session message"),
                ))
            except Exception as exc:
                count = notification.retry_count + 1
                notification.write({
                    "state": "failed",
                    "retry_count": count,
                    "error_message": str(exc)[:2000],
                    "next_retry_at": (
                        fields.Datetime.now() + timedelta(minutes=min(2 ** count, 60))
                        if count < 5 else False
                    ),
                })
                notification.lead_id.message_post(body=_(
                    "Could not notify salesperson on WhatsApp (attempt %(attempt)s): %(error)s",
                    attempt=count,
                    error=str(exc)[:500],
                ))
        return True

    @api.model
    def create_for_lead(self, form, lead):
        form.ensure_one()
        lead.ensure_one()
        if not form.whatsapp_salesperson_notify_enabled:
            return self.browse()
        account = form.whatsapp_salesperson_account_id
        template = form.whatsapp_salesperson_template_id
        salesperson = lead.user_id
        if not salesperson:
            lead.message_post(body=_("WhatsApp salesperson notification was skipped because the lead is unassigned."))
            return self.browse()
        existing = self.sudo().search([
            ("lead_id", "=", lead.id),
            ("salesperson_id", "=", salesperson.id),
            ("state", "in", ["pending", "sent", "delivered", "read"]),
        ], limit=1)
        if existing:
            return existing
        recipient_phone = self._recipient_phone(salesperson, account)
        parameters = form._render_salesperson_notification_parameters(lead)
        notification = self.sudo().create({
            "lead_id": lead.id,
            "account_id": account.id,
            "salesperson_id": salesperson.id,
            "recipient_phone": recipient_phone or "missing",
            "template_id": template.id,
            "template_parameters_json": json.dumps(parameters),
            "rendered_body": template._render_body(parameters),
            "action_token": secrets.token_urlsafe(18),
        })
        if not is_valid_whatsapp_phone(recipient_phone):
            notification.write({
                "state": "failed",
                "retry_count": 5,
                "error_message": _(
                    "The assigned salesperson needs a valid Phone number in their Odoo user contact."
                ),
            })
            lead.message_post(body=notification.error_message)
            return notification
        notification._send()
        return notification

    @api.model
    def _extract_action(self, payload):
        value = ""
        if payload.get("type") == "button":
            value = (payload.get("button") or {}).get("payload") or ""
        elif payload.get("type") == "interactive":
            interactive = payload.get("interactive") or {}
            reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
            value = reply.get("id") or ""
        match = _ACTION_RE.fullmatch(value)
        return match.groups() if match else False

    @api.model
    def ingest_salesperson_message(self, account, payload):
        """Consume employee replies before they can be interpreted as customer enquiries."""
        raw_sender = re.sub(r"\D", "", payload.get("from") or "")
        normalized_sender = normalize_phone(payload.get("from"), account.default_country_code)
        sender_candidates = list(dict.fromkeys(filter(None, [raw_sender, normalized_sender])))
        latest = self.sudo().search([
            ("account_id", "=", account.id),
            ("recipient_phone", "in", sender_candidates),
        ], order="create_date desc, id desc", limit=1)
        sender = latest.recipient_phone if latest else raw_sender
        action_data = self._extract_action(payload)
        if not latest and not action_data:
            return False
        now = fields.Datetime.now()
        if latest:
            body = (
                (payload.get("text") or {}).get("body")
                or (payload.get("button") or {}).get("text")
                or ((payload.get("interactive") or {}).get("button_reply") or {}).get("title")
                or payload.get("type")
            )
            latest.write({
                "last_recipient_message_at": now,
                "last_recipient_message": str(body or "")[:255],
            })
        if not action_data:
            return bool(latest)
        notification_id, token, action = action_data
        notification = self.sudo().browse(int(notification_id)).exists()
        if not notification or notification.account_id != account or notification.action_token != token:
            _logger.warning("Ignored an invalid WhatsApp salesperson action for notification %s", notification_id)
            return True
        self.env.cr.execute(
            "SELECT id FROM odx_whatsapp_salesperson_notification WHERE id = %s FOR UPDATE",
            [notification.id],
        )
        notification.invalidate_recordset()
        if notification.action != "pending":
            return True
        if sender != notification.recipient_phone or notification.lead_id.user_id != notification.salesperson_id:
            notification.write({
                "action": "rejected",
                "action_at": now,
                "reply_meta_message_id": payload.get("id") or False,
                "error_message": _("This response was rejected because the lead is no longer assigned to this salesperson."),
            })
            return True
        lead = notification.lead_id.sudo()
        if action == "won":
            if not lead.stage_id.is_won:
                if not lead.active:
                    lead.action_restore()
                lead.action_set_won_rainbowman()
            note = _("Salesperson %(salesperson)s marked this lead Won from WhatsApp.", salesperson=notification.salesperson_id.display_name)
        else:
            if lead.active and not lead.stage_id.is_won:
                reason = self.env["crm.lost.reason"].sudo().search([
                    ("name", "=", "Closed by Salesperson")
                ], limit=1) or self.env["crm.lost.reason"].sudo().create({"name": "Closed by Salesperson"})
                lead.action_set_lost(lost_reason_id=reason.id)
            note = _("Salesperson %(salesperson)s closed this lead from WhatsApp.", salesperson=notification.salesperson_id.display_name)
        notification.write({
            "action": action,
            "action_at": now,
            "reply_meta_message_id": payload.get("id") or False,
            "error_message": False,
        })
        lead.message_post(body=note)
        return True

    @api.model
    def apply_status(self, payload):
        notification = self.sudo().search([("meta_message_id", "=", payload.get("id"))], limit=1)
        if not notification:
            return False
        status = payload.get("status")
        ranks = {"pending": 0, "sent": 1, "delivered": 2, "read": 3}
        values = {}
        if status == "failed" or (status in ranks and ranks[status] >= ranks.get(notification.state, 0)):
            values["state"] = status
        timestamp = (
            datetime.fromtimestamp(int(payload.get("timestamp", 0)), timezone.utc).replace(tzinfo=None)
            if payload.get("timestamp") else fields.Datetime.now()
        )
        if status == "sent":
            values["sent_at"] = timestamp
        elif status == "delivered":
            values["delivered_at"] = timestamp
        elif status == "read":
            values["read_at"] = timestamp
        elif status == "failed":
            count = notification.retry_count + 1
            values.update({
                "retry_count": count,
                "error_message": json.dumps(payload.get("errors", []))[:2000],
                "next_retry_at": (
                    fields.Datetime.now() + timedelta(minutes=min(2 ** count, 60))
                    if count < 5 else False
                ),
            })
        if values:
            notification.write(values)
        return notification

    def action_retry(self):
        self.filtered(lambda item: item.state == "failed" and item.action == "pending").write({
            "state": "pending",
            "next_retry_at": False,
        })
        self._send()
        return True

    @api.model
    def _cron_retry(self):
        due = self.sudo().search([
            ("state", "=", "failed"),
            ("action", "=", "pending"),
            ("retry_count", "<", 5),
            ("next_retry_at", "!=", False),
            ("next_retry_at", "<=", fields.Datetime.now()),
        ], limit=100)
        due._send()
        return True
