import json

from odoo import http
from odoo.http import request

from ..models.dashboard_api_service import DashboardAPIService


class DashboardHubAPIController(http.Controller):
    def _dispatch(self, page):
        env = request.env
        error = DashboardAPIService.check_api_key(env)
        if error:
            status = 401
            body = {"ok": False, "error": error}
        else:
            try:
                payload = request.get_json_data() or {}
            except Exception:
                payload = {}
            status = 200
            body = DashboardAPIService.build_page(env, page, payload)
        return request.make_response(
            json.dumps(body),
            headers=[("Content-Type", "application/json")],
            status=status,
        )

    @http.route("/dashboard_hub_api/overview", auth="public", type="http", csrf=False, methods=["POST"])
    def dashboard_overview(self, **kwargs):
        return self._dispatch("overview")

    @http.route("/dashboard_hub_api/sales", auth="public", type="http", csrf=False, methods=["POST"])
    def dashboard_sales(self, **kwargs):
        return self._dispatch("sales")

    @http.route("/dashboard_hub_api/purchases", auth="public", type="http", csrf=False, methods=["POST"])
    def dashboard_purchases(self, **kwargs):
        return self._dispatch("purchases")

    @http.route("/dashboard_hub_api/stock", auth="public", type="http", csrf=False, methods=["POST"])
    def dashboard_stock(self, **kwargs):
        return self._dispatch("stock")

    @http.route("/dashboard_hub_api/finance", auth="public", type="http", csrf=False, methods=["POST"])
    def dashboard_finance(self, **kwargs):
        return self._dispatch("finance")

    @http.route("/dashboard_hub_api/cash", auth="public", type="http", csrf=False, methods=["POST"])
    def dashboard_cash(self, **kwargs):
        return self._dispatch("cash")

    @http.route("/dashboard_hub_api/bank", auth="public", type="http", csrf=False, methods=["POST"])
    def dashboard_bank(self, **kwargs):
        return self._dispatch("bank")

    @http.route("/dashboard_hub_api/expenses", auth="public", type="http", csrf=False, methods=["POST"])
    def dashboard_expenses(self, **kwargs):
        return self._dispatch("expenses")
