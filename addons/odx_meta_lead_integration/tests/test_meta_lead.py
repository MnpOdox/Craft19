from odoo import fields
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
