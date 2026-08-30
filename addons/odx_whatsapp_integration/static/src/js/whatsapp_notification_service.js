/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";

export const whatsappNotificationService = {
    dependencies: ["action", "bus_service", "notification"],

    start(env, { action, bus_service, notification }) {
        const displayed = new Set();
        bus_service.subscribe("odx_whatsapp/new_message", (payload) => {
            if (!payload?.message_id || displayed.has(payload.message_id)) {
                return;
            }
            displayed.add(payload.message_id);
            let removeNotification;
            removeNotification = notification.add(payload.preview || _t("New WhatsApp message"), {
                title: _t("WhatsApp · %s", payload.contact_name || _t("Customer")),
                type: "success",
                sticky: true,
                onClose: () => displayed.delete(payload.message_id),
                buttons: [
                    {
                        name: _t("Open Conversation"),
                        primary: true,
                        onClick: async () => {
                            await action.doAction({
                                type: "ir.actions.client",
                                tag: "odx_whatsapp_integration.Inbox",
                                name: _t("WhatsApp Inbox"),
                                params: { conversation_id: payload.conversation_id },
                            });
                            removeNotification();
                        },
                    },
                    {
                        name: _t("Dismiss"),
                        onClick: () => removeNotification(),
                    },
                ],
            });
        });
        bus_service.start();
    },
};

registry.category("services").add("odx_whatsapp_notification", whatsappNotificationService);
