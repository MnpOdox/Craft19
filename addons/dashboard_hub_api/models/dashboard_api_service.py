from collections import defaultdict
from datetime import date, datetime, time, timedelta

from odoo import api, fields
from odoo.http import request


class DashboardAPIService:
    @classmethod
    def _icp(cls, env):
        return env["ir.config_parameter"].sudo()

    @classmethod
    def _is_enabled(cls, env):
        value = str(cls._icp(env).get_param("dashboard_hub_api.enabled", "False") or "").strip().lower()
        return value in {"1", "true", "yes", "on"}

    @classmethod
    def _api_key(cls, env):
        return str(cls._icp(env).get_param("dashboard_hub_api.api_key", "") or "").strip()

    @classmethod
    def _api_key_header(cls, env):
        return str(cls._icp(env).get_param("dashboard_hub_api.api_key_header", "X-DASHBOARD-KEY") or "X-DASHBOARD-KEY").strip()

    @classmethod
    def _source_name(cls, env):
        return str(cls._icp(env).get_param("dashboard_hub_api.source_name", "Odoo Source") or "Odoo Source").strip()

    @classmethod
    def _base_url(cls, env):
        return str(cls._icp(env).get_param("web.base.url", "") or "").rstrip("/")

    @classmethod
    def check_api_key(cls, env):
        if not cls._is_enabled(env):
            return "Dashboard Hub API is disabled."
        expected = cls._api_key(env)
        if not expected:
            return "Dashboard Hub API key is not configured."
        header_name = cls._api_key_header(env)
        provided = (request.httprequest.headers.get(header_name) or "").strip()
        if not provided:
            return f"Missing API key header: {header_name}"
        if provided != expected:
            return "Invalid API key."
        return None

    @classmethod
    def _safe_int(cls, value, default=0):
        try:
            return int(value)
        except Exception:
            return default

    @classmethod
    def _parse_date(cls, value):
        if not value:
            return None
        if isinstance(value, date):
            return value
        try:
            return fields.Date.to_date(value)
        except Exception:
            return None

    @classmethod
    def _resolve_period(cls, payload):
        period = str(payload.get("period") or "this_month").strip()
        today = fields.Date.context_today(request.env.user)
        date_from = cls._parse_date(payload.get("date_from"))
        date_to = cls._parse_date(payload.get("date_to"))
        if date_from and date_to:
            return date_from, date_to, "custom"
        if period == "today":
            return today, today, period
        if period == "this_week":
            start = today - timedelta(days=today.weekday())
            return start, start + timedelta(days=6), period
        if period == "this_year":
            return today.replace(month=1, day=1), today.replace(month=12, day=31), period
        if period == "all":
            return None, None, period
        start = today.replace(day=1)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1) - timedelta(days=1)
        else:
            end = start.replace(month=start.month + 1) - timedelta(days=1)
        return start, end, "this_month"

    @classmethod
    def _date_domain(cls, field_name, date_from, date_to):
        domain = []
        if date_from:
            if field_name.endswith("_date") or field_name == "date":
                domain.append((field_name, ">=", date_from))
            else:
                domain.append((field_name, ">=", datetime.combine(date_from, time.min)))
        if date_to:
            if field_name.endswith("_date") or field_name == "date":
                domain.append((field_name, "<=", date_to))
            else:
                domain.append((field_name, "<", datetime.combine(date_to + timedelta(days=1), time.min)))
        return domain

    @classmethod
    def _companies(cls, env, payload):
        company_ids = payload.get("company_ids") or []
        company_ids = [cls._safe_int(company_id) for company_id in company_ids if cls._safe_int(company_id)]
        companies = env["res.company"].sudo().browse(company_ids) if company_ids else env["res.company"].sudo().search([])
        return companies

    @classmethod
    def _scope(cls, env, payload, date_from, date_to, period):
        companies = cls._companies(env, payload)
        return {
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "period": period,
            "company_ids": companies.ids,
            "company_names": companies.mapped("name"),
            "source_name": cls._source_name(env),
            "base_url": cls._base_url(env),
        }, companies

    @classmethod
    def _model_url(cls, base_url, model, record_id):
        if not base_url or not record_id:
            return ""
        return f"{base_url}/web#id={record_id}&model={model}&view_type=form"

    @classmethod
    def _kpi(cls, key, label, value, fmt="number", suffix="", currency_symbol=""):
        return {
            "key": key,
            "label": label,
            "value": round(value or 0.0, 2) if fmt in {"currency", "decimal"} else int(value or 0),
            "format": fmt,
            "suffix": suffix,
            "currency_symbol": currency_symbol,
        }

    @classmethod
    def _chart(cls, key, title, items, chart_type="bar"):
        return {
            "key": key,
            "title": title,
            "type": chart_type,
            "items": items,
        }

    @classmethod
    def _table(cls, key, title, columns, rows):
        return {
            "key": key,
            "title": title,
            "columns": columns,
            "rows": rows,
        }

    @classmethod
    def _currency_symbol(cls, companies):
        currency = companies[:1].currency_id
        return currency.symbol or ""

    @classmethod
    def _group_amounts_by_label(cls, rows, key_label, key_value):
        grouped = defaultdict(float)
        for row in rows:
            label = row.get(key_label) or "Unknown"
            grouped[label] += float(row.get(key_value) or 0.0)
        return [{"label": label, "value": round(value, 2)} for label, value in sorted(grouped.items(), key=lambda item: item[1], reverse=True)]

    @classmethod
    def build_page(cls, env, page, payload):
        env = api.Environment(env.cr, 1, env.context)
        date_from, date_to, period = cls._resolve_period(payload)
        scope, companies = cls._scope(env, payload, date_from, date_to, period)
        handlers = {
            "overview": cls._build_overview,
            "sales": cls._build_sales,
            "purchases": cls._build_purchases,
            "stock": cls._build_stock,
            "finance": cls._build_finance,
            "expenses": cls._build_expenses,
        }
        handler = handlers.get(page)
        if not handler:
            return {"ok": False, "error": f"Unsupported dashboard page: {page}"}
        data = handler(env, companies, scope, date_from, date_to)
        data.update({"ok": True, "page": page, "scope": scope})
        return data

    @classmethod
    def _build_overview(cls, env, companies, scope, date_from, date_to):
        sales = cls._build_sales(env, companies, scope, date_from, date_to)
        purchases = cls._build_purchases(env, companies, scope, date_from, date_to)
        stock = cls._build_stock(env, companies, scope, date_from, date_to)
        finance = cls._build_finance(env, companies, scope, date_from, date_to)
        expenses = cls._build_expenses(env, companies, scope, date_from, date_to)
        currency_symbol = cls._currency_symbol(companies)
        kpis = [
            cls._kpi("pos_sales", "POS Sales", sales["summary"]["sales_amount"], "currency", currency_symbol=currency_symbol),
            cls._kpi("purchase_amount", "Purchase Amount", purchases["summary"]["purchase_amount"], "currency", currency_symbol=currency_symbol),
            cls._kpi("stock_value", "Stock Value", stock["summary"]["inventory_value"], "currency", currency_symbol=currency_symbol),
            cls._kpi("cash_current", "Cash Current", finance["summary"]["cash_current_balance"], "currency", currency_symbol=currency_symbol),
            cls._kpi("bank_current", "Bank Current", finance["summary"]["bank_current_balance"], "currency", currency_symbol=currency_symbol),
            cls._kpi("expense_total", "Expenses", expenses["summary"]["expense_total"], "currency", currency_symbol=currency_symbol),
        ]
        charts = [
            sales["charts"][0],
            purchases["charts"][0],
            expenses["charts"][0],
        ]
        return {
            "title": "Overview",
            "kpis": kpis,
            "charts": charts,
            "tables": [],
            "summary": {
                "sales_amount": sales["summary"]["sales_amount"],
                "purchase_amount": purchases["summary"]["purchase_amount"],
                "inventory_value": stock["summary"]["inventory_value"],
                "cash_current_balance": finance["summary"]["cash_current_balance"],
                "bank_current_balance": finance["summary"]["bank_current_balance"],
                "expense_total": expenses["summary"]["expense_total"],
            },
        }

    @classmethod
    def _build_sales(cls, env, companies, scope, date_from, date_to):
        orders_domain = cls._date_domain("date_order", date_from, date_to) + [("company_id", "in", companies.ids), ("state", "not in", ["cancel", "draft"])]
        orders = env["pos.order"].search(orders_domain)
        order_lines = env["pos.order.line"].search([("order_id", "in", orders.ids)]) if orders else env["pos.order.line"]
        payments = env["pos.payment"].search([("pos_order_id", "in", orders.ids)]) if orders else env["pos.payment"]
        currency_symbol = cls._currency_symbol(companies)

        sales_amount = sum(orders.mapped("amount_total"))
        order_count = len(orders)
        items_sold = sum(order_lines.mapped("qty")) if orders else 0.0
        avg_bill = sales_amount / order_count if order_count else 0.0
        online_orders = orders.filtered("online_order")
        walking_orders = orders - online_orders
        online_sales_amount = sum(online_orders.mapped("amount_total"))
        walking_sales_amount = sum(walking_orders.mapped("amount_total"))
        online_order_count = len(online_orders)
        walking_order_count = len(walking_orders)

        trend = defaultdict(float)
        for order in orders:
            label = fields.Datetime.to_datetime(order.date_order).strftime("%Y-%m-%d")
            trend[label] += order.amount_total

        product_sales = defaultdict(lambda: {"qty": 0.0, "amount": 0.0})
        for line in order_lines:
            label = line.full_product_name or line.product_id.display_name
            product_sales[label]["qty"] += line.qty or 0.0
            product_sales[label]["amount"] += line.price_subtotal_incl or line.price_subtotal or 0.0

        payment_split = defaultdict(float)
        for payment in payments:
            label = payment.payment_method_id.display_name if payment.payment_method_id else "Unknown"
            payment_split[label] += payment.amount or 0.0

        return {
            "title": "Sales",
            "kpis": [
                cls._kpi("sales_amount", "POS Sales Amount", sales_amount, "currency", currency_symbol=currency_symbol),
                cls._kpi("online_sales_amount", "Online POS Sales", online_sales_amount, "currency", currency_symbol=currency_symbol),
                cls._kpi("walking_sales_amount", "Walking POS Sales", walking_sales_amount, "currency", currency_symbol=currency_symbol),
                cls._kpi("order_count", "POS Orders", order_count),
                cls._kpi("online_order_count", "Online Orders", online_order_count),
                cls._kpi("walking_order_count", "Walking Orders", walking_order_count),
                cls._kpi("avg_bill", "Average Bill", avg_bill, "currency", currency_symbol=currency_symbol),
                cls._kpi("items_sold", "Items Sold", items_sold, "decimal"),
            ],
            "charts": [
                cls._chart("sales_trend", "POS Sales Trend", [{"label": label, "value": round(value, 2)} for label, value in sorted(trend.items())]),
                cls._chart(
                    "payment_split",
                    "Payment Method Split",
                    [{"label": label, "value": round(value, 2)} for label, value in sorted(payment_split.items(), key=lambda item: item[1], reverse=True)],
                ),
            ],
            "tables": [],
            "summary": {
                "sales_amount": sales_amount,
                "online_sales_amount": online_sales_amount,
                "walking_sales_amount": walking_sales_amount,
                "order_count": order_count,
                "online_order_count": online_order_count,
                "walking_order_count": walking_order_count,
                "avg_bill": avg_bill,
                "items_sold": items_sold,
            },
        }

    @classmethod
    def _build_purchases(cls, env, companies, scope, date_from, date_to):
        po_domain = cls._date_domain("date_order", date_from, date_to) + [("company_id", "in", companies.ids), ("state", "not in", ["cancel"])]
        purchase_orders = env["purchase.order"].search(po_domain)
        purchase_lines = env["purchase.order.line"].search([("order_id", "in", purchase_orders.ids)]) if purchase_orders else env["purchase.order.line"]
        currency_symbol = cls._currency_symbol(companies)

        purchase_amount = sum(purchase_orders.mapped("amount_total"))
        po_count = len(purchase_orders)
        avg_po = purchase_amount / po_count if po_count else 0.0

        trend = defaultdict(float)
        vendor_totals = defaultdict(float)
        product_totals = defaultdict(float)
        for order in purchase_orders:
            label = fields.Datetime.to_datetime(order.date_order).strftime("%Y-%m-%d")
            trend[label] += order.amount_total
            vendor_totals[order.partner_id.display_name or "Unknown"] += order.amount_total
        for line in purchase_lines:
            product_totals[line.product_id.display_name or line.name] += line.price_total or line.price_subtotal or 0.0

        return {
            "title": "Purchases",
            "kpis": [
                cls._kpi("purchase_amount", "Purchase Amount", purchase_amount, "currency", currency_symbol=currency_symbol),
                cls._kpi("po_count", "Purchase Orders", po_count),
                cls._kpi("avg_po", "Average PO", avg_po, "currency", currency_symbol=currency_symbol),
            ],
            "charts": [
                cls._chart("purchase_trend", "Purchase Trend", [{"label": label, "value": round(value, 2)} for label, value in sorted(trend.items())]),
                cls._chart(
                    "vendor_totals",
                    "Top Vendors",
                    [{"label": label, "value": round(value, 2)} for label, value in sorted(vendor_totals.items(), key=lambda item: item[1], reverse=True)[:10]],
                ),
            ],
            "tables": [],
            "summary": {
                "purchase_amount": purchase_amount,
                "po_count": po_count,
                "avg_po": avg_po,
            },
        }

    @classmethod
    def _build_stock(cls, env, companies, scope, date_from, date_to):
        quant_domain = [("company_id", "in", companies.ids), ("location_id.usage", "=", "internal")]
        quants = env["stock.quant"].search(quant_domain)
        moves_domain = cls._date_domain("date", date_from, date_to) + [("company_id", "in", companies.ids), ("state", "=", "done")]
        moves = env["stock.move"].search(moves_domain)
        on_hand_qty = sum(quants.mapped("quantity"))
        available_qty = sum((quant.quantity - quant.reserved_quantity) for quant in quants)
        inventory_value = sum((quant.quantity or 0.0) * (quant.product_id.standard_price or 0.0) for quant in quants)
        quantity_by_product = defaultdict(float)
        out_of_stock = 0
        low_stock = 0
        for quant in quants:
            quantity_by_product[quant.product_id.display_name] += quant.quantity or 0.0
        for qty in quantity_by_product.values():
            if qty <= 0:
                out_of_stock += 1
            elif qty <= 5:
                low_stock += 1
        movement_by_product = defaultdict(float)
        trend = defaultdict(float)
        for move in moves:
            label = move.product_id.display_name or move.reference or "Unknown"
            moved_qty = move.product_uom_qty or 0.0
            if "quantity" in move._fields:
                moved_qty = move.quantity or moved_qty
            movement_by_product[label] += moved_qty
            trend[(move.date or fields.Datetime.now()).strftime("%Y-%m-%d")] += moved_qty
        currency_symbol = cls._currency_symbol(companies)
        return {
            "title": "Stock",
            "kpis": [
                cls._kpi("on_hand_qty", "On Hand Quantity", on_hand_qty, "decimal"),
                cls._kpi("available_qty", "Available Quantity", available_qty, "decimal"),
                cls._kpi("inventory_value", "Inventory Value", inventory_value, "currency", currency_symbol=currency_symbol),
                cls._kpi("low_stock_count", "Low Stock Items", low_stock),
                cls._kpi("out_of_stock_count", "Out of Stock Items", out_of_stock),
            ],
            "charts": [
                cls._chart("stock_movement_trend", "Stock Movement Trend", [{"label": label, "value": round(value, 2)} for label, value in sorted(trend.items())]),
            ],
            "tables": [],
            "summary": {
                "inventory_value": inventory_value,
                "on_hand_qty": on_hand_qty,
                "available_qty": available_qty,
                "low_stock": low_stock,
                "out_of_stock": out_of_stock,
            },
        }

    @classmethod
    def _build_finance(cls, env, companies, scope, date_from, date_to):
        cash_books = env["cash.book"].search([("company_id", "in", companies.ids)])
        bank_books = env["bank.book"].search([("company_id", "in", companies.ids)])
        cash_lines = env["cash.book.line"].search(cls._date_domain("date", date_from, date_to) + [("company_id", "in", companies.ids)])
        bank_lines = env["bank.book.line"].search(cls._date_domain("date", date_from, date_to) + [("company_id", "in", companies.ids)])
        currency_symbol = cls._currency_symbol(companies)

        cash_open = sum(cash_books.mapped("open_balance"))
        cash_current = sum(cash_books.mapped("cur_balance"))
        bank_open = sum(bank_books.mapped("open_balance"))
        bank_current = sum(bank_books.mapped("cur_balance"))
        cash_in = sum(line.amount for line in cash_lines if (line.amount or 0.0) > 0)
        cash_out = abs(sum(line.amount for line in cash_lines if (line.amount or 0.0) < 0))
        bank_in = sum(line.amount for line in bank_lines if (line.amount or 0.0) > 0)
        bank_out = abs(sum(line.amount for line in bank_lines if (line.amount or 0.0) < 0))

        cash_trend = defaultdict(float)
        for line in cash_lines:
            cash_trend[(line.date or fields.Date.today()).strftime("%Y-%m-%d")] += line.amount or 0.0
        bank_trend = defaultdict(float)
        for line in bank_lines:
            bank_trend[(line.date or fields.Date.today()).strftime("%Y-%m-%d")] += line.amount or 0.0

        return {
            "title": "Finance",
            "kpis": [
                cls._kpi("cash_open", "Cash Opening", cash_open, "currency", currency_symbol=currency_symbol),
                cls._kpi("cash_current", "Cash Current", cash_current, "currency", currency_symbol=currency_symbol),
                cls._kpi("bank_open", "Bank Opening", bank_open, "currency", currency_symbol=currency_symbol),
                cls._kpi("bank_current", "Bank Current", bank_current, "currency", currency_symbol=currency_symbol),
                cls._kpi("cash_in", "Cash Inflow", cash_in, "currency", currency_symbol=currency_symbol),
                cls._kpi("cash_out", "Cash Outflow", cash_out, "currency", currency_symbol=currency_symbol),
                cls._kpi("bank_in", "Bank Inflow", bank_in, "currency", currency_symbol=currency_symbol),
                cls._kpi("bank_out", "Bank Outflow", bank_out, "currency", currency_symbol=currency_symbol),
            ],
            "charts": [
                cls._chart("cash_trend", "Cash Movement Trend", [{"label": label, "value": round(value, 2)} for label, value in sorted(cash_trend.items())]),
                cls._chart("bank_trend", "Bank Movement Trend", [{"label": label, "value": round(value, 2)} for label, value in sorted(bank_trend.items())]),
            ],
            "tables": [],
            "summary": {
                "cash_current_balance": cash_current,
                "bank_current_balance": bank_current,
                "cash_open_balance": cash_open,
                "bank_open_balance": bank_open,
            },
        }

    @classmethod
    def _build_expenses(cls, env, companies, scope, date_from, date_to):
        expenses = env["expense.book"].search(cls._date_domain("date", date_from, date_to) + [("company_id", "in", companies.ids)])
        currency_symbol = cls._currency_symbol(companies)
        expense_total = sum(expenses.mapped("amount"))
        expense_count = len(expenses)
        trend = defaultdict(float)
        heads = defaultdict(float)
        for expense in expenses:
            trend[(expense.date or fields.Date.today()).strftime("%Y-%m-%d")] += expense.amount or 0.0
            heads[expense.head_id.display_name or "Unknown"] += expense.amount or 0.0
        return {
            "title": "Expenses",
            "kpis": [
                cls._kpi("expense_total", "Total Expenses", expense_total, "currency", currency_symbol=currency_symbol),
                cls._kpi("expense_count", "Expense Entries", expense_count),
            ],
            "charts": [
                cls._chart("expense_trend", "Expense Trend", [{"label": label, "value": round(value, 2)} for label, value in sorted(trend.items())]),
                cls._chart(
                    "expense_heads",
                    "Top Expense Heads",
                    [{"label": label, "value": round(value, 2)} for label, value in sorted(heads.items(), key=lambda item: item[1], reverse=True)[:10]],
                ),
            ],
            "tables": [],
            "summary": {
                "expense_total": expense_total,
                "expense_count": expense_count,
            },
        }
