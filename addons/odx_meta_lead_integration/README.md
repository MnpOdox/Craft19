# ODX Meta Lead Integration

Odoo 19 Community connector for Facebook and Instagram Lead Ads.

Configure an account, Page, form, CRM field mappings, and sales team under **Meta Leads**. Register this callback in Meta:

`https://YOUR_ODOO/odx/meta/leads/webhook/ACCOUNT_ID`

Subscribe the Page to the `leadgen` webhook field. The callback validates `X-Hub-Signature-256`, retrieves the full lead from Graph API, deduplicates on Meta lead ID, and assigns it round-robin. A 15-minute reconciliation cron catches missed events.

Secrets are limited to Settings administrators. Use a permanent token with the Meta permissions required for Page and Lead Ads access.
