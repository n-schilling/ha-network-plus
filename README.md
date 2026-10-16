# Network Plus for Home Assistant

[![CI](https://github.com/n-schilling/ha-network-plus/actions/workflows/ci.yaml/badge.svg?branch=dev)](https://github.com/n-schilling/ha-network-plus/actions/workflows/ci.yaml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

> **Work in progress.** Not released yet.

A custom integration that adds what the UniFi integration shipped with Home Assistant does not show: uplink status and failover, latency, speed test results, public addresses, the LTE data plan and remote clients. It complements that integration, it does not replace it. This project is not affiliated with or endorsed by Ubiquiti.

## What it shows

### Uplinks and failover

Which internet uplink of the gateway is online, which one carries the traffic, and an event whenever the gateway fails over to its backup (for example LTE) or returns.

The built-in UniFi integration reports latency, but not whether an uplink is down or which one is active. A failover that lasts a few seconds is easy to miss by polling, so this integration listens to the console's websocket, which sends the gateway's state about once a second. Numbers (latency, speed test, data usage, remote clients) are sampled every 60 seconds instead, so the recorder is not flooded.

| Entity | What it shows |
|---|---|
| Binary sensor per uplink, named like the uplink in UniFi Network | Online or offline, as the gateway sees it |
| Binary sensor "Backup uplink active" | On while another uplink than the preferred one carries the traffic |
| Sensor "Active uplink" | `wan`, `wan2`, `wan_lte_failover`, …; the attribute `uplink_name` holds the name set in UniFi Network |
| Sensor "Internet latency" | Latency on the uplink that carries the traffic, one series across failovers |
| Event "Internet" | `failover`, `restored`, `brief_failover`, `uplink_down`, `uplink_up`, `public_ip_changed` |
| Sensor "Last LTE failover" (diagnostic) | When the gateway last switched to or from the LTE backup |
| Latency and availability per uplink (diagnostic, disabled) | The gateway's own monitoring values |

Only uplinks the gateway monitors get entities; a configured but unused WAN port has none. An uplink is reported offline only when the gateway says so. If the console cannot be reached, the entities become unavailable instead.

`brief_failover` fires when the gateway switched to the backup and back between two states the integration saw, for example while Home Assistant restarted.

### Speed test

Download, upload and ping of the gateway's last speed test, and when it ran (diagnostic, with the result as attribute `status`). The integration only reads the results; speed tests are started in UniFi Network, by hand or on its schedule.

### Public addresses

Per uplink a sensor with the public IPv4 and one with the IPv6 address of its port (diagnostic, disabled by default: an address in the recorder is your choice). The event `public_ip_changed` fires when an address changes, with `family`, `address` and `previous`, so DynDNS or similar can follow. An address that appears or vanishes, for example while an uplink is down, is no change.

### LTE backup

If the console has an LTE backup device, it gets a device of its own below the gateway:

| Entity | What it shows |
|---|---|
| Sensor "LTE data used" | Share of the data plan used this month, in percent. Unknown while the limit is turned off |
| Sensor "LTE data this month" | Bytes used this month, as the console counts them; the monthly drop is the reset |
| Sensor "LTE data limit" (diagnostic) | The data plan set in UniFi Network; attribute `warning_bytes` is the warning limit. Unknown while the limit is turned off |
| Sensor "LTE signal" (diagnostic, disabled) | RSSI in dBm; attribute `rsrp` |

### Remote clients

Sensor "VPN clients": how many Teleport or VPN clients are connected right now, with their names in the attribute `clients` and the kinds in `types`. Unknown if the console does not offer the client list.

### Example: latency, not failover

A failover to a working backup is not a problem; a slow internet is. A [threshold helper](https://www.home-assistant.io/integrations/threshold/) on the latency sensor gives a binary sensor with hysteresis, for example on above 100 ms, off below 60 ms, and the automation triggers on that:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.internet_slow
    to: "on"
    for: "00:02:00"
actions:
  - action: notify.notify
    data:
      message: >-
        Internet is slow: {{ states('sensor.gateway_internet_latency') }} ms
        over {{ state_attr('sensor.gateway_active_uplink', 'uplink_name') }}.
```

### Example: data plan nearly used up

```yaml
triggers:
  - trigger: numeric_state
    entity_id: sensor.lte_backup_lte_data_used
    above: 80
actions:
  - action: notify.notify
    data:
      message: "LTE backup: {{ states('sensor.lte_backup_lte_data_used') }} % of the data plan used."
```

## Requirements

- A UniFi OS console with a gateway (Dream Machine, Cloud Gateway, UXG), UniFi Network 10
- A local user of the console; the read-only role is enough. Accounts of UI.com do not work.

Developed against a Dream Machine with UniFi Network 10.

## Setup

Settings → Devices & services → Add integration → Network Plus. Enter the console's address, the local user and its password. Turn off the certificate check only if the console uses a self-signed certificate.

## For developers

[ARCHITECTURE.md](ARCHITECTURE.md) explains the data sources, the push/poll design, the decisions taken on purpose and what is not verified yet.

## License

[Apache 2.0](LICENSE)
