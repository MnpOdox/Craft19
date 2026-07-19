/** @odoo-module */

import { CashMovePopup } from "@point_of_sale/app/components/popups/cash_move_popup/cash_move_popup";
import { patch } from "@web/core/utils/patch";

patch(CashMovePopup.prototype, {
    setup() {
        super.setup(...arguments);
        this.state.posBookHeadId = "";
    },

    get cashBookHeads() {
        return this.pos.models["book.head"]?.getAll() || [];
    },

    onCashBookHeadChange() {
        const head = this.cashBookHeads.find(
            (record) => record.id === Number(this.state.posBookHeadId)
        );
        this.state.reason = head?.head_name || "";
        if (head?.pos_drawer_transfer) {
            this.state.type = "out";
        }
    },

    _prepareTryCashInOutPayload() {
        const result = super._prepareTryCashInOutPayload(...arguments);
        result[result.length - 1] = {
            ...result[result.length - 1],
            pos_book_head_id: Number(this.state.posBookHeadId),
        };
        return result;
    },

    isValidCashMove() {
        return super.isValidCashMove(...arguments) && Boolean(this.state.posBookHeadId);
    },
});
