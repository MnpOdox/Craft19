import { NumberPopup } from "@point_of_sale/app/components/popups/number_popup/number_popup";
import { useState } from "@odoo/owl";

export class DiscountAmountPopup extends NumberPopup {
    static template = "odx_pos_delivery_charge.DiscountAmountPopup";
    static props = {
        ...NumberPopup.props,
        showRefundToggle: { type: Boolean, optional: true },
        defaultRefundPositive: { type: Boolean, optional: true },
    };

    setup() {
        super.setup();
        this.refundState = useState({
            refundPositive: !!this.props.defaultRefundPositive,
        });
    }

    toggleRefundPositive() {
        this.refundState.refundPositive = !this.refundState.refundPositive;
    }

    confirm() {
        this.props.getPayload({
            value: this.state.buffer,
            refundPositive: this.refundState.refundPositive,
        });
        this.props.close();
    }
}
