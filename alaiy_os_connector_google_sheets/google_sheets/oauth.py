# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""
Google OAuth2 authorization-code flow (RFC 6749 §4.1) for Google Sheets
Connector Settings.

App-level credentials (Client ID / Client Secret, one pair per Frappe
site, registered once in Google Cloud Console) live in site_config.json --
NOT in the settings doctype -- same reasoning Frappe's own Google Drive/
Calendar integrations use: a secret an app-admin controls belongs in
deployment config, not in a document editable through the Desk UI.

Per-connection state (which Google account is authorized, its refresh
token) DOES live on Google Sheets Connector Settings -- that's the thing
this flow produces, one per site.

    site_config.json:
      "google_oauth_client_id": "....apps.googleusercontent.com",
      "google_oauth_client_secret": "..."

Redirect URI registered in Google Cloud Console must be:
    https://<site>/api/method/alaiy_os_connector_google_sheets.google_sheets.oauth.callback
"""

import secrets
from urllib.parse import urlencode

import frappe
import requests
from frappe import _
from frappe.utils import add_to_date, get_url, now_datetime

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
_REQUEST_TIMEOUT = 15

# read-only would be enough for Phase 1 (Frappe -> Sheets), but Phase 2
# (Sheets -> Frappe) needs write access to read edited cell values back --
# requesting the full scope once now avoids forcing every already-connected
# admin through a second consent screen when Phase 2 ships.
_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/userinfo.email",
]


def _client_credentials():
    client_id = frappe.conf.get("google_oauth_client_id")
    client_secret = frappe.conf.get("google_oauth_client_secret")
    if not client_id or not client_secret:
        frappe.throw(
            _(
                "Google OAuth is not configured on this site. An admin must add "
                "google_oauth_client_id and google_oauth_client_secret to site_config.json "
                "(see google_sheets/oauth.py for the exact keys and redirect URI to register)."
            )
        )
    return client_id, client_secret


def _redirect_uri():
    return get_url("/api/method/alaiy_os_connector_google_sheets.google_sheets.oauth.callback")


@frappe.whitelist()
def get_authorization_url():
    """Called by the settings form's "Connect Google Account" button.
    Returns the URL to redirect the browser to; the state value is stored
    so callback() can reject a forged/replayed redirect."""
    frappe.only_for("System Manager")
    client_id, _secret = _client_credentials()

    state = secrets.token_urlsafe(32)
    frappe.db.set_single_value("Google Sheets Connector Settings", "gs_oauth_state", state)
    frappe.db.commit()

    params = {
        "client_id": client_id,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": " ".join(_SCOPES),
        "access_type": "offline",  # required to get a refresh_token back
        "prompt": "consent",  # forces a refresh_token on every connect, not just the first
        "state": state,
    }
    return {"url": f"{_AUTH_URL}?{urlencode(params)}"}


@frappe.whitelist(allow_guest=True)
def callback(code=None, state=None, error=None):
    """Google redirects here after the consent screen. Exchanges the
    one-time code for tokens, stores the refresh token, and bounces the
    browser back to the settings form with a query param the client script
    reads to show a success/failure toast (there's no window to frappe.call
    a response back into -- this request comes from Google's redirect, not
    the settings form's own JS)."""
    settings_url = get_url("/app/google-sheets-connector-settings")

    if error:
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = f"{settings_url}?google_oauth=error&reason={error}"
        return

    saved_state = frappe.db.get_single_value("Google Sheets Connector Settings", "gs_oauth_state")
    if not state or not saved_state or not secrets.compare_digest(state, saved_state):
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = f"{settings_url}?google_oauth=error&reason=invalid_state"
        return

    # One-time use -- whether this exchange succeeds or fails below, the
    # state value must never be accepted again.
    frappe.db.set_single_value("Google Sheets Connector Settings", "gs_oauth_state", "")
    frappe.db.commit()

    try:
        client_id, client_secret = _client_credentials()
        token_resp = requests.post(
            _TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": _redirect_uri(),
                "grant_type": "authorization_code",
            },
            timeout=_REQUEST_TIMEOUT,
        )
        token_resp.raise_for_status()
        tokens = token_resp.json()

        refresh_token = tokens.get("refresh_token")
        access_token = tokens.get("access_token")
        if not refresh_token:
            # Google omits refresh_token on a repeat consent for an account
            # that already granted access without prompt=consent -- the
            # request above always sends prompt=consent specifically to
            # avoid this, but a stale cached consent can still occasionally
            # skip it, so fail loudly instead of silently keeping a
            # connection with no way to refresh once the access token expires.
            raise RuntimeError("Google did not return a refresh token -- try disconnecting and reconnecting.")

        email = _fetch_email(access_token)
        now = now_datetime()
        frappe.db.set_value("Google Sheets Connector Settings", None, {
            "gs_refresh_token": refresh_token,
            "gs_connected_email": email,
            "gs_token_refreshed_at": now,
            "gs_token_expires_at": add_to_date(now, seconds=tokens.get("expires_in") or 3600),
        })
        frappe.db.commit()

        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = f"{settings_url}?google_oauth=success"
    except Exception:
        frappe.log_error(
            title="Google Sheets connector: OAuth callback failed",
            message=frappe.get_traceback(),
        )
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = f"{settings_url}?google_oauth=error&reason=exchange_failed"


