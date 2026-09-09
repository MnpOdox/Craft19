import base64
import binascii
import hashlib
import hmac
import json
import logging
import mimetypes
import re
import secrets
from datetime import datetime, timedelta, timezone

import requests

from odoo import _, api, fields, models, Command
from odoo.exceptions import AccessError, UserError, ValidationError

from .crm_lead import is_valid_whatsapp_phone, normalize_phone

_logger = logging.getLogger(__name__)


class WhatsAppApiMixin(models.AbstractModel):
    _name = "odx.whatsapp.api.mixin"
    _description = "WhatsApp Cloud API helper"

    def _api(self, account, method, path, **kwargs):
        url = "https://graph.facebook.com/%s/%s" % (account.graph_version, path.lstrip("/"))
        # Salespeople may send through an account, but credentials must remain
        # manager-only fields.  Elevate only this server-side credential read;
        # conversation, lead, company and send permissions remain evaluated in
        # the caller's environment before this helper is reached.
        headers = dict(
            kwargs.pop("headers", {}),
            Authorization="Bearer %s" % account.sudo().access_token,
        )
        try:
            response = requests.request(method, url, headers=headers, timeout=30, **kwargs)
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise UserError(_("WhatsApp API connection failed: %s", exc)) from exc
        if not response.ok:
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            details = (error.get("error_data") or {}).get("details")
            message = details or error.get("error_user_msg") or error.get("message") or response.text[:500]
            code = error.get("code")
            raise UserError(_("WhatsApp API error%(code)s: %(message)s",
                              code=" (%s)" % code if code else "", message=message))
        return payload


class WhatsAppAccount(models.Model):
    _name = "odx.whatsapp.account"
    _description = "WhatsApp Business Account"
    _inherit = ["mail.thread", "odx.whatsapp.api.mixin"]
    _order = "company_id, name"

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    app_id = fields.Char(required=True, groups="base.group_system")
    app_secret = fields.Char(required=True, groups="base.group_system", copy=False)
    access_token = fields.Char(required=True, groups="base.group_system", copy=False)
    verify_token = fields.Char(required=True, default=lambda self: secrets.token_urlsafe(32), groups="base.group_system", copy=False)
    graph_version = fields.Char(required=True, default="v26.0")
    webhook_base_url = fields.Char(
        string="Public Webhook Base URL",
        default=lambda self: self.env["ir.config_parameter"].sudo().get_param("web.base.url"),
        groups="base.group_system",
        help="Public HTTPS origin without a trailing slash.",
    )
    webhook_callback_url = fields.Char(
        string="Webhook Callback URL",
        compute="_compute_webhook_callback_url",
        groups="base.group_system",
    )
    waba_id = fields.Char(string="WABA ID", required=True)
    phone_number_id = fields.Char(required=True, index=True)
    display_phone = fields.Char()
    default_country_code = fields.Char(help="Digits only, for example 91 for India.")
    team_id = fields.Many2one("crm.team", required=True)
    fallback_user_id = fields.Many2one("res.users", domain="[('share', '=', False)]")
    assignment_cursor = fields.Integer(default=-1, groups="base.group_system", copy=False)
    template_ids = fields.One2many("odx.whatsapp.template", "account_id")
    webhook_state = fields.Selection([("unknown", "Unknown"), ("verified", "Verified"), ("error", "Error")], default="unknown", readonly=True)
    last_template_sync_at = fields.Datetime(readonly=True)

    _phone_number_unique = models.Constraint("UNIQUE(phone_number_id)", "This WhatsApp phone number is already configured.")

    @api.depends("webhook_base_url")
    def _compute_webhook_callback_url(self):
        for account in self:
            account.webhook_callback_url = (
                "%s/odx/whatsapp/webhook/%s" % (account.webhook_base_url.rstrip("/"), account.id)
                if account.id and account.webhook_base_url
                else False
            )

    def action_test_connection(self):
        self.ensure_one()
        data = self._api(self, "GET", self.phone_number_id, params={"fields": "id,display_phone_number,verified_name"})
        self.write({"display_phone": data.get("display_phone_number"), "webhook_state": "verified"})
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": _("Connection successful"), "message": data.get("verified_name", self.name), "type": "success"
        }}

    def verify_signature(self, raw, signature):
        self.ensure_one()
        if not signature or not signature.startswith("sha256="):
            return False
        expected = hmac.new(self.app_secret.encode(), raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature[7:], expected)

    def _next_salesperson(self):
        self.ensure_one()
        self.env.cr.execute("SELECT id FROM odx_whatsapp_account WHERE id = %s FOR UPDATE", [self.id])
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

    def action_sync_templates(self):
        for account in self:
            data = account._api(account, "GET", "%s/message_templates" % account.waba_id,
                                params={
                                    "fields": "id,name,language,status,category,components,rejected_reason,quality_score",
                                    "limit": 250,
                                })
            for item in data.get("data", []):
                template = self.env["odx.whatsapp.template"].with_context(active_test=False).search([
                    ("account_id", "=", account.id), ("meta_template_id", "=", str(item["id"]))
                ], limit=1)
                values = self.env["odx.whatsapp.template"]._values_from_meta(item, account.id)
                template.write(values) if template else template.create(values)
            account.last_template_sync_at = fields.Datetime.now()
        return True

    @api.model
    def _cron_sync_templates(self):
        for account in self.search([("active", "=", True)]):
            try:
                with self.env.cr.savepoint():
                    account.action_sync_templates()
            except Exception:
                _logger.exception("WhatsApp template synchronization failed for account %s", account.id)


