import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { NumberPopup } from "@point_of_sale/app/components/popups/number_popup/number_popup";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

patch(ControlButtons.prototype, {
    async clickDiscount() {
        this.dialog.add(NumberPopup, {
            title: _t("Discount Amount"),
            startingValue: "0",
            getPayload: async (value) => {
                const amount = Number(value) || 0;
                await this.applyAmountDiscount(amount);
            },
        });
    },

    async applyAmountDiscount(amount) {
        const order = this.pos.getOrder();
        const product = this.pos.config.discount_product_id;
        if (!order || !product) {
            this.dialog.add(AlertDialog, {
                title: _t("No discount product found"),
                body: _t(
                    "The discount product is missing. Configure Discount Product in Point of Sale settings."
                ),
            });
            return;
        }

        const value = Math.max(0, Number(amount) || 0);
        const existingDiscount = order.getDiscountLine();
        if (existingDiscount) {
            existingDiscount.delete();
        }
        if (!value) {
            return;
        }

        await this.pos.addLineToCurrentOrder(
            {
                product_id: product,
                product_tmpl_id: product.product_tmpl_id,
                price_unit: -value,
                qty: 1,
            },
            { merge: false }
        );
        this.pos.numpadMode = "price";
    },

    async clickDeliveryCharge() {
        this.dialog.add(NumberPopup, {
            title: _t("Delivery Charge"),
            startingValue: "0",
            getPayload: async (value) => {
                const amount = Number(value) || 0;
                await this.applyDeliveryCharge(amount);
            },
        });
    },

    async applyDeliveryCharge(amount) {
        const order = this.pos.getOrder();
        const product = this.pos.config.del_charge_pro_id;
        if (!order || !product) {
            this.dialog.add(AlertDialog, {
                title: _t("No delivery charge product found"),
                body: _t(
                    "Set a Charge Product in Point of Sale settings to use Delivery Charge."
                ),
            });
            return;
        }

        const value = Math.max(0, Number(amount) || 0);
        order
            .getOrderlines()
            .filter((line) => line.product_id.id === product.id)
            .forEach((line) => line.delete());

        if (!value) {
            return;
        }

        await this.pos.addLineToCurrentOrder(
            {
                product_id: product,
                product_tmpl_id: product.product_tmpl_id,
                price_unit: value,
                qty: 1,
            },
            { merge: false }
        );
        this.pos.numpadMode = "price";
    },
});

patch(ProductScreen.prototype, {
    getNumpadButtons() {
        const buttons = super.getNumpadButtons(...arguments);
        if (this.pos.config.price_button === false) {
            return buttons.map((button) =>
                button.value === "price" ? { ...button, disabled: true } : button
            );
        }
        return buttons;
    },

    onNumpadClick(buttonValue) {
        if (buttonValue === "price" && this.pos.config.price_button === false) {
            return;
        }
        return super.onNumpadClick(buttonValue);
    },
});