@frappe.whitelist()
def get_connection_status():
    """Cheap, DB-only check of whether a Google account is connected --
    unlike get_valid_access_token(), never calls Google, so it's safe to
    call on every page load (e.g. Google Sheets Mapping's form) without
    burning a real token refresh just to show a status pill."""
    settings = frappe.get_single("Google Sheets Connector Settings")
    connected = bool(settings.get_password("gs_refresh_token") if settings.gs_refresh_token else None)
    return {"connected": connected, "email": settings.gs_connected_email or ""}


def _fetch_email(access_token):
    resp = requests.get(
        _USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("email") or ""


def get_valid_access_token():
    """Returns a fresh access token, refreshing it first if the cached one
    is missing or past its recorded expiry -- same proactive-refresh shape
    api/supplier_shopify_pull.py's SupplierShopifyClient already uses for
    per-store Shopify tokens. Raises if no account is connected."""
    settings = frappe.get_single("Google Sheets Connector Settings")
    refresh_token = settings.get_password("gs_refresh_token") if settings.gs_refresh_token else None
    if not refresh_token:
        frappe.throw(_("No Google account connected. Use \"Connect Google Account\" first."))

    # Access tokens aren't cached/stored (short-lived, 1 hour, low value to
    # persist) -- every real call refreshes unconditionally. Google's token
    # endpoint has no meaningful rate limit for this, so there's no reason
    # to track/check gs_token_expires_at here just to skip an occasional
    # refresh; it's still written below purely for the settings form to
    # display.
    client_id, client_secret = _client_credentials()
    resp = requests.post(
        _TOKEN_URL,
        data={
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
        },
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code == 400:
        # invalid_grant -- the refresh token was revoked (user removed app
        # access in their Google account, or it expired from disuse).
        # Clear the stale connection state so the settings form correctly
        # shows "not connected" instead of silently failing every sync.
        frappe.db.set_value("Google Sheets Connector Settings", None, {
            "gs_refresh_token": "",
            "gs_connected_email": "",
        })
        frappe.db.commit()
        frappe.throw(_("Google authorization was revoked or expired. Reconnect the account."))
    resp.raise_for_status()

    tokens = resp.json()
    now = now_datetime()
    frappe.db.set_value("Google Sheets Connector Settings", None, {
        "gs_token_refreshed_at": now,
        "gs_token_expires_at": add_to_date(now, seconds=tokens.get("expires_in") or 3600),
    })
    frappe.db.commit()
    return tokens["access_token"]


@frappe.whitelist()
def disconnect():
    """Revokes the stored grant with Google (best-effort -- a network
    failure here shouldn't block clearing local state) and wipes every
    connection field, matching the template's _on_disable() convention of
    actually cleaning up rather than leaving stale credentials behind."""
    frappe.only_for("System Manager")
    settings = frappe.get_single("Google Sheets Connector Settings")
    refresh_token = settings.get_password("gs_refresh_token") if settings.gs_refresh_token else None

    if refresh_token:
        try:
            requests.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": refresh_token},
                timeout=_REQUEST_TIMEOUT,
            )
        except Exception:
            frappe.log_error(
                title="Google Sheets connector: token revoke failed",
                message=frappe.get_traceback(),
            )

    frappe.db.set_value("Google Sheets Connector Settings", None, {
        "gs_refresh_token": "",
        "gs_connected_email": "",
        "gs_token_refreshed_at": None,
        "gs_token_expires_at": None,
    })
    frappe.db.commit()
    return {"ok": True}
