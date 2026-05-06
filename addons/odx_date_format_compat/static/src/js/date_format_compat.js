/** @odoo-module **/

import { registry } from "@web/core/registry";
import {
    formatDate as _formatDate,
    formatDateTime as _formatDateTime,
} from "@web/core/l10n/dates";
import {
    formatDate as origFormatDate,
    formatDateTime as origFormatDateTime,
} from "@web/views/fields/formatters";

// In Odoo 19, list views render dates with toLocaleDateString (e.g. "1 Aug 2024")
// unless the field has options="{'numeric': 1}". This re-registers the date
// and datetime formatters so they ALWAYS use res.lang.date_format / dateTimeFormat
// (e.g. "01/08/2024"), matching the v14 behavior.

function numericFormatDate(value, options = {}) {
    return _formatDate(value, options);
}
numericFormatDate.extractOptions = origFormatDate.extractOptions;

function numericFormatDateTime(value, options = {}) {
    if (options.showTime === false) {
        return _formatDate(value, options);
    }
    return _formatDateTime(value, options);
}
numericFormatDateTime.extractOptions = origFormatDateTime.extractOptions;

const formatters = registry.category("formatters");
formatters.add("date", numericFormatDate, { force: true });
formatters.add("datetime", numericFormatDateTime, { force: true });
