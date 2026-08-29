import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import html_escape

_logger = logging.getLogger(__name__)


def _parse_meta_datetime(value):
    """Convert Meta ISO-8601 timestamps to Odoo's naive UTC datetime."""
    if not value:
        return fields.Datetime.now()
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


class MetaApiMixin(models.AbstractModel):
    _name = "odx.meta.api.mixin"
    _description = "Meta Graph API helper"

    def _graph_request(self, account, method, path, **kwargs):
        url = "https://graph.facebook.com/%s/%s" % (account.graph_version, path.lstrip("/"))
        access_token = kwargs.pop("access_token", None) or account.access_token
        headers = dict(kwargs.pop("headers", {}), Authorization="Bearer %s" % access_token)
        try:
            response = requests.request(method, url, headers=headers, timeout=30, **kwargs)
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise UserError(_("Meta API connection failed: %s", exc)) from exc
        if not response.ok:
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            raise UserError(_("Meta API error: %s", error.get("message") or response.text[:500]))
        return payload


class MetaAccount(models.Model):
    _name = "odx.meta.account"
    _description = "Meta Lead Ads Account"
    _inherit = ["mail.thread", "odx.meta.api.mixin"]
    _order = "company_id, name"

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    app_id = fields.Char(required=True, groups="base.group_system")
    app_secret = fields.Char(required=True, groups="base.group_system", copy=False)
    access_token = fields.Char(required=True, groups="base.group_system", copy=False)
    verify_token = fields.Char(required=True, default=lambda self: secrets.token_urlsafe(32), groups="base.group_system", copy=False)
    graph_version = fields.Char(required=True, default="v23.0")
    webhook_base_url = fields.Char(
        string="Public Webhook Base URL",
        default=lambda self: self.env["ir.config_parameter"].sudo().get_param("web.base.url"),
        groups="base.group_system",
        help="Public HTTPS origin without a trailing slash, for example https://example.trycloudflare.com.",
    )
    webhook_callback_url = fields.Char(
        string="Webhook Callback URL",
        compute="_compute_webhook_callback_url",
        groups="base.group_system",
    )
    page_ids = fields.One2many("odx.meta.page", "account_id")
    last_reconcile_at = fields.Datetime(readonly=True, copy=False)

    @api.depends("webhook_base_url")
    def _compute_webhook_callback_url(self):
        for account in self:
            if account.id and account.webhook_base_url:
                account.webhook_callback_url = "%s/odx/meta/leads/webhook/%s" % (
                    account.webhook_base_url.rstrip("/"), account.id
                )
            else:
                account.webhook_callback_url = False

    def action_test_connection(self):
        self.ensure_one()
        result = self._graph_request(self, "GET", "me", params={"fields": "id,name"})
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": _("Connection successful"), "message": result.get("name", result.get("id")), "type": "success"
        }}

    def verify_signature(self, raw_body, signature):
        self.ensure_one()
        if not signature or not signature.startswith("sha256="):
            return False
        digest = hmac.new(self.app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature[7:], digest)

    @api.model
    def _cron_reconcile(self):
        for account in self.search([("active", "=", True)]):
            for form in account.page_ids.form_ids.filtered("active"):
                try:
                    form._reconcile()
                except Exception as exc:  # cron must continue with other forms
                    _logger.exception("Meta reconciliation failed for form %s", form.id)
                    self.env["odx.meta.import.event"].sudo().create({
                        "account_id": account.id, "form_id": form.id, "state": "failed",
                        "event_type": "reconcile", "error_message": str(exc)[:2000],
                    })
            account.last_reconcile_at = fields.Datetime.now()


