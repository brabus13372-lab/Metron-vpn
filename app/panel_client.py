# DEPRECATED: moved to app/core/panel_client.py
# Kept for backward compatibility — remove after confirming no external imports.
from app.core.panel_client import (  # noqa: F401
    panel_request,
    get_panel_session,
    close_panel_session,
    get_inbound,
    parse_inbound_settings,
    find_client_in_settings,
    update_client_fields,
    deactivate_client,
)
