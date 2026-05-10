import { patch } from "@web/core/utils/patch";
import { TicketScreen } from "@point_of_sale/app/screens/ticket_screen/ticket_screen";

patch(TicketScreen.prototype, {
    async onDoRefund() {
        const sourceOrder = this.getSelectedOrder();
        const manualDiscountLines =
            sourceOrder?.discountLines?.filter(
                (line) => !(line.extra_tax_data?.discount_percentage > 0)
            ) || [];

        await super.onDoRefund(...arguments);

        const destinationOrder = this.pos.getOrder();
        if (!destinationOrder || !manualDiscountLines.length) {
            return;
        }

        destinationOrder
            .getOrderlines()
            .filter((line) => line.isDiscountLine)
            .forEach((line) => line.delete());

        for (const sourceLine of manualDiscountLines) {
            this.pos.models["pos.order.line"].create({
                qty: -Math.abs(sourceLine.qty || 1),
                price_unit: sourceLine.price_unit,
                product_id: sourceLine.product_id,
                order_id: destinationOrder,
                discount: sourceLine.discount,
                tax_ids: sourceLine.tax_ids.map((tax) => ["link", tax]),
                refunded_orderline_id: sourceLine,
                price_type: "automatic",
                attribute_value_ids: sourceLine.attribute_value_ids.map((attr) => ["link", attr]),
            });
        }
    },
});
