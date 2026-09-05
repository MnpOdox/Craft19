# ODX WhatsApp Integration

Private WhatsApp Cloud API messaging for Odoo 19 Community CRM.

Configure a WABA account under **WhatsApp → Configuration → Accounts**, then register:

`https://YOUR_ODOO/odx/whatsapp/webhook/ACCOUNT_ID`

Subscribe the Meta app to WhatsApp messages. The webhook requires `X-Hub-Signature-256`. Assign **WhatsApp User** to salespeople and **WhatsApp Manager** to supervisors.

Required account values are the Meta App ID and App Secret, permanent system-user access token, WABA ID, phone-number ID, public HTTPS Odoo URL, sales team, and default country calling code. The access token must have access to the configured WhatsApp Business Account and phone number.

Use **Test Connection**, copy the generated callback URL and verification token into Meta's WhatsApp webhook configuration, subscribe the WABA to the app, and then use **Sync Templates**. Never commit access tokens or app secrets to source control.

Salespeople can only access conversations whose CRM lead is currently assigned to them. Managers can access all conversations. Reassignment transfers the complete conversation and immediately removes the previous salesperson's access.

Free-form text and media require an inbound message within 24 hours. Outside that window, use an approved synchronized template.

## Meta Lead follow-up automation

Open **Meta Leads → Lead Forms**, enable **WhatsApp Follow-Up Automation**, select the business number, and add the approved templates in send order. The first row must use a zero-hour delay; each later row waits its configured hours after the prior successful send. Set **Close After Final Message** for the final no-response deadline.

New Meta leads start the sequence immediately. Any inbound WhatsApp reply cancels pending messages. A lead with no reply is marked Lost with reason **No WhatsApp Response**; a later customer reply restores that same automatically closed lead. Won leads and manually lost leads are never reopened by this automation.
