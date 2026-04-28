/** @odoo-module **/

import { PosStore } from "@point_of_sale/app/services/pos_store";
import { patch } from "@web/core/utils/patch";

const ACTIVITY_EVENTS = ["click", "mousemove", "mousedown", "keydown", "touchstart", "scroll"];

patch(PosStore.prototype, {
    async afterProcessServerData() {
        const result = await super.afterProcessServerData(...arguments);
        this._setupAutoLockTimer();
        return result;
    },

    _setupAutoLockTimer() {
        if (this._autoLockSetupDone) {
            this._resetAutoLockTimer();
            return;
        }

        this._autoLockSetupDone = true;
        this._autoLockActivityHandler = () => this._resetAutoLockTimer();
        for (const eventName of ACTIVITY_EVENTS) {
            window.addEventListener(eventName, this._autoLockActivityHandler, { passive: true });
        }
        this._resetAutoLockTimer();
    },

    _resetAutoLockTimer() {
        clearTimeout(this._autoLockTimer);

        const minutes = Number(this.config?.auto_lock_minutes || 0);
        if (!this.config?.auto_lock_enabled || !minutes || minutes <= 0) {
            return;
        }

        this._autoLockTimer = setTimeout(() => this._triggerAutoLock(), minutes * 60 * 1000);
    },

    _triggerAutoLock() {
        if (!this.config?.auto_lock_enabled || !this.config?.module_pos_hr) {
            return;
        }
        if (this.router?.state?.current === "LoginScreen") {
            return;
        }
        if (typeof this.showLoginScreen === "function") {
            this.showLoginScreen();
        }
    },
});
