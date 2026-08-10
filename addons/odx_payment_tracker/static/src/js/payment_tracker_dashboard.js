/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class PaymentTrackerDashboard extends Component {
    static template = "odx_payment_tracker.Dashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ loading: true, data: null });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call("payment.tracker.schedule", "get_dashboard_data", []);
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
        return currency.position === "after" ? `${amount} ${currency.symbol || ""}` : `${currency.symbol || ""} ${amount}`;
    }

    openPayments() {
        return this.action.doAction("odx_payment_tracker.action_payment_tracker_schedule");
    }

    newPayment() {
        return this.action.doAction({
            type: "ir.actions.act_window",
            name: "New Payment Schedule",
            res_model: "payment.tracker.schedule",
            views: [[false, "form"]],
            target: "current",
        });
    }

    openSchedule(id) {
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "payment.tracker.schedule",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("actions").add("odx_payment_tracker.Dashboard", PaymentTrackerDashboard);
