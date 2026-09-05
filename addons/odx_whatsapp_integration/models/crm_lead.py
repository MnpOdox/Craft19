import json
import re
from datetime import timedelta

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
    whatsapp_automation_state = fields.Selection([
        ("running", "Running"),
        ("replied", "Customer Replied"),
        ("auto_lost", "Closed - No Reply"),
        ("stopped", "Stopped"),
        ("failed", "Failed"),
    ], string="WhatsApp Follow-Up", readonly=True, copy=False, index=True)
    whatsapp_automation_started_at = fields.Datetime(readonly=True, copy=False)
    whatsapp_automation_next_step_id = fields.Many2one(
        "odx.meta.whatsapp.followup.step", string="Next Follow-Up", readonly=True, copy=False,
        ondelete="restrict",
    )
    whatsapp_automation_next_run_at = fields.Datetime(readonly=True, copy=False, index=True)
    whatsapp_automation_last_sent_at = fields.Datetime(readonly=True, copy=False)
    whatsapp_automation_last_message_id = fields.Many2one(
        "odx.whatsapp.message", string="Last Automated Message", readonly=True, copy=False,
    )
    whatsapp_automation_retry_count = fields.Integer(readonly=True, copy=False)
    whatsapp_automation_error = fields.Text(readonly=True, copy=False)
    whatsapp_automation_completion_reason = fields.Selection([
        ("customer_reply", "Customer replied"),
        ("customer_reply_after_close", "Customer replied after automatic close"),
        ("no_reply", "No reply before deadline"),
        ("won", "Lead was won"),
        ("manual_lost", "Lead was manually marked lost"),
        ("configuration_changed", "Automation configuration changed"),
        ("send_failed", "Message could not be sent"),
    ], string="Automation Completion Reason", readonly=True, copy=False)
    whatsapp_automation_auto_closed = fields.Boolean(readonly=True, copy=False)

    def write(self, vals):
        result = super().write(vals)
        if self.env.context.get("odx_whatsapp_automation_write"):
            return result
        running = self.filtered(lambda lead: lead.whatsapp_automation_state == "running")
        if not running:
            return result
        if vals.get("active") is False:
            running._finish_whatsapp_automation("stopped", "manual_lost")
        elif "stage_id" in vals:
            running.filtered("stage_id.is_won")._finish_whatsapp_automation("stopped", "won")
        return result

    def _finish_whatsapp_automation(self, state, reason, error=False):
        if not self:
            return
        self.with_context(odx_whatsapp_automation_write=True).sudo().write({
            "whatsapp_automation_state": state,
            "whatsapp_automation_next_step_id": False,
            "whatsapp_automation_next_run_at": False,
            "whatsapp_automation_completion_reason": reason,
            "whatsapp_automation_error": error or False,
        })

    def _has_whatsapp_automation_reply(self):
        self.ensure_one()
        if not self.whatsapp_automation_started_at:
            return False
        return bool(self.env["odx.whatsapp.message"].sudo().search_count([
            ("conversation_id.lead_id", "=", self.id),
            ("direction", "=", "inbound"),
            ("message_at", ">=", self.whatsapp_automation_started_at),
        ]))

    def _handle_whatsapp_automation_reply(self, message_at):
        """Stop a running sequence and restore only leads it closed itself."""
        for lead in self.sudo():
            if lead.whatsapp_automation_state not in ("running", "auto_lost"):
                continue
            if lead.whatsapp_automation_started_at and message_at < lead.whatsapp_automation_started_at:
                continue
            restored = lead.whatsapp_automation_state == "auto_lost" and lead.whatsapp_automation_auto_closed
            if restored:
                lead.with_context(odx_whatsapp_automation_write=True).action_restore()
            lead._finish_whatsapp_automation(
                "replied",
                "customer_reply_after_close" if restored else "customer_reply",
            )
            lead.with_context(odx_whatsapp_automation_write=True).write({
                "whatsapp_automation_auto_closed": False,
            })
            lead.message_post(body=(
                _("Lead restored because the customer replied after the automatic no-response closure.")
                if restored else _("WhatsApp follow-up automation stopped because the customer replied.")
            ))

    def _next_whatsapp_followup_step(self, current_step):
        self.ensure_one()
        steps = self.meta_form_id.whatsapp_followup_step_ids.sorted(
            key=lambda item: (item.sequence, item.id)
        )
        current_index = list(steps).index(current_step)
        return steps[current_index + 1] if current_index + 1 < len(steps) else self.env[current_step._name]

    def _schedule_whatsapp_automation_retry(self, error):
        self.ensure_one()
        retry_count = self.whatsapp_automation_retry_count + 1
        if retry_count >= 5:
            self._finish_whatsapp_automation("failed", "send_failed", error)
            self.with_context(odx_whatsapp_automation_write=True).write({
                "whatsapp_automation_retry_count": retry_count,
            })
            next_run = False
        else:
            next_run = fields.Datetime.now() + timedelta(minutes=min(5 * (2 ** (retry_count - 1)), 60))
            self.with_context(odx_whatsapp_automation_write=True).write({
                "whatsapp_automation_retry_count": retry_count,
                "whatsapp_automation_next_run_at": next_run,
                "whatsapp_automation_error": error,
            })
        self.with_context(odx_whatsapp_automation_write=True).write({
            "whatsapp_auto_template_state": "failed",
            "whatsapp_auto_template_error": error,
        })
        self.meta_form_id.sudo().write({"whatsapp_auto_last_error": error})
        self.message_post(body=_(
            "Automatic WhatsApp follow-up failed (attempt %(attempt)s of 5): %(error)s",
            attempt=retry_count, error=error,
        ))
        return next_run

    def _process_whatsapp_followup_automation(self):
        for lead in self.sudo():
            if lead.whatsapp_automation_state != "running":
                continue
            if (
                lead.whatsapp_automation_next_run_at
                and lead.whatsapp_automation_next_run_at > fields.Datetime.now()
            ):
                continue
            if not lead.active:
                lead._finish_whatsapp_automation("stopped", "manual_lost")
                continue
            if lead.stage_id.is_won:
                lead._finish_whatsapp_automation("stopped", "won")
                continue
            if lead._has_whatsapp_automation_reply():
                lead._finish_whatsapp_automation("replied", "customer_reply")
                continue
            form = lead.meta_form_id
            if not form or not form.whatsapp_auto_send_enabled or not form.whatsapp_auto_account_id:
                lead._finish_whatsapp_automation("stopped", "configuration_changed")
                continue
            step = lead.whatsapp_automation_next_step_id
            if step and step.form_id != form:
                lead._finish_whatsapp_automation("stopped", "configuration_changed")
                continue
            if not step:
                lost_reason = self.env["crm.lost.reason"].sudo().with_context(active_test=False).search([
                    ("name", "=", "No WhatsApp Response"),
                ], limit=1)
                if not lost_reason:
                    lost_reason = self.env["crm.lost.reason"].sudo().create({
                        "name": "No WhatsApp Response",
                    })
                lead.with_context(
                    odx_whatsapp_automation_write=True,
                ).action_set_lost(lost_reason_id=lost_reason.id)
                lead.whatsapp_conversation_ids.sudo().write({"state": "closed"})
                lead.with_context(odx_whatsapp_automation_write=True).write({
                    "whatsapp_automation_state": "auto_lost",
                    "whatsapp_automation_next_run_at": False,
                    "whatsapp_automation_completion_reason": "no_reply",
                    "whatsapp_automation_auto_closed": True,
                    "whatsapp_automation_error": False,
                })
                lead.message_post(body=_(
                    "Lead automatically marked Lost because no WhatsApp reply was received before the deadline."
                ))
                continue
            try:
                account = form.whatsapp_auto_account_id.sudo()
                template = step.template_id.sudo()
                if not account.active or not template.active or template.status != "approved":
                    raise ValidationError(_("The configured WhatsApp account or template is no longer available."))
                parameters = step._render_parameters(lead)
                if len(parameters) != step._expected_parameter_count():
                    raise ValidationError(_("The configured template-variable values no longer match the template."))
                conversation = self.env["odx.whatsapp.conversation"].sudo()._find_or_create_outbound(account, lead)
                message = conversation.sudo().send_template(template, parameters)
                now = fields.Datetime.now()
                next_step = lead._next_whatsapp_followup_step(step)
                next_run = now + timedelta(
                    hours=next_step.delay_hours if next_step else form.whatsapp_auto_close_hours
                )
                lead.with_context(odx_whatsapp_automation_write=True).write({
                    "whatsapp_automation_next_step_id": next_step.id,
                    "whatsapp_automation_next_run_at": next_run,
                    "whatsapp_automation_last_sent_at": now,
                    "whatsapp_automation_last_message_id": message.id,
                    "whatsapp_automation_retry_count": 0,
                    "whatsapp_automation_error": False,
                    "whatsapp_auto_template_state": "sent",
                    "whatsapp_auto_template_message_id": message.id,
                    "whatsapp_auto_template_error": False,
                })
                form.sudo().write({
                    "whatsapp_auto_last_sent_at": now,
                    "whatsapp_auto_last_error": False,
                })
                lead.message_post(body=_(
                    "WhatsApp follow-up template %(template)s was sent automatically.",
                    template=template.display_name,
                ))
            except Exception as exc:  # lead creation and other due leads must continue
                error = str(exc)[:2000]
                lead._schedule_whatsapp_automation_retry(error)

    @api.model
    def _cron_process_whatsapp_followup_automation(self):
        now = fields.Datetime.now()
        self.env.cr.execute("""
            SELECT id
              FROM crm_lead
             WHERE whatsapp_automation_state = 'running'
               AND whatsapp_automation_next_run_at IS NOT NULL
               AND whatsapp_automation_next_run_at <= %s
             ORDER BY whatsapp_automation_next_run_at, id
             FOR UPDATE SKIP LOCKED
             LIMIT 100
        """, [now])
        lead_ids = [row[0] for row in self.env.cr.fetchall()]
        for lead in self.sudo().with_context(active_test=False).browse(lead_ids).exists():
            with self.env.cr.savepoint():
                lead._process_whatsapp_followup_automation()
        return True

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
