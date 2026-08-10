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
        if period == "yesterday":
            yesterday = today - timedelta(days=1)
            return yesterday, yesterday, period
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
    def _previous_period(cls, date_from, date_to, period):
        if not date_from or not date_to or period == "all":
            return None, None
        if period == "today":
            previous_day = date_from - timedelta(days=1)
            return previous_day, previous_day
        if period == "yesterday":
            previous_day = date_from - timedelta(days=1)
            return previous_day, previous_day
        if period == "this_week":
            previous_start = date_from - timedelta(days=7)
            return previous_start, previous_start + timedelta(days=6)
        if period == "this_month":
            previous_end = date_from - timedelta(days=1)
            previous_start = previous_end.replace(day=1)
            return previous_start, previous_end
        if period == "this_year":
            previous_start = date(date_from.year - 1, 1, 1)
            previous_end = date(date_from.year - 1, 12, 31)
            return previous_start, previous_end
        day_span = (date_to - date_from).days + 1
        previous_end = date_from - timedelta(days=1)
        previous_start = previous_end - timedelta(days=day_span - 1)
        return previous_start, previous_end

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
    def _comparison_meta(cls, current_value, previous_value, label):
        current = float(current_value or 0.0)
        previous = float(previous_value or 0.0)
        delta = current - previous
        rounded_delta = round(delta, 2)
        if previous:
            pct = (rounded_delta / previous) * 100.0
        elif current:
            pct = 100.0
        else:
            pct = 0.0
        rounded_pct = round(pct, 2)
        if rounded_delta > 0:
            direction = "up"
        elif rounded_delta < 0:
            direction = "down"
        else:
            direction = "flat"
        return {
            "previous_value": round(previous, 2),
            "delta_value": rounded_delta,
            "delta_pct": rounded_pct,
            "direction": direction,
            "label": label,
        }

    @classmethod
    def _chart(cls, key, title, items, chart_type="bar", preview_limit=None):
        return {
            "key": key,
            "title": title,
            "type": chart_type,
            "items": items,
            "preview_limit": preview_limit,
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
    def _top_breakdown_items(cls, totals, limit=8, aggregate_others=True):
        ordered = sorted(
            ((label or "Unknown", round(value, 2)) for label, value in totals.items() if abs(value or 0.0) > 0.0),
            key=lambda item: item[1],
            reverse=True,
        )
        if not ordered:
            return []
        if aggregate_others and len(ordered) > limit:
            top_items = ordered[:limit]
            others_total = round(sum(value for _, value in ordered[limit:]), 2)
            if others_total:
                top_items.append(("Others", others_total))
            ordered = top_items
        return [{"label": label, "value": value} for label, value in ordered]

    @classmethod
    def _format_decimal(cls, value):
        number = round(float(value or 0.0), 2)
        if number.is_integer():
            return f"{int(number):,}"
        return f"{number:,.2f}"

    @classmethod
    def _format_currency_value(cls, value, currency_symbol=""):
        amount = cls._format_decimal(value)
        return f"{currency_symbol}{amount}" if currency_symbol else amount

    @classmethod
    def _stock_age_buckets(cls):
        return [
            ("0_30", "0-30", 0, 30),
            ("31_60", "31-60", 31, 60),
            ("61_90", "61-90", 61, 90),
            ("90_plus", "90+", 91, None),
        ]

    @classmethod
    def _age_bucket_key(cls, age_days):
        for key, _label, start, end in cls._stock_age_buckets():
            if age_days >= start and (end is None or age_days <= end):
                return key
        return "0_30"

    @classmethod
    def _stock_product_snapshot(cls, env, companies):
        quant_domain = [("company_id", "in", companies.ids), ("location_id.usage", "=", "internal")]
        quants = env["stock.quant"].search(quant_domain)
        today = fields.Date.context_today(env.user)
        products = {}
        quant_in_dates = {}

        for quant in quants:
            product = quant.product_id
            if not product or product.id not in products:
                products[product.id] = {
                    "product": product,
                    "name": product.display_name or "Unknown",
                    "category": product.categ_id.display_name or "Uncategorized",
                    "on_hand_qty": 0.0,
                    "available_qty": 0.0,
                    "stock_value": 0.0,
                    "valuation_qty": 0.0,
                    "valuation_value": 0.0,
                    "layers_count": 0,
                    "bucket_qty": defaultdict(float),
                    "bucket_value": defaultdict(float),
                    "old_value_61_plus": 0.0,
                    "old_value_90_plus": 0.0,
                    "old_qty_61_plus": 0.0,
                    "old_qty_90_plus": 0.0,
                    "latest_receipt_date": None,
                    "dominant_bucket": "0-30",
                    "movement_in_qty": 0.0,
                    "movement_out_qty": 0.0,
                    "movement_total_qty": 0.0,
                    "movement_days": 0,
                    "last_movement_date": None,
                }
            item = products[product.id]
            quantity = float(quant.quantity or 0.0)
            item["on_hand_qty"] += quantity
            item["available_qty"] += quantity - float(quant.reserved_quantity or 0.0)
            raw_in_date = getattr(quant, "in_date", None)
            if raw_in_date:
                quant_date = fields.Date.to_date(raw_in_date)
                previous_date = quant_in_dates.get(product.id)
                quant_in_dates[product.id] = min(previous_date, quant_date) if previous_date else quant_date

        valuation_model = env.registry.get("stock.valuation.layer") and env["stock.valuation.layer"]
        used_valuation_layers = False
        if valuation_model and {"remaining_qty", "remaining_value", "product_id", "company_id"} <= set(valuation_model._fields):
            valuation_domain = [("company_id", "in", companies.ids), ("remaining_qty", ">", 0)]
            valuation_layers = valuation_model.search(valuation_domain)
            for layer in valuation_layers:
                product = layer.product_id
                if not product:
                    continue
                item = products.setdefault(
                    product.id,
                    {
                        "product": product,
                        "name": product.display_name or "Unknown",
                        "category": product.categ_id.display_name or "Uncategorized",
                        "on_hand_qty": 0.0,
                        "available_qty": 0.0,
                        "stock_value": 0.0,
                        "valuation_qty": 0.0,
                        "valuation_value": 0.0,
                        "layers_count": 0,
                        "bucket_qty": defaultdict(float),
                        "bucket_value": defaultdict(float),
                        "old_value_61_plus": 0.0,
                        "old_value_90_plus": 0.0,
                        "old_qty_61_plus": 0.0,
                        "old_qty_90_plus": 0.0,
                        "latest_receipt_date": None,
                        "dominant_bucket": "0-30",
                        "movement_in_qty": 0.0,
                        "movement_out_qty": 0.0,
                        "movement_total_qty": 0.0,
                        "movement_days": 0,
                        "last_movement_date": None,
                    },
                )
                remaining_qty = float(layer.remaining_qty or 0.0)
                remaining_value = float(layer.remaining_value or 0.0)
                if remaining_qty <= 0 and abs(remaining_value) <= 0.00001:
                    continue
                layer_date = None
                for field_name in ("accounting_date", "create_date"):
                    if field_name not in layer._fields:
                        continue
                    raw_value = getattr(layer, field_name)
                    if raw_value:
                        layer_date = fields.Date.to_date(raw_value)
                        break
                layer_date = layer_date or today
                age_days = max((today - layer_date).days, 0)
                bucket_key = cls._age_bucket_key(age_days)
                item["valuation_qty"] += remaining_qty
                item["valuation_value"] += remaining_value
                item["bucket_qty"][bucket_key] += remaining_qty
                item["bucket_value"][bucket_key] += remaining_value
                item["layers_count"] += 1
                if age_days >= 61:
                    item["old_qty_61_plus"] += remaining_qty
                    item["old_value_61_plus"] += remaining_value
                if age_days >= 91:
                    item["old_qty_90_plus"] += remaining_qty
                    item["old_value_90_plus"] += remaining_value
                if not item["latest_receipt_date"] or layer_date > item["latest_receipt_date"]:
                    item["latest_receipt_date"] = layer_date
                used_valuation_layers = True

        for item in products.values():
            if item["valuation_qty"] > 0 or abs(item["valuation_value"]) > 0.00001:
                item["stock_value"] = item["valuation_value"]
                if not item["on_hand_qty"]:
                    item["on_hand_qty"] = item["valuation_qty"]
                if not item["available_qty"]:
                    item["available_qty"] = item["valuation_qty"]
            else:
                fallback_value = item["on_hand_qty"] * float(item["product"].standard_price or 0.0)
                item["stock_value"] = fallback_value
                quant_date = quant_in_dates.get(item["product"].id) or today
                age_days = max((today - quant_date).days, 0)
                bucket_key = cls._age_bucket_key(age_days)
                item["bucket_qty"][bucket_key] += item["on_hand_qty"]
                item["bucket_value"][bucket_key] += item["stock_value"]
                if age_days >= 61:
                    item["old_qty_61_plus"] += item["on_hand_qty"]
                    item["old_value_61_plus"] += item["stock_value"]
                if age_days >= 91:
                    item["old_qty_90_plus"] += item["on_hand_qty"]
                    item["old_value_90_plus"] += item["stock_value"]
                item["latest_receipt_date"] = quant_date

            dominant_bucket_key = max(
                cls._stock_age_buckets(),
                key=lambda bucket: item["bucket_value"].get(bucket[0], 0.0),
            )[0]
            bucket_meta = next((bucket for bucket in cls._stock_age_buckets() if bucket[0] == dominant_bucket_key), None)
            item["dominant_bucket"] = bucket_meta[1] if bucket_meta else "0-30"
        return products, used_valuation_layers

    @classmethod
    def _stock_movement_snapshot(cls, env, companies, date_from, date_to):
        moves_domain = cls._date_domain("date", date_from, date_to) + [("company_id", "in", companies.ids), ("state", "=", "done")]
        moves = env["stock.move"].search(moves_domain)
        movement_by_product = defaultdict(
            lambda: {
                "in_qty": 0.0,
                "out_qty": 0.0,
                "total_qty": 0.0,
                "movement_days": set(),
                "last_movement_date": None,
            }
        )
        movement_trend = defaultdict(lambda: {"in": 0.0, "out": 0.0})

        for move in moves:
            product = move.product_id
            if not product:
                continue
            source_usage = getattr(move.location_id, "usage", "")
            dest_usage = getattr(move.location_dest_id, "usage", "")
            moved_qty = float(getattr(move, "quantity", 0.0) or move.product_uom_qty or 0.0)
            if moved_qty <= 0:
                continue
            movement_date = fields.Datetime.to_datetime(move.date).date() if move.date else None
            item = movement_by_product[product.id]
            if movement_date:
                item["movement_days"].add(movement_date)
                item["last_movement_date"] = max(filter(None, [item["last_movement_date"], movement_date]))
            if source_usage == "internal" and dest_usage != "internal":
                item["out_qty"] += moved_qty
                if movement_date:
                    movement_trend[movement_date.isoformat()]["out"] += moved_qty
            elif source_usage != "internal" and dest_usage == "internal":
                item["in_qty"] += moved_qty
                if movement_date:
                    movement_trend[movement_date.isoformat()]["in"] += moved_qty
            else:
                continue
            item["total_qty"] = item["in_qty"] + item["out_qty"]

        return movement_by_product, movement_trend

    @classmethod
    def _is_bank_payment_method(cls, payment_method):
        return getattr(payment_method, "type", "") == "bank"

    @classmethod
    def _book_period_domain(cls, date_from, date_to):
        if not date_from and not date_to:
            return []
        domain = []
        if date_to:
            domain.append(("start_date", "<=", date_to))
        if date_from:
            domain.append(("end_date", ">=", date_from))
        return domain

    @classmethod
    def _finance_data(cls, env, companies, date_from, date_to):
        book_period_domain = cls._book_period_domain(date_from, date_to)
        cash_books = env["cash.book"].search([("company_id", "in", companies.ids)] + book_period_domain)
        bank_books = env["bank.book"].search([("company_id", "in", companies.ids)] + book_period_domain)
        cash_lines = env["cash.book.line"].search(cls._date_domain("date", date_from, date_to) + [("company_id", "in", companies.ids)])
        bank_lines = env["bank.book.line"].search(cls._date_domain("date", date_from, date_to) + [("company_id", "in", companies.ids)])
        cash_sales_heads = env["book.head"].search([("cash", "=", True), ("head_name", "ilike", "sales")])
        bank_sales_heads = env["book.head"].search([("bank", "=", True), ("head_name", "ilike", "sales")])
        cash_sales_lines = cash_lines.filtered(lambda line: line.head_id.id in cash_sales_heads.ids)
        bank_sales_lines = bank_lines.filtered(lambda line: line.head_id.id in bank_sales_heads.ids)

        cash_open = sum(cash_books.mapped("open_balance"))
        cash_current = sum(cash_books.mapped("cur_balance"))
        bank_open = sum(bank_books.mapped("open_balance"))
        bank_current = sum(bank_books.mapped("cur_balance"))
        cash_in = sum(line.amount for line in cash_lines if (line.amount or 0.0) > 0)
        cash_out = abs(sum(line.amount for line in cash_lines if (line.amount or 0.0) < 0))
        cash_sales_book = sum(line.amount or 0.0 for line in cash_sales_lines)
        bank_sales_book = sum(line.amount or 0.0 for line in bank_sales_lines)
        bank_in = sum(line.amount for line in bank_lines if (line.amount or 0.0) > 0)
        bank_out = abs(sum(line.amount for line in bank_lines if (line.amount or 0.0) < 0))

        cash_trend = defaultdict(float)
        for line in cash_lines:
            cash_trend[(line.date or fields.Date.today()).strftime("%Y-%m-%d")] += line.amount or 0.0
        cash_sales_trend = defaultdict(float)
        for line in cash_sales_lines:
            cash_sales_trend[(line.date or fields.Date.today()).strftime("%Y-%m-%d")] += line.amount or 0.0
        cash_outflow_heads = defaultdict(float)
        for line in cash_lines:
            amount = line.amount or 0.0
            if amount < 0:
                cash_outflow_heads[line.head_id.display_name or "Unknown"] += abs(amount)
        bank_sales_trend = defaultdict(float)
        for line in bank_sales_lines:
            bank_sales_trend[(line.date or fields.Date.today()).strftime("%Y-%m-%d")] += line.amount or 0.0
        bank_trend = defaultdict(float)
        for line in bank_lines:
            bank_trend[(line.date or fields.Date.today()).strftime("%Y-%m-%d")] += line.amount or 0.0
        bank_outflow_heads = defaultdict(float)
        for line in bank_lines:
            amount = line.amount or 0.0
            if amount < 0:
                bank_outflow_heads[line.head_id.display_name or "Unknown"] += abs(amount)
        total_outflow_heads = defaultdict(float)
        for label, value in cash_outflow_heads.items():
            total_outflow_heads[label] += value
        for label, value in bank_outflow_heads.items():
            total_outflow_heads[label] += value

        orders_domain = cls._date_domain("date_order", date_from, date_to) + [("company_id", "in", companies.ids), ("state", "not in", ["cancel", "draft"])]
        orders = env["pos.order"].search(orders_domain)
        payments = env["pos.payment"].search([("pos_order_id", "in", orders.ids)]) if orders else env["pos.payment"]
        pos_cash_collected = 0.0
        pos_cash_trend = defaultdict(float)
        pos_bank_collected = 0.0
        pos_bank_trend = defaultdict(float)
        for payment in payments:
            if not payment.payment_method_id:
                continue
            amount = payment.amount or 0.0
            order_date = payment.pos_order_id.date_order if payment.pos_order_id else None
            label = fields.Datetime.to_datetime(order_date).strftime("%Y-%m-%d") if order_date else fields.Date.today().strftime("%Y-%m-%d")
            if getattr(payment.payment_method_id, "is_cash_count", False):
                pos_cash_collected += amount
                pos_cash_trend[label] += amount
                continue
            if cls._is_bank_payment_method(payment.payment_method_id):
                pos_bank_collected += amount
                pos_bank_trend[label] += amount

        cash_match_rows = []
        for label in sorted(set(list(pos_cash_trend.keys()) + list(cash_sales_trend.keys()))):
            pos_amount = round(pos_cash_trend.get(label, 0.0), 2)
            cashbook_amount = round(cash_sales_trend.get(label, 0.0), 2)
            diff = round(cashbook_amount - pos_amount, 2)
            cash_match_rows.append(
                {
                    "date": label,
                    "pos_cash": pos_amount,
                    "cashbook_sales": cashbook_amount,
                    "difference": diff,
                    "status": "Matched" if diff == 0 else "Not Matching",
                }
            )
        bank_match_rows = []
        for label in sorted(set(list(pos_bank_trend.keys()) + list(bank_sales_trend.keys()))):
            pos_amount = round(pos_bank_trend.get(label, 0.0), 2)
            bankbook_amount = round(bank_sales_trend.get(label, 0.0), 2)
            diff = round(bankbook_amount - pos_amount, 2)
            bank_match_rows.append(
                {
                    "date": label,
                    "pos_bank": pos_amount,
                    "bankbook_sales": bankbook_amount,
                    "difference": diff,
                    "status": "Matched" if diff == 0 else "Not Matching",
                }
            )

        return {
            "cash_books": cash_books,
            "bank_books": bank_books,
            "cash_lines": cash_lines,
            "bank_lines": bank_lines,
            "cash_sales_lines": cash_sales_lines,
            "bank_sales_lines": bank_sales_lines,
            "cash_open": cash_open,
            "cash_current": cash_current,
            "bank_open": bank_open,
            "bank_current": bank_current,
            "cash_in": cash_in,
            "cash_out": cash_out,
            "cash_sales_book": cash_sales_book,
            "bank_sales_book": bank_sales_book,
            "bank_in": bank_in,
            "bank_out": bank_out,
            "cash_trend": cash_trend,
            "cash_sales_trend": cash_sales_trend,
            "cash_outflow_heads": cash_outflow_heads,
            "bank_sales_trend": bank_sales_trend,
            "bank_trend": bank_trend,
            "bank_outflow_heads": bank_outflow_heads,
            "total_outflow_heads": total_outflow_heads,
            "pos_cash_collected": pos_cash_collected,
            "pos_cash_trend": pos_cash_trend,
            "pos_bank_collected": pos_bank_collected,
            "pos_bank_trend": pos_bank_trend,
            "cash_gap": round(cash_sales_book - pos_cash_collected, 2),
            "bank_gap": round(bank_sales_book - pos_bank_collected, 2),
            "cash_match_rows": cash_match_rows,
            "bank_match_rows": bank_match_rows,
            "cash_available": round(cash_open + cash_in, 2),
            "bank_available": round(bank_open + bank_in, 2),
            "cash_remaining_pct": round(((cash_current / (cash_open + cash_in)) * 100.0) if (cash_open + cash_in) else 0.0, 2),
            "bank_remaining_pct": round(((bank_current / (bank_open + bank_in)) * 100.0) if (bank_open + bank_in) else 0.0, 2),
            "cash_spent_pct": round(((cash_out / (cash_open + cash_in)) * 100.0) if (cash_open + cash_in) else 0.0, 2),
            "bank_spent_pct": round(((bank_out / (bank_open + bank_in)) * 100.0) if (bank_open + bank_in) else 0.0, 2),
        }

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
            "cash": cls._build_cash,
            "bank": cls._build_bank,
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
        previous_date_from, previous_date_to = cls._previous_period(date_from, date_to, scope.get("period"))
        previous_scope_label = None
        previous_sales = previous_purchases = previous_stock = previous_finance = previous_expenses = None
        if previous_date_from and previous_date_to:
            previous_scope_label = f"{previous_date_from.isoformat()} to {previous_date_to.isoformat()}"
            previous_sales = cls._build_sales(env, companies, scope, previous_date_from, previous_date_to)
            previous_purchases = cls._build_purchases(env, companies, scope, previous_date_from, previous_date_to)
            previous_stock = cls._build_stock(env, companies, scope, previous_date_from, previous_date_to)
            previous_finance = cls._build_finance(env, companies, scope, previous_date_from, previous_date_to)
            previous_expenses = cls._build_expenses(env, companies, scope, previous_date_from, previous_date_to)
        kpis = [
            cls._kpi("pos_sales", "POS Sales", sales["summary"]["sales_amount"], "currency", currency_symbol=currency_symbol),
            cls._kpi("purchase_amount", "Purchase Amount", purchases["summary"]["purchase_amount"], "currency", currency_symbol=currency_symbol),
            cls._kpi("stock_value", "Stock Value", stock["summary"]["inventory_value"], "currency", currency_symbol=currency_symbol),
            cls._kpi("cash_current", "Cash Current", finance["summary"]["cash_current_balance"], "currency", currency_symbol=currency_symbol),
            cls._kpi("bank_current", "Bank Current", finance["summary"]["bank_current_balance"], "currency", currency_symbol=currency_symbol),
            cls._kpi("expense_total", "Expenses", expenses["summary"]["expense_total"], "currency", currency_symbol=currency_symbol),
        ]
        if previous_scope_label:
            comparisons = {
                "pos_sales": previous_sales["summary"]["sales_amount"],
                "purchase_amount": previous_purchases["summary"]["purchase_amount"],
                "stock_value": previous_stock["summary"]["inventory_value"],
                "cash_current": previous_finance["summary"]["cash_current_balance"],
                "bank_current": previous_finance["summary"]["bank_current_balance"],
                "expense_total": previous_expenses["summary"]["expense_total"],
            }
            for kpi in kpis:
                kpi["comparison"] = cls._comparison_meta(kpi["value"], comparisons.get(kpi["key"]), previous_scope_label)
        charts = [
            sales["charts"][0],
            sales["charts"][2],
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
        crm_orders = orders.filtered("crm_sale")
        walking_orders = orders.filtered(lambda order: not order.online_order and not order.crm_sale)
        online_sales_amount = sum(online_orders.mapped("amount_total"))
        crm_sales_amount = sum(crm_orders.mapped("amount_total"))
        walking_sales_amount = sum(walking_orders.mapped("amount_total"))
        online_order_count = len(online_orders)
        crm_order_count = len(crm_orders)
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
                cls._kpi("crm_sales_amount", "CRM POS Sales", crm_sales_amount, "currency", currency_symbol=currency_symbol),
                cls._kpi("walking_sales_amount", "Walking Customer Sales", walking_sales_amount, "currency", currency_symbol=currency_symbol),
                cls._kpi("order_count", "POS Orders", order_count),
                cls._kpi("online_order_count", "Online Orders", online_order_count),
                cls._kpi("crm_order_count", "CRM Orders", crm_order_count),
                cls._kpi("walking_order_count", "Walking Customer Orders", walking_order_count),
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
                cls._chart(
                    "top_selling_products",
                    "Top Selling Products",
                    [{"label": label, "value": round(values["qty"], 2)} for label, values in sorted(product_sales.items(), key=lambda item: item[1]["qty"], reverse=True)[:10]],
                ),
            ],
            "tables": [],
            "summary": {
                "sales_amount": sales_amount,
                "online_sales_amount": online_sales_amount,
                "crm_sales_amount": crm_sales_amount,
                "walking_sales_amount": walking_sales_amount,
                "order_count": order_count,
                "online_order_count": online_order_count,
                "crm_order_count": crm_order_count,
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
        products, uses_valuation_layers = cls._stock_product_snapshot(env, companies)
        movement_by_product, movement_trend = cls._stock_movement_snapshot(env, companies, date_from, date_to)
        currency_symbol = cls._currency_symbol(companies)

        on_hand_qty = sum(item["on_hand_qty"] for item in products.values())
        available_qty = sum(item["available_qty"] for item in products.values())
        inventory_value = sum(item["stock_value"] for item in products.values())
        low_stock = 0
        out_of_stock = 0
        aged_value_61_plus = 0.0
        aged_value_90_plus = 0.0
        aged_qty_61_plus = 0.0
        aged_qty_90_plus = 0.0
        age_value_totals = defaultdict(float)
        age_qty_totals = defaultdict(float)

        for product_id, item in products.items():
            movement = movement_by_product.get(product_id, {})
            item["movement_in_qty"] = movement.get("in_qty", 0.0)
            item["movement_out_qty"] = movement.get("out_qty", 0.0)
            item["movement_total_qty"] = movement.get("total_qty", 0.0)
            item["movement_days"] = len(movement.get("movement_days", set()))
            item["last_movement_date"] = movement.get("last_movement_date")

            qty = item["on_hand_qty"]
            if qty <= 0:
                out_of_stock += 1
            elif qty <= 5:
                low_stock += 1

            aged_value_61_plus += item["old_value_61_plus"]
            aged_value_90_plus += item["old_value_90_plus"]
            aged_qty_61_plus += item["old_qty_61_plus"]
            aged_qty_90_plus += item["old_qty_90_plus"]
            for bucket_key, label, _start, _end in cls._stock_age_buckets():
                age_value_totals[label] += item["bucket_value"].get(bucket_key, 0.0)
                age_qty_totals[label] += item["bucket_qty"].get(bucket_key, 0.0)

        aged_value_pct = (aged_value_61_plus / inventory_value * 100.0) if inventory_value else 0.0
        sorted_products = sorted(products.values(), key=lambda item: item["stock_value"], reverse=True)
        fast_movers = sorted(
            [item for item in sorted_products if item["movement_total_qty"] > 0],
            key=lambda item: (item["movement_total_qty"], item["stock_value"]),
            reverse=True,
        )
        slow_movers = sorted(
            [item for item in sorted_products if item["on_hand_qty"] > 0],
            key=lambda item: (item["movement_total_qty"], -item["stock_value"], item["name"]),
        )

        age_value_chart = [{"label": label, "value": round(age_value_totals[label], 2)} for _key, label, _start, _end in cls._stock_age_buckets()]
        age_qty_chart = [{"label": label, "value": round(age_qty_totals[label], 2)} for _key, label, _start, _end in cls._stock_age_buckets()]
        movement_summary_chart = []
        for label in sorted(movement_trend):
            totals = movement_trend[label]
            movement_summary_chart.append({"label": f"{label} In", "value": round(totals["in"], 2)})
            movement_summary_chart.append({"label": f"{label} Out", "value": round(totals["out"], 2)})

        return {
            "title": "Stock",
            "kpis": [
                cls._kpi("on_hand_qty", "On Hand Quantity", on_hand_qty, "decimal"),
                cls._kpi("available_qty", "Available Quantity", available_qty, "decimal"),
                cls._kpi("inventory_value", "Stock Value", inventory_value, "currency", currency_symbol=currency_symbol),
                cls._kpi("aged_value_61_plus", "Aged Stock 61+ Days", aged_value_61_plus, "currency", currency_symbol=currency_symbol),
                cls._kpi("aged_value_90_plus", "Aged Stock 90+ Days", aged_value_90_plus, "currency", currency_symbol=currency_symbol),
                cls._kpi("aged_value_pct", "Aged Stock %", aged_value_pct, "decimal", suffix="%"),
                cls._kpi("low_stock_count", "Low Stock Items", low_stock),
                cls._kpi("out_of_stock_count", "Out of Stock Items", out_of_stock),
            ],
            "charts": [
                cls._chart("stock_aging_value", "Stock Aging by Value", age_value_chart),
                cls._chart("stock_aging_qty", "Stock Aging by Quantity", age_qty_chart),
                cls._chart(
                    "high_movement_products",
                    "High Movement Products",
                    [{"label": item["name"], "value": round(item["movement_total_qty"], 2)} for item in fast_movers[:20]],
                    preview_limit=8,
                ),
                cls._chart(
                    "slow_moving_stock",
                    "Slow Moving Stock Value",
                    [{"label": item["name"], "value": round(item["stock_value"], 2)} for item in slow_movers[:20]],
                    preview_limit=8,
                ),
                cls._chart("stock_flow_trend", "Stock In / Out Trend", movement_summary_chart, preview_limit=10),
            ],
            "tables": [
                cls._table(
                    "stock_aging_detail",
                    "Top Aged Stock",
                    [
                        {"key": "product", "label": "Product"},
                        {"key": "category", "label": "Category"},
                        {"key": "on_hand_qty", "label": "On Hand Qty"},
                        {"key": "stock_value", "label": "Stock Value"},
                        {"key": "aged_61_plus", "label": "61+ Days Value"},
                        {"key": "aged_90_plus", "label": "90+ Days Value"},
                        {"key": "dominant_bucket", "label": "Main Age Bucket"},
                    ],
                    [
                        {
                            "product": item["name"],
                            "category": item["category"],
                            "on_hand_qty": cls._format_decimal(item["on_hand_qty"]),
                            "stock_value": cls._format_currency_value(item["stock_value"], currency_symbol),
                            "aged_61_plus": cls._format_currency_value(item["old_value_61_plus"], currency_symbol),
                            "aged_90_plus": cls._format_currency_value(item["old_value_90_plus"], currency_symbol),
                            "dominant_bucket": item["dominant_bucket"],
                        }
                        for item in sorted(sorted_products, key=lambda row: row["old_value_61_plus"], reverse=True)[:20]
                    ],
                ),
                cls._table(
                    "fast_moving_products",
                    "Fast Moving Products",
                    [
                        {"key": "product", "label": "Product"},
                        {"key": "current_qty", "label": "Current Qty"},
                        {"key": "current_value", "label": "Current Value"},
                        {"key": "in_qty", "label": "In Qty"},
                        {"key": "out_qty", "label": "Out Qty"},
                        {"key": "total_qty", "label": "Total Movement"},
                    ],
                    [
                        {
                            "product": item["name"],
                            "current_qty": cls._format_decimal(item["on_hand_qty"]),
                            "current_value": cls._format_currency_value(item["stock_value"], currency_symbol),
                            "in_qty": cls._format_decimal(item["movement_in_qty"]),
                            "out_qty": cls._format_decimal(item["movement_out_qty"]),
                            "total_qty": cls._format_decimal(item["movement_total_qty"]),
                        }
                        for item in fast_movers[:20]
                    ],
                ),
                cls._table(
                    "slow_moving_products",
                    "Slow / Low Movement Products",
                    [
                        {"key": "product", "label": "Product"},
                        {"key": "current_qty", "label": "Current Qty"},
                        {"key": "current_value", "label": "Current Value"},
                        {"key": "movement_qty", "label": "Movement Qty"},
                        {"key": "movement_days", "label": "Active Days"},
                        {"key": "dominant_bucket", "label": "Main Age Bucket"},
                    ],
                    [
                        {
                            "product": item["name"],
                            "current_qty": cls._format_decimal(item["on_hand_qty"]),
                            "current_value": cls._format_currency_value(item["stock_value"], currency_symbol),
                            "movement_qty": cls._format_decimal(item["movement_total_qty"]),
                            "movement_days": item["movement_days"],
                            "dominant_bucket": item["dominant_bucket"],
                        }
                        for item in sorted(
                            slow_movers,
                            key=lambda row: (row["movement_total_qty"], -row["stock_value"]),
                        )[:20]
                    ],
                ),
            ],
            "summary": {
                "inventory_value": inventory_value,
                "on_hand_qty": on_hand_qty,
                "available_qty": available_qty,
                "low_stock": low_stock,
                "out_of_stock": out_of_stock,
                "aged_value_61_plus": aged_value_61_plus,
                "aged_value_90_plus": aged_value_90_plus,
                "aged_value_pct": aged_value_pct,
                "aged_qty_61_plus": aged_qty_61_plus,
                "aged_qty_90_plus": aged_qty_90_plus,
                "uses_valuation_layers": uses_valuation_layers,
            },
        }

    @classmethod
    def _build_finance(cls, env, companies, scope, date_from, date_to):
        finance = cls._finance_data(env, companies, date_from, date_to)
        currency_symbol = cls._currency_symbol(companies)
        cash_outflow_items = cls._top_breakdown_items(finance["cash_outflow_heads"], aggregate_others=False)
        bank_outflow_items = cls._top_breakdown_items(finance["bank_outflow_heads"], aggregate_others=False)
        combined_outflow_items = cls._top_breakdown_items(finance["total_outflow_heads"], aggregate_others=False)

        return {
            "title": "Finance",
            "kpis": [
                cls._kpi("cash_open", "Cash Opening", finance["cash_open"], "currency", currency_symbol=currency_symbol),
                cls._kpi("cash_current", "Cash Current", finance["cash_current"], "currency", currency_symbol=currency_symbol),
                cls._kpi("bank_open", "Bank Opening", finance["bank_open"], "currency", currency_symbol=currency_symbol),
                cls._kpi("bank_current", "Bank Current", finance["bank_current"], "currency", currency_symbol=currency_symbol),
                cls._kpi("cash_in", "Cash Inflow", finance["cash_in"], "currency", currency_symbol=currency_symbol),
                cls._kpi("cash_out", "Cash Outflow", finance["cash_out"], "currency", currency_symbol=currency_symbol),
                cls._kpi("bank_in", "Bank Inflow", finance["bank_in"], "currency", currency_symbol=currency_symbol),
                cls._kpi("bank_out", "Bank Outflow", finance["bank_out"], "currency", currency_symbol=currency_symbol),
                cls._kpi("cash_spent_pct", "Cash Spent", finance["cash_spent_pct"], "decimal", suffix="%"),
                cls._kpi("cash_remaining_pct", "Cash Remaining", finance["cash_remaining_pct"], "decimal", suffix="%"),
                cls._kpi("bank_spent_pct", "Bank Spent", finance["bank_spent_pct"], "decimal", suffix="%"),
                cls._kpi("bank_remaining_pct", "Bank Remaining", finance["bank_remaining_pct"], "decimal", suffix="%"),
            ],
            "charts": [
                cls._chart(
                    "cash_position_summary",
                    "Cash Position Summary",
                    [
                        {"label": "Opening", "value": round(finance["cash_open"], 2)},
                        {"label": "Inflow", "value": round(finance["cash_in"], 2)},
                        {"label": "Outflow", "value": round(finance["cash_out"], 2)},
                        {"label": "Current", "value": round(finance["cash_current"], 2)},
                    ],
                ),
                cls._chart(
                    "cash_outflow_heads",
                    "Where Cash Went",
                    cash_outflow_items,
                    preview_limit=8,
                ),
                cls._chart(
                    "bank_position_summary",
                    "Bank Position Summary",
                    [
                        {"label": "Opening", "value": round(finance["bank_open"], 2)},
                        {"label": "Inflow", "value": round(finance["bank_in"], 2)},
                        {"label": "Outflow", "value": round(finance["bank_out"], 2)},
                        {"label": "Current", "value": round(finance["bank_current"], 2)},
                    ],
                ),
                cls._chart(
                    "bank_outflow_heads",
                    "Where Bank Went",
                    bank_outflow_items,
                    preview_limit=8,
                ),
                cls._chart(
                    "total_outflow_heads",
                    "Overall Money Outflow Areas",
                    combined_outflow_items,
                    preview_limit=8,
                ),
            ],
            "tables": [],
            "summary": {
                "cash_current_balance": finance["cash_current"],
                "bank_current_balance": finance["bank_current"],
                "cash_open_balance": finance["cash_open"],
                "bank_open_balance": finance["bank_open"],
                "cash_spent_pct": finance["cash_spent_pct"],
                "cash_remaining_pct": finance["cash_remaining_pct"],
                "bank_spent_pct": finance["bank_spent_pct"],
                "bank_remaining_pct": finance["bank_remaining_pct"],
            },
        }

    @classmethod
    def _build_cash(cls, env, companies, scope, date_from, date_to):
        finance = cls._finance_data(env, companies, date_from, date_to)
        currency_symbol = cls._currency_symbol(companies)
        cash_outflow_items = cls._top_breakdown_items(finance["cash_outflow_heads"], aggregate_others=False)
        return {
            "title": "Cash",
            "kpis": [
                cls._kpi("cash_open", "Cash Opening", finance["cash_open"], "currency", currency_symbol=currency_symbol),
                cls._kpi("cash_current", "Cash Current", finance["cash_current"], "currency", currency_symbol=currency_symbol),
                cls._kpi("cash_in", "Cash Inflow", finance["cash_in"], "currency", currency_symbol=currency_symbol),
                cls._kpi("cash_out", "Cash Outflow", finance["cash_out"], "currency", currency_symbol=currency_symbol),
                cls._kpi("cash_spent_pct", "Cash Spent", finance["cash_spent_pct"], "decimal", suffix="%"),
                cls._kpi("cash_remaining_pct", "Cash Remaining", finance["cash_remaining_pct"], "decimal", suffix="%"),
                cls._kpi("pos_cash_collected", "POS Cash Collected", finance["pos_cash_collected"], "currency", currency_symbol=currency_symbol),
                cls._kpi("cashbook_sales", "Cashbook Sales Head", finance["cash_sales_book"], "currency", currency_symbol=currency_symbol),
                cls._kpi("cash_gap", "Sales Match Gap", finance["cash_gap"], "currency", currency_symbol=currency_symbol),
            ],
            "charts": [
                cls._chart(
                    "cash_position_summary",
                    "Cash Position Summary",
                    [
                        {"label": "Opening", "value": round(finance["cash_open"], 2)},
                        {"label": "Inflow", "value": round(finance["cash_in"], 2)},
                        {"label": "Outflow", "value": round(finance["cash_out"], 2)},
                        {"label": "Current", "value": round(finance["cash_current"], 2)},
                    ],
                ),
                cls._chart(
                    "cash_outflow_heads",
                    "Where Cash Went",
                    cash_outflow_items,
                    preview_limit=8,
                ),
            ],
            "tables": [
                cls._table(
                    "cash_sales_match",
                    "POS Cash vs Cashbook Sales Head",
                    [
                        {"key": "date", "label": "Date"},
                        {"key": "pos_cash", "label": "POS Cash"},
                        {"key": "cashbook_sales", "label": "Cashbook Sales"},
                        {"key": "difference", "label": "Difference"},
                        {"key": "status", "label": "Status"},
                    ],
                    finance["cash_match_rows"],
                ),
            ],
            "summary": {
                "cash_current_balance": finance["cash_current"],
                "cash_open_balance": finance["cash_open"],
                "cash_inflow": finance["cash_in"],
                "cash_outflow": finance["cash_out"],
                "cash_spent_pct": finance["cash_spent_pct"],
                "cash_remaining_pct": finance["cash_remaining_pct"],
                "pos_cash_collected": finance["pos_cash_collected"],
                "cashbook_sales": finance["cash_sales_book"],
                "cash_gap": finance["cash_gap"],
            },
        }

    @classmethod
    def _build_bank(cls, env, companies, scope, date_from, date_to):
        finance = cls._finance_data(env, companies, date_from, date_to)
        currency_symbol = cls._currency_symbol(companies)
        bank_outflow_items = cls._top_breakdown_items(finance["bank_outflow_heads"], aggregate_others=False)
        return {
            "title": "Bank",
            "kpis": [
                cls._kpi("bank_open", "Bank Opening", finance["bank_open"], "currency", currency_symbol=currency_symbol),
                cls._kpi("bank_current", "Bank Current", finance["bank_current"], "currency", currency_symbol=currency_symbol),
                cls._kpi("bank_in", "Bank Inflow", finance["bank_in"], "currency", currency_symbol=currency_symbol),
                cls._kpi("bank_out", "Bank Outflow", finance["bank_out"], "currency", currency_symbol=currency_symbol),
                cls._kpi("bank_spent_pct", "Bank Spent", finance["bank_spent_pct"], "decimal", suffix="%"),
                cls._kpi("bank_remaining_pct", "Bank Remaining", finance["bank_remaining_pct"], "decimal", suffix="%"),
                cls._kpi("pos_bank_collected", "POS Bank Collected", finance["pos_bank_collected"], "currency", currency_symbol=currency_symbol),
                cls._kpi("bankbook_sales", "Bankbook Sales Head", finance["bank_sales_book"], "currency", currency_symbol=currency_symbol),
                cls._kpi("bank_gap", "Sales Match Gap", finance["bank_gap"], "currency", currency_symbol=currency_symbol),
            ],
            "charts": [
                cls._chart(
                    "bank_position_summary",
                    "Bank Position Summary",
                    [
                        {"label": "Opening", "value": round(finance["bank_open"], 2)},
                        {"label": "Inflow", "value": round(finance["bank_in"], 2)},
                        {"label": "Outflow", "value": round(finance["bank_out"], 2)},
                        {"label": "Current", "value": round(finance["bank_current"], 2)},
                    ],
                ),
                cls._chart(
                    "bank_outflow_heads",
                    "Where Bank Went",
                    bank_outflow_items,
                    preview_limit=8,
                ),
            ],
            "tables": [
                cls._table(
                    "bank_sales_match",
                    "POS Bank vs Bankbook Sales Head",
                    [
                        {"key": "date", "label": "Date"},
                        {"key": "pos_bank", "label": "POS Bank"},
                        {"key": "bankbook_sales", "label": "Bankbook Sales"},
                        {"key": "difference", "label": "Difference"},
                        {"key": "status", "label": "Status"},
                    ],
                    finance["bank_match_rows"],
                ),
            ],
            "summary": {
                "bank_current_balance": finance["bank_current"],
                "bank_open_balance": finance["bank_open"],
                "bank_inflow": finance["bank_in"],
                "bank_outflow": finance["bank_out"],
                "bank_spent_pct": finance["bank_spent_pct"],
                "bank_remaining_pct": finance["bank_remaining_pct"],
                "pos_bank_collected": finance["pos_bank_collected"],
                "bankbook_sales": finance["bank_sales_book"],
                "bank_gap": finance["bank_gap"],
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
