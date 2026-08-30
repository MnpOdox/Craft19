import json
import hashlib
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class WhatsAppWebhook(http.Controller):

    @http.route("/odx/whatsapp/webhook/<int:account_id>", type="http", auth="public", methods=["GET"], csrf=False)
    def verify(self, account_id, **query):
        account = request.env["odx.whatsapp.account"].sudo().browse(account_id).exists()
        if account and account.active and query.get("hub.mode") == "subscribe" and query.get("hub.verify_token") == account.verify_token:
            account.webhook_state = "verified"
            return request.make_response(query.get("hub.challenge", ""), status=200)
        return request.make_response("Forbidden", status=403)

    @http.route("/odx/whatsapp/webhook/<int:account_id>", type="http", auth="public", methods=["POST"], csrf=False)
    def receive(self, account_id, **kwargs):
        account = request.env["odx.whatsapp.account"].sudo().browse(account_id).exists()
        raw = request.httprequest.get_data(cache=True)
        if not account or not account.active or not account.verify_signature(raw, request.httprequest.headers.get("X-Hub-Signature-256")):
            return request.make_response("Forbidden", status=403)
        try:
            payload = json.loads(raw)
        except ValueError:
            return request.make_response("Invalid JSON", status=400)
        Event = request.env["odx.whatsapp.event"].sudo()
        digest = hashlib.sha256(raw).hexdigest()
        event = Event.search([("account_id", "=", account.id), ("payload_hash", "=", digest)], limit=1)
        if not event:
            event = Event.create({"account_id": account.id, "payload_hash": digest, "payload": raw.decode("utf-8")})
            event._process()
        return request.make_response("EVENT_RECEIVED", status=200)
