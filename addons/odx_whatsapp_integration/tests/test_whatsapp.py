import base64
import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import Mock, patch

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..controllers.webhook import _redact_sensitive_query


@tagged("post_install", "-at_install")
class TestWhatsApp(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        wa_group = cls.env.ref("odx_whatsapp_integration.group_whatsapp_user")
        cls.sellers = cls.env["res.users"].create([
            {"name": "WA Seller A", "login": "wa_a", "group_ids": [(6, 0, wa_group.ids)]},
            {"name": "WA Seller B", "login": "wa_b", "group_ids": [(6, 0, wa_group.ids)]},
        ])
        cls.team = cls.env["crm.team"].create({"name": "WA Team", "company_id": cls.env.company.id})
        cls.env["crm.team.member"].create([{"crm_team_id": cls.team.id, "user_id": user.id} for user in cls.sellers])
        cls.account = cls.env["odx.whatsapp.account"].create({
            "name": "WhatsApp Test", "app_id": "app", "app_secret": "secret", "access_token": "token",
            "waba_id": "waba", "phone_number_id": "phone-id", "team_id": cls.team.id, "default_country_code": "91",
        })
        cls.sellers[0].partner_id.phone = "+919900000001"
        cls.sellers[1].partner_id.phone = "+919900000002"

    def test_unknown_sender_is_assigned_and_private(self):
        conversation = self.env["odx.whatsapp.conversation"]._find_or_create_inbound(self.account, "+919999999999", "Customer")
        self.assertEqual(conversation.owner_id, self.sellers[0])
        self.assertEqual(self.env["odx.whatsapp.conversation"].with_user(self.sellers[0]).search_count([("id", "=", conversation.id)]), 1)
        self.assertEqual(self.env["odx.whatsapp.conversation"].with_user(self.sellers[1]).search_count([("id", "=", conversation.id)]), 0)
        with self.assertRaises(AccessError):
            conversation.with_user(self.sellers[1]).action_mark_read()

    def test_reassignment_moves_full_history(self):
        conversation = self.env["odx.whatsapp.conversation"]._find_or_create_inbound(self.account, "+918888888888", "Customer")
        message = self.env["odx.whatsapp.message"]._ingest_message(self.account, {
            "id": "wamid.test", "from": "+918888888888", "timestamp": "1700000000", "type": "text", "text": {"body": "Hello"},
        })
        conversation.lead_id.user_id = self.sellers[1]
        self.assertEqual(self.env["odx.whatsapp.message"].with_user(self.sellers[0]).search_count([("id", "=", message.id)]), 0)
        self.assertEqual(self.env["odx.whatsapp.message"].with_user(self.sellers[1]).search_count([("id", "=", message.id)]), 1)

    def test_salesperson_sends_without_credential_field_access(self):
        conversation = self.env["odx.whatsapp.conversation"]._find_or_create_inbound(
            self.account, "+919988776655", "Credential Test"
        )
        conversation.last_inbound_at = fields.Datetime.now()
        salesperson = conversation.owner_id

        with self.assertRaises(AccessError):
            self.account.with_user(salesperson).read(["access_token"])

        response = Mock(ok=True)
        response.json.return_value = {"messages": [{"id": "wamid.salesperson"}]}
        with patch(
            "odoo.addons.odx_whatsapp_integration.models.whatsapp.requests.request",
            return_value=response,
        ) as request:
            message = conversation.with_user(salesperson).send_text("Hello from salesperson")

        self.assertEqual(message.meta_message_id, "wamid.salesperson")
        self.assertEqual(
            request.call_args.kwargs["headers"]["Authorization"],
            "Bearer token",
        )

    def test_same_owner_routes_existing_phone_to_newer_lead(self):
        conversation = self.env["odx.whatsapp.conversation"]._find_or_create_inbound(
            self.account, "+919966554433", "Repeat Customer"
        )
        old_lead = conversation.lead_id
        new_lead = self.env["crm.lead"].create({
            "name": "New enquiry from repeat customer",
            "contact_name": "Repeat Customer",
            "phone": "+919966554433",
            "company_id": self.env.company.id,
            "team_id": self.team.id,
            "user_id": old_lead.user_id.id,
        })

        routed = self.env["odx.whatsapp.conversation"].with_user(old_lead.user_id)._find_or_create_outbound(
            self.account.with_user(old_lead.user_id), new_lead.with_user(old_lead.user_id)
        )

        self.assertEqual(routed, conversation.with_user(old_lead.user_id))
        self.assertEqual(conversation.lead_id, new_lead)
        self.assertEqual(self.env["odx.whatsapp.conversation"].sudo().search_count([
            ("account_id", "=", self.account.id), ("partner_phone", "=", "919966554433"),
        ]), 1)

    def test_salesperson_cannot_take_other_owner_existing_phone(self):
        conversation = self.env["odx.whatsapp.conversation"]._find_or_create_inbound(
            self.account, "+919955443322", "Private Customer"
        )
        other_lead = self.env["crm.lead"].create({
            "name": "Other salesperson enquiry",
            "contact_name": "Private Customer",
            "phone": "+919955443322",
            "company_id": self.env.company.id,
            "team_id": self.team.id,
            "user_id": self.sellers[1].id,
        })

        with self.assertRaises(AccessError):
            self.env["odx.whatsapp.conversation"].with_user(self.sellers[1])._find_or_create_outbound(
                self.account.with_user(self.sellers[1]), other_lead.with_user(self.sellers[1])
            )

        self.assertEqual(conversation.lead_id.user_id, self.sellers[0])
        self.assertNotEqual(conversation.lead_id, other_lead)

    def test_lead_panel_recovers_from_stale_conversation_selection(self):
        previous = self.env["odx.whatsapp.conversation"]._find_or_create_inbound(
            self.account, "+918811111111", "Previous Lead"
        )
        current = self.env["odx.whatsapp.conversation"]._find_or_create_inbound(
            self.account, "+918822222222", "Current Lead"
        )

        panel = current.lead_id.get_whatsapp_panel_data(previous.id)

        self.assertEqual(panel["lead_id"], current.lead_id.id)
        self.assertEqual(panel["selected_conversation_id"], current.id)
        self.assertEqual(panel["chat"]["id"], current.id)

    def _meta_form_with_auto_template(self, suffix="success"):
        meta_account = self.env["odx.meta.account"].create({
            "name": "Meta Auto %s" % suffix,
            "app_id": "meta-app-%s" % suffix,
            "app_secret": "meta-secret",
            "access_token": "meta-token",
        })
        page = self.env["odx.meta.page"].create({
            "name": "Meta Page %s" % suffix,
            "account_id": meta_account.id,
            "meta_page_ref": "page-%s" % suffix,
        })
        template = self.env["odx.whatsapp.template"].create({
            "account_id": self.account.id,
            "meta_template_id": "auto-template-%s" % suffix,
            "name": "welcome_%s" % suffix,
            "language": "en_US",
            "status": "approved",
            "category": "marketing",
            "components_json": json.dumps([{
                "type": "BODY", "text": "Hello {{1}}, thank you for your enquiry.",
            }]),
        })
        form = self.env["odx.meta.form"].create({
            "name": "Auto Form %s" % suffix,
            "page_id": page.id,
            "meta_form_ref": "form-%s" % suffix,
            "team_id": self.team.id,
            "whatsapp_auto_send_enabled": False,
            "whatsapp_auto_account_id": self.account.id,
        })
        self.env["odx.meta.whatsapp.followup.step"].create({
            "form_id": form.id,
            "sequence": 10,
            "template_id": template.id,
            "delay_hours": 0,
            "template_parameters": "{{contact_name}}",
        })
        form.whatsapp_auto_send_enabled = True
        phone_field = self.env["ir.model.fields"]._get("crm.lead", "phone")
        self.env["odx.meta.field.mapping"].create({
            "form_id": form.id,
            "meta_field": "phone_number",
            "odoo_field_id": phone_field.id,
        })
        return form

    def _meta_form_with_salesperson_notification(self, suffix="salesperson"):
        meta_account = self.env["odx.meta.account"].create({
            "name": "Meta Salesperson %s" % suffix,
            "app_id": "meta-salesperson-%s" % suffix,
            "app_secret": "meta-secret",
            "access_token": "meta-token",
        })
        page = self.env["odx.meta.page"].create({
            "name": "Meta Salesperson Page %s" % suffix,
            "account_id": meta_account.id,
            "meta_page_ref": "salesperson-page-%s" % suffix,
        })
        template = self.env["odx.whatsapp.template"].create({
            "account_id": self.account.id,
            "meta_template_id": "salesperson-template-%s" % suffix,
            "name": "salesperson_assignment_%s" % suffix,
            "language": "en_US",
            "status": "approved",
            "category": "utility",
            "body_text": "Lead {{1}}\nCustomer: {{2}}\nPhone: {{3}}\nProduct: {{4}}\nChat: {{5}}",
            "components_json": json.dumps([
                {"type": "BODY", "text": "Lead {{1}}\nCustomer: {{2}}\nPhone: {{3}}\nProduct: {{4}}\nChat: {{5}}"},
                {"type": "BUTTONS", "buttons": [
                    {"type": "QUICK_REPLY", "text": "Won"},
                    {"type": "QUICK_REPLY", "text": "Closed"},
                ]},
            ]),
            "button_ids": [
                (0, 0, {"sequence": 10, "button_type": "quick_reply", "text": "Won"}),
                (0, 0, {"sequence": 20, "button_type": "quick_reply", "text": "Closed"}),
            ],
        })
        form = self.env["odx.meta.form"].create({
            "name": "Salesperson Form %s" % suffix,
            "page_id": page.id,
            "meta_form_ref": "salesperson-form-%s" % suffix,
            "team_id": self.team.id,
            "whatsapp_salesperson_account_id": self.account.id,
            "whatsapp_salesperson_template_id": template.id,
            "whatsapp_salesperson_template_parameters": (
                "{{lead_number}}\n{{contact_name}}\n{{customer_phone}}\n{{lead_name}}\n{{customer_whatsapp_link}}"
            ),
        })
        form.whatsapp_salesperson_notify_enabled = True
        phone_field = self.env["ir.model.fields"]._get("crm.lead", "phone")
        self.env["odx.meta.field.mapping"].create({
            "form_id": form.id,
            "meta_field": "phone_number",
            "odoo_field_id": phone_field.id,
        })
        return form

    def _salesperson_notification_payload(self, reference="salesperson-lead-1"):
        return {
            "id": reference,
            "created_time": "2026-09-09T10:00:00+0000",
            "field_data": [
                {"name": "full_name", "values": ["Notification Customer"]},
                {"name": "phone_number", "values": ["+919811223344"]},
            ],
        }

    def test_meta_lead_notifies_salesperson_without_messaging_customer(self):
        form = self._meta_form_with_salesperson_notification()
        with patch.object(type(self.account), "_api", return_value={
            "messages": [{"id": "wamid.salesperson.assignment"}],
        }) as api_call:
            lead = form._import_payload(self._salesperson_notification_payload())
            duplicate = form._import_payload(self._salesperson_notification_payload())

        self.assertEqual(duplicate, lead)
        self.assertEqual(api_call.call_count, 1)
        notification = self.env["odx.whatsapp.salesperson.notification"].search([
            ("lead_id", "=", lead.id)
        ])
        self.assertEqual(notification.salesperson_id, lead.user_id)
        self.assertEqual(notification.recipient_phone, "919900000001")
        self.assertEqual(notification.send_mode, "template")
        request = api_call.call_args.kwargs["json"]
        self.assertEqual(request["to"], "919900000001")
        self.assertEqual(request["type"], "template")
        components = request["template"]["components"]
        self.assertEqual(components[0]["parameters"][0]["text"], "LEAD-%06d" % lead.id)
        self.assertEqual(components[0]["parameters"][4]["text"], "https://wa.me/919811223344")
        self.assertTrue(components[1]["parameters"][0]["payload"].endswith(":won"))
        self.assertTrue(components[2]["parameters"][0]["payload"].endswith(":closed"))
        self.assertFalse(self.env["odx.whatsapp.conversation"].search_count([
            ("partner_phone", "=", "919811223344")
        ]))

    def test_salesperson_whatsapp_button_marks_correct_lead_won(self):
        form = self._meta_form_with_salesperson_notification("won")
        with patch.object(type(self.account), "_api", return_value={
            "messages": [{"id": "wamid.salesperson.won.assignment"}],
        }):
            lead = form._import_payload(self._salesperson_notification_payload("salesperson-lead-won"))
        notification = lead.whatsapp_salesperson_notification_ids
        handled = notification.ingest_salesperson_message(self.account, {
            "id": "wamid.salesperson.won.reply",
            "from": notification.recipient_phone,
            "timestamp": "1788948000",
            "type": "button",
            "button": {"payload": notification._button_id("won"), "text": "Won"},
        })

        self.assertTrue(handled)
        self.assertTrue(lead.stage_id.is_won)
        self.assertEqual(notification.action, "won")
        self.assertFalse(self.env["odx.whatsapp.conversation"].search_count([
            ("partner_phone", "=", notification.recipient_phone)
        ]))

    def test_salesperson_whatsapp_closed_button_marks_lead_lost(self):
        form = self._meta_form_with_salesperson_notification("closed")
        with patch.object(type(self.account), "_api", return_value={
            "messages": [{"id": "wamid.salesperson.closed.assignment"}],
        }):
            lead = form._import_payload(self._salesperson_notification_payload("salesperson-lead-closed"))
        notification = lead.whatsapp_salesperson_notification_ids
        self.assertTrue(notification.ingest_salesperson_message(self.account, {
            "id": "wamid.salesperson.closed.reply",
            "from": notification.recipient_phone,
            "timestamp": "1788948000",
            "type": "interactive",
            "interactive": {"type": "button_reply", "button_reply": {
                "id": notification._button_id("closed"), "title": "Closed",
            }},
        }))

        self.assertFalse(lead.active)
        self.assertEqual(lead.lost_reason_id.name, "Closed by Salesperson")
        self.assertEqual(notification.action, "closed")

    def test_salesperson_reply_opens_session_for_later_notifications(self):
        form = self._meta_form_with_salesperson_notification("session")
        with patch.object(type(self.account), "_api", return_value={
            "messages": [{"id": "wamid.salesperson.first"}],
        }):
            first = form._import_payload(self._salesperson_notification_payload("salesperson-lead-session-1"))
        first_notification = first.whatsapp_salesperson_notification_ids
        self.assertTrue(first_notification.ingest_salesperson_message(self.account, {
            "id": "wamid.salesperson.hello",
            "from": first_notification.recipient_phone,
            "timestamp": "1788948000",
            "type": "text",
            "text": {"body": "Received"},
        }))
        second = self.env["crm.lead"].create({
            "name": "Second assigned lead",
            "contact_name": "Second Customer",
            "phone": "+919822334455",
            "team_id": self.team.id,
            "user_id": first.user_id.id,
            "company_id": self.env.company.id,
            "meta_form_id": form.id,
        })
        with patch.object(type(self.account), "_api", return_value={
            "messages": [{"id": "wamid.salesperson.second"}],
        }) as api_call:
            second_notification = form._send_salesperson_notification(second)

        self.assertEqual(second_notification.send_mode, "session")
        request = api_call.call_args.kwargs["json"]
        self.assertEqual(request["type"], "interactive")
        self.assertIn("Second Customer", request["interactive"]["body"]["text"])
        self.assertEqual(
            [button["reply"]["title"] for button in request["interactive"]["action"]["buttons"]],
            ["Won", "Closed"],
        )

    def test_customer_and_salesperson_workflows_are_mutually_exclusive(self):
        form = self._meta_form_with_salesperson_notification("exclusive")
        with self.assertRaises(ValidationError):
            form.whatsapp_auto_send_enabled = True

    def test_meta_form_sends_configured_template_once(self):
        form = self._meta_form_with_auto_template()
        payload = {
            "id": "meta-auto-lead-success",
            "created_time": "2026-08-30T10:00:00+0000",
            "field_data": [
                {"name": "full_name", "values": ["Auto Customer"]},
                {"name": "phone_number", "values": ["+919811223344"]},
            ],
        }
        with patch.object(type(self.account), "_api", return_value={
            "messages": [{"id": "wamid.auto-template"}],
        }) as api_call:
            lead = form._import_payload(payload)
            duplicate = form._import_payload(payload)

        self.assertEqual(duplicate, lead)
        self.assertEqual(api_call.call_count, 1)
        request = api_call.call_args.kwargs["json"]
        self.assertEqual(request["to"], "919811223344")
        self.assertEqual(request["template"]["components"][0]["parameters"][0]["text"], "Auto Customer")
        self.assertEqual(lead.whatsapp_auto_template_state, "sent")
        self.assertEqual(lead.whatsapp_auto_template_message_id.meta_message_id, "wamid.auto-template")
        self.assertTrue(form.whatsapp_auto_last_sent_at)
        self.assertFalse(form.whatsapp_auto_last_error)

    def test_pending_ad_route_does_not_start_whatsapp_automation(self):
        form = self._meta_form_with_auto_template("pending_route")
        form.route_by_ad = True
        payload = {
            "id": "meta-pending-ad-route",
            "ad_id": "unconfigured-ad",
            "field_data": [
                {"name": "full_name", "values": ["Pending Customer"]},
                {"name": "phone_number", "values": ["+919811223399"]},
            ],
        }

        with patch.object(type(form.account_id), "_graph_request", return_value={
            "id": "unconfigured-ad", "name": "New Unconfigured Ad",
        }):
            lead = form._import_payload(payload)

        self.assertFalse(lead)
        route = self.env["odx.meta.ad.route"].search([("meta_ad_ref", "=", "unconfigured-ad")])
        self.assertEqual(route.configuration_state, "needs_configuration")
        self.assertFalse(self.env["odx.whatsapp.message"].search_count([
            ("body", "ilike", "Pending Customer"),
        ]))

    def test_meta_auto_template_sudo_job_is_not_blocked_by_lead_owner(self):
        form = self._meta_form_with_auto_template("sudo_job")
        lead = self.env["crm.lead"].create({
            "name": "Automated Lead",
            "contact_name": "Automation Customer",
            "phone": "+919811223355",
            "company_id": self.env.company.id,
            "team_id": self.team.id,
            "user_id": self.sellers[0].id,
            "meta_form_id": form.id,
        })

        with patch.object(
            type(self.account),
            "_api",
            return_value={"messages": [{"id": "wamid.sudo-job"}]},
        ):
            form.with_user(self.sellers[1]).sudo()._send_automatic_whatsapp_template(
                lead.with_user(self.sellers[1])
            )

        self.assertEqual(lead.whatsapp_auto_template_state, "sent")
        self.assertEqual(lead.whatsapp_auto_template_message_id.meta_message_id, "wamid.sudo-job")

    def test_meta_lead_creation_survives_automatic_template_failure(self):
        form = self._meta_form_with_auto_template("failure")
        payload = {
            "id": "meta-auto-lead-failure",
            "field_data": [
                {"name": "full_name", "values": ["No Phone Customer"]},
                {"name": "phone_number", "values": ["invalid"]},
            ],
        }

        lead = form._import_payload(payload)

        self.assertTrue(lead.exists())
        self.assertEqual(lead.whatsapp_auto_template_state, "failed")
        self.assertIn("valid phone", lead.whatsapp_auto_template_error)
        self.assertEqual(form.whatsapp_auto_last_error, lead.whatsapp_auto_template_error)

    def test_meta_followup_sequence_uses_delay_after_previous_send(self):
        form = self._meta_form_with_auto_template("sequence")
        second_template = self.env["odx.whatsapp.template"].create({
            "account_id": self.account.id,
            "meta_template_id": "auto-template-sequence-second",
            "name": "reminder_sequence",
            "language": "en_US",
            "status": "approved",
            "category": "marketing",
            "components_json": json.dumps([{
                "type": "BODY", "text": "Hello {{1}}, are you still interested?",
            }]),
        })
        second_step = self.env["odx.meta.whatsapp.followup.step"].create({
            "form_id": form.id,
            "sequence": 20,
            "template_id": second_template.id,
            "delay_hours": 4,
            "template_parameters": "{{contact_name}}",
        })
        payload = {
            "id": "meta-auto-lead-sequence",
            "field_data": [
                {"name": "full_name", "values": ["Sequence Customer"]},
                {"name": "phone_number", "values": ["+919811223401"]},
            ],
        }
        with patch.object(type(self.account), "_api", side_effect=[
            {"messages": [{"id": "wamid.sequence-first"}]},
            {"messages": [{"id": "wamid.sequence-second"}]},
        ]) as api_call:
            lead = form._import_payload(payload)
            first_sent_at = lead.whatsapp_automation_last_sent_at
            self.assertEqual(lead.whatsapp_automation_next_step_id, second_step)
            self.assertEqual(
                lead.whatsapp_automation_next_run_at,
                first_sent_at + timedelta(hours=4),
            )
            lead.with_context(odx_whatsapp_automation_write=True).whatsapp_automation_next_run_at = fields.Datetime.now()
            lead._process_whatsapp_followup_automation()

        self.assertEqual(api_call.call_count, 2)
        self.assertFalse(lead.whatsapp_automation_next_step_id)
        self.assertEqual(
            lead.whatsapp_automation_next_run_at,
            lead.whatsapp_automation_last_sent_at + timedelta(hours=form.whatsapp_auto_close_hours),
        )
        self.assertEqual(lead.whatsapp_automation_last_message_id.meta_message_id, "wamid.sequence-second")

    def test_inbound_reply_stops_pending_meta_followups(self):
        form = self._meta_form_with_auto_template("reply")
        payload = {
            "id": "meta-auto-lead-reply",
            "field_data": [
                {"name": "full_name", "values": ["Reply Customer"]},
                {"name": "phone_number", "values": ["+919811223402"]},
            ],
        }
        with patch.object(type(self.account), "_api", return_value={
            "messages": [{"id": "wamid.reply-first"}],
        }):
            lead = form._import_payload(payload)

        self.env["odx.whatsapp.message"]._ingest_message(self.account, {
            "id": "wamid.customer-reply", "from": "+919811223402",
            "type": "text", "text": {"body": "Yes, I am interested"},
        }, "Reply Customer")

        self.assertEqual(lead.whatsapp_automation_state, "replied")
        self.assertEqual(lead.whatsapp_automation_completion_reason, "customer_reply")
        self.assertFalse(lead.whatsapp_automation_next_run_at)

    def test_no_reply_marks_lost_and_late_reply_restores_same_lead(self):
        form = self._meta_form_with_auto_template("late_reply")
        payload = {
            "id": "meta-auto-lead-late-reply",
            "field_data": [
                {"name": "full_name", "values": ["Late Reply Customer"]},
                {"name": "phone_number", "values": ["+919811223403"]},
            ],
        }
        with patch.object(type(self.account), "_api", return_value={
            "messages": [{"id": "wamid.late-reply-first"}],
        }):
            lead = form._import_payload(payload)
        conversation = lead.whatsapp_conversation_ids
        lead.with_context(odx_whatsapp_automation_write=True).write({
            "whatsapp_automation_next_step_id": False,
            "whatsapp_automation_next_run_at": fields.Datetime.now(),
        })
        lead._process_whatsapp_followup_automation()

        self.assertFalse(lead.active)
        self.assertEqual(lead.whatsapp_automation_state, "auto_lost")
        self.assertEqual(lead.lost_reason_id.name, "No WhatsApp Response")
        self.assertEqual(conversation.state, "closed")

        reply = self.env["odx.whatsapp.message"]._ingest_message(self.account, {
            "id": "wamid.reply-after-close", "from": "+919811223403",
            "type": "text", "text": {"body": "Sorry, I just saw this"},
        }, "Late Reply Customer")

        self.assertEqual(reply.conversation_id.lead_id, lead)
        self.assertTrue(lead.active)
        self.assertEqual(lead.whatsapp_automation_state, "replied")
        self.assertEqual(lead.whatsapp_automation_completion_reason, "customer_reply_after_close")
        self.assertEqual(conversation.state, "open")

    def test_manually_lost_meta_lead_is_not_restored_by_reply(self):
        form = self._meta_form_with_auto_template("manual_lost")
        payload = {
            "id": "meta-auto-lead-manual-lost",
            "field_data": [
                {"name": "full_name", "values": ["Manual Lost Customer"]},
                {"name": "phone_number", "values": ["+919811223404"]},
            ],
        }
        with patch.object(type(self.account), "_api", return_value={
            "messages": [{"id": "wamid.manual-lost-first"}],
        }):
            lead = form._import_payload(payload)
        lead.action_set_lost()
        self.assertEqual(lead.whatsapp_automation_state, "stopped")

        self.env["odx.whatsapp.message"]._ingest_message(self.account, {
            "id": "wamid.reply-manual-lost", "from": "+919811223404",
            "type": "text", "text": {"body": "I am replying"},
        }, "Manual Lost Customer")

        self.assertFalse(lead.active)
        self.assertEqual(lead.whatsapp_automation_completion_reason, "manual_lost")

    def test_won_meta_lead_stops_pending_followups(self):
        form = self._meta_form_with_auto_template("won_stop")
        payload = {
            "id": "meta-auto-lead-won-stop",
            "field_data": [
                {"name": "full_name", "values": ["Won Customer"]},
                {"name": "phone_number", "values": ["+919811223405"]},
            ],
        }
        with patch.object(type(self.account), "_api", return_value={
            "messages": [{"id": "wamid.won-stop-first"}],
        }):
            lead = form._import_payload(payload)
        won_stage = self.env["crm.stage"].create({"name": "Automation Won", "is_won": True})

        lead.stage_id = won_stage

        self.assertEqual(lead.whatsapp_automation_state, "stopped")
        self.assertEqual(lead.whatsapp_automation_completion_reason, "won")
        self.assertFalse(lead.whatsapp_automation_next_run_at)

    def test_meta_followup_failure_retries_are_bounded(self):
        form = self._meta_form_with_auto_template("bounded_retry")
        payload = {
            "id": "meta-auto-lead-bounded-retry",
            "field_data": [
                {"name": "full_name", "values": ["Invalid Phone"]},
                {"name": "phone_number", "values": ["bad"]},
            ],
        }
        lead = form._import_payload(payload)
        self.assertEqual(lead.whatsapp_automation_retry_count, 1)
        for _attempt in range(4):
            lead.with_context(odx_whatsapp_automation_write=True).whatsapp_automation_next_run_at = fields.Datetime.now()
            lead._process_whatsapp_followup_automation()

        self.assertEqual(lead.whatsapp_automation_state, "failed")
        self.assertEqual(lead.whatsapp_automation_retry_count, 5)
        self.assertEqual(lead.whatsapp_automation_completion_reason, "send_failed")
        self.assertFalse(lead.whatsapp_automation_next_run_at)

    def test_message_after_won_lead_creates_new_lead(self):
        phone = "+918787878787"
        first = self.env["odx.whatsapp.message"]._ingest_message(self.account, {
            "id": "wamid.before-won", "from": phone, "timestamp": "1700000000",
            "type": "text", "text": {"body": "First enquiry"},
        }, "Returning Customer")
        conversation = first.conversation_id
        won_lead = conversation.lead_id
        original_owner = won_lead.user_id
        won_stage = self.env["crm.stage"].create({"name": "Won WhatsApp Test", "is_won": True})
        won_lead.stage_id = won_stage
        conversation.state = "closed"

        second = self.env["odx.whatsapp.message"]._ingest_message(self.account, {
            "id": "wamid.after-won", "from": phone, "timestamp": "1700000100",
            "type": "text", "text": {"body": "A new enquiry"},
        }, "Returning Customer")
        new_lead = second.conversation_id.lead_id

        self.assertNotEqual(new_lead, won_lead)
        self.assertEqual(won_lead.stage_id, won_stage)
        self.assertEqual(new_lead.whatsapp_previous_lead_id, won_lead)
        self.assertNotEqual(new_lead.user_id, original_owner)
        self.assertEqual(second.conversation_id.state, "open")
        self.assertEqual(set(second.conversation_id.message_ids.ids), {first.id, second.id})
        self.assertEqual(first.lead_id, new_lead)

    def test_service_window(self):
        conversation = self.env["odx.whatsapp.conversation"]._find_or_create_inbound(self.account, "+917777777777", "Customer")
        conversation.last_inbound_at = fields.Datetime.now() - timedelta(hours=25)
        with self.assertRaises(ValidationError):
            conversation.with_user(conversation.owner_id).send_text("Too late")
        conversation.last_inbound_at = fields.Datetime.now()
        with patch.object(type(self.account), "_api", return_value={"messages": [{"id": "wamid.out"}]}):
            message = conversation.with_user(conversation.owner_id).send_text("Within window")
        self.assertEqual(message.state, "sent")

    def test_signature_validation(self):
        raw = b'{"object":"whatsapp_business_account"}'
        digest = hmac.new(b"secret", raw, hashlib.sha256).hexdigest()
        self.assertTrue(self.account.verify_signature(raw, "sha256=%s" % digest))
        self.assertFalse(self.account.verify_signature(raw, "sha256=wrong"))
        self.assertFalse(self.account.verify_signature(raw, False))

    def test_sensitive_webhook_query_values_are_redacted(self):
        value = (
            'GET /odx/whatsapp/webhook/1?hub.mode=subscribe&'
            'hub.verify_token=super-secret&hub.challenge=test&access_token=api-secret HTTP/1.1'
        )
        redacted = _redact_sensitive_query(value)
        self.assertNotIn("super-secret", redacted)
        self.assertNotIn("api-secret", redacted)
        self.assertIn("hub.verify_token=[REDACTED]", redacted)
        self.assertIn("access_token=[REDACTED]", redacted)

    def test_manager_can_submit_template_and_refresh_meta_approval(self):
        template = self.env["odx.whatsapp.template"].create({
            "account_id": self.account.id,
            "name": "order_ready_notice",
            "language": "en_US",
            "category": "utility",
            "header_text": "Order {{1}}",
            "header_example": "ORDER-1001",
            "body_text": "Hello {{1}}, your order {{2}} is ready.",
            "body_examples": "Customer\nORDER-1001",
            "footer_text": "Thank you",
            "button_ids": [(0, 0, {"button_type": "quick_reply", "text": "Confirm"}),
                           (0, 0, {"button_type": "url", "text": "Track order",
                                   "url": "https://example.com/orders/{{1}}",
                                   "url_example": "https://example.com/orders/ORDER-1001"})],
        })
        with patch.object(type(self.account), "_api", return_value={
            "id": "meta-template-new", "status": "PENDING", "category": "UTILITY",
        }) as api_call:
            template.action_submit_to_meta()
        payload = api_call.call_args.kwargs["json"]
        self.assertEqual(payload["name"], "order_ready_notice")
        self.assertEqual(payload["components"][0]["example"]["header_text"], ["ORDER-1001"])
        self.assertEqual(payload["components"][1]["example"]["body_text"], [["Customer", "ORDER-1001"]])
        self.assertEqual(payload["components"][-1]["buttons"][1]["type"], "URL")
        self.assertEqual(template.meta_template_id, "meta-template-new")
        self.assertEqual(template.status, "pending")

        approved_components = payload["components"]
        with patch.object(type(self.account), "_api", return_value={
            "id": "meta-template-new", "name": "order_ready_notice", "language": "en_US",
            "status": "APPROVED", "category": "UTILITY", "components": approved_components,
            "quality_score": {"score": "GREEN"},
        }):
            template.action_refresh_status()
        self.assertEqual(template.status, "approved")
        self.assertEqual(template.quality_score, "GREEN")
        self.assertEqual(len(template.button_ids), 2)

    def test_template_sync_updates_archived_template_without_duplicate(self):
        template = self.env["odx.whatsapp.template"].create({
            "account_id": self.account.id,
            "meta_template_id": "meta-archived-template",
            "name": "archived_notice",
            "language": "en_US",
            "status": "pending",
            "category": "utility",
            "active": False,
        })
        with patch.object(type(self.account), "_api", return_value={"data": [{
            "id": "meta-archived-template",
            "name": "archived_notice",
            "language": "en_US",
            "status": "APPROVED",
            "category": "UTILITY",
            "components": [{"type": "BODY", "text": "Archived template"}],
        }]}):
            self.account.action_sync_templates()

        self.assertEqual(template.status, "approved")
        self.assertFalse(template.active)
        self.assertEqual(self.env["odx.whatsapp.template"].with_context(active_test=False).search_count([
            ("account_id", "=", self.account.id),
            ("meta_template_id", "=", "meta-archived-template"),
        ]), 1)

    def test_template_submission_validates_variable_examples(self):
        template = self.env["odx.whatsapp.template"].create({
            "account_id": self.account.id,
            "name": "invalid_examples",
            "language": "en_US",
            "category": "utility",
            "body_text": "Hello {{1}}, order {{2}} is ready.",
            "body_examples": "Customer",
        })
        with self.assertRaises(ValidationError):
            template.action_submit_to_meta()

    def test_duplicate_inbound_message_is_idempotent(self):
        payload = {
            "id": "wamid.duplicate", "from": "+916666666666", "timestamp": "1700000000",
            "type": "text", "text": {"body": "Only once"},
        }
        first = self.env["odx.whatsapp.message"]._ingest_message(self.account, payload, "Customer")
        second = self.env["odx.whatsapp.message"]._ingest_message(self.account, payload, "Customer")
        self.assertEqual(first, second)
        self.assertEqual(self.env["odx.whatsapp.message"].sudo().search_count([
            ("meta_message_id", "=", "wamid.duplicate")
        ]), 1)
        self.assertEqual(first.conversation_id.unread_count, 1)

    def test_inbound_message_sends_private_realtime_notification(self):
        bus_model = self.env["bus.bus"]
        with patch.object(type(bus_model), "_sendone", autospec=True) as sendone:
            message = self.env["odx.whatsapp.message"]._ingest_message(self.account, {
                "id": "wamid.popup", "from": "+916565656565", "timestamp": "1700000000",
                "type": "text", "text": {"body": "Please call me"},
            }, "Popup Customer")
        owner_partner = message.conversation_id.owner_id.partner_id
        owner_calls = [call for call in sendone.call_args_list if call.args[1] == owner_partner]
        self.assertEqual(len(owner_calls), 1)
        self.assertEqual(owner_calls[0].args[2], "odx_whatsapp/new_message")
        self.assertEqual(owner_calls[0].args[3]["conversation_id"], message.conversation_id.id)
        self.assertEqual(owner_calls[0].args[3]["preview"], "Please call me")

        message.conversation_id.state = "closed"
        self.env["odx.whatsapp.message"]._ingest_message(self.account, {
            "id": "wamid.popup-reopen", "from": "+916565656565", "timestamp": "1700000100",
            "type": "text", "text": {"body": "I am back"},
        }, "Popup Customer")
        self.assertEqual(message.conversation_id.state, "open")

    def test_delivery_status_is_monotonic(self):
        conversation = self.env["odx.whatsapp.conversation"]._find_or_create_inbound(
            self.account, "+915555555555", "Customer"
        )
        message = self.env["odx.whatsapp.message"].sudo().create({
            "conversation_id": conversation.id, "meta_message_id": "wamid.status",
            "direction": "outbound", "message_type": "text", "body": "Hello", "state": "sent",
        })
        self.env["odx.whatsapp.message"]._apply_status({
            "id": "wamid.status", "status": "read", "timestamp": "1700000010",
        })
        self.assertEqual(message.state, "read")
        self.assertTrue(message.read_at)
        self.env["odx.whatsapp.message"]._apply_status({
            "id": "wamid.status", "status": "delivered", "timestamp": "1700000005",
        })
        self.assertEqual(message.state, "read")
        self.assertTrue(message.delivered_at)

    def test_template_required_outside_window(self):
        conversation = self.env["odx.whatsapp.conversation"]._find_or_create_inbound(
            self.account, "+914444444444", "Customer"
        )
        conversation.last_inbound_at = fields.Datetime.now() - timedelta(hours=25)
        approved = self.env["odx.whatsapp.template"].create({
            "account_id": self.account.id, "meta_template_id": "template-1", "name": "follow_up",
            "language": "en_US", "status": "approved", "category": "utility",
        })
        rejected = self.env["odx.whatsapp.template"].create({
            "account_id": self.account.id, "meta_template_id": "template-2", "name": "rejected",
            "language": "en_US", "status": "rejected", "category": "utility",
        })
        with self.assertRaises(ValidationError):
            conversation.with_user(conversation.owner_id).send_template(rejected, [])
        with patch.object(type(self.account), "_api", return_value={"messages": [{"id": "wamid.template"}]}):
            message = conversation.with_user(conversation.owner_id).send_template(approved, ["Customer"])
        self.assertEqual(message.state, "sent")
        self.assertEqual(message.message_type, "template")

    def test_invalid_number_goes_to_failed_event(self):
        payload = {
            "entry": [{"changes": [{"value": {
                "metadata": {"phone_number_id": self.account.phone_number_id},
                "messages": [{"id": "wamid.bad-phone", "from": "123", "type": "text", "text": {"body": "Hi"}}],
            }}]}],
        }
        raw = json.dumps(payload)
        event = self.env["odx.whatsapp.event"].create({
            "account_id": self.account.id, "payload_hash": hashlib.sha256(raw.encode()).hexdigest(), "payload": raw,
        })
        event._process()
        self.assertEqual(event.state, "failed")
        self.assertIn("E.164", event.error_message)

    def test_direct_rpc_mutation_is_blocked(self):
        conversation = self.env["odx.whatsapp.conversation"]._find_or_create_inbound(
            self.account, "+913333333333", "Customer"
        )
        owner_conversation = conversation.with_user(conversation.owner_id)
        with self.assertRaises(AccessError):
            owner_conversation.write({"partner_phone": "919000000000"})
        with self.assertRaises(AccessError):
            self.env["odx.whatsapp.message"].with_user(conversation.owner_id).create({
                "conversation_id": conversation.id, "direction": "outbound",
                "message_type": "text", "body": "Bypass", "state": "sent",
            })

    def test_inbox_payload_respects_owner_and_supports_templates(self):
        conversation = self.env["odx.whatsapp.conversation"]._find_or_create_inbound(
            self.account, "+912222222222", "Inbox Customer"
        )
        self.env["odx.whatsapp.message"]._ingest_message(self.account, {
            "id": "wamid.inbox", "from": "+912222222222", "timestamp": "1700000000",
            "type": "text", "text": {"body": "Show in inbox"},
        })
        self.env["odx.whatsapp.template"].create({
            "account_id": self.account.id, "meta_template_id": "template-inbox", "name": "welcome",
            "language": "en_US", "status": "approved", "category": "utility",
            "components_json": json.dumps([{"type": "BODY", "text": "Hello {{1}}, order {{2}}"}]),
        })
        owner_model = self.env["odx.whatsapp.conversation"].with_user(conversation.owner_id)
        rows = owner_model.get_inbox_data("Inbox Customer", "open")
        self.assertEqual([row["id"] for row in rows], [conversation.id])
        chat = conversation.with_user(conversation.owner_id).get_chat_data()
        self.assertEqual(chat["messages"][0]["body"], "Show in inbox")
        self.assertEqual(chat["templates"][0]["parameter_count"], 2)
        other_model = self.env["odx.whatsapp.conversation"].with_user(self.sellers[1])
        self.assertFalse(other_model.get_inbox_data("Inbox Customer", "open"))
        with self.assertRaises(AccessError):
            conversation.with_user(self.sellers[1]).get_chat_data()

    def test_history_pagination_uses_stable_cursor(self):
        conversation = self.env["odx.whatsapp.conversation"]._find_or_create_inbound(
            self.account, "+911111111111", "Pagination Customer"
        )
        self.env["odx.whatsapp.message"].sudo().create([{
            "conversation_id": conversation.id, "meta_message_id": "wamid.page.%s" % index,
            "direction": "inbound", "message_type": "text", "body": "Message %s" % index,
            "state": "received", "message_at": fields.Datetime.now() - timedelta(minutes=120 - index),
        } for index in range(105)])
        owner_conversation = conversation.with_user(conversation.owner_id)
        newest = owner_conversation.get_chat_data(100)
        older = owner_conversation.get_chat_data(100, newest["oldest_message_id"])
        self.assertTrue(newest["has_older"])
        self.assertEqual(len(newest["messages"]), 100)
        self.assertEqual(len(older["messages"]), 5)
        self.assertFalse(older["has_older"])
        self.assertFalse({row["id"] for row in newest["messages"]} & {row["id"] for row in older["messages"]})

    def test_interactive_send_and_inbound_reply(self):
        conversation = self.env["odx.whatsapp.conversation"]._find_or_create_inbound(
            self.account, "+919191919191", "Interactive Customer"
        )
        conversation.last_inbound_at = fields.Datetime.now()
        with patch.object(type(self.account), "_api", return_value={"messages": [{"id": "wamid.interactive.out"}]}) as api_call:
            sent = conversation.with_user(conversation.owner_id).send_interactive("Choose one", ["Yes", "No"])
        self.assertEqual(sent.message_type, "interactive")
        payload = api_call.call_args.kwargs["json"]
        self.assertEqual(payload["type"], "interactive")
        self.assertEqual(len(payload["interactive"]["action"]["buttons"]), 2)
        received = self.env["odx.whatsapp.message"]._ingest_message(self.account, {
            "id": "wamid.interactive.in", "from": "+919191919191", "timestamp": "1700000000",
            "type": "interactive", "interactive": {"type": "button_reply", "button_reply": {
                "id": payload["interactive"]["action"]["buttons"][0]["reply"]["id"], "title": "Yes",
            }},
        })
        self.assertEqual(received.interactive_reply_title, "Yes")
        self.assertEqual(received.body, "Yes")

    def test_media_service_enforces_limits_and_reuses_upload(self):
        conversation = self.env["odx.whatsapp.conversation"]._find_or_create_inbound(
            self.account, "+919292929292", "Media Customer"
        )
        conversation.last_inbound_at = fields.Datetime.now()

        uploads = []

        def fake_api(account, method, path, **kwargs):
            if path.endswith("/media"):
                uploads.append(kwargs["files"]["file"])
            return {"id": "media-id"} if path.endswith("/media") else {"messages": [{"id": "wamid.media.out"}]}

        with patch.object(type(self.account), "_api", side_effect=fake_api):
            message = conversation.with_user(conversation.owner_id).send_media(
                "image", "aGVsbG8=", "hello.png", False, "Caption"
            )
        self.assertEqual(message.message_type, "image")
        self.assertEqual(message.mimetype, "image/png")
        self.assertEqual(uploads[0][2], "image/png")
        self.assertEqual(message.state, "sent")
        with self.assertRaises(ValidationError):
            conversation.with_user(conversation.owner_id).send_media(
                "image", "aGVsbG8=", "photo.heic", "application/octet-stream"
            )
        with self.assertRaises(ValidationError):
            conversation.with_user(conversation.owner_id).send_media(
                "image", "A" * (23 * 1024 * 1024), "large.png", "image/png"
            )

        # Chromium emits fragmented AAC/MP4 that Meta identifies as octet-stream.
        # Build a two-frame sample and verify it is losslessly demuxed to ADTS AAC.
        mp4a = bytearray(36)
        mp4a[:4] = (36).to_bytes(4, "big")
        mp4a[4:8] = b"mp4a"
        mp4a[14:16] = (1).to_bytes(2, "big")
        mp4a[24:26] = (1).to_bytes(2, "big")
        mp4a[26:28] = (16).to_bytes(2, "big")
        mp4a[32:36] = (48000 << 16).to_bytes(4, "big")
        audio_frames = [b"first", b"second"]
        trun_payload = (
            b"\x00\x00\x03\x01" + (2).to_bytes(4, "big") + (0).to_bytes(4, "big")
            + (1024).to_bytes(4, "big") + len(audio_frames[0]).to_bytes(4, "big")
            + (1024).to_bytes(4, "big") + len(audio_frames[1]).to_bytes(4, "big")
        )
        trun = (8 + len(trun_payload)).to_bytes(4, "big") + b"trun" + trun_payload
        traf = (8 + len(trun)).to_bytes(4, "big") + b"traf" + trun
        moof = bytearray((8 + len(traf)).to_bytes(4, "big") + b"moof" + traf)
        data_offset = len(moof) + 8
        moof[32:36] = data_offset.to_bytes(4, "big", signed=True)
        mdat_payload = b"".join(audio_frames)
        mdat = (8 + len(mdat_payload)).to_bytes(4, "big") + b"mdat" + mdat_payload
        browser_mp4 = bytes(mp4a) + b"\x00\x00\x00\x0cfreesoun" + bytes(moof) + mdat
        uploads.clear()
        with patch.object(type(self.account), "_api", side_effect=fake_api):
            voice = conversation.with_user(conversation.owner_id).send_media(
                "audio", base64.b64encode(browser_mp4), "voice.m4a", "audio/mp4"
            )
        uploaded_voice = uploads[0][1]
        self.assertTrue(uploaded_voice.startswith(b"\xff\xf1"))
        self.assertEqual(uploads[0][0], "voice.aac")
        self.assertEqual(uploads[0][2], "audio/aac")
        self.assertIn(audio_frames[0], uploaded_voice)
        self.assertIn(audio_frames[1], uploaded_voice)
        self.assertEqual(voice.attachment[:12], base64.b64encode(uploaded_voice)[:12])

    def test_lead_panel_privacy_and_template_only_initiation(self):
        lead = self.env["crm.lead"].create({
            "name": "Panel Lead", "contact_name": "Panel Customer", "phone": "+919393939393",
            "company_id": self.env.company.id, "team_id": self.team.id, "user_id": self.sellers[0].id,
        })
        template = self.env["odx.whatsapp.template"].create({
            "account_id": self.account.id, "meta_template_id": "template-panel", "name": "panel_start",
            "language": "en_US", "status": "approved", "category": "utility",
            "components_json": json.dumps([{"type": "BODY", "text": "Hello {{1}}"}]),
        })
        panel = lead.with_user(self.sellers[0]).get_whatsapp_panel_data(False, self.account.id)
        self.assertFalse(panel["selected_conversation_id"])
        self.assertFalse(panel["chat"]["window_open"])
        self.assertIn(template.id, [item["id"] for item in panel["chat"]["templates"]])
        with patch.object(type(self.account), "_api", return_value={"messages": [{"id": "wamid.panel.template"}]}):
            sent_panel = lead.with_user(self.sellers[0]).whatsapp_panel_send_template(
                self.account.id, False, template.id, ["Customer"]
            )
        self.assertTrue(sent_panel["selected_conversation_id"])
        self.assertEqual(sent_panel["chat"]["messages"][-1]["type"], "template")
        self.assertEqual(sent_panel["chat"]["messages"][-1]["body"], "Hello Customer")
        sent_message = self.env["odx.whatsapp.message"].browse(sent_panel["chat"]["messages"][-1]["id"])
        self.assertEqual(sent_message.body, "Hello Customer")
        self.assertEqual(json.loads(sent_message.template_parameters_json), ["Customer"])
        with self.assertRaises(AccessError):
            lead.with_user(self.sellers[1]).get_whatsapp_panel_data()
        lead.user_id = self.sellers[1]
        with self.assertRaises(AccessError):
            lead.with_user(self.sellers[0]).get_whatsapp_panel_data()
        self.assertTrue(lead.with_user(self.sellers[1]).get_whatsapp_panel_data()["selected_conversation_id"])

    def test_legacy_template_history_renders_complete_message(self):
        conversation = self.env["odx.whatsapp.conversation"]._find_or_create_inbound(
            self.account, "+919399887766", "Legacy Template Customer"
        )
        template = self.env["odx.whatsapp.template"].create({
            "account_id": self.account.id,
            "meta_template_id": "legacy-template",
            "name": "legacy_greeting",
            "language": "en_US",
            "status": "approved",
            "category": "utility",
            "components_json": json.dumps([{
                "type": "BODY", "text": "Hello {{1}}, thank you for your enquiry.",
            }]),
        })
        legacy = self.env["odx.whatsapp.message"].sudo().create({
            "conversation_id": conversation.id,
            "direction": "outbound",
            "message_type": "template",
            "template_id": template.id,
            "body": "Legacy Customer",
            "state": "sent",
        })

        chat = conversation.with_user(conversation.owner_id).get_chat_data()

        rendered = next(item for item in chat["messages"] if item["id"] == legacy.id)
        self.assertEqual(rendered["body"], "Hello Legacy Customer, thank you for your enquiry.")

    def test_failed_message_retry_is_authorized_and_audited(self):
        conversation = self.env["odx.whatsapp.conversation"]._find_or_create_inbound(
            self.account, "+919494949494", "Retry Customer"
        )
        conversation.last_inbound_at = fields.Datetime.now()
        failed = self.env["odx.whatsapp.message"].sudo().create({
            "conversation_id": conversation.id, "direction": "outbound", "message_type": "text",
            "body": "Try again", "state": "failed", "error_message": "Temporary API failure",
        })
        with patch.object(type(self.account), "_api", return_value={"messages": [{"id": "wamid.retry"}]}):
            panel = conversation.lead_id.with_user(conversation.owner_id).whatsapp_panel_retry_message(
                conversation.id, failed.id
            )
        self.assertEqual(failed.error_message, "Temporary API failure")
        self.assertEqual(panel["chat"]["messages"][-1]["state"], "sent")
        with self.assertRaises(AccessError):
            conversation.lead_id.with_user(self.sellers[1]).whatsapp_panel_retry_message(conversation.id, failed.id)
