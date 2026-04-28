import { patch } from "@web/core/utils/patch";
import { ProductCard } from "@point_of_sale/app/components/product_card/product_card";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";

patch(ProductCard.prototype, {
    setup() {
        if (super.setup) {
            super.setup(...arguments);
        }
        this.pos = usePos();
    },

    get showProductPrice() {
        return Boolean(this.pos?.config?.show_product_price);
    },

    get displayPrice() {
        const product = this.props.product;
        if (!product) {
            return "";
        }
        const order = this.pos?.getOrder?.();
        const pricelist = order?.pricelist_id || this.pos?.config?.pricelist_id || false;
        const variant = product.product_variant_ids?.[0] || false;
        const price = product.getPrice?.(pricelist, 1, 0, false, variant) ?? product.list_price ?? 0;
        return this.env.utils.formatCurrency(price);
    },
});
