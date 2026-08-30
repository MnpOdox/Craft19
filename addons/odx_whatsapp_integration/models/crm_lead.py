import json
import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


def normalize_phone(value, default_country_code=""):
    digits = re.sub(r"\D", "", value or "")
    if value and str(value).strip().startswith("+"):
        return digits
    digits = digits.lstrip("0")
    prefix = re.sub(r"\D", "", default_country_code or "")
    return "%s%s" % (prefix, digits) if digits else ""


def is_valid_whatsapp_phone(value):
    """WhatsApp addresses use E.164 digits without the leading plus sign."""
    return bool(re.fullmatch(r"[1-9]\d{7,14}", value or ""))


class CrmLead(models.Model):
    _inherit = "crm.lead"

    whatsapp_phone = fields.Char(compute="_compute_whatsapp", store=True, index=True)
    whatsapp_conversation_ids = fields.One2many("odx.whatsapp.conversation", "lead_id")
    whatsapp_conversation_count = fields.Integer(compute="_compute_whatsapp_count")
    whatsapp_last_message_at = fields.Datetime(compute="_compute_whatsapp", store=True)
    whatsapp_can_access = fields.Boolean(compute="_compute_whatsapp_can_access")
    whatsapp_previous_lead_id = fields.Many2one(
        "crm.lead", string="Previous WhatsApp Lead", readonly=True, copy=False,
        help="The won lead that preceded this new WhatsApp enquiry.",
    )
    whatsapp_auto_template_state = fields.Selection([
        ("pending", "Pending"), ("sent", "Sent"), ("failed", "Failed"),
    ], string="Automatic WhatsApp", readonly=True, copy=False)
    whatsapp_auto_template_message_id = fields.Many2one(
        "odx.whatsapp.message", string="Automatic WhatsApp Message", readonly=True, copy=False,
    )
    whatsapp_auto_template_error = fields.Text(
        string="Automatic WhatsApp Error", readonly=True, copy=False,
    )

    @api.depends("phone", "whatsapp_conversation_ids.last_message_at")
    def _compute_whatsapp(self):
        for lead in self:
            lead.whatsapp_phone = normalize_phone(lead.phone)
            lead.whatsapp_last_message_at = max(lead.whatsapp_conversation_ids.mapped("last_message_at"), default=False)

    def _compute_whatsapp_count(self):
        data = self.env["odx.whatsapp.conversation"]._read_group([("lead_id", "in", self.ids)], ["lead_id"], ["__count"])
        counts = {lead.id: count for lead, count in data}
        for lead in self:
            lead.whatsapp_conversation_count = counts.get(lead.id, 0)

    @api.depends_context("uid")
    def _compute_whatsapp_can_access(self):
        is_manager = self.env.user.has_group("odx_whatsapp_integration.group_whatsapp_manager")
        for lead in self:
            lead.whatsapp_can_access = bool(lead.id and (is_manager or lead.user_id == self.env.user))

    def _assert_whatsapp_access(self):
        self.ensure_one()
        if not self.id:
            raise ValidationError(_("Save the lead before opening WhatsApp."))
        # Trusted webhook/cron automation runs with ``sudo`` while preserving
        # the triggering user's uid.  It must not be mistaken for an
        # interactive salesperson; normal RPC calls never have ``env.su``.
        if self.env.su:
            return True
        if not self.env.user.has_group("odx_whatsapp_integration.group_whatsapp_manager") and self.user_id != self.env.user:
            raise AccessError(_("Only the assigned salesperson can access this lead's WhatsApp messages."))
        return True

    def get_whatsapp_panel_data(self, conversation_id=False, account_id=False, before_message_id=False, limit=100):
        self.ensure_one()
        self._assert_whatsapp_access()
        accounts = self.env["odx.whatsapp.account"].search([
            ("company_id", "=", self.company_id.id),
        ], order="active desc, name")
        conversations = self.env["odx.whatsapp.conversation"].search([
            ("lead_id", "=", self.id),
        ], order="last_message_at desc, id desc")
        selected = self.env["odx.whatsapp.conversation"]
        if conversation_id:
            selected = conversations.filtered(lambda item: item.id == int(conversation_id))
            if not selected:
                # A form widget can briefly retain the previous lead's selection
                # while Odoo switches records.  Reading the panel should recover
                # to this lead's latest conversation; send methods remain strict.
                selected = conversations[:1]
        elif conversations:
            selected = conversations[0]
        selected_account = self.env["odx.whatsapp.account"]
        if account_id:
            selected_account = accounts.filtered(lambda item: item.id == int(account_id))
            if not selected_account:
                raise AccessError(_("This WhatsApp account is not available for this lead."))
        elif selected:
            selected_account = selected.account_id
        elif accounts:
            selected_account = accounts[0]
        chat = selected.get_chat_data(limit, before_message_id) if selected else {
            "id": False, "name": self.contact_name or self.partner_name or self.name,
            "phone": normalize_phone(self.phone or self.mobile, selected_account.default_country_code if selected_account else ""),
            "lead_id": self.id, "lead_name": self.name, "owner_name": self.user_id.name or _("Unassigned"),
            "account_id": selected_account.id, "account_name": selected_account.name or "",
            "account_active": bool(selected_account.active), "state": "open", "unread_count": 0,
            "last_inbound_at": False, "window_open": False, "messages": [], "has_older": False,
            "oldest_message_id": False,
            "templates": self.env["odx.whatsapp.template"].search([
                ("account_id", "=", selected_account.id), ("status", "=", "approved"), ("active", "=", True),
            ], order="name, language")._panel_data() if selected_account else [],
        }
        return {
            "lead_id": self.id,
            "lead_name": self.name,
            "contact_name": self.contact_name or self.partner_name or self.name,
            "phone": self.phone or self.mobile or "",
            "accounts": [{
                "id": account.id, "name": account.name, "display_phone": account.display_phone or "",
                "active": account.active,
            } for account in accounts],
            "conversations": [{
                "id": conversation.id, "account_id": conversation.account_id.id,
                "account_name": conversation.account_id.name, "phone": conversation.partner_phone,
                "last_message_at": self.env["odx.whatsapp.conversation"]._ui_datetime(conversation.last_message_at),
                "unread_count": conversation.unread_count, "state": conversation.state,
            } for conversation in conversations],
            "selected_account_id": selected_account.id,
            "selected_conversation_id": selected.id,
            "chat": chat,
        }

    def whatsapp_panel_mark_read(self, conversation_id):
        self.ensure_one()
        self._assert_whatsapp_access()
        conversation = self.env["odx.whatsapp.conversation"].browse(int(conversation_id)).exists()
        if not conversation or conversation.lead_id != self:
            raise AccessError(_("This WhatsApp conversation is not available for this lead."))
        conversation.action_mark_read()
        return True

    def _panel_conversation(self, account_id, conversation_id=False, create=False):
        self.ensure_one()
        self._assert_whatsapp_access()
        account = self.env["odx.whatsapp.account"].browse(int(account_id)).exists()
        if not account or account.company_id != self.company_id:
            raise AccessError(_("This WhatsApp account is not available for this lead."))
        if conversation_id:
            conversation = self.env["odx.whatsapp.conversation"].browse(int(conversation_id)).exists()
            if not conversation or conversation.lead_id != self or conversation.account_id != account:
                raise AccessError(_("This WhatsApp conversation is not available for this account."))
            conversation._assert_access()
            return conversation
        if create:
            return self.env["odx.whatsapp.conversation"]._find_or_create_outbound(account, self)
        raise ValidationError(_("Select an existing WhatsApp conversation."))

    def whatsapp_panel_send_text(self, account_id, conversation_id, body):
        conversation = self._panel_conversation(account_id, conversation_id)
        conversation.send_text(body)
        return self.get_whatsapp_panel_data(conversation.id, account_id)

    def whatsapp_panel_send_template(self, account_id, conversation_id, template_id, parameters=None):
        conversation = self._panel_conversation(account_id, conversation_id, create=True)
        template = self.env["odx.whatsapp.template"].browse(int(template_id)).exists()
        conversation.send_template(template, parameters or [])
        return self.get_whatsapp_panel_data(conversation.id, account_id)

    def whatsapp_panel_send_media(self, account_id, conversation_id, media_type, attachment, filename, mimetype, caption=False):
        conversation = self._panel_conversation(account_id, conversation_id)
        conversation.send_media(media_type, attachment, filename, mimetype, caption)
        return self.get_whatsapp_panel_data(conversation.id, account_id)

    def whatsapp_panel_send_interactive(self, account_id, conversation_id, body, buttons):
        conversation = self._panel_conversation(account_id, conversation_id)
        conversation.send_interactive(body, buttons)
        return self.get_whatsapp_panel_data(conversation.id, account_id)

    def whatsapp_panel_retry_message(self, conversation_id, message_id):
        self.ensure_one()
        self._assert_whatsapp_access()
        message = self.env["odx.whatsapp.message"].browse(int(message_id)).exists()
        if (
            not message or message.conversation_id.id != int(conversation_id)
            or message.lead_id != self or message.direction != "outbound" or message.state != "failed"
        ):
            raise AccessError(_("This failed WhatsApp message cannot be retried."))
        conversation = message.conversation_id
        if message.message_type == "text":
            conversation.send_text(message.body)
        elif message.message_type == "template":
            conversation.send_template(message.template_id, message._template_parameters())
        elif message.message_type in ("image", "document", "audio", "video"):
            conversation.send_media(
                message.message_type, message.attachment, message.attachment_name, message.mimetype, message.body
            )
        elif message.message_type == "interactive":
            interactive = json.loads(message.interactive_json or "{}")
            buttons = [item.get("reply", {}).get("title") for item in interactive.get("action", {}).get("buttons", [])]
            conversation.send_interactive(message.body, buttons)
        else:
            raise ValidationError(_("This WhatsApp message type cannot be retried."))
        return self.get_whatsapp_panel_data(conversation.id, conversation.account_id.id)

    def action_open_whatsapp(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("odx_whatsapp_integration.action_whatsapp_conversation")
        action["domain"] = [("lead_id", "=", self.id)]
        action["context"] = {"default_lead_id": self.id}
        return action

    def action_whatsapp_compose(self):
        self.ensure_one()
        self._assert_whatsapp_access()
        return {"type": "ir.actions.act_window", "res_model": "odx.whatsapp.compose", "view_mode": "form",
                "target": "new", "context": {"default_lead_id": self.id}}
