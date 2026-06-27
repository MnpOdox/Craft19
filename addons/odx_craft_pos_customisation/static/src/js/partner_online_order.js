import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { patch } from "@web/core/utils/patch";

patch(PosOrder.prototype, {
    setup(vals) {
        super.setup(vals);
        this.online_order = Boolean(vals.online_order);
        this.crm_sale = Boolean(vals.crm_sale);
    },

    serializeForORM(opts = {}) {
        const data = super.serializeForORM(opts);
        data.online_order = Boolean(this.online_order);
        data.crm_sale = Boolean(this.crm_sale);
        return data;
    },
});

patch(PaymentScreen.prototype, {
    toggleOnlineOrder(ev) {
        this.currentOrder.online_order = Boolean(ev.target.checked);
    },

    toggleCrmSale(ev) {
        this.currentOrder.crm_sale = Boolean(ev.target.checked);
    },
});
