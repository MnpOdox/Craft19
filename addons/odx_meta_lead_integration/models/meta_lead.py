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
    ad_account_ref = fields.Char(
        string="Meta Ad Account ID",
        help="Meta ad account used to synchronize campaigns and lead ads, for example act_123456789.",
    )
    ad_route_ids = fields.One2many("odx.meta.ad.route", "account_id")
    last_ad_sync_at = fields.Datetime(readonly=True, copy=False)
    last_reconcile_at = fields.Datetime(readonly=True, copy=False)
    conversion_sync_enabled = fields.Boolean(
        string="Send CRM Statuses to Meta",
        tracking=True,
        help="Send Meta Lead Ads lifecycle outcomes to the configured Dataset through the Conversions API.",
    )
    conversion_dataset_id = fields.Char(
        string="Dataset / Pixel ID",
        groups="base.group_system",
        copy=False,
    )
    conversion_access_token = fields.Char(
        string="Conversions API Token",
        groups="base.group_system",
        copy=False,
    )
    conversion_test_event_code = fields.Char(
        string="Test Event Code",
        groups="base.group_system",
        copy=False,
        help="Optional Events Manager test code. Remove it before using the integration in production.",
    )

    @api.constrains("conversion_sync_enabled", "conversion_dataset_id", "conversion_access_token")
    def _check_conversion_configuration(self):
        for account in self:
            if account.conversion_sync_enabled and (
                not account.conversion_dataset_id or not account.conversion_access_token
            ):
                raise ValidationError(_(
                    "A Dataset / Pixel ID and Conversions API Token are required to send CRM statuses to Meta."
                ))

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

    def _resolved_ad_account_ref(self):
        self.ensure_one()
        if self.ad_account_ref:
            return self.ad_account_ref if self.ad_account_ref.startswith("act_") else "act_%s" % self.ad_account_ref
        payload = self._graph_request(
            self, "GET", "me/adaccounts",
            params={"fields": "id,name,account_status", "limit": 50},
        )
        accounts = [item for item in payload.get("data", []) if item.get("account_status") in (1, "1")]
        if len(accounts) != 1:
            raise ValidationError(_(
                "Set the Meta Ad Account ID because the token exposes %(count)s active ad accounts.",
                count=len(accounts),
            ))
        self.ad_account_ref = accounts[0]["id"]
        return accounts[0]["id"]

    @staticmethod
    def _lead_form_ref_from_ad(ad):
        def _walk(value):
            if isinstance(value, dict):
                if value.get("lead_gen_form_id"):
                    return str(value["lead_gen_form_id"])
                for nested in value.values():
                    found = _walk(nested)
                    if found:
                        return found
            elif isinstance(value, list):
                for nested in value:
                    found = _walk(nested)
                    if found:
                        return found
            return False

        return _walk((ad.get("creative") or {}).get("object_story_spec") or {})

    def _sync_ad_routes(self):
        self.ensure_one()
        ad_account_ref = self._resolved_ad_account_ref()
        params = {
            "fields": (
                "id,name,status,effective_status,"
                "adset{id,name,campaign{id,name}},creative{id,object_story_spec}"
            ),
            "limit": 100,
        }
        synced = self.env["odx.meta.ad.route"]
        while True:
            payload = self._graph_request(self, "GET", "%s/ads" % ad_account_ref, params=params)
            for ad in payload.get("data", []):
                form_ref = self._lead_form_ref_from_ad(ad)
                if not form_ref:
                    continue
                story = (ad.get("creative") or {}).get("object_story_spec") or {}
                page_ref = story.get("page_id")
                form = self.env["odx.meta.form"].sudo().search([
                    ("account_id", "=", self.id), ("meta_form_ref", "=", form_ref),
                ], limit=1)
                if not form and page_ref:
                    form = self._find_or_create_discovered_form(page_ref, form_ref)
                if form:
                    synced |= form._upsert_ad_route(ad)
            paging = payload.get("paging") or {}
            after = (paging.get("cursors") or {}).get("after") if paging.get("next") else False
            if not after:
                break
            params["after"] = after
        self.last_ad_sync_at = fields.Datetime.now()
        return synced

    def action_sync_ads(self):
        total = self.env["odx.meta.ad.route"]
        for account in self:
            total |= account._sync_ad_routes()
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": _("Meta ads synchronized"),
            "message": _("%(count)s lead-ad routing record(s) were synchronized.", count=len(total)),
            "type": "success",
        }}

    @api.model
    def _cron_sync_ads(self):
        for account in self.search([("active", "=", True)]):
            try:
                account._sync_ad_routes()
            except Exception:
                _logger.exception("Meta ad synchronization failed for account %s", account.id)
        return True

    def verify_signature(self, raw_body, signature):
        self.ensure_one()
        if not signature or not signature.startswith("sha256="):
            return False
        digest = hmac.new(self.app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature[7:], digest)

    @api.model
    def _cron_reconcile(self):
        for account in self.search([("active", "=", True)]):
            for form in account.page_ids.form_ids.filtered(
                lambda item: item.active and item.configuration_state == "configured"
            ):
                try:
                    form._reconcile()
                except Exception as exc:  # cron must continue with other forms
                    _logger.exception("Meta reconciliation failed for form %s", form.id)
                    self.env["odx.meta.import.event"].sudo().create({
                        "account_id": account.id, "form_id": form.id, "state": "failed",
                        "event_type": "reconcile", "error_message": str(exc)[:2000],
                    })
            account.last_reconcile_at = fields.Datetime.now()

    def _find_or_create_discovered_form(self, page_ref, form_ref):
        """Create a visible configuration placeholder for a new Meta Instant Form."""
        self.ensure_one()
        page_ref, form_ref = str(page_ref or ""), str(form_ref or "")
        if not page_ref or not form_ref:
            return self.env["odx.meta.form"]
        lock_key = "odx.meta.form:%s:%s" % (self.id, form_ref)
        self.env.cr.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [lock_key])
        form = self.env["odx.meta.form"].sudo().with_context(active_test=False).search([
            ("account_id", "=", self.id), ("meta_form_ref", "=", form_ref),
        ], limit=1)
        if form:
            return form
        page = self.env["odx.meta.page"].sudo().with_context(active_test=False).search([
            ("account_id", "=", self.id), ("meta_page_ref", "=", page_ref),
        ], limit=1)
        if not page:
            return self.env["odx.meta.form"]
        name = _("Discovered Meta Form %s", form_ref)
        try:
            metadata = self._graph_request(
                self,
                "GET",
                form_ref,
                access_token=page.sudo().page_access_token,
                params={"fields": "id,name,status"},
            )
            name = metadata.get("name") or name
        except Exception as exc:
            _logger.warning("Could not retrieve metadata for new Meta form %s: %s", form_ref, exc)
        form = self.env["odx.meta.form"].sudo().create({
            "name": name,
            "page_id": page.id,
            "meta_form_ref": form_ref,
            "configuration_state": "needs_configuration",
            "route_by_ad": True,
            "active": True,
        })
        form._create_default_mappings()
        return form


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
    configuration_state = fields.Selection([
        ("needs_configuration", "Needs Configuration"),
        ("configured", "Configured"),
    ], required=True, default="configured", readonly=True, copy=False, index=True)
    pending_event_count = fields.Integer(compute="_compute_pending_event_count")
    team_id = fields.Many2one("crm.team", domain="[('company_id', 'in', [False, company_id])]" )
    fallback_user_id = fields.Many2one("res.users", domain="[('share', '=', False)]")
    assignment_cursor = fields.Integer(default=-1, groups="base.group_system", copy=False)
    mapping_ids = fields.One2many("odx.meta.field.mapping", "form_id")
    source_id = fields.Many2one("utm.source")
    medium_id = fields.Many2one("utm.medium")
    campaign_id = fields.Many2one("utm.campaign")
    route_by_ad = fields.Boolean(
        string="Route Leads by Meta Ad",
        help="Require a configured ad-routing record before importing each submission.",
    )
    ad_route_ids = fields.One2many("odx.meta.ad.route", "form_id", string="Ad Routing")
    last_lead_created_time = fields.Datetime(copy=False)

    _form_unique = models.Constraint(
        "UNIQUE(account_id, meta_form_ref)", "This lead form is already configured."
    )

    @api.depends("configuration_state")
    def _compute_pending_event_count(self):
        grouped = self.env["odx.meta.import.event"].sudo()._read_group(
            [("form_id", "in", self.ids), ("state", "in", ["pending", "failed"])],
            ["form_id"], ["__count"],
        ) if self.ids else []
        counts = {form.id: count for form, count in grouped}
        for form in self:
            form.pending_event_count = counts.get(form.id, 0)

    @api.constrains("configuration_state", "team_id")
    def _check_ready_configuration(self):
        for form in self.filtered(lambda item: item.configuration_state == "configured"):
            if not form.route_by_ad and not form.team_id:
                raise ValidationError(_("Select a Sales Team before marking the Meta form as configured."))

    def _create_default_mappings(self):
        targets = {
            "full_name": "contact_name",
            "email": "email_from",
            "phone_number": "phone",
            "state": "meta_location",
        }
        field_records = self.env["ir.model.fields"].sudo().search([
            ("model", "=", "crm.lead"), ("name", "in", list(targets.values())),
        ])
        fields_by_name = {field.name: field for field in field_records}
        for form in self:
            existing = set(form.mapping_ids.mapped("meta_field"))
            values = [{
                "form_id": form.id,
                "meta_field": meta_field,
                "odoo_field_id": fields_by_name[odoo_field].id,
            } for meta_field, odoo_field in targets.items()
              if meta_field not in existing and odoo_field in fields_by_name]
            if values:
                self.env["odx.meta.field.mapping"].sudo().create(values)

    def action_mark_configured(self):
        for form in self:
            if not form.route_by_ad and not form.team_id:
                raise ValidationError(_("Select a Sales Team before activating this Meta form."))
            if not form.mapping_ids:
                raise ValidationError(_("Add at least one CRM field mapping before activating this Meta form."))
        self.write({"configuration_state": "configured"})
        self._process_waiting_events()
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": _("Meta form configured"),
            "message": _("Waiting submissions were processed. Reconciliation will recover any others."),
            "type": "success",
        }}

    def _process_waiting_events(self):
        events = self.env["odx.meta.import.event"].sudo().search([
            ("form_id", "in", self.ids),
            ("state", "in", ["pending", "failed"]),
            ("meta_lead_ref", "!=", False),
        ], order="create_date, id")
        for event in events:
            with self.env.cr.savepoint():
                try:
                    event.state = "processing"
                    event.form_id._fetch_and_import(event.meta_lead_ref, event=event)
                except Exception as exc:
                    count = event.retry_count + 1
                    event.write({
                        "state": "failed", "retry_count": count,
                        "error_message": str(exc)[:2000],
                        "next_retry_at": fields.Datetime.now() + timedelta(minutes=min(2 ** count, 60)),
                    })

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

    def _upsert_ad_route(self, payload):
        self.ensure_one()
        ad_ref = str(payload.get("id") or payload.get("ad_id") or "")
        if not ad_ref:
            return self.env["odx.meta.ad.route"]
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            ["odx.meta.ad.route:%s:%s" % (self.account_id.id, ad_ref)],
        )
        route = self.env["odx.meta.ad.route"].sudo().with_context(active_test=False).search([
            ("account_id", "=", self.account_id.id), ("meta_ad_ref", "=", ad_ref),
        ], limit=1)
        adset = payload.get("adset") or {}
        campaign = adset.get("campaign") or {}
        values = {
            "form_id": self.id,
            "name": payload.get("name") or payload.get("ad_name") or _("Discovered Meta Ad %s", ad_ref),
            "meta_ad_name": payload.get("name") or payload.get("ad_name") or False,
            "meta_adset_ref": str(adset.get("id") or payload.get("adset_id") or "") or False,
            "meta_adset_name": adset.get("name") or payload.get("adset_name") or False,
            "meta_campaign_ref": str(campaign.get("id") or payload.get("campaign_id") or "") or False,
            "meta_campaign_name": campaign.get("name") or payload.get("campaign_name") or False,
            "meta_status": payload.get("status") or False,
            "meta_effective_status": payload.get("effective_status") or False,
        }
        if route:
            route.write({key: value for key, value in values.items() if value is not False})
        else:
            values.update({
                "meta_ad_ref": ad_ref,
                "configuration_state": "needs_configuration",
                "active": True,
                "source_id": self.source_id.id,
                "medium_id": self.medium_id.id,
                "utm_campaign_id": self.campaign_id.id,
            })
            route = self.env["odx.meta.ad.route"].sudo().create(values)
        return route

    def _find_or_create_ad_route(self, payload):
        self.ensure_one()
        if not self.route_by_ad:
            return self.env["odx.meta.ad.route"]
        ad_ref = str(payload.get("ad_id") or "")
        if not ad_ref:
            return self.env["odx.meta.ad.route"]
        route = self.env["odx.meta.ad.route"].sudo().with_context(active_test=False).search([
            ("account_id", "=", self.account_id.id), ("meta_ad_ref", "=", ad_ref),
        ], limit=1)
        if route:
            return route
        metadata = {"id": ad_ref}
        try:
            metadata = self.account_id._graph_request(
                self.account_id, "GET", ad_ref,
                params={"fields": "id,name,status,effective_status,adset{id,name,campaign{id,name}}"},
            )
        except Exception as exc:
            _logger.warning("Could not retrieve metadata for new Meta ad %s: %s", ad_ref, exc)
        return self._upsert_ad_route(metadata)

    def _routing_for_payload(self, payload, event=None):
        self.ensure_one()
        route = self._find_or_create_ad_route(payload)
        if event and route:
            event.route_id = route.id
        if not self.route_by_ad:
            return route
        if not route:
            if event:
                event.write({"state": "pending", "error_message": _("Waiting for Meta ad identification")})
            return False
        if not route.active or route.configuration_state != "configured":
            if event:
                event.write({"state": "pending", "error_message": _("Waiting for ad routing configuration")})
            return False
        return route

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
            (_("Campaign ID"), payload.get("campaign_id")),
            (_("Ad Set"), payload.get("adset_name")),
            (_("Ad Set ID"), payload.get("adset_id")),
            (_("Ad"), payload.get("ad_name")),
            (_("Ad ID"), payload.get("ad_id")),
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
        route = self._routing_for_payload(payload, event=event)
        if self.route_by_ad and not route:
            return self.env["crm.lead"]
        values = self._mapped_values(payload)
        routing = route or self
        user = routing._next_salesperson()
        created = _parse_meta_datetime(payload.get("created_time"))
        full_name = self._full_name_from_payload(payload) or values.get("contact_name")
        title = route.name if route else self.name
        if full_name:
            title = "%s - %s" % (title, full_name)
        values.update({
            "name": title,
            "contact_name": full_name or False,
            "description": self._lead_description(payload, created),
            "type": "lead", "team_id": routing.team_id.id, "user_id": user.id if user else False,
            "company_id": self.company_id.id, "meta_lead_id": lead_ref,
            "meta_page_id": self.page_id.id, "meta_form_id": self.id,
            "meta_ad_route_id": route.id if route else False, "meta_created_time": created,
            "meta_campaign_ref": payload.get("campaign_id"), "meta_adset_ref": payload.get("adset_id"),
            "meta_ad_ref": payload.get("ad_id"),
            "meta_campaign_name": payload.get("campaign_name"), "meta_adset_name": payload.get("adset_name"),
            "meta_ad_name": payload.get("ad_name"), "source_id": routing.source_id.id,
            "medium_id": routing.medium_id.id,
            "campaign_id": (route.utm_campaign_id if route else self.campaign_id).id,
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
        lead.message_post(body=_(
            "Imported from Meta Lead Ads form %(form)s using routing %(route)s and assigned automatically.",
            form=self.name, route=route.name if route else _("Form default"),
        ))
        return lead

    def _fetch_and_import(self, lead_ref, event=None):
        self.ensure_one()
        fields_list = (
            "id,created_time,field_data,campaign_id,campaign_name,adset_id,adset_name,"
            "ad_id,ad_name,form_id"
        )
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
        params = {"fields": (
            "id,created_time,field_data,campaign_id,campaign_name,adset_id,adset_name,"
            "ad_id,ad_name,form_id"
        ), "limit": 100}
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
                "route_id": self._find_or_create_ad_route(item).id,
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


class MetaAdRoute(models.Model):
    _name = "odx.meta.ad.route"
    _description = "Meta Lead Ad Routing"
    _order = "meta_effective_status, name"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    form_id = fields.Many2one("odx.meta.form", required=True, ondelete="cascade", index=True)
    page_id = fields.Many2one(related="form_id.page_id", store=True)
    account_id = fields.Many2one(related="form_id.account_id", store=True, index=True)
    company_id = fields.Many2one(related="form_id.company_id", store=True, index=True)
    configuration_state = fields.Selection([
        ("needs_configuration", "Needs Configuration"),
        ("configured", "Configured"),
    ], required=True, default="needs_configuration", readonly=True, copy=False, index=True)
    pending_event_count = fields.Integer(compute="_compute_pending_event_count")
    meta_campaign_ref = fields.Char(string="Meta Campaign ID", readonly=True)
    meta_campaign_name = fields.Char(string="Meta Campaign", readonly=True)
    meta_adset_ref = fields.Char(string="Meta Ad Set ID", readonly=True)
    meta_adset_name = fields.Char(string="Meta Ad Set", readonly=True)
    meta_ad_ref = fields.Char(string="Meta Ad ID", required=True, readonly=True, index=True)
    meta_ad_name = fields.Char(string="Meta Ad", readonly=True)
    meta_status = fields.Char(string="Configured Status", readonly=True)
    meta_effective_status = fields.Char(string="Effective Status", readonly=True)
    team_id = fields.Many2one("crm.team", domain="[('company_id', 'in', [False, company_id])]")
    fallback_user_id = fields.Many2one("res.users", domain="[('share', '=', False)]")
    assignment_cursor = fields.Integer(default=-1, groups="base.group_system", copy=False)
    source_id = fields.Many2one("utm.source")
    medium_id = fields.Many2one("utm.medium")
    utm_campaign_id = fields.Many2one("utm.campaign", string="Odoo Campaign")

    _ad_unique = models.Constraint(
        "UNIQUE(account_id, meta_ad_ref)", "This Meta ad already has a routing record."
    )

    @api.depends("configuration_state")
    def _compute_pending_event_count(self):
        grouped = self.env["odx.meta.import.event"].sudo()._read_group(
            [("route_id", "in", self.ids), ("state", "in", ["pending", "failed"])],
            ["route_id"], ["__count"],
        ) if self.ids else []
        counts = {route.id: count for route, count in grouped}
        for route in self:
            route.pending_event_count = counts.get(route.id, 0)

    @api.constrains("configuration_state", "team_id")
    def _check_configuration(self):
        for route in self.filtered(lambda item: item.configuration_state == "configured"):
            if not route.team_id:
                raise ValidationError(_("Select a Sales Team before activating this ad routing record."))

    def _next_salesperson(self):
        self.ensure_one()
        self.env.cr.execute("SELECT id FROM odx_meta_ad_route WHERE id = %s FOR UPDATE", [self.id])
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

    def action_mark_configured(self):
        for route in self:
            if route.form_id.configuration_state != "configured":
                raise ValidationError(_("Configure the Meta form field mappings before activating its ad routing."))
            if not route.team_id:
                raise ValidationError(_("Select a Sales Team before activating this ad routing record."))
        self.write({"configuration_state": "configured"})
        self._process_waiting_events()
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": _("Ad routing configured"),
            "message": _("Waiting submissions for this ad were processed."),
            "type": "success",
        }}

    def _process_waiting_events(self):
        for route in self:
            events = self.env["odx.meta.import.event"].sudo().search([
                ("route_id", "=", route.id), ("state", "in", ["pending", "failed"]),
                ("meta_lead_ref", "!=", False),
            ], order="create_date, id")
            for event in events:
                with self.env.cr.savepoint():
                    try:
                        event.state = "processing"
                        event.form_id._fetch_and_import(event.meta_lead_ref, event=event)
                    except Exception as exc:
                        event.write({"state": "failed", "error_message": str(exc)[:2000]})
        return True


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
    route_id = fields.Many2one("odx.meta.ad.route", string="Ad Routing", ondelete="set null", index=True)
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
    def _discover_unmapped_forms(self):
        events = self.sudo().search([
            ("form_id", "=", False),
            ("state", "=", "failed"),
            ("error_message", "=", "No active form mapping"),
        ], order="create_date, id", limit=100)
        for event in events:
            try:
                payload = json.loads(event.payload or "{}")
            except ValueError:
                continue
            form = event.account_id.sudo()._find_or_create_discovered_form(
                payload.get("page_id"), payload.get("form_id")
            )
            if not form:
                continue
            event.write({
                "form_id": form.id,
                "state": "pending",
                "error_message": "Waiting for form configuration",
                "next_retry_at": False,
            })
        return True

    @api.model
    def _discover_unmapped_routes(self):
        events = self.sudo().search([
            ("route_id", "=", False),
            ("form_id.route_by_ad", "=", True),
            ("state", "in", ["pending", "failed"]),
            ("meta_lead_ref", "!=", False),
        ], order="create_date, id", limit=100)
        for event in events:
            try:
                payload = json.loads(event.payload or "{}")
            except ValueError:
                continue
            route = event.form_id._find_or_create_ad_route(payload)
            if route:
                event.write({
                    "route_id": route.id,
                    "state": "pending",
                    "error_message": "Waiting for ad routing configuration",
                    "next_retry_at": False,
                })
        return True

    @api.model
    def _cron_retry(self):
        self._discover_unmapped_forms()
        self._discover_unmapped_routes()
        events = self.search([
            ("state", "in", ["pending", "failed"]), ("retry_count", "<", 5),
            ("form_id", "!=", False), ("meta_lead_ref", "!=", False),
            ("form_id.active", "=", True), ("form_id.configuration_state", "=", "configured"),
            "|", ("next_retry_at", "=", False), ("next_retry_at", "<=", fields.Datetime.now()),
        ], limit=200).filtered(lambda event: (
            not event.form_id.route_by_ad
            or (
                event.route_id.active
                and event.route_id.configuration_state == "configured"
            )
        ))[:100]
        for event in events:
            try:
                event.state = "processing"
                lead = event.form_id._fetch_and_import(event.meta_lead_ref, event=event)
                if not lead and event.state == "processing":
                    event.write({"state": "pending", "error_message": "Waiting for ad routing configuration"})
            except Exception as exc:
                count = event.retry_count + 1
                event.write({"state": "failed", "retry_count": count, "error_message": str(exc)[:2000],
                             "next_retry_at": fields.Datetime.now() + timedelta(minutes=2 ** count)})


