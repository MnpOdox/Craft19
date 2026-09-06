# ODX Meta Lead Integration

Odoo 19 Community connector for Facebook and Instagram Lead Ads.

Configure an account, Page, form, CRM field mappings, and sales team under **Meta Leads**. Register this callback in Meta:

`https://YOUR_ODOO/odx/meta/leads/webhook/ACCOUNT_ID`

Subscribe the Page to the `leadgen` webhook field. The callback validates `X-Hub-Signature-256`, retrieves the full lead from Graph API, deduplicates on Meta lead ID, and assigns it round-robin. A 15-minute reconciliation cron catches missed events.

When a webhook references a new Instant Form, Odoo automatically creates that form as **Needs Configuration**, adds standard name/email/phone/state mappings, and retains its submissions. Meta's `state` answer is stored in the lead's **Enquiry State / Location** field. A manager selects the sales team, reviews mappings and automation, then clicks **Confirm Configuration & Import Waiting Leads**. Odoo immediately processes the waiting webhook events; reconciliation recovers any additional submissions.

## Ad-level routing

Enable **Route Leads by Meta Ad** on a lead form when the same Instant Form is reused by different advertisements. Odoo identifies every submission by Meta `ad_id`, stores the campaign/ad-set/ad identifiers on the CRM lead, and uses the matching **Meta Leads > Ad Routing** record for the lead title, sales team, round-robin salesperson, source, medium, and Odoo campaign.

Use **Sync Lead Ads** on the Meta account to discover routes immediately. An hourly synchronization also discovers new lead ads automatically. A newly discovered route remains **Needs Configuration** and its submissions stay safely pending until a manager selects its sales team and clicks **Confirm Routing & Import Waiting Leads**. This prevents a new or reused ad from being assigned to the wrong product team.

Secrets are limited to Settings administrators. Use a permanent token with the Meta permissions required for Page and Lead Ads access.

## CRM status feedback

Under **Meta Leads > Configuration > Accounts**, enable **Send CRM Statuses to Meta** and enter the Meta Dataset/Pixel ID and its Conversions API token. Closing a Meta lead as Lost sends a `Lost` CRM event; moving it to a won stage sends `Won`. Delivery is attempted immediately and failed events are retried every five minutes. Administrators can inspect every attempt under **Meta Leads > Status Feedback**.

The optional Test Event Code routes events to Meta Events Manager's test view. Remove the code after verification so production events are processed normally.
