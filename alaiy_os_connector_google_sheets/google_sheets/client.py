# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""
Thin client for the Google Sheets API v4, authenticated via the OAuth2
refresh token stored on Google Sheets Connector Settings (see oauth.py).
Every caller goes through this one place so token refresh stays in a
single spot rather than re-implemented per sync direction.
"""

import requests

from alaiy_os_connector_google_sheets.google_sheets.oauth import get_valid_access_token

_SHEETS_API_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
_REQUEST_TIMEOUT = 30


class GoogleSheetsClient:
    def __init__(self):
        # Raises if no account is connected -- same fail-fast contract the
        # template's Bearer-token client had, just backed by a real OAuth
        # refresh instead of a static token.
        self.access_token = get_valid_access_token()

    def _headers(self):
        return {"Authorization": f"Bearer {self.access_token}"}

    def get_values(self, spreadsheet_id, a1_range):
        """Read cell values for a range (e.g. "Sheet1!A1:Z1000")."""
        resp = requests.get(
            f"{_SHEETS_API_BASE}/{spreadsheet_id}/values/{a1_range}",
            headers=self._headers(),
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("values", [])

    def update_values(self, spreadsheet_id, a1_range, values):
        """Overwrite cell values for a range. values is a list of rows,
        each a list of cell values (Sheets API row-major shape)."""
        resp = requests.put(
            f"{_SHEETS_API_BASE}/{spreadsheet_id}/values/{a1_range}",
            headers=self._headers(),
            params={"valueInputOption": "RAW"},
            json={"values": values},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def append_values(self, spreadsheet_id, a1_range, values):
        """Append rows after the last row with data in the range's sheet --
        used for pushing newly created Frappe records as new Sheet rows."""
        resp = requests.post(
            f"{_SHEETS_API_BASE}/{spreadsheet_id}/values/{a1_range}:append",
            headers=self._headers(),
            params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
            json={"values": values},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def get_spreadsheet_metadata(self, spreadsheet_id):
        """Title + list of tab names -- used to validate a mapping's
        Sheet ID/tab actually exist and are reachable before saving it."""
        resp = requests.get(
            f"{_SHEETS_API_BASE}/{spreadsheet_id}",
            headers=self._headers(),
            params={"fields": "properties.title,sheets.properties.title"},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "title": (data.get("properties") or {}).get("title", ""),
            "tabs": [
                (s.get("properties") or {}).get("title", "")
                for s in data.get("sheets", [])
            ],
        }
