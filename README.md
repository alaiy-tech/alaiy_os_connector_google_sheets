# Alaiy OS Connector — Google Sheets

A [Frappe](https://frappeframework.com) app that syncs Alaiy OS doctypes with Google Sheets, two-way. Built from `alaiy_os_connector_template`, so it plugs into the Alaiy OS workspace, sidebar, and Connector Registry the same way every other connector does.

**Status:** Google OAuth (#1), the Mapping doctype (#2), Frappe → Sheets push sync (#3), and Sheets → Frappe pull sync (#4) are all done. Conflict detection (#5) is not implemented yet — see [sheets.txt](../sheets.txt) for the full feature spec this connector is being built against.

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

- **Google OAuth** (done, #1) — `google_sheets/oauth.py` implements the real OAuth2 authorization-code flow (consent screen redirect, callback, refresh-token storage, proactive access-token refresh), replacing the template's static Bearer-token fields entirely.
- **A Mapping doctype** (done, #2) — `Google Sheets Mapping` (doctype → Spreadsheet ID + tab → ID Column (matching key) → `Google Sheets Field Map` child table rows). Validates the source doctype's fields are real and, once enabled, that the Sheet/tab are actually reachable via the connected account.
- **Frappe → Sheets push sync** (done, #3) — `run_push_sync` mirrors every record of a mapping's source doctype into its Sheet range (mapped fields plus the ID Column), diffing before writing to skip a no-op API call. Retries transient Google API errors with exponential backoff.
- **Sheets → Frappe pull sync** (done, #4) — `run_pull_sync` reads the mapped range, matches each row to a record via the ID Column, and applies every field marked "Editable from Sheet" to that record through normal `doc.save()` validation/permissions. A row with no matching record is skipped and counted, never auto-created; a row that fails validation is caught, logged, and doesn't stop the rest of the run.
- **Conflict detection** (not started, #5): if a record changed on both sides since the last sync, flag it in the Sync Log rather than silently overwriting either side.

## File reference

| Path | Role |
|---|---|
| `hooks.py` | App manifest — name/dependencies, install/migrate hooks, sidebar log registration, scheduler cron. |
| `connector_meta.py` | Single source of truth for this connector's `OS Connector Registry` row. |
| `setup/install.py` | `after_install`, `sync_connector_registry`, `setup_custom_fields` (first-enable only), plus reusable Single-doctype migration helpers. |
| `api/test_connection.py` | Whitelisted reachability check — wired as `connector_meta["test_method"]`. Checks the OAuth connection is live. |
| `api/sync.py` | Whitelisted trigger/status endpoints the connector card and settings form call — delegates to `google_sheets/sync.py`. |
| `google_sheets/oauth.py` | Google OAuth2 flow: authorization URL, callback (token exchange), proactive access-token refresh, disconnect/revoke. |
| `google_sheets/client.py` | Google Sheets API v4 client, authenticated via `oauth.py`'s access token — read/write/append cell ranges, read spreadsheet metadata. |
| `google_sheets/sync.py` | `run_pull_sync` (Sheets → Frappe) / `run_push_sync` (Frappe → Sheets) — both implemented, fan out over every enabled mapping, one Sync Log per mapping per run. |
| `google_sheets/sync_jobs.py` | Scheduler entry point — decides what's due and enqueues it. |
| `alaiy_os_connector_google_sheets/doctype/google_sheets_connector_settings/` | Single DocType: connected Google account, refresh token, sync intervals. `.js` mounts the shared connector card, a primary Connect/Disconnect button, and Actions buttons. |
| `alaiy_os_connector_google_sheets/doctype/google_sheets_mapping/` | One row per doctype↔Sheet mapping: source doctype, unique-id field + its dedicated ID Column, Spreadsheet ID/tab, and the `Google Sheets Field Map` child table. Validates fields are real, the ID Column isn't reused as a data column, and the Sheet is reachable. |
| `alaiy_os_connector_google_sheets/doctype/google_sheets_field_map/` | Child table row: one doctype field ↔ one Sheet column, plus whether it's editable from the Sheet side (pull sync only writes fields flagged here — issue #8's field-level permission mapping). |
| `alaiy_os_connector_google_sheets/doctype/google_sheets_sync_log/` | One row per sync run, with a `mapping` link field; `.js` list view color-codes status. |
| `.pre-commit-config.yaml`, `.eslintrc`, `.editorconfig`, `pyproject.toml` | Lint/format tooling (ruff, eslint, prettier, pyupgrade). |

---

## Before you ship it

- [ ] `bench --site <site> install-app alaiy_os_connector_google_sheets`
- [ ] `bench --site <site> migrate` — confirm the registry row appears under OS Settings → Connectors and the Sync Log link appears under Logs.
- [ ] `bench build --app alaiy_os_connector_google_sheets`
- [x] Real Google OAuth (Connect/Disconnect, refresh-token flow).
- [x] Mapping doctype (source doctype, Sheet ID/tab, field map, reachability validation).
- [x] Push sync runs manually and produces a `success` log with sane counters.
- [ ] Pull sync runs manually and produces a `success` log with sane counters.
- [x] Test Connection succeeds against a real Google account and the connector card flips to "Connected".
- [ ] Conflict detection (#5).
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
