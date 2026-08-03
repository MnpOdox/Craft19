/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class BooksDashboard extends Component {
    static template = "odx_receivable_automation.BooksDashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ loading: true, data: null });
        onWillStart(() => this.loadDashboard());
    }

    async loadDashboard() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "daily.book.dashboard",
                "get_dashboard_data",
                []
            );
        } finally {
            this.state.loading = false;
        }
    }

    formatAmount(value) {
        const currency = this.state.data?.currency || {};
        const amount = new Intl.NumberFormat("en-IN", {
            minimumFractionDigits: currency.digits ?? 2,
            maximumFractionDigits: currency.digits ?? 2,
        }).format(value || 0);
        return currency.position === "after"
            ? `${amount} ${currency.symbol || ""}`
            : `${currency.symbol || ""} ${amount}`;
    }

    openExpense() {
        return this.action.doAction("odx_receivable_automation.action_daily_expense");
    }

    openCash() {
        return this.action.doAction("odx_receivable_automation.action_daily_cash_transaction");
    }

    openBank() {
        return this.action.doAction("odx_receivable_automation.action_daily_bank_transaction");
    }
}

registry.category("actions").add(
    "odx_receivable_automation.BooksDashboard",
    BooksDashboard
);
