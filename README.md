# Network Plus for Home Assistant

[![CI](https://github.com/n-schilling/ha-network-plus/actions/workflows/ci.yaml/badge.svg?branch=dev)](https://github.com/n-schilling/ha-network-plus/actions/workflows/ci.yaml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

> **Work in progress.** Not released yet.

A custom integration that adds what the UniFi integration shipped with Home Assistant does not show. It complements that integration, it does not replace it. This project is not affiliated with or endorsed by Ubiquiti.

## First feature: uplinks and failover

Which internet uplink of the gateway is online, which one carries the traffic, and an event whenever the gateway fails over to its backup (for example LTE) or returns.

The built-in UniFi integration reports latency, but not whether an uplink is down or which one is active. A failover that lasts a few seconds is easy to miss by polling, so this integration listens to the console's websocket, which sends the gateway's state about once a second. While nothing changes, it also reads the state every 60 seconds.

### Entities

| Entity | What it shows |
|---|---|
| Binary sensor per uplink, named like the uplink in UniFi Network | Online or offline, as the gateway sees it |
| Binary sensor "Backup uplink active" | On while another uplink than the preferred one carries the traffic |
| Sensor "Active uplink" | `wan`, `wan2`, `wan_lte_failover`, …; the attribute `uplink_name` holds the name set in UniFi Network |
| Event "Internet" | `failover`, `restored`, `brief_failover`, `uplink_down`, `uplink_up` |
| Sensor "Last LTE failover" (diagnostic) | When the gateway last switched to or from the LTE backup |
| Latency and availability per uplink (diagnostic, disabled) | The gateway's own monitoring values |

Only uplinks the gateway monitors get entities; a configured but unused WAN port has none. An uplink is reported offline only when the gateway says so. If the console cannot be reached, the entities become unavailable instead.

`brief_failover` fires when the gateway switched to the backup and back between two states the integration saw, for example while Home Assistant restarted.

### Example: notify after a minute on the backup

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.gateway_backup_uplink_active
    to: "on"
    for: "00:01:00"
actions:
  - action: notify.notify
    data:
      message: >-
        Internet runs over {{ state_attr('sensor.gateway_active_uplink', 'uplink_name') }}.
```

## Requirements

- A UniFi OS console with a gateway (Dream Machine, Cloud Gateway, UXG), UniFi Network 10
- A local user of the console; the read-only role is enough. Accounts of UI.com do not work.

Tested with a UDM SE, UniFi OS 5.1, UniFi Network 10.6.

## Setup

Settings → Devices & services → Add integration → Network Plus. Enter the console's address, the local user and its password. Turn off the certificate check only if the console uses a self-signed certificate.

## License

[Apache 2.0](LICENSE)