class WhatsAppTemplate(models.Model):
    _name = "odx.whatsapp.template"
    _description = "WhatsApp Message Template"
    _order = "name, language"

    account_id = fields.Many2one("odx.whatsapp.account", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    meta_template_id = fields.Char(index=True, readonly=True, copy=False)
    name = fields.Char(required=True)
    language = fields.Char(required=True, default="en_US")
    status = fields.Selection([
        ("draft", "Draft"), ("pending", "Pending"), ("approved", "Approved"),
        ("rejected", "Rejected"), ("paused", "Paused"), ("disabled", "Disabled"),
        ("in_appeal", "In Appeal"), ("pending_deletion", "Pending Deletion"),
    ], required=True, default="draft", readonly=True, copy=False)
    category = fields.Selection([("marketing", "Marketing"), ("utility", "Utility"), ("authentication", "Authentication")], default="utility")
    allow_category_change = fields.Boolean(
        default=True,
        help="Allow Meta to move the template to the correct category instead of rejecting it.",
    )
    header_text = fields.Char(help="Optional text header. Meta allows one variable, {{1}}, in a text header.")
    header_example = fields.Char(help="Example value for header variable {{1}}.")
    body_text = fields.Text(help="Template body. Use sequential variables such as {{1}}, {{2}}.")
    body_examples = fields.Text(help="One example value per line, in the same order as the body variables.")
    footer_text = fields.Char(help="Optional footer shown below the message body.")
    button_ids = fields.One2many("odx.whatsapp.template.button", "template_id", string="Buttons", copy=True)
    components_json = fields.Text(readonly=True)
    rejection_reason = fields.Text(readonly=True, copy=False)
    quality_score = fields.Char(readonly=True, copy=False)
    submitted_at = fields.Datetime(readonly=True, copy=False)
    last_status_check_at = fields.Datetime(readonly=True, copy=False)
    active = fields.Boolean(default=True)

    _template_unique = models.Constraint("UNIQUE(account_id, meta_template_id)", "This template is already synchronized.")

    @api.model
    def _status_from_meta(self, value):
        status = (value or "pending").lower()
        return status if status in dict(self._fields["status"].selection) else "pending"

    @api.model
    def _values_from_meta(self, item, account_id=False):
        components = item.get("components") or []
        category = (item.get("category") or "utility").lower()
        if category not in dict(self._fields["category"].selection):
            category = "utility"
        values = {
            "meta_template_id": str(item["id"]),
            "name": item.get("name"),
            "language": item.get("language"),
            "status": self._status_from_meta(item.get("status")),
            "category": category,
            "components_json": json.dumps(components),
            "rejection_reason": item.get("rejected_reason") or False,
            "last_status_check_at": fields.Datetime.now(),
        }
        if account_id:
            values["account_id"] = account_id
        quality = item.get("quality_score")
        values["quality_score"] = quality.get("score") if isinstance(quality, dict) else quality or False
        button_commands = [Command.clear()]
        for component in components:
            component_type = (component.get("type") or "").upper()
            if component_type == "HEADER" and (component.get("format") or "TEXT").upper() == "TEXT":
                values["header_text"] = component.get("text") or False
                examples = (component.get("example") or {}).get("header_text") or []
                values["header_example"] = examples[0] if examples else False
            elif component_type == "BODY":
                values["body_text"] = component.get("text") or False
                examples = (component.get("example") or {}).get("body_text") or []
                values["body_examples"] = "\n".join(str(value) for value in (examples[0] if examples else []))
            elif component_type == "FOOTER":
                values["footer_text"] = component.get("text") or False
            elif component_type == "BUTTONS":
                for button in component.get("buttons") or []:
                    kind = (button.get("type") or "").upper()
                    mapped = {"QUICK_REPLY": "quick_reply", "URL": "url", "PHONE_NUMBER": "phone"}.get(kind)
                    if not mapped:
                        continue
                    example = button.get("example") or []
                    button_commands.append(Command.create({
                        "button_type": mapped,
                        "text": button.get("text"),
                        "url": button.get("url"),
                        "url_example": example[0] if isinstance(example, list) and example else False,
                        "phone_number": button.get("phone_number"),
                    }))
        values["button_ids"] = button_commands
        return values

    @staticmethod
    def _placeholder_numbers(value):
        return [int(number) for number in re.findall(r"\{\{(\d+)\}\}", value or "")]

    @staticmethod
    def _example_values(value):
        return [line.strip() for line in (value or "").splitlines() if line.strip()]

    @api.constrains("name")
    def _check_template_name(self):
        for template in self:
            if not re.fullmatch(r"[a-z0-9_]{1,512}", template.name or ""):
                raise ValidationError(_("Template names may contain only lowercase letters, numbers, and underscores."))

    def _validate_draft(self):
        self.ensure_one()
        if self.category == "authentication":
            raise ValidationError(_("Authentication templates have a Meta-specific OTP structure and are currently sync-only."))
        if not self.body_text:
            raise ValidationError(_("Enter the template body before submitting it."))
        limits = [(self.header_text, 60, _("Header")), (self.body_text, 1024, _("Body")),
                  (self.footer_text, 60, _("Footer"))]
        for value, limit, label in limits:
            if value and len(value) > limit:
                raise ValidationError(_("%(label)s cannot exceed %(limit)s characters.", label=label, limit=limit))
        body_numbers = self._placeholder_numbers(self.body_text)
        unique_body_numbers = sorted(set(body_numbers))
        if unique_body_numbers and unique_body_numbers != list(range(1, max(unique_body_numbers) + 1)):
            raise ValidationError(_("Body variables must be sequential, starting with {{1}}."))
        body_examples = self._example_values(self.body_examples)
        if len(body_examples) != len(unique_body_numbers):
            raise ValidationError(_("Provide exactly one body example per variable, one value per line."))
        header_numbers = self._placeholder_numbers(self.header_text)
        if header_numbers not in ([], [1]):
            raise ValidationError(_("A text header may contain only one variable: {{1}}."))
        if bool(header_numbers) != bool(self.header_example):
            raise ValidationError(_("Provide a header example when using {{1}}, and remove it when no header variable is used."))
        if len(self.button_ids) > 10:
            raise ValidationError(_("Meta permits at most 10 template buttons."))
        if len(self.button_ids.filtered(lambda button: button.button_type in ("url", "phone"))) > 2:
            raise ValidationError(_("Meta permits at most two call-to-action buttons."))
        for button in self.button_ids:
            button._validate_for_meta()

    def _build_components(self):
        self.ensure_one()
        self._validate_draft()
        components = []
        if self.header_text:
            header = {"type": "HEADER", "format": "TEXT", "text": self.header_text}
            if self._placeholder_numbers(self.header_text):
                header["example"] = {"header_text": [self.header_example]}
            components.append(header)
        body = {"type": "BODY", "text": self.body_text}
        body_examples = self._example_values(self.body_examples)
        if body_examples:
            body["example"] = {"body_text": [body_examples]}
        components.append(body)
        if self.footer_text:
            components.append({"type": "FOOTER", "text": self.footer_text})
        if self.button_ids:
            components.append({"type": "BUTTONS", "buttons": [button._meta_payload() for button in self.button_ids]})
        return components

    def action_submit_to_meta(self):
        self.ensure_one()
        if not self.env.user.has_group("odx_whatsapp_integration.group_whatsapp_manager"):
            raise AccessError(_("Only WhatsApp managers can submit templates to Meta."))
        if self.status != "draft" or self.meta_template_id:
            raise ValidationError(_("Only an unsubmitted draft can be submitted to Meta."))
        components = self._build_components()
        payload = {
            "name": self.name,
            "language": self.language,
            "category": self.category.upper(),
            "allow_category_change": self.allow_category_change,
            "components": components,
        }
        result = self.account_id._api(
            self.account_id, "POST", "%s/message_templates" % self.account_id.waba_id, json=payload
        )
        if not result.get("id"):
            raise UserError(_("Meta accepted no template ID. Please try again or check the integration logs."))
        self.write({
            "meta_template_id": str(result["id"]),
            "status": self._status_from_meta(result.get("status")),
            "category": (result.get("category") or self.category).lower(),
            "components_json": json.dumps(components),
            "submitted_at": fields.Datetime.now(),
            "last_status_check_at": fields.Datetime.now(),
            "rejection_reason": False,
        })
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": _("Template submitted"),
            "message": _("Meta is reviewing %(name)s. Use Check Approval Status to refresh it.", name=self.name),
            "type": "success",
        }}

    def action_refresh_status(self):
        for template in self:
            if not template.meta_template_id:
                raise ValidationError(_("Submit the draft to Meta before checking its status."))
            item = template.account_id._api(
                template.account_id, "GET", template.meta_template_id,
                params={"fields": "id,name,language,status,category,components,rejected_reason,quality_score"},
            )
            template.write(template._values_from_meta(item))
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": _("Template status refreshed"),
            "message": _("Meta's latest template status is now displayed."),
            "type": "success",
        }}

    def _panel_data(self):
        result = []
        for template in self:
            try:
                components = json.loads(template.components_json or "[]")
            except (TypeError, ValueError):
                components = []
            body = next((item.get("text", "") for item in components if item.get("type", "").upper() == "BODY"), "")
            header = next((item for item in components if item.get("type", "").upper() == "HEADER"), {})
            buttons = next((item.get("buttons", []) for item in components if item.get("type", "").upper() == "BUTTONS"), [])
            placeholders = [int(value) for value in re.findall(r"\{\{(\d+)\}\}", body)]
            result.append({
                "id": template.id, "name": template.name, "language": template.language,
                "category": template.category, "body": body, "header": header,
                "buttons": buttons, "parameter_count": max(placeholders, default=0),
            })
        return result

    def _render_body(self, parameters=None):
        """Render the customer-visible template body for conversation history."""
        self.ensure_one()
        try:
            components = json.loads(self.components_json or "[]")
        except (TypeError, ValueError):
            components = []
        body = next(
            (item.get("text", "") for item in components if (item.get("type") or "").upper() == "BODY"),
            self.body_text or "",
        )
        for index, value in enumerate(parameters or [], start=1):
            body = body.replace("{{%s}}" % index, str(value))
        return body or self.name


