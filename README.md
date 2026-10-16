# Network Plus for Home Assistant

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

> **Work in progress.** Nothing to install yet.

A custom integration that adds what the UniFi integration shipped with Home Assistant does not show. It complements that integration, it does not replace it. This project is not affiliated with or endorsed by Ubiquiti.

## First feature: uplinks and failover

Which internet uplink of the gateway is online, which one carries the traffic, and an event whenever the gateway fails over to its backup (for example LTE) or returns.

The built-in UniFi integration reports latency, but not whether an uplink is down or which one is active. A failover that lasts a few seconds is easy to miss by polling, so this integration listens to the gateway's websocket and also checks the state every minute.

| Entity | What it shows |
|---|---|
| Binary sensor per uplink | Online or offline, as the gateway sees it |
| Sensor "Active uplink" | Which uplink carries the traffic |
| Binary sensor "Backup active" | On while the backup uplink is in use |
| Event "Internet" | Failover, return, packet loss, repeated failures |

If the gateway cannot be reached, the entities become unavailable; they never report an uplink as offline only because Home Assistant lost its connection.

## License

[Apache 2.0](LICENSE)
