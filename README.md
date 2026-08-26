# Alaiy OS Connector — Google Sheets

A [Frappe](https://frappeframework.com) app that syncs Alaiy OS doctypes with Google Sheets, two-way. Built from `alaiy_os_connector_template`, so it plugs into the Alaiy OS workspace, sidebar, and Connector Registry the same way every other connector does.

**Status:** scaffolded from the template, renamed throughout. The Google OAuth client, the mapping config, and the real sync logic are not implemented yet — see [sheets.txt](../sheets.txt) for the full feature spec this connector is being built against.

## What a connector is, in this architecture

`alaiy_os` (core) owns one thing connectors all plug into: the **`OS Connector Registry`** DocType, plus a generic API to read/configure/test whatever row is selected (`alaiy_os.api.connectors`). Core never contains connector-specific code. Every connector — this one included — is its own separate Frappe app that:

1. Registers itself into that registry on every `bench migrate` (`connector_meta.py` → `setup/install.py:sync_connector_registry()`).
2. Owns its own settings (a Single DocType holding credentials/config).
3. Owns its own sync logic and log history.
4. Points the registry at its own methods via dotted Python paths — core calls them, but never knows what's inside them.

## Prerequisites

- A Frappe v16 / ERPNext v16 bench with `alaiy_os` already installed (`required_apps = ["alaiy_os", "erpnext"]` in `hooks.py` — bench will refuse to install this app without it).
- Python ≥ 3.14 (see `pyproject.toml`).

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app alaiy_os_connector_google_sheets /path/to/this/repo
bench install-app alaiy_os_connector_google_sheets
bench --site <site> migrate
bench build --app alaiy_os_connector_google_sheets
```

---

## How this differs from a typical pull/push connector

Every other Alaiy OS connector (Shopify, BigCommerce, WooCommerce, ...) syncs a fixed domain (products, orders, stock) against one external catalog API. This connector is generic instead: an admin maps an *arbitrary* Frappe doctype to a Google Sheet tab, field-by-field, and both sides stay in sync. That means a few things the template's default shape doesn't cover, still to be built:

- **Google OAuth**, not a static Bearer token — `google_sheets/client.py` needs a real OAuth2 authorization-code flow (consent screen redirect, callback, refresh-token storage), not the template's `gs_api_url` + `gs_api_token` fields.
- **A Mapping doctype** (new, not part of the template): doctype → Sheet ID + tab → field↔column config, one row per mapped doctype. `run_pull_sync`/`run_push_sync` iterate active mappings instead of talking to one hardcoded resource.
- **Conflict detection**: if a record changed on both sides since the last sync, flag it in the Sync Log rather than silently overwriting either side.
- **Phase 1 scope** (per the spec): one-way Frappe → Sheets mirror for the Settlement Reconciliation doctype only, to validate latency/reliability, before Sheets → Frappe write-back and before generalizing to arbitrary doctypes.

## File reference

| Path | Role |
|---|---|
| `hooks.py` | App manifest — name/dependencies, install/migrate hooks, sidebar log registration, scheduler cron. |
| `connector_meta.py` | Single source of truth for this connector's `OS Connector Registry` row. |
| `setup/install.py` | `after_install`, `sync_connector_registry`, `setup_custom_fields` (first-enable only), plus reusable Single-doctype migration helpers. |
| `api/test_connection.py` | Whitelisted reachability check — wired as `connector_meta["test_method"]`. Still the template's Bearer-token check; needs replacing with a real Google OAuth reachability check. |
| `api/sync.py` | Whitelisted trigger/status endpoints the connector card and settings form call — delegates to `google_sheets/sync.py`. |
| `google_sheets/client.py` | HTTP client — still the template's generic REST client; needs replacing with a Google Sheets API client built on OAuth2 credentials. |
| `google_sheets/sync.py` | `run_pull_sync` (Sheets → Frappe) / `run_push_sync` (Frappe → Sheets) — both still stubs. Real sync logic + the Sync Log queued→running→success/failed lifecycle helpers. |
| `google_sheets/sync_jobs.py` | Scheduler entry point — decides what's due and enqueues it. |
| `alaiy_os_connector_google_sheets/doctype/google_sheets_connector_settings/` | Single DocType: credentials, ERPNext defaults, sync intervals. `.js` mounts the shared connector card and Actions buttons. |
| `alaiy_os_connector_google_sheets/doctype/google_sheets_sync_log/` | One row per sync run; `.js` list view color-codes status. |
| `.pre-commit-config.yaml`, `.eslintrc`, `.editorconfig`, `pyproject.toml` | Lint/format tooling (ruff, eslint, prettier, pyupgrade). |

---

## Before you ship it

- [ ] `bench --site <site> install-app alaiy_os_connector_google_sheets`
- [ ] `bench --site <site> migrate` — confirm the registry row appears under OS Settings → Connectors and the Sync Log link appears under Logs.
- [ ] `bench build --app alaiy_os_connector_google_sheets`
- [ ] Replace the settings fields, client, and test_connection with real Google OAuth.
- [ ] Build the Mapping doctype and wire `run_pull_sync`/`run_push_sync` to iterate its rows.
- [ ] Enable the connector from its settings form; confirm first-enable setup actually ran.
- [ ] Test Connection succeeds against a real Google account and the connector card flips to "Connected".
- [ ] Both sync directions run manually from the Actions menu and produce a `success` log with sane counters.
- [ ] Leave the scheduled interval on for a few cycles and confirm `check_and_enqueue` fires correctly and doesn't double-run.
- [ ] Disable the connector and confirm `_on_disable()` cleans up whatever it should (revoked OAuth grant, etc.).

## Contributing

This app uses `pre-commit` for formatting/linting (ruff, eslint, prettier, pyupgrade):

```bash
cd apps/alaiy_os_connector_google_sheets
pre-commit install
```

## License

AGPL-3.0 (`license.txt`) — matches `app_license` in `hooks.py`.
