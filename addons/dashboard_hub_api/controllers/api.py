from odoo import http
from odoo.http import request

from ..models.dashboard_api_service import DashboardAPIService


class DashboardHubAPIController(http.Controller):
    def _dispatch(self, page, payload):
        env = request.env
        error = DashboardAPIService.check_api_key(env)
        if error:
            return {"ok": False, "error": error}
        return DashboardAPIService.build_page(env, page, payload or {})

    @http.route("/dashboard_hub_api/overview", auth="none", type="json", csrf=False, methods=["POST"])
    def dashboard_overview(self, **payload):
        return self._dispatch("overview", payload)

    @http.route("/dashboard_hub_api/sales", auth="none", type="json", csrf=False, methods=["POST"])
    def dashboard_sales(self, **payload):
        return self._dispatch("sales", payload)

    @http.route("/dashboard_hub_api/purchases", auth="none", type="json", csrf=False, methods=["POST"])
    def dashboard_purchases(self, **payload):
        return self._dispatch("purchases", payload)

    @http.route("/dashboard_hub_api/stock", auth="none", type="json", csrf=False, methods=["POST"])
    def dashboard_stock(self, **payload):
        return self._dispatch("stock", payload)

    @http.route("/dashboard_hub_api/finance", auth="none", type="json", csrf=False, methods=["POST"])
    def dashboard_finance(self, **payload):
        return self._dispatch("finance", payload)

    @http.route("/dashboard_hub_api/expenses", auth="none", type="json", csrf=False, methods=["POST"])
    def dashboard_expenses(self, **payload):
        return self._dispatch("expenses", payload)
