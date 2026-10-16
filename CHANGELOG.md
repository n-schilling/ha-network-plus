# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow [Semantic Versioning](https://semver.org/).

## [0.1.0] - unreleased

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
