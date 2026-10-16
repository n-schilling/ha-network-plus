# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow [Semantic Versioning](https://semver.org/).

## [0.4.0] - 2026-10-16

### Added

- Button "Run speed test" (admin features only): starts the gateway's speed test, the same one UniFi Network runs on its schedule, over the uplink that carries the traffic. Refuses while a test is running. With the switch "Enforce backup uplink" this measures the backup; the README has the recipe and the data warning.
- Attributes `uplink` and `uplink_name` on the four speed test sensors: the uplink the last test ran over, from the interface the gateway reports. Values measured over the backup no longer pass for the main line's.
- Attribute `running` on "Last speed test".

### Changed

- A speed test that starts or finishes is applied from the websocket at once; the result no longer waits for the next poll.

## [0.3.0] - 2026-10-16

### Added

- Sensor "<uplink> link speed" per WAN port (diagnostic): what the port negotiated, in Mbit/s, with `full_duplex` as attribute.
- Option "Expected link speed of WAN 1…4" and, for ports with a value, the binary sensor "<uplink> link slower than expected": on while the port negotiated less, or half duplex.
- Binary sensor "SIM problem" of the LTE device: a PIN the device could not verify, or no PIN tries left.
- Drill protocol of the switch "Enforce backup uplink": sensors "Last backup drill", "Backup drill failover time", "Backup drill restore time", "Backup drill data used", and the event type `drill_completed`. Kept across restarts.
- Repair issues: console not reachable for 15 polls, LTE data limit turned off, admin user cannot write.

### Changed

- README: new section "Using the data" with a latency helper that knows the active uplink, three automations, an LTE signal comparison against the weekly mean, and dashboard cards. It replaces the two examples; the threshold-helper example would have alarmed on every failover to a slower backup.

## [0.2.1] - 2026-10-16

### Changed

- The switch is named "Enforce backup uplink" (German "Backup-Leitung erzwingen"), so nobody takes it for a data backup. Entity IDs stay as they are.

## [0.2.0] - 2026-10-16

### Added

- Admin features as an opt-in at setup, with a separate dialog for admin credentials kept apart from the read-only user.
- Switch "Enforce backup uplink" (admin features only): switches the preferred uplink off in the console so the gateway fails over, and on again. Switches back by itself after a configurable time (default 15 min), refuses while the backup is active or from a configurable LTE usage on (default 80 %).
- Options for the two limits of the switch.

## [0.1.1] - 2026-10-16

### Added

- Brand icon (a globe with a plus badge) for the integration page and HACS.

## [0.1.0] - 2026-10-16

### Added

- Config flow for a UniFi OS console with a local user; reauthentication and reconfiguration.
- Uplink status from the console's websocket, checked by polling every 60 s while nothing changes.
- Binary sensor per uplink (online), "Backup uplink active", sensor "Active uplink", "Last LTE failover".
- Sensor "Internet latency": latency on the uplink that carries the traffic, sampled every 60 s.
- Speed test: download, upload, ping and the time of the last run.
- Public IPv4 and IPv6 per uplink (diagnostic, disabled by default) and the event `public_ip_changed`.
- LTE backup device with data used this month, share of the data plan, the limit and the signal.
- Sensor "VPN clients": connected Teleport and VPN clients with their names.
- Latency and availability per uplink (diagnostic, disabled by default).
- Event entity "Internet": failover, restored, brief failover, uplink down, uplink up, public address changed.
- Diagnostics without addresses, user, password, MAC, uplink or client names or failover times.
