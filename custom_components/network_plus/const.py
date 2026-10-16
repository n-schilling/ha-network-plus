"""Constants for Network Plus."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "network_plus"

# The UniFi Network site; consoles with one site call it "default".
SITE: Final = "default"

# The websocket pushes the gateway about once a second. Polling only catches
# what the websocket missed (reconnects, restarts) and refreshes the uplink names.
UPDATE_INTERVAL: Final = timedelta(seconds=60)

# Waiting time before the websocket connects again, doubled per failure.
RECONNECT_MIN: Final = 5.0
RECONNECT_MAX: Final = 300.0

# Admin features: an explicit opt-in with its own credentials, kept apart
# from the read-only ones. Without them the integration never writes.
CONF_USE_ADMIN: Final = "use_admin"
CONF_ADMIN_USERNAME: Final = "admin_username"
CONF_ADMIN_PASSWORD: Final = "admin_password"

# While the backup is enforced: the WAN type to restore and when it started
CONF_ENFORCED_WAN_TYPE: Final = "enforced_wan_type"
CONF_ENFORCED_AT: Final = "enforced_at"

# Options of the "Enforce backup" switch
CONF_AUTO_OFF_MINUTES: Final = "auto_off_minutes"
CONF_LTE_LIMIT_PERCENT: Final = "lte_limit_percent"
DEFAULT_AUTO_OFF_MINUTES: Final = 15
MIN_AUTO_OFF_MINUTES: Final = 1
MAX_AUTO_OFF_MINUTES: Final = 1440
DEFAULT_LTE_LIMIT_PERCENT: Final = 80
MIN_LTE_LIMIT_PERCENT: Final = 1
MAX_LTE_LIMIT_PERCENT: Final = 100

# The WAN type the console uses for an uplink that is switched off
WAN_TYPE_DISABLED: Final = "disabled"
