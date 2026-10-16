# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow [Semantic Versioning](https://semver.org/).

## [0.1.0] - unreleased

### Added

- Config flow for a UniFi OS console with a local user; reauthentication and reconfiguration.
- Uplink status from the console's websocket, checked by polling every 60 s while nothing changes.
- Binary sensor per uplink (online), "Backup uplink active", sensor "Active uplink", "Last LTE failover".
- Latency and availability per uplink (diagnostic, disabled by default).
- Event entity "Internet": failover, restored, brief failover, uplink down, uplink up.
- Diagnostics without address, user, password, MAC, uplink names or failover times.
