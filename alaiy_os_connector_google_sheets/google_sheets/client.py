# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""
Thin client for the Google Sheets API v4, authenticated via the OAuth2
refresh token stored on Google Sheets Connector Settings (see oauth.py).
Every caller goes through this one place so token refresh stays in a
single spot rather than re-implemented per sync direction.
"""

import time

import requests

from alaiy_os_connector_google_sheets.google_sheets.oauth import get_valid_access_token

_SHEETS_API_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
_REQUEST_TIMEOUT = 30

# Same retry shape as gavindsouza/sheets' fetch_remote_worksheet: transient
# server-side/rate-limit errors get retried with exponential backoff, a real
# client error (403 no access, 404 not found, 400 bad request) fails
# immediately since retrying it can never succeed.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503}
_MAX_RETRIES = 3


class GoogleSheetsClient:
    def __init__(self):
        # Raises if no account is connected -- same fail-fast contract the
        # template's Bearer-token client had, just backed by a real OAuth
        # refresh instead of a static token.
        self.access_token = get_valid_access_token()

    def _headers(self):
        return {"Authorization": f"Bearer {self.access_token}"}

    def _request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", _REQUEST_TIMEOUT)
        kwargs.setdefault("headers", self._headers())
        last_exc = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = requests.request(method, url, **kwargs)
                if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < _MAX_RETRIES - 1:
                    time.sleep(2**attempt)
                    continue
                resp.raise_for_status()
                return resp
            except requests.exceptions.ConnectionError as e:
                # A dropped connection is just as transient as a 5xx --
                # retry it the same way rather than failing the whole sync
                # over one flaky request.
                last_exc = e
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(2**attempt)
                    continue
                raise
        raise last_exc or RuntimeError("Request failed after retries")

    def get_values(self, spreadsheet_id, a1_range):
        """Read cell values for a range (e.g. "Sheet1!A1:Z1000")."""
        resp = self._request("GET", f"{_SHEETS_API_BASE}/{spreadsheet_id}/values/{a1_range}")
        return resp.json().get("values", [])

    def update_values(self, spreadsheet_id, a1_range, values):
        """Overwrite cell values for a range. values is a list of rows,
        each a list of cell values (Sheets API row-major shape)."""
        resp = self._request(
            "PUT",
            f"{_SHEETS_API_BASE}/{spreadsheet_id}/values/{a1_range}",
            params={"valueInputOption": "RAW"},
            json={"values": values},
        )
        return resp.json()

    def append_values(self, spreadsheet_id, a1_range, values):
        """Append rows after the last row with data in the range's sheet --
        used for pushing newly created Frappe records as new Sheet rows."""
        resp = self._request(
            "POST",
            f"{_SHEETS_API_BASE}/{spreadsheet_id}/values/{a1_range}:append",
            params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
            json={"values": values},
        )
        return resp.json()

    def get_spreadsheet_metadata(self, spreadsheet_id):
        """Title + list of tab names -- used to validate a mapping's
        Sheet ID/tab actually exist and are reachable before saving it."""
        resp = self._request(
            "GET",
            f"{_SHEETS_API_BASE}/{spreadsheet_id}",
            params={"fields": "properties.title,sheets.properties.title"},
        )
        data = resp.json()
        return {
            "title": (data.get("properties") or {}).get("title", ""),
            "tabs": [
                (s.get("properties") or {}).get("title", "")
                for s in data.get("sheets", [])
            ],
        }
