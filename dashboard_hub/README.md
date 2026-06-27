# Dashboard Hub

Standalone multi-region dashboard application for:

- India
  - Craft and Creation
  - Lebeauty
- UAE
  - Craft and Creation UAE

## Structure

- `server.js`
  - Zero-dependency Node server
  - Session-based dashboard login
  - Region-aware proxy/cache for Odoo reporting endpoints
- `public/`
  - Plain HTML/CSS/JS dashboard UI
- `config.example.json`
  - Sample source and login configuration

## Setup

1. Copy `config.example.json` to `config.json`.
2. Update:
   - Odoo server URLs
   - API keys
   - Company IDs
   - Dashboard users
3. Start the app:

```bash
npm start
```

4. Open `http://127.0.0.1:8787`.

## Odoo module

Install `dashboard_hub_api` on each Odoo 19 server and enable it from Settings.

Exposed endpoints:

- `/api/dashboard/overview`
- `/api/dashboard/sales`
- `/api/dashboard/purchases`
- `/api/dashboard/stock`
- `/api/dashboard/finance`
- `/api/dashboard/expenses`
