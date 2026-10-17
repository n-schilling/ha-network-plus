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
# The WAN type to restore when none was recorded (a console freshly set up)
FALLBACK_WAN_TYPE: Final = "dhcp"

# Waiting times of the speed test over the backup uplink, in seconds: live,
# the failover took about 15 s and a speed test well under a minute
FAILOVER_TIMEOUT: Final = 90.0
RESULT_TIMEOUT: Final = 180.0

# The last backup drill (the preferred uplink switched off on purpose), kept
# in the entry so a restart neither forgets it nor loses a running one
CONF_DRILL: Final = "drill"

# The uplink the last speed test ran over, by the test's run time, kept in the
# entry: the gateway names the interface only for a while after the test
CONF_SPEEDTEST_UPLINK: Final = "speedtest_uplink"

# Event on the bus for every finished speed test; the logbook describes it,
# and the speed test sensors write their new state in its context
EVENT_SPEEDTEST: Final = "network_plus_speedtest"

# Options: the link speed a WAN port should negotiate, in Mbit/s, by network group
EXPECTED_SPEED_OPTIONS: Final[dict[str, str]] = {
    "WAN": "expected_speed_wan",
    "WAN2": "expected_speed_wan2",
    "WAN3": "expected_speed_wan3",
    "WAN4": "expected_speed_wan4",
}
MAX_EXPECTED_SPEED: Final = 100_000

# Consecutive failed polls after which a repair issue says the console is unreachable
UNREACHABLE_POLLS: Final = 15

# Consecutive polls with a rejected login before the password is asked for: a
# console in maintenance or a session edge case rejects a login once in a while
AUTH_FAILURES_FOR_REAUTH: Final = 3

# Repair issues
ISSUE_UNREACHABLE: Final = "console_unreachable"
ISSUE_LTE_LIMIT: Final = "lte_limit_disabled"
ISSUE_ADMIN: Final = "admin_not_allowed"