class MetaPage(models.Model):
    _name = "odx.meta.page"
    _description = "Meta Page"
    _order = "name"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    account_id = fields.Many2one("odx.meta.account", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    meta_page_ref = fields.Char(string="Meta Page ID", required=True, index=True)
    page_access_token = fields.Char(groups="base.group_system", copy=False)
    form_ids = fields.One2many("odx.meta.form", "page_id")

    _page_unique = models.Constraint(
        "UNIQUE(account_id, meta_page_ref)", "This Page is already configured for the account."
    )


class MetaForm(models.Model):
    _name = "odx.meta.form"
    _description = "Meta Lead Form"
    _inherit = "odx.meta.api.mixin"
    _order = "name"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    page_id = fields.Many2one("odx.meta.page", required=True, ondelete="cascade")
    account_id = fields.Many2one(related="page_id.account_id", store=True)
    company_id = fields.Many2one(related="page_id.company_id", store=True)
    meta_form_ref = fields.Char(string="Meta Form ID", required=True, index=True)
    team_id = fields.Many2one("crm.team", required=True, domain="[('company_id', 'in', [False, company_id])]" )
    fallback_user_id = fields.Many2one("res.users", domain="[('share', '=', False)]")
    assignment_cursor = fields.Integer(default=-1, groups="base.group_system", copy=False)
    mapping_ids = fields.One2many("odx.meta.field.mapping", "form_id")
    source_id = fields.Many2one("utm.source")
    medium_id = fields.Many2one("utm.medium")
    campaign_id = fields.Many2one("utm.campaign")
    last_lead_created_time = fields.Datetime(copy=False)

    _form_unique = models.Constraint(
        "UNIQUE(account_id, meta_form_ref)", "This lead form is already configured."
    )

    def _next_salesperson(self):
        self.ensure_one()
        self.env.cr.execute("SELECT id FROM odx_meta_form WHERE id = %s FOR UPDATE", [self.id])
        members = self.env["crm.team.member"].sudo().search([
            ("crm_team_id", "=", self.team_id.id), ("active", "=", True),
            ("user_id.active", "=", True), ("user_id.share", "=", False),
        ], order="id")
        users = members.mapped("user_id")
        if not users:
            return self.fallback_user_id or self.team_id.user_id
        cursor = (self.assignment_cursor + 1) % len(users)
        self.assignment_cursor = cursor
        return users[cursor]

    def _mapped_values(self, payload):
        self.ensure_one()
        raw = {
            str(item.get("name") or "").strip().lower(): "\n".join(item.get("values") or [])
            for item in payload.get("field_data", [])
        }
        values = {}
        for mapping in self.mapping_ids:
            meta_field = (mapping.meta_field or "").strip().lower()
            value = raw.pop(meta_field, False)
            # Meta can return the standard phone question under either name,
            # depending on how the Instant Form was created or migrated.
            if not value and meta_field in ("phone", "phone_number"):
                alias = "phone" if meta_field == "phone_number" else "phone_number"
                value = raw.pop(alias, False)
            if value:
                values[mapping.odoo_field_id.name] = value
        return values

    def _payload_answers(self, payload):
        """Return normalized Meta form answers while preserving their labels."""
        self.ensure_one()
        answers = []
        for item in payload.get("field_data", []):
            key = str(item.get("name") or "").strip()
            if not key:
                continue
            answers.append((key, "\n".join(str(value) for value in (item.get("values") or []))))
        return answers

    def _full_name_from_payload(self, payload):
        self.ensure_one()
        answers = {key.lower(): value for key, value in self._payload_answers(payload)}
        full_name = answers.get("full_name") or answers.get("name")
        if not full_name:
            full_name = " ".join(filter(None, [answers.get("first_name"), answers.get("last_name")]))
        return full_name.strip() if full_name else False

    def _lead_description(self, payload, created):
        """Build a complete, readable Meta summary for the CRM Description."""
        self.ensure_one()
        details = [
            (_("Created Datetime"), fields.Datetime.to_string(created)),
            (_("Page"), self.page_id.name),
            (_("Form / Product"), self.name),
            (_("Campaign"), payload.get("campaign_name")),
            (_("Ad Set"), payload.get("adset_name")),
            (_("Ad"), payload.get("ad_name")),
            (_("Meta Lead ID"), payload.get("id")),
        ]
        rows = [
            "<tr><th>%s</th><td>%s</td></tr>" % (
                html_escape(label), html_escape(value or "-"),
            )
            for label, value in details
        ]
        answer_rows = [
            "<tr><th>%s</th><td>%s</td></tr>" % (
                html_escape(key.replace("_", " ").title()),
                html_escape(value or "-"),
            )
            for key, value in self._payload_answers(payload)
        ]
        return (
            "<h4>%s</h4><table class=\"table table-sm\"><tbody>%s</tbody></table>"
            "<h4>%s</h4><table class=\"table table-sm\"><tbody>%s</tbody></table>"
        ) % (
            html_escape(_("Meta Lead Details")),
            "".join(rows),
            html_escape(_("Submitted Form Answers")),
            "".join(answer_rows) or "<tr><td>-</td></tr>",
        )

    def _import_payload(self, payload, event=None):
        self.ensure_one()
        lead_ref = str(payload.get("id") or "")
        if not lead_ref:
            raise ValidationError(_("Meta lead payload has no ID."))
        existing = self.env["crm.lead"].sudo().search([("meta_lead_id", "=", lead_ref)], limit=1)
        if existing:
            if event:
                event.write({
                    "state": "duplicate",
                    "lead_id": existing.id,
                    "error_message": False,
                    "next_retry_at": False,
                    "processed_at": fields.Datetime.now(),
                })
            return existing
        values = self._mapped_values(payload)
        user = self._next_salesperson()
        created = _parse_meta_datetime(payload.get("created_time"))
        full_name = self._full_name_from_payload(payload) or values.get("contact_name")
        title = self.name
        if full_name:
            title = "%s - %s" % (self.name, full_name)
        values.update({
            "name": title,
            "contact_name": full_name or False,
            "description": self._lead_description(payload, created),
            "type": "lead", "team_id": self.team_id.id, "user_id": user.id if user else False,
            "company_id": self.company_id.id, "meta_lead_id": lead_ref,
            "meta_page_id": self.page_id.id, "meta_form_id": self.id, "meta_created_time": created,
            "meta_campaign_name": payload.get("campaign_name"), "meta_adset_name": payload.get("adset_name"),
            "meta_ad_name": payload.get("ad_name"), "source_id": self.source_id.id,
            "medium_id": self.medium_id.id, "campaign_id": self.campaign_id.id,
        })
        lead = self.env["crm.lead"].sudo().create(values)
        self.last_lead_created_time = max(filter(None, [self.last_lead_created_time, created]))
        if event:
            event.write({
                "state": "done",
                "lead_id": lead.id,
                "error_message": False,
                "next_retry_at": False,
                "processed_at": fields.Datetime.now(),
            })
        lead.message_post(body=_("Imported from Meta Lead Ads form %s and assigned automatically.", self.name))
        return lead

    def _fetch_and_import(self, lead_ref, event=None):
        self.ensure_one()
        fields_list = "id,created_time,field_data,campaign_name,adset_name,ad_name,form_id"
        payload = self._graph_request(
            self.account_id,
            "GET",
            lead_ref,
            access_token=self.page_id.page_access_token,
            params={"fields": fields_list},
        )
        return self._import_payload(payload, event=event)

    def _reconcile(self):
        self.ensure_one()
        params = {"fields": "id,created_time,field_data,campaign_name,adset_name,ad_name,form_id", "limit": 100}
        since = self.last_lead_created_time or (fields.Datetime.now() - timedelta(days=7))
        params["filtering"] = json.dumps([{"field": "time_created", "operator": "GREATER_THAN", "value": int(since.timestamp())}])
        payload = self._graph_request(
            self.account_id,
            "GET",
            "%s/leads" % self.meta_form_ref,
            access_token=self.page_id.page_access_token,
            params=params,
        )
        for item in reversed(payload.get("data", [])):
            event = self.env["odx.meta.import.event"].sudo().create({
                "account_id": self.account_id.id, "form_id": self.id, "meta_lead_ref": item.get("id"),
                "event_type": "reconcile", "state": "processing", "payload": json.dumps(item),
            })
            try:
                self._import_payload(item, event=event)
            except Exception as exc:
                event.write({
                    "state": "failed",
                    "error_message": str(exc)[:2000],
                    "next_retry_at": fields.Datetime.now() + timedelta(minutes=2),
                })
                _logger.exception("Unable to import Meta lead %s", item.get("id"))


class MetaFieldMapping(models.Model):
    _name = "odx.meta.field.mapping"
    _description = "Meta Lead Field Mapping"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    form_id = fields.Many2one("odx.meta.form", required=True, ondelete="cascade")
    meta_field = fields.Char(required=True)
    odoo_field_id = fields.Many2one(
        "ir.model.fields",
        required=True,
        ondelete="cascade",
        domain="[('model', '=', 'crm.lead'), ('ttype', 'in', ['char', 'text', 'html']), ('readonly', '=', False)]",
    )

    _mapping_unique = models.Constraint(
        "UNIQUE(form_id, meta_field)", "A Meta field can only be mapped once per form."
    )


class MetaImportEvent(models.Model):
    _name = "odx.meta.import.event"
    _description = "Meta Lead Import Event"
    _order = "create_date desc"

    account_id = fields.Many2one("odx.meta.account", required=True, ondelete="cascade", index=True)
    form_id = fields.Many2one("odx.meta.form", ondelete="set null", index=True)
    meta_lead_ref = fields.Char(index=True)
    event_type = fields.Selection([("webhook", "Webhook"), ("reconcile", "Reconciliation")], required=True)
    state = fields.Selection([
        ("pending", "Pending"), ("processing", "Processing"), ("done", "Done"),
        ("duplicate", "Duplicate"), ("failed", "Failed"),
    ], required=True, default="pending", index=True)
    payload = fields.Text(groups="base.group_system")
    error_message = fields.Text(readonly=True)
    retry_count = fields.Integer(readonly=True)
    next_retry_at = fields.Datetime(index=True)
    processed_at = fields.Datetime(readonly=True)
    lead_id = fields.Many2one("crm.lead", readonly=True, ondelete="set null")

    @api.model
    def _cron_retry(self):
        events = self.search([
            ("state", "=", "failed"), ("retry_count", "<", 5),
            ("form_id", "!=", False), ("meta_lead_ref", "!=", False),
            "|", ("next_retry_at", "=", False), ("next_retry_at", "<=", fields.Datetime.now()),
        ], limit=100)
        for event in events:
            try:
                event.state = "processing"
                event.form_id._fetch_and_import(event.meta_lead_ref, event=event)
            except Exception as exc:
                count = event.retry_count + 1
                event.write({"state": "failed", "retry_count": count, "error_message": str(exc)[:2000],
                             "next_retry_at": fields.Datetime.now() + timedelta(minutes=2 ** count)})
