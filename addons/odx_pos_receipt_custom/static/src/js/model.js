import { PosOrderline } from "@point_of_sale/app/models/pos_order_line";
import { patch } from "@web/core/utils/patch";

patch(PosOrderline.prototype, {
    getReceiptNote() {
        if (!this.note) {
            return "";
        }
        if (typeof this.note !== "string") {
            return String(this.note);
        }
        try {
            const parsed = JSON.parse(this.note);
            if (Array.isArray(parsed)) {
                return parsed.join(", ");
            }
            return String(parsed || "");
        } catch {
            return this.note;
        }
    },
});
