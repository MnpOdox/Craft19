import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class MetaLeadWebhook(http.Controller):

    @http.route("/odx/meta/leads/webhook/<int:account_id>", type="http", auth="public", methods=["GET"], csrf=False)
    def verify(self, account_id, **query):
        account = request.env["odx.meta.account"].sudo().browse(account_id).exists()
        if account and query.get("hub.mode") == "subscribe" and query.get("hub.verify_token") == account.verify_token:
            return request.make_response(query.get("hub.challenge", ""), status=200)
        return request.make_response("Forbidden", status=403)

    @http.route("/odx/meta/leads/webhook/<int:account_id>", type="http", auth="public", methods=["POST"], csrf=False)
    def receive(self, account_id, **kwargs):
        account = request.env["odx.meta.account"].sudo().browse(account_id).exists()
        raw = request.httprequest.get_data(cache=True)
        if not account or not account.verify_signature(raw, request.httprequest.headers.get("X-Hub-Signature-256")):
            return request.make_response("Forbidden", status=403)
        try:
            payload = json.loads(raw)
        except ValueError:
            return request.make_response("Invalid JSON", status=400)
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                lead_ref, form_ref = str(value.get("leadgen_id") or ""), str(value.get("form_id") or "")
                if change.get("field") != "leadgen" or not lead_ref:
                    continue
                form = request.env["odx.meta.form"].sudo().with_context(active_test=False).search([
                    ("account_id", "=", account.id), ("meta_form_ref", "=", form_ref),
                ], limit=1)
                if not form:
                    form = account._find_or_create_discovered_form(value.get("page_id"), form_ref)
                route = form._find_or_create_ad_route(value) if form else request.env["odx.meta.ad.route"]
                route_ready = bool(
                    not form
                    or not form.route_by_ad
                    or (route and route.active and route.configuration_state == "configured")
                )
                ready = bool(
                    form and form.active and form.configuration_state == "configured" and route_ready
                )
                event = request.env["odx.meta.import.event"].sudo().create({
                    "account_id": account.id, "form_id": form.id, "route_id": route.id,
                    "meta_lead_ref": lead_ref,
                    "event_type": "webhook", "state": "processing" if ready else ("pending" if form else "failed"),
                    "payload": json.dumps(value),
                    "error_message": False if ready else (
                        (
                            "Waiting for form configuration"
                            if form and form.configuration_state != "configured"
                            else "Waiting for ad routing configuration"
                        ) if form else "No active form mapping"
                    ),
                })
                if ready:
                    try:
                        form._fetch_and_import(lead_ref, event=event)
                    except Exception as exc:
                        _logger.exception("Meta webhook import failed")
                        event.write({"state": "failed", "error_message": str(exc)[:2000]})
        return request.make_response("EVENT_RECEIVED", status=200)
