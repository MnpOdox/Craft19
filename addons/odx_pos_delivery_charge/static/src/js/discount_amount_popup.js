import { NumberPopup } from "@point_of_sale/app/components/popups/number_popup/number_popup";
import { onMounted, useRef, useState } from "@odoo/owl";

export class DiscountAmountPopup extends NumberPopup {
    static template = "odx_pos_delivery_charge.DiscountAmountPopup";
    static props = {
        ...NumberPopup.props,
        showRefundToggle: { type: Boolean, optional: true },
        defaultRefundPositive: { type: Boolean, optional: true },
    };

    setup() {
        super.setup();
        this.amountInputRef = useRef("amountInput");
        this.refundState = useState({
            refundPositive: !!this.props.defaultRefundPositive,
        });
        onMounted(() => {
            const input = this.amountInputRef.el;
            if (input) {
                input.focus();
                input.select();
            }
        });
    }

    toggleRefundPositive() {
        this.refundState.refundPositive = !this.refundState.refundPositive;
    }

    onInput(ev) {
        this.state.buffer = ev.target.value;
        this.numberBuffer.set(this.state.buffer);
    }

    confirm() {
        this.props.getPayload({
            value: this.state.buffer,
            refundPositive: this.refundState.refundPositive,
        });
        this.props.close();
    }
}
