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
