"""
Single source of truth for this connector's registration metadata.
Consumed by setup/install.py → upserted into alaiy_os's OS Connector Registry.
"""

connector_meta = {
    "connector_id": "google_sheets",
    "connector_name": "Google Sheets",
    "connector_app": "alaiy_os_connector_google_sheets",
    # Neither "channel" nor "supplier" really fits a bidirectional doctype<->
    # sheet sync -- "channel" is the closer of the two registry-defined
    # options (an external surface data flows to/from, not a source we buy
    # inventory from), kept only because the registry doesn't model a third
    # kind yet.
    "connector_type": "channel",
    "description": "Sync Alaiy OS records with a Google Sheet, two-way",
    "icon": "sheet",
    "icon_url": "",
    "settings_doctype": "Google Sheets Connector Settings",
    "test_method": "alaiy_os_connector_google_sheets.api.test_connection.test_connection",
    # The registry exposes two sync "slots" -- mapped here to this
    # connector's two real directions, not a generic pull/push.
    "sync_categories_method": "alaiy_os_connector_google_sheets.api.sync.trigger_pull_sync",
    "sync_items_method": "alaiy_os_connector_google_sheets.api.sync.trigger_push_sync",
    "sync_status_method": "alaiy_os_connector_google_sheets.api.sync.get_sync_status",
    "sync_categories_label": "Sheets -> Alaiy OS",
    "sync_items_label": "Alaiy OS -> Sheets",
    "is_enabled": 0,
    "connection_status": "untested",
}
