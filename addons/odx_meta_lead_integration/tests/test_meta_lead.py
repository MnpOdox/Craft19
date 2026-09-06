import json
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestMetaLead(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = cls.env.ref("sales_team.group_sale_salesman")
        cls.users = cls.env["res.users"].create([
            {"name": "Meta Seller A", "login": "meta_a", "group_ids": [(6, 0, group.ids)]},
            {"name": "Meta Seller B", "login": "meta_b", "group_ids": [(6, 0, group.ids)]},
        ])
        cls.team = cls.env["crm.team"].create({"name": "Meta Team", "company_id": cls.env.company.id})
        cls.env["crm.team.member"].create([
            {"crm_team_id": cls.team.id, "user_id": user.id} for user in cls.users
        ])
        cls.account = cls.env["odx.meta.account"].create({
            "name": "Test Meta", "app_id": "app", "app_secret": "secret", "access_token": "token",
        })
        cls.page = cls.env["odx.meta.page"].create({"name": "Page", "account_id": cls.account.id, "meta_page_ref": "page1"})
        cls.form = cls.env["odx.meta.form"].create({
            "name": "Lead form", "page_id": cls.page.id, "meta_form_ref": "form1", "team_id": cls.team.id,
        })
        phone_field = cls.env["ir.model.fields"].search([("model", "=", "crm.lead"), ("name", "=", "phone")], limit=1)
        cls.env["odx.meta.field.mapping"].create({"form_id": cls.form.id, "meta_field": "phone_number", "odoo_field_id": phone_field.id})

    def test_round_robin_and_deduplication(self):
        first = self.form._import_payload({
            "id": "lead-1",
            "created_time": "2026-08-28T03:43:50+0000",
            "campaign_name": "Ribbon Campaign",
            "adset_name": "Ribbon Ad Set",
            "ad_name": "Ribbon Ad",
            "field_data": [
                {"name": "full_name", "values": ["Test Customer"]},
                {"name": "phone", "values": ["+911111111111"]},
                {"name": "preferred_colour", "values": ["Blue"]},
            ],
        })
        second = self.form._import_payload({"id": "lead-2", "field_data": []})
        duplicate = self.form._import_payload({"id": "lead-1", "field_data": []})
        self.assertEqual([first.user_id.id, second.user_id.id], self.users.ids)
        self.assertEqual(duplicate, first)
        self.assertEqual(first.name, "Lead form - Test Customer")
        self.assertEqual(first.contact_name, "Test Customer")
        self.assertEqual(first.phone, "+911111111111")
        self.assertEqual(first.meta_created_time, fields.Datetime.to_datetime("2026-08-28 03:43:50"))
        self.assertIn("2026-08-28 03:43:50", first.description)
        self.assertIn("Ribbon Campaign", first.description)
        self.assertIn("Preferred Colour", first.description)
        self.assertIn("Blue", first.description)

    def _conversion_lead(self, reference):
        return self.form._import_payload({
            "id": reference,
            "created_time": "2026-08-28T03:43:50+0000",
            "field_data": [{"name": "full_name", "values": ["Status Customer"]}],
        })

    def test_lost_lead_sends_conversion_status_to_meta(self):
        self.account.write({
            "conversion_dataset_id": "dataset-123",
            "conversion_access_token": "conversion-token",
            "conversion_sync_enabled": True,
        })
        lead = self._conversion_lead("lead-status-lost")
        with patch.object(type(self.account), "_graph_request", return_value={"events_received": 1}) as request:
            lead.action_set_lost()

        event = self.env["odx.meta.status.event"].search([("lead_id", "=", lead.id)])
        self.assertFalse(lead.active)
        self.assertEqual(event.state, "done")
        self.assertEqual(event.event_name, "Lost")
        self.assertEqual(lead.meta_status_sync_state, "done")
        args, kwargs = request.call_args
        self.assertEqual(args[1:3], ("POST", "dataset-123/events"))
        self.assertEqual(kwargs["access_token"], "conversion-token")
        payload = kwargs["json"]
        self.assertEqual(payload["data"][0]["user_data"]["lead_id"], "lead-status-lost")
        self.assertEqual(payload["data"][0]["action_source"], "system_generated")
        self.assertEqual(payload["data"][0]["custom_data"]["event_source"], "crm")

    def test_meta_failure_does_not_block_closing_and_is_retryable(self):
        self.account.write({
            "conversion_dataset_id": "dataset-123",
            "conversion_access_token": "conversion-token",
            "conversion_sync_enabled": True,
        })
        lead = self._conversion_lead("lead-status-retry")
        with patch.object(type(self.account), "_graph_request", side_effect=UserError("temporary outage")):
            lead.action_set_lost()

        event = self.env["odx.meta.status.event"].search([("lead_id", "=", lead.id)])
        self.assertFalse(lead.active)
        self.assertEqual(event.state, "failed")
        self.assertEqual(event.retry_count, 1)
        self.assertTrue(event.next_retry_at)
        self.assertEqual(lead.meta_status_sync_state, "failed")
        self.assertIn("temporary outage", lead.meta_status_sync_error)

        event.next_retry_at = fields.Datetime.now()
        with patch.object(type(self.account), "_graph_request", return_value={"events_received": 1}):
            self.env["odx.meta.status.event"]._cron_retry()
        self.assertEqual(event.state, "done")
        self.assertEqual(event.retry_count, 1)

    def test_won_lead_sends_won_status_once(self):
        self.account.write({
            "conversion_dataset_id": "dataset-123",
            "conversion_access_token": "conversion-token",
            "conversion_sync_enabled": True,
        })
        lead = self._conversion_lead("lead-status-won")
        won_stage = self.env["crm.stage"].create({"name": "Won Feedback", "is_won": True})
        with patch.object(type(self.account), "_graph_request", return_value={"events_received": 1}) as request:
            lead.stage_id = won_stage
            lead.write({"stage_id": won_stage.id})

        events = self.env["odx.meta.status.event"].search([("lead_id", "=", lead.id)])
        self.assertEqual(len(events), 1)
        self.assertEqual(events.event_name, "Won")
        self.assertEqual(events.state, "done")
        self.assertEqual(request.call_count, 1)

    def test_conversion_configuration_requires_dataset_and_token(self):
        with self.assertRaises(ValidationError):
            self.account.write({"conversion_sync_enabled": True})

    def test_unknown_form_is_discovered_and_waiting_lead_imports_after_confirmation(self):
        webhook_value = {
            "leadgen_id": "waiting-lead-1",
            "page_id": self.page.meta_page_ref,
            "form_id": "new-form-1",
        }
        event = self.env["odx.meta.import.event"].create({
            "account_id": self.account.id,
            "meta_lead_ref": webhook_value["leadgen_id"],
            "event_type": "webhook",
            "state": "failed",
            "payload": json.dumps(webhook_value),
            "error_message": "No active form mapping",
        })
        with patch.object(type(self.account), "_graph_request", return_value={
            "id": "new-form-1", "name": "New Product Form", "status": "ACTIVE",
        }):
            self.env["odx.meta.import.event"]._discover_unmapped_forms()

        form = self.env["odx.meta.form"].search([("meta_form_ref", "=", "new-form-1")])
        self.assertEqual(form.configuration_state, "needs_configuration")
        self.assertFalse(form.team_id)
        self.assertEqual(set(form.mapping_ids.mapped("meta_field")), {
            "full_name", "email", "phone_number", "state",
        })
        self.assertEqual(event.form_id, form)
        self.assertEqual(event.state, "pending")

        form.team_id = self.team
        lead_payload = {
            "id": "waiting-lead-1",
            "created_time": "2026-09-06T03:00:00+0000",
            "field_data": [
                {"name": "full_name", "values": ["Waiting Customer"]},
                {"name": "phone_number", "values": ["+919999999999"]},
                {"name": "state", "values": ["Uttar Pradesh"]},
            ],
        }
        with patch.object(type(form), "_graph_request", return_value=lead_payload):
            form.action_mark_configured()

        lead = self.env["crm.lead"].search([("meta_lead_id", "=", "waiting-lead-1")])
        self.assertEqual(form.configuration_state, "configured")
        self.assertEqual(event.state, "done")
        self.assertEqual(lead.contact_name, "Waiting Customer")
        self.assertEqual(lead.phone, "+919999999999")
        self.assertEqual(lead.meta_location, "Uttar Pradesh")
