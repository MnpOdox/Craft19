import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { patch } from "@web/core/utils/patch";

function normalizeReceiptNote(note) {
    if (!note) {
        return "";
    }
    if (Array.isArray(note)) {
        return note.map(normalizeReceiptNote).filter(Boolean).join(", ");
    }
    if (typeof note === "object") {
        for (const key of ["text", "label", "name", "value", "note"]) {
            if (typeof note[key] === "string" && note[key].trim()) {
                return note[key];
            }
        }
        return Object.values(note)
            .map(normalizeReceiptNote)
            .filter(Boolean)
            .join(", ");
    }
    if (typeof note !== "string") {
        return String(note);
    }
    try {
        const parsed = JSON.parse(note);
        return normalizeReceiptNote(parsed);
    } catch {
        return note;
    }
}

patch(PosOrderline.prototype, {
    getReceiptNote() {
        return normalizeReceiptNote(this.note);
    },
});

patch(PosOrder.prototype, {
    getSaleTypeLabel() {
        if (this.crm_sale) {
            return "CRM Sale";
        }
        if (this.online_order) {
            return "Online Sale";
        }
        return "Walking Customer";
    },

    export_for_printing(baseUrl, headerData) {
        const data = super.export_for_printing(baseUrl, headerData);
        data.sale_type_label = this.getSaleTypeLabel();
        return data;
    },
});