class WhatsAppTemplateButton(models.Model):
    _name = "odx.whatsapp.template.button"
    _description = "WhatsApp Template Button"
    _order = "sequence, id"

    template_id = fields.Many2one("odx.whatsapp.template", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    button_type = fields.Selection([
        ("quick_reply", "Quick Reply"), ("url", "Visit Website"), ("phone", "Call Phone Number"),
    ], required=True, default="quick_reply")
    text = fields.Char(required=True)
    url = fields.Char()
    url_example = fields.Char(help="Required when the URL contains {{1}}. Enter the complete sample URL.")
    phone_number = fields.Char(help="Phone number in international format, for example +919876543210.")

    def _validate_for_meta(self):
        self.ensure_one()
        if not self.text or len(self.text) > 25:
            raise ValidationError(_("Button text is required and cannot exceed 25 characters."))
        if self.button_type == "url":
            if not self.url or not self.url.startswith(("https://", "http://")):
                raise ValidationError(_("Website buttons require a complete HTTP or HTTPS URL."))
            variables = WhatsAppTemplate._placeholder_numbers(self.url)
            if variables not in ([], [1]):
                raise ValidationError(_("A website button URL may contain only one variable: {{1}}."))
            if bool(variables) != bool(self.url_example):
                raise ValidationError(_("Provide a complete URL example when using {{1}}."))
        elif self.button_type == "phone":
            if not is_valid_whatsapp_phone(normalize_phone(self.phone_number)):
                raise ValidationError(_("Phone buttons require a valid international phone number."))

    def _meta_payload(self):
        self.ensure_one()
        self._validate_for_meta()
        if self.button_type == "quick_reply":
            return {"type": "QUICK_REPLY", "text": self.text}
        if self.button_type == "url":
            payload = {"type": "URL", "text": self.text, "url": self.url}
            if self.url_example:
                payload["example"] = [self.url_example]
            return payload
        return {"type": "PHONE_NUMBER", "text": self.text, "phone_number": normalize_phone(self.phone_number)}


class WhatsAppSessionTemplate(models.Model):
    _name = "odx.whatsapp.session.template"
    _description = "WhatsApp Session Message / Quick Reply"
    _order = "name, id"

    name = fields.Char(required=True)
    account_id = fields.Many2one("odx.whatsapp.account", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True, index=True)
    body_text = fields.Text(
        required=True,
        help="Reusable session message. Use sequential variables such as {{1}}, {{2}}.",
    )
    button_ids = fields.One2many(
        "odx.whatsapp.session.template.button", "template_id", string="Reply Buttons", copy=True,
    )
    active = fields.Boolean(default=True)

    _name_unique = models.Constraint(
        "UNIQUE(account_id, name)", "A quick-reply template with this name already exists for the account."
    )

    @api.constrains("body_text", "button_ids")
    def _check_content(self):
        for template in self:
            if not (template.body_text or "").strip():
                raise ValidationError(_("Enter the quick-reply message."))
            if len(template.body_text) > 1024:
                raise ValidationError(_("A WhatsApp session message cannot exceed 1024 characters."))
            numbers = sorted(set(WhatsAppTemplate._placeholder_numbers(template.body_text)))
            if numbers and numbers != list(range(1, max(numbers) + 1)):
                raise ValidationError(_("Quick-reply variables must be sequential, starting with {{1}}."))
            if len(template.button_ids) > 3:
                raise ValidationError(_("A quick reply can contain at most three reply buttons."))
            labels = [button.text.strip().lower() for button in template.button_ids]
            if len(labels) != len(set(labels)):
                raise ValidationError(_("Quick-reply button labels must be unique."))

    def _parameter_count(self):
        self.ensure_one()
        return max(WhatsAppTemplate._placeholder_numbers(self.body_text), default=0)

    def _render_body(self, parameters=None):
        self.ensure_one()
        parameters = parameters or []
        if len(parameters) != self._parameter_count():
            raise ValidationError(_(
                "Quick reply %(template)s requires %(expected)s variable value(s).",
                template=self.display_name, expected=self._parameter_count(),
            ))
        body = self.body_text or ""
        for index, value in enumerate(parameters, start=1):
            body = body.replace("{{%s}}" % index, str(value))
        return body

    def _button_labels(self):
        self.ensure_one()
        return self.button_ids.sorted(key=lambda button: (button.sequence, button.id)).mapped("text")

    def _panel_data(self):
        return [{
            "id": template.id,
            "name": template.name,
            "body": template.body_text,
            "buttons": template._button_labels(),
            "parameter_count": template._parameter_count(),
        } for template in self]


class WhatsAppSessionTemplateButton(models.Model):
    _name = "odx.whatsapp.session.template.button"
    _description = "WhatsApp Session Template Reply Button"
    _order = "sequence, id"

    template_id = fields.Many2one(
        "odx.whatsapp.session.template", required=True, ondelete="cascade", index=True,
    )
    sequence = fields.Integer(default=10)
    text = fields.Char(required=True)

    @api.constrains("text")
    def _check_text(self):
        for button in self:
            if not (button.text or "").strip() or len(button.text) > 20:
                raise ValidationError(_("Reply-button text is required and cannot exceed 20 characters."))
            siblings = button.template_id.button_ids
            if len(siblings) > 3:
                raise ValidationError(_("A quick reply can contain at most three reply buttons."))
            labels = [item.text.strip().lower() for item in siblings]
            if len(labels) != len(set(labels)):
                raise ValidationError(_("Quick-reply button labels must be unique."))


class WhatsAppConversation(models.Model):
    _name = "odx.whatsapp.conversation"
    _description = "Private WhatsApp Conversation"
    _order = "last_message_at desc, id desc"

    account_id = fields.Many2one("odx.whatsapp.account", required=True, ondelete="restrict", index=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True, index=True)
    lead_id = fields.Many2one("crm.lead", required=True, ondelete="cascade", index=True)
    owner_id = fields.Many2one(related="lead_id.user_id", store=True, index=True)
    partner_phone = fields.Char(required=True, index=True)
    partner_name = fields.Char()
    message_ids = fields.One2many("odx.whatsapp.message", "conversation_id")
    last_message_at = fields.Datetime(index=True)
    last_inbound_at = fields.Datetime(index=True)
    unread_count = fields.Integer(default=0)
    state = fields.Selection([("open", "Open"), ("closed", "Closed"), ("exception", "Exception")], default="open", index=True)

    _conversation_unique = models.Constraint("UNIQUE(account_id, partner_phone)", "A phone number can have only one conversation per WhatsApp account.")

    @staticmethod
    def _ui_datetime(value):
        return fields.Datetime.to_string(value) if value else False

    @api.model
    def get_inbox_data(self, search_term=False, state_filter="open", limit=100):
        """Return only conversations visible through the current user's record rules."""
        domain = []
        if state_filter in ("open", "closed", "exception"):
            domain.append(("state", "=", state_filter))
        if search_term:
            domain += ["|", "|", ("partner_name", "ilike", search_term),
                       ("partner_phone", "ilike", search_term), ("lead_id.name", "ilike", search_term)]
        conversations = self.search(domain, order="last_message_at desc, id desc", limit=min(int(limit or 100), 200))
        last_by_conversation = {
            conversation.id: self.env["odx.whatsapp.message"].search(
                [("conversation_id", "=", conversation.id)], order="message_at desc, id desc", limit=1
            )
            for conversation in conversations
        }
        return [{
            "id": conversation.id,
            "name": conversation.partner_name or conversation.lead_id.contact_name or conversation.partner_phone,
            "phone": conversation.partner_phone,
            "lead_id": conversation.lead_id.id,
            "lead_name": conversation.lead_id.name,
            "owner_name": conversation.owner_id.name or _("Unassigned"),
            "account_name": conversation.account_id.name,
            "unread_count": conversation.unread_count,
            "state": conversation.state,
            "last_message_at": self._ui_datetime(conversation.last_message_at),
            "last_inbound_at": self._ui_datetime(conversation.last_inbound_at),
            "last_body": last_by_conversation.get(conversation.id)._display_body()[:160]
                if last_by_conversation.get(conversation.id) else "",
            "last_direction": last_by_conversation.get(conversation.id).direction
                if last_by_conversation.get(conversation.id) else False,
            "last_state": last_by_conversation.get(conversation.id).state
                if last_by_conversation.get(conversation.id) else False,
        } for conversation in conversations]

    def get_chat_data(self, message_limit=500, before_message_id=False):
        self.ensure_one()
        self._assert_access()
        limit = min(max(int(message_limit or 100), 1), 500)
        domain = [("conversation_id", "=", self.id)]
        if before_message_id:
            boundary = self.env["odx.whatsapp.message"].browse(int(before_message_id)).exists()
            if not boundary or boundary.conversation_id != self:
                raise ValidationError(_("Invalid WhatsApp history cursor."))
            domain += ["|", ("message_at", "<", boundary.message_at),
                       "&", ("message_at", "=", boundary.message_at), ("id", "<", boundary.id)]
        descending = self.env["odx.whatsapp.message"].search(
            domain, order="message_at desc, id desc", limit=limit + 1
        )
        has_older = len(descending) > limit
        messages = descending[:limit].sorted(key=lambda message: (message.message_at, message.id))
        templates = self.env["odx.whatsapp.template"].search([
            ("account_id", "=", self.account_id.id), ("status", "=", "approved"), ("active", "=", True),
        ], order="name, language")
        template_data = templates._panel_data()
        session_templates = self.env["odx.whatsapp.session.template"].search([
            ("account_id", "=", self.account_id.id), ("active", "=", True),
        ], order="name")
        now = fields.Datetime.now()
        return {
            "id": self.id,
            "name": self.partner_name or self.lead_id.contact_name or self.partner_phone,
            "phone": self.partner_phone,
            "lead_id": self.lead_id.id,
            "lead_name": self.lead_id.name,
            "owner_name": self.owner_id.name or _("Unassigned"),
            "account_id": self.account_id.id,
            "account_name": self.account_id.name,
            "account_active": self.account_id.active,
            "state": self.state,
            "unread_count": self.unread_count,
            "last_inbound_at": self._ui_datetime(self.last_inbound_at),
            "window_open": bool(self.last_inbound_at and self.last_inbound_at >= now - timedelta(hours=24)),
            "templates": template_data,
            "session_templates": session_templates._panel_data(),
            "has_older": has_older,
            "oldest_message_id": messages[0].id if messages else False,
            "messages": [{
                "id": message.id,
                "direction": message.direction,
                "type": message.message_type,
                "body": message._display_body(),
                "state": message.state,
                "message_at": self._ui_datetime(message.message_at),
                "attachment_name": message.attachment_name or "",
                "has_attachment": bool(message.attachment),
                "mimetype": message.mimetype or "",
                "error": message.error_message or "",
                "template_name": message.template_id.name or "",
                "interactive": json.loads(message.interactive_json or "{}"),
                "interactive_reply_id": message.interactive_reply_id or "",
                "interactive_reply_title": message.interactive_reply_title or "",
            } for message in messages],
        }

    def ui_mark_read(self):
        self.action_mark_read()
        return self.get_chat_data()

    def ui_set_state(self, state):
        self.ensure_one()
        if state not in ("open", "closed"):
            raise ValidationError(_("Invalid conversation state."))
        self.write({"state": state})
        return self.get_chat_data()

    def ui_send_text(self, body):
        self.send_text(body)
        return self.get_chat_data()

    def ui_send_template(self, template_id, parameters=None):
        template = self.env["odx.whatsapp.template"].browse(int(template_id)).exists()
        self.send_template(template, parameters or [])
        return self.get_chat_data()

    def ui_send_session_template(self, template_id, parameters=None):
        template = self.env["odx.whatsapp.session.template"].browse(int(template_id)).exists()
        self.send_session_template(template, parameters or [])
        return self.get_chat_data()

    def send_session_template(self, template, parameters=None):
        self.ensure_one()
        self._assert_access()
        if not template or not template.active or template.account_id != self.account_id:
            raise ValidationError(_("Select an active quick reply for this WhatsApp account."))
        body = template._render_body(parameters or [])
        labels = template._button_labels()
        return self.send_interactive(body, labels) if labels else self.send_text(body)

    def ui_send_media(self, media_type, attachment, filename=False, mimetype=False, caption=False):
        self.send_media(media_type, attachment, filename, mimetype, caption)
        return self.get_chat_data()

    @api.model
    def _find_or_create_outbound(self, account, lead):
        lead.ensure_one()
        if not account or not account.active or account.company_id != lead.company_id:
            raise ValidationError(_("Select an active WhatsApp account for this lead's company."))
        lead._assert_whatsapp_access()
        phone = normalize_phone(lead.phone or lead.mobile, account.default_country_code)
        if not is_valid_whatsapp_phone(phone):
            raise ValidationError(_("The lead needs a valid phone or mobile number."))
        # Search beyond the current owner's record rule so an existing private
        # thread is detected before the unique database constraint is reached.
        conversation = self.sudo().search([
            ("account_id", "=", account.id), ("partner_phone", "=", phone),
        ], limit=1)
        if conversation and conversation.lead_id.id != lead.id:
            old_lead = conversation.lead_id
            same_owner = bool(old_lead.user_id and old_lead.user_id == lead.user_id == self.env.user)
            can_route = (
                self.env.su
                or self.env.user.has_group("odx_whatsapp_integration.group_whatsapp_manager")
                or same_owner
            )
            if not can_route:
                raise AccessError(_(
                    "This WhatsApp number belongs to a lead assigned to another salesperson. "
                    "Ask a WhatsApp Manager to route the conversation to this lead."
                ))
            conversation.write({
                "lead_id": lead.id,
                "partner_name": lead.contact_name or lead.partner_name or lead.name,
                "state": "open",
            })
            old_lead.sudo().message_post(body=_(
                "WhatsApp conversation moved to newer lead %s.", lead.display_name,
            ))
            lead.sudo().message_post(body=_(
                "Existing WhatsApp conversation moved from lead %s.", old_lead.display_name,
            ))
        if not conversation:
            conversation = self.create({
                "account_id": account.id, "lead_id": lead.id, "partner_phone": phone,
                "partner_name": lead.contact_name or lead.partner_name or lead.name,
            })
        # Drop the narrow routing sudo before enforcing the caller's access and
        # returning the record to interactive code.
        conversation = conversation.with_env(self.env)
        conversation._assert_access()
        return conversation

    def _assert_access(self):
        if self.env.su:
            return
        if not self.env.user.has_group("odx_whatsapp_integration.group_whatsapp_manager"):
            if any(conversation.owner_id != self.env.user for conversation in self):
                raise AccessError(_("Only the assigned salesperson can access this conversation."))

    def action_mark_read(self):
        self._assert_access()
        self.write({"unread_count": 0})
        return True

    def write(self, vals):
        self._assert_access()
        if (
            not self.env.su
            and not self.env.user.has_group("odx_whatsapp_integration.group_whatsapp_manager")
            and set(vals) - {"unread_count", "state"}
        ):
            raise AccessError(_("Salespeople cannot alter WhatsApp conversation ownership or routing."))
        return super().write(vals)

    def action_compose(self):
        self.ensure_one()
        self._assert_access()
        return {
            "type": "ir.actions.act_window", "res_model": "odx.whatsapp.compose",
            "view_mode": "form", "target": "new",
            "context": {"default_lead_id": self.lead_id.id, "default_account_id": self.account_id.id},
        }

    @api.model
    def _find_or_create_inbound(self, account, phone, contact_name=False):
        normalized = normalize_phone(phone, account.default_country_code)
        if not is_valid_whatsapp_phone(normalized):
            raise ValidationError(_("The inbound WhatsApp number is not a valid E.164 number."))
        conversation = self.sudo().search([("account_id", "=", account.id), ("partner_phone", "=", normalized)], limit=1)
        if conversation:
            # Serialize routing for different inbound messages arriving together.
            self.env.cr.execute("SELECT id FROM odx_whatsapp_conversation WHERE id = %s FOR UPDATE", [conversation.id])
            conversation.invalidate_recordset()
            if conversation.lead_id.stage_id.is_won:
                previous_lead = conversation.lead_id
                user = account._next_salesperson()
                lead = self.env["crm.lead"].sudo().create({
                    "name": _("WhatsApp enquiry from %s", contact_name or conversation.partner_name or normalized),
                    "type": "lead",
                    "partner_id": previous_lead.partner_id.id,
                    "contact_name": previous_lead.contact_name or contact_name,
                    "phone": "+%s" % normalized,
                    "email_from": previous_lead.email_from,
                    "team_id": account.team_id.id,
                    "user_id": user.id if user else False,
                    "company_id": account.company_id.id,
                    "whatsapp_previous_lead_id": previous_lead.id,
                    "description": _(
                        "Created automatically from a new WhatsApp enquiry because the previous lead “%s” was won.",
                        previous_lead.display_name,
                    ),
                })
                conversation.sudo().write({
                    "lead_id": lead.id,
                    "partner_name": contact_name or conversation.partner_name,
                    "state": "open" if lead.user_id else "exception",
                    "unread_count": 0,
                })
                lead.message_post(body=_(
                    "New WhatsApp lead created from won lead %s and assigned to %s.",
                    previous_lead.display_name, lead.user_id.display_name or _("Unassigned"),
                ))
                previous_lead.sudo().message_post(body=_(
                    "A later WhatsApp enquiry created new lead %s. This won lead was not reopened.",
                    lead.display_name,
                ))
            return conversation
        lead = self.env["crm.lead"].sudo().search([
            ("company_id", "=", account.company_id.id), "|", ("whatsapp_phone", "=", normalized),
            ("phone", "ilike", normalized[-9:]),
        ], order="active desc, create_date desc", limit=1)
        if not lead:
            user = account._next_salesperson()
            lead = self.env["crm.lead"].sudo().create({
                "name": _("WhatsApp inquiry from %s", contact_name or normalized), "type": "lead",
                "phone": "+%s" % normalized, "team_id": account.team_id.id,
                "user_id": user.id if user else False, "company_id": account.company_id.id,
            })
            lead.message_post(body=_("Created automatically from an inbound WhatsApp message."))
        return self.sudo().create({"account_id": account.id, "lead_id": lead.id, "partner_phone": normalized,
                                   "partner_name": contact_name, "state": "open" if lead.user_id else "exception"})

    def _send_payload(self, payload, message):
        self.ensure_one()
        self._assert_access()
        if not self.account_id.active:
            raise ValidationError(_("This WhatsApp account is inactive. Ask an administrator to activate it."))
        try:
            result = self.account_id._api(self.account_id, "POST", "%s/messages" % self.account_id.phone_number_id, json=payload)
            meta_id = (result.get("messages") or [{}])[0].get("id")
            message.sudo().write({"meta_message_id": meta_id, "state": "sent", "sent_at": fields.Datetime.now()})
            self.sudo().write({"last_message_at": fields.Datetime.now()})
        except Exception as exc:
            message.sudo().write({"state": "failed", "error_message": str(exc)[:2000]})
            raise
        return message

    def send_text(self, body):
        self.ensure_one()
        self._assert_access()
        if not (body or "").strip():
            raise ValidationError(_("Enter a message."))
        if not self.last_inbound_at or self.last_inbound_at < fields.Datetime.now() - timedelta(hours=24):
            raise ValidationError(_("The 24-hour customer service window is closed. Send an approved template instead."))
        message = self.env["odx.whatsapp.message"].with_context(odx_whatsapp_internal=True).create({"conversation_id": self.id, "direction": "outbound", "message_type": "text", "body": body.strip(), "state": "pending"})
        payload = {"messaging_product": "whatsapp", "to": self.partner_phone, "type": "text", "text": {"body": body.strip()}}
        return self._send_payload(payload, message)

    def send_template(self, template, parameters=None):
        self.ensure_one()
        self._assert_access()
        if not template or template.account_id != self.account_id or template.status != "approved" or not template.active:
            raise ValidationError(_("Select an approved template for this WhatsApp account."))
        if parameters and not all(isinstance(value, str) for value in parameters):
            raise ValidationError(_("Template parameters must contain text values only."))
        components = []
        if parameters:
            components = [{"type": "body", "parameters": [{"type": "text", "text": value} for value in parameters]}]
        payload = {"messaging_product": "whatsapp", "to": self.partner_phone, "type": "template",
                   "template": {"name": template.name, "language": {"code": template.language}, "components": components}}
        message = self.env["odx.whatsapp.message"].with_context(odx_whatsapp_internal=True).create({
            "conversation_id": self.id,
            "direction": "outbound",
            "message_type": "template",
            "template_id": template.id,
            "body": template._render_body(parameters or []),
            "template_parameters_json": json.dumps(parameters or []),
            "state": "pending",
        })
        return self._send_payload(payload, message)

    @staticmethod
    def _mp4_boxes(raw, start=0, end=None):
        """Yield validated ISO-BMFF boxes as (type, start, payload_start, end)."""
        end = len(raw) if end is None else end
        position = start
        while position + 8 <= end:
            size = int.from_bytes(raw[position:position + 4], "big")
            box_type = raw[position + 4:position + 8]
            header_size = 8
            if size == 1 and position + 16 <= end:
                size = int.from_bytes(raw[position + 8:position + 16], "big")
                header_size = 16
            elif size == 0:
                size = end - position
            if size < header_size or position + size > end:
                break
            yield box_type, position, position + header_size, position + size
            position += size

    def _fragmented_mp4_aac_to_adts(self, raw):
        """Losslessly demux Chromium's fragmented AAC/MP4 recording to ADTS AAC."""
        mp4a_at = raw.find(b"mp4a")
        if mp4a_at < 4 or mp4a_at + 32 > len(raw) or b"soun" not in raw:
            raise ValidationError(_("The recorded MP4 file does not contain supported AAC audio."))
        sample_entry_start = mp4a_at - 4
        sample_entry_size = int.from_bytes(raw[sample_entry_start:mp4a_at], "big")
        if sample_entry_size < 36 or sample_entry_start + sample_entry_size > len(raw):
            raise ValidationError(_("The recorded MP4 audio description is invalid."))
        channels = int.from_bytes(raw[sample_entry_start + 24:sample_entry_start + 26], "big")
        sample_rate = int.from_bytes(raw[sample_entry_start + 32:sample_entry_start + 36], "big") >> 16
        frequency_indexes = {
            96000: 0, 88200: 1, 64000: 2, 48000: 3, 44100: 4, 32000: 5,
            24000: 6, 22050: 7, 16000: 8, 12000: 9, 11025: 10, 8000: 11,
        }
        frequency_index = frequency_indexes.get(sample_rate)
        if frequency_index is None or channels not in range(1, 8):
            raise ValidationError(_("The recorded AAC sample rate or channel layout is not supported."))

        frames = []
        for box_type, moof_start, moof_payload, moof_end in self._mp4_boxes(raw):
            if box_type != b"moof":
                continue
            for child_type, _child_start, child_payload, child_end in self._mp4_boxes(raw, moof_payload, moof_end):
                if child_type != b"traf":
                    continue
                for leaf_type, _leaf_start, leaf_payload, leaf_end in self._mp4_boxes(raw, child_payload, child_end):
                    if leaf_type != b"trun" or leaf_payload + 8 > leaf_end:
                        continue
                    version_flags = int.from_bytes(raw[leaf_payload:leaf_payload + 4], "big")
                    flags = version_flags & 0xFFFFFF
                    sample_count = int.from_bytes(raw[leaf_payload + 4:leaf_payload + 8], "big")
                    cursor = leaf_payload + 8
                    if not flags & 0x000001 or not flags & 0x000200:
                        raise ValidationError(_("The recorded MP4 fragment has no usable AAC sample table."))
                    data_offset = int.from_bytes(raw[cursor:cursor + 4], "big", signed=True)
                    cursor += 4
                    if flags & 0x000004:
                        cursor += 4
                    sample_sizes = []
                    for _index in range(sample_count):
                        if flags & 0x000100:
                            cursor += 4
                        if cursor + 4 > leaf_end:
                            raise ValidationError(_("The recorded MP4 sample table is truncated."))
                        sample_sizes.append(int.from_bytes(raw[cursor:cursor + 4], "big"))
                        cursor += 4
                        if flags & 0x000400:
                            cursor += 4
                        if flags & 0x000800:
                            cursor += 4
                    data_cursor = moof_start + data_offset
                    for sample_size in sample_sizes:
                        if not sample_size or data_cursor + sample_size > len(raw):
                            raise ValidationError(_("The recorded MP4 audio data is truncated."))
                        frames.append(raw[data_cursor:data_cursor + sample_size])
                        data_cursor += sample_size
        if not frames:
            raise ValidationError(_("No AAC audio frames were found in the recorded MP4 file."))

        adts = bytearray()
        # AAC-LC is profile 1 in the ADTS header (AudioSpecificConfig object type 2).
        profile = 1
        for frame in frames:
            frame_length = len(frame) + 7
            if frame_length >= 8192:
                raise ValidationError(_("A recorded AAC frame is too large."))
            adts.extend((
                0xFF, 0xF1,
                (profile << 6) | (frequency_index << 2) | (channels >> 2),
                ((channels & 3) << 6) | (frame_length >> 11),
                (frame_length >> 3) & 0xFF,
                ((frame_length & 7) << 5) | 0x1F,
                0xFC,
            ))
            adts.extend(frame)
        return bytes(adts)

    def _prepare_audio_upload(self, raw, mimetype, filename):
        if mimetype == "audio/mp4" and b"moof" in raw and b"trun" in raw:
            raw = self._fragmented_mp4_aac_to_adts(raw)
            mimetype = "audio/aac"
            filename = re.sub(r"\.(?:m4a|mp4)$", "", filename or "voice-note", flags=re.I) + ".aac"
        return raw, mimetype, filename

    def send_media(self, media_type, attachment, filename=False, mimetype=False, caption=False):
        self.ensure_one()
        self._assert_access()
        if media_type not in ("image", "document", "audio", "video"):
            raise ValidationError(_("Unsupported WhatsApp media type."))
        if not self.last_inbound_at or self.last_inbound_at < fields.Datetime.now() - timedelta(hours=24):
            raise ValidationError(_("Media can only be sent inside the 24-hour service window."))
        try:
            raw = base64.b64decode(attachment or b"", validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValidationError(_("The uploaded media is invalid.")) from exc
        maximum = 50 * 1024 * 1024 if media_type == "document" else 16 * 1024 * 1024
        if not raw or len(raw) > maximum:
            raise ValidationError(_("Documents are limited to 50 MB; images, audio, and video are limited to 16 MB."))
        detected_mimetype = (mimetype or "").split(";", 1)[0].strip().lower()
        if not detected_mimetype or detected_mimetype == "application/octet-stream":
            detected_mimetype = (mimetypes.guess_type(filename or "")[0] or "").lower()
        allowed = {
            "image": {"image/jpeg", "image/png", "image/webp"},
            "audio": {"audio/aac", "audio/mp4", "audio/mpeg", "audio/amr", "audio/ogg", "audio/opus"},
            "video": {"video/mp4", "video/3gpp"},
            "document": {
                "text/plain", "application/pdf", "application/msword", "application/vnd.ms-excel",
                "application/vnd.ms-powerpoint",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            },
        }
        if detected_mimetype not in allowed[media_type]:
            raise ValidationError(_(
                "The selected file type (%s) is not supported by WhatsApp for %s messages.",
                detected_mimetype or _("unknown"), media_type,
            ))
        if media_type == "audio":
            raw, detected_mimetype, filename = self._prepare_audio_upload(raw, detected_mimetype, filename)
        stored_attachment = base64.b64encode(raw)
        result = self.account_id._api(
            self.account_id, "POST", "%s/media" % self.account_id.phone_number_id,
            data={"messaging_product": "whatsapp"},
            files={"file": (filename or "attachment", raw, detected_mimetype)},
        )
        message = self.env["odx.whatsapp.message"].with_context(odx_whatsapp_internal=True).create({
            "conversation_id": self.id, "direction": "outbound", "message_type": media_type,
            "body": caption, "attachment": stored_attachment, "attachment_name": filename,
            "mimetype": detected_mimetype, "state": "pending",
        })
        media = {"id": result["id"]}
        if caption and media_type in ("image", "document", "video"):
            media["caption"] = caption
        return self._send_payload({
            "messaging_product": "whatsapp", "to": self.partner_phone,
            "type": media_type, media_type: media,
        }, message)

    def send_interactive(self, body, buttons):
        self.ensure_one()
        self._assert_access()
        if not self.last_inbound_at or self.last_inbound_at < fields.Datetime.now() - timedelta(hours=24):
            raise ValidationError(_("Interactive replies can only be sent inside the 24-hour service window."))
        body = (body or "").strip()
        labels = [(label or "").strip() for label in (buttons or []) if (label or "").strip()]
        if not body or not 1 <= len(labels) <= 3:
            raise ValidationError(_("Enter a question and one to three reply buttons."))
        if any(len(label) > 20 for label in labels):
            raise ValidationError(_("Quick reply button labels can contain at most 20 characters."))
        replies = [{"type": "reply", "reply": {
            "id": "odx_%s_%s" % (secrets.token_hex(5), index), "title": label,
        }} for index, label in enumerate(labels, start=1)]
        interactive = {"type": "button", "body": {"text": body}, "action": {"buttons": replies}}
        message = self.env["odx.whatsapp.message"].with_context(odx_whatsapp_internal=True).create({
            "conversation_id": self.id, "direction": "outbound", "message_type": "interactive",
            "body": body, "interactive_json": json.dumps(interactive), "state": "pending",
        })
        return self._send_payload({
            "messaging_product": "whatsapp", "to": self.partner_phone,
            "type": "interactive", "interactive": interactive,
        }, message)


class WhatsAppMessage(models.Model):
    _name = "odx.whatsapp.message"
    _description = "Private WhatsApp Message"
    _order = "message_at desc, id desc"

    conversation_id = fields.Many2one("odx.whatsapp.conversation", required=True, ondelete="cascade", index=True)
    lead_id = fields.Many2one(related="conversation_id.lead_id", store=True, index=True)
    owner_id = fields.Many2one(related="conversation_id.owner_id", store=True, index=True)
    company_id = fields.Many2one(related="conversation_id.company_id", store=True, index=True)
    meta_message_id = fields.Char(index=True, copy=False)
    direction = fields.Selection([("inbound", "Inbound"), ("outbound", "Outbound")], required=True, index=True)
    message_type = fields.Selection([("text", "Text"), ("template", "Template"), ("image", "Image"), ("document", "Document"), ("audio", "Audio"), ("video", "Video"), ("interactive", "Interactive"), ("unsupported", "Unsupported")], required=True)
    body = fields.Text()
    template_id = fields.Many2one("odx.whatsapp.template", ondelete="set null")
    template_parameters_json = fields.Text(readonly=True)
    attachment = fields.Binary(attachment=True)
    attachment_name = fields.Char()
    mimetype = fields.Char()
    state = fields.Selection([("pending", "Pending"), ("sent", "Sent"), ("delivered", "Delivered"), ("read", "Read"), ("received", "Received"), ("failed", "Failed")], required=True, default="pending", index=True)
    message_at = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    sent_at = fields.Datetime()
    delivered_at = fields.Datetime()
    read_at = fields.Datetime()
    error_message = fields.Text(readonly=True)
    interactive_json = fields.Text(readonly=True)
    interactive_reply_id = fields.Char(readonly=True)
    interactive_reply_title = fields.Char(readonly=True)

    _message_unique = models.Constraint("UNIQUE(meta_message_id)", "This WhatsApp event was already processed.")

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and not self.env.context.get("odx_whatsapp_internal"):
            raise AccessError(_("WhatsApp messages must be created through the send or webhook services."))
        messages = super().create(vals_list)
        for message in messages:
            message.conversation_id._assert_access()
        return messages

    def write(self, vals):
        if not self.env.su and not self.env.context.get("odx_whatsapp_internal"):
            raise AccessError(_("WhatsApp message audit records cannot be edited directly."))
        self.mapped("conversation_id")._assert_access()
        return super().write(vals)

    def unlink(self):
        raise AccessError(_("WhatsApp messages are audit records and cannot be deleted."))

    def _template_parameters(self):
        self.ensure_one()
        if self.template_parameters_json:
            try:
                values = json.loads(self.template_parameters_json)
                return values if isinstance(values, list) else []
            except (TypeError, ValueError):
                return []
        # Compatibility with messages created before rendered template bodies
        # were stored: their body contained parameters separated by " | ".
        return (self.body or "").split(" | ") if self.body else []

    def _display_body(self):
        self.ensure_one()
        if self.message_type != "template" or not self.template_id:
            return self.body or ""
        if self.template_parameters_json:
            return self.body or self.template_id._render_body(self._template_parameters())
        return self.template_id._render_body(self._template_parameters())

    def _notify_inbound(self):
        self.ensure_one()
        conversation = self.conversation_id
        manager_group = self.env.ref(
            "odx_whatsapp_integration.group_whatsapp_manager", raise_if_not_found=False
        )
        recipients = conversation.owner_id.partner_id
        if manager_group:
            managers = self.env["res.users"].sudo().search([
                ("active", "=", True), ("share", "=", False),
                ("group_ids", "in", manager_group.ids),
            ])
            recipients |= managers.partner_id
        if not recipients:
            return
        preview_by_type = {
            "image": _("Photo"), "document": self.attachment_name or _("Document"),
            "audio": _("Voice message"), "video": _("Video"),
            "interactive": self.interactive_reply_title or _("Interactive reply"),
            "unsupported": _("Unsupported WhatsApp message"),
        }
        preview = (self.body or preview_by_type.get(self.message_type) or _("New message")).strip()
        payload = {
            "message_id": self.id,
            "conversation_id": conversation.id,
            "lead_id": conversation.lead_id.id,
            "contact_name": conversation.partner_name or conversation.lead_id.contact_name or conversation.partner_phone,
            "preview": preview[:240],
        }
        for partner in recipients:
            self.env["bus.bus"]._sendone(partner, "odx_whatsapp/new_message", payload)

    @api.model
    def _ingest_message(self, account, payload, contact_name=False):
        meta_id = payload.get("id")
        existing = self.sudo().search([("meta_message_id", "=", meta_id)], limit=1)
        if existing:
            return existing
        conversation = self.env["odx.whatsapp.conversation"]._find_or_create_inbound(account, payload.get("from"), contact_name)
        kind = payload.get("type", "unsupported")
        values = {"conversation_id": conversation.id, "meta_message_id": meta_id, "direction": "inbound",
                  "message_type": kind if kind in dict(self._fields["message_type"].selection) else "unsupported",
                  "state": "received", "message_at": datetime.fromtimestamp(int(payload.get("timestamp", 0)), timezone.utc).replace(tzinfo=None) if payload.get("timestamp") else fields.Datetime.now()}
        if kind == "text":
            values["body"] = payload.get("text", {}).get("body")
        elif kind == "interactive":
            interactive = payload.get("interactive", {})
            reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
            values.update({
                "body": reply.get("title") or reply.get("description") or _("Interactive response"),
                "interactive_json": json.dumps(interactive),
                "interactive_reply_id": reply.get("id"),
                "interactive_reply_title": reply.get("title"),
            })
        elif kind in ("image", "document", "audio", "video"):
            media = payload.get(kind, {})
            values.update({"body": media.get("caption"), "attachment_name": media.get("filename"), "mimetype": media.get("mime_type")})
            media_id = media.get("id")
            if media_id:
                try:
                    info = account._api(account, "GET", media_id)
                    response = requests.get(info["url"], headers={"Authorization": "Bearer %s" % account.access_token}, timeout=30)
                    response.raise_for_status()
                    values.update({"attachment": base64.b64encode(response.content), "mimetype": response.headers.get("Content-Type") or values["mimetype"]})
                except Exception as exc:
                    values["body"] = "%s\n[Media download failed: %s]" % (values.get("body") or "", str(exc)[:300])
        message = self.sudo().create(values)
        now = fields.Datetime.now()
        conversation.lead_id._handle_whatsapp_automation_reply(values["message_at"])
        conversation.sudo().write({
            "last_inbound_at": now, "last_message_at": now,
            "unread_count": conversation.unread_count + 1,
            "state": "open" if conversation.owner_id else "exception",
        })
        message._notify_inbound()
        return message

    @api.model
    def _apply_status(self, payload):
        message = self.sudo().search([("meta_message_id", "=", payload.get("id"))], limit=1)
        if not message:
            return False
        status = payload.get("status")
        values = {}
        ranks = {"pending": 0, "sent": 1, "delivered": 2, "read": 3}
        if status == "failed" or (status in ranks and ranks[status] >= ranks.get(message.state, 0)):
            values["state"] = status
        timestamp = datetime.fromtimestamp(int(payload.get("timestamp", 0)), timezone.utc).replace(tzinfo=None) if payload.get("timestamp") else fields.Datetime.now()
        if status == "sent": values["sent_at"] = timestamp
        if status == "delivered": values["delivered_at"] = timestamp
        if status == "read": values["read_at"] = timestamp
        if status == "failed": values["error_message"] = json.dumps(payload.get("errors", []))[:2000]
        if values:
            message.sudo().write(values)
        return message


class WhatsAppEvent(models.Model):
    _name = "odx.whatsapp.event"
    _description = "WhatsApp Webhook Event"
    _order = "create_date desc"

    account_id = fields.Many2one("odx.whatsapp.account", required=True, ondelete="cascade", index=True)
    payload_hash = fields.Char(required=True, index=True)
    payload = fields.Text(required=True, groups="base.group_system")
    state = fields.Selection([("pending", "Pending"), ("processing", "Processing"), ("done", "Done"), ("failed", "Failed")], default="pending", required=True, index=True)
    retry_count = fields.Integer(readonly=True)
    next_retry_at = fields.Datetime(index=True)
    error_message = fields.Text(readonly=True)
    processed_at = fields.Datetime(readonly=True)

    _event_unique = models.Constraint("UNIQUE(account_id, payload_hash)", "This WhatsApp webhook payload was already recorded.")

    def _process(self):
        for event in self:
            event.state = "processing"
            try:
                payload = json.loads(event.payload)
                for entry in payload.get("entry", []):
                    for change in entry.get("changes", []):
                        value = change.get("value", {})
                        metadata = value.get("metadata", {})
                        if metadata.get("phone_number_id") and str(metadata["phone_number_id"]) != event.account_id.phone_number_id:
                            continue
                        contacts = {item.get("wa_id"): item.get("profile", {}).get("name") for item in value.get("contacts", [])}
                        for message in value.get("messages", []):
                            handled = self.env["odx.whatsapp.salesperson.notification"].ingest_salesperson_message(
                                event.account_id, message
                            )
                            if not handled:
                                self.env["odx.whatsapp.message"]._ingest_message(
                                    event.account_id, message, contacts.get(message.get("from"))
                                )
                        for status in value.get("statuses", []):
                            message = self.env["odx.whatsapp.message"]._apply_status(status)
                            if not message:
                                self.env["odx.whatsapp.salesperson.notification"].apply_status(status)
                event.write({"state": "done", "processed_at": fields.Datetime.now(), "error_message": False})
            except Exception as exc:
                count = event.retry_count + 1
                event.write({"state": "failed", "retry_count": count, "error_message": str(exc)[:2000],
                             "next_retry_at": fields.Datetime.now() + timedelta(minutes=min(2 ** count, 60))})
                _logger.exception("WhatsApp event %s failed", event.id)
        return True

    @api.model
    def _cron_retry(self):
        self.search([("state", "=", "failed"), ("retry_count", "<", 5), "|",
                     ("next_retry_at", "=", False), ("next_retry_at", "<=", fields.Datetime.now())], limit=100)._process()