class MetaStatusEvent(models.Model):
    _name = "odx.meta.status.event"
    _description = "Meta CRM Status Feedback Event"
    _order = "create_date desc"

    account_id = fields.Many2one("odx.meta.account", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True, index=True)
    lead_id = fields.Many2one("crm.lead", required=True, ondelete="cascade", index=True)
    meta_lead_ref = fields.Char(string="Meta Lead ID", required=True, index=True)
    event_name = fields.Char(required=True, index=True)
    event_id = fields.Char(required=True, readonly=True, copy=False, index=True)
    event_time = fields.Datetime(required=True, default=fields.Datetime.now, readonly=True)
    state = fields.Selection([
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("done", "Done"),
        ("failed", "Failed"),
    ], required=True, default="pending", readonly=True, index=True)
    retry_count = fields.Integer(readonly=True)
    next_retry_at = fields.Datetime(readonly=True, index=True)
    processed_at = fields.Datetime(readonly=True)
    response = fields.Text(readonly=True, groups="base.group_system")
    error_message = fields.Text(readonly=True)

    _event_id_unique = models.Constraint(
        "UNIQUE(event_id)", "A Meta CRM status event can only be recorded once."
    )

    def _payload(self):
        self.ensure_one()
        event_time = fields.Datetime.to_datetime(self.event_time).replace(tzinfo=timezone.utc)
        event = {
            "event_name": self.event_name,
            "event_time": int(event_time.timestamp()),
            "event_id": self.event_id,
            "action_source": "system_generated",
            "user_data": {"lead_id": self.meta_lead_ref},
            "custom_data": {
                "lead_event_source": "Odoo-CRM",
                "event_source": "crm",
            },
        }
        payload = {"data": [event]}
        if self.account_id.conversion_test_event_code:
            payload["test_event_code"] = self.account_id.conversion_test_event_code
        return payload

    def _update_lead_sync_state(self, state, error=False):
        self.ensure_one()
        values = {
            "meta_status_sync_state": state,
            "meta_status_sync_event_id": self.id,
            "meta_status_sync_error": error or False,
        }
        if state == "done":
            values.update({
                "meta_status_synced_at": fields.Datetime.now(),
                "meta_status_synced_name": self.event_name,
            })
        self.lead_id.with_context(odx_meta_status_sync_write=True).sudo().write(values)

    def _send(self):
        self.ensure_one()
        account = self.account_id.sudo()
        if not account.conversion_sync_enabled:
            error = _("Meta CRM status synchronization is disabled for this account.")
            self.write({"state": "failed", "error_message": error, "next_retry_at": False})
            self._update_lead_sync_state("failed", error)
            return False
        self.state = "processing"
        try:
            result = account._graph_request(
                account,
                "POST",
                "%s/events" % account.conversion_dataset_id,
                access_token=account.conversion_access_token,
                json=self._payload(),
            )
            self.write({
                "state": "done",
                "processed_at": fields.Datetime.now(),
                "next_retry_at": False,
                "error_message": False,
                "response": json.dumps(result, ensure_ascii=False)[:4000],
            })
            self._update_lead_sync_state("done")
            return True
        except Exception as exc:  # closing a CRM lead must never be blocked by Meta
            count = self.retry_count + 1
            error = str(exc)[:2000]
            self.write({
                "state": "failed",
                "retry_count": count,
                "error_message": error,
                "next_retry_at": (
                    fields.Datetime.now() + timedelta(minutes=min(2 ** count, 60))
                    if count < 5 else False
                ),
            })
            self._update_lead_sync_state("failed", error)
            _logger.warning("Meta CRM status feedback failed for lead %s: %s", self.lead_id.id, error)
            return False

    @api.model
    def _cron_retry(self):
        self.flush_model(["state", "retry_count", "next_retry_at"])
        self.env.cr.execute("""
            SELECT id
              FROM odx_meta_status_event
             WHERE state IN ('pending', 'failed')
               AND retry_count < 5
               AND (next_retry_at IS NULL OR next_retry_at <= %s)
             ORDER BY create_date, id
             FOR UPDATE SKIP LOCKED
             LIMIT 100
        """, [fields.Datetime.now()])
        for event in self.sudo().browse([row[0] for row in self.env.cr.fetchall()]).exists():
            with self.env.cr.savepoint():
                event._send()
        return True
