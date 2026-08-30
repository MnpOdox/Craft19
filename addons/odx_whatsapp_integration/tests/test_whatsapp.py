import base64
import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch

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
        with self.assertRaises(AccessError):
            lead.with_user(self.sellers[1]).get_whatsapp_panel_data()
        lead.user_id = self.sellers[1]
        with self.assertRaises(AccessError):
            lead.with_user(self.sellers[0]).get_whatsapp_panel_data()
        self.assertTrue(lead.with_user(self.sellers[1]).get_whatsapp_panel_data()["selected_conversation_id"])

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
