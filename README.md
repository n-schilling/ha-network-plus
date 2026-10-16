# Network Plus for Home Assistant

[![Release](https://img.shields.io/github/v/release/n-schilling/ha-network-plus)](https://github.com/n-schilling/ha-network-plus/releases)
[![CI](https://github.com/n-schilling/ha-network-plus/actions/workflows/ci.yaml/badge.svg?branch=main)](https://github.com/n-schilling/ha-network-plus/actions/workflows/ci.yaml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![HACS: custom](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)

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

Per uplink a sensor with the IPv4 and one with the IPv6 address of its port (diagnostic, disabled by default: an address in the recorder is your choice). Behind another router this is a private address. The event `public_ip_changed` fires when an address changes, with `family`, `address` and `previous`, so DynDNS or similar can follow. An address that appears or vanishes, for example while an uplink is down, is no change.

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

### Enforce backup uplink (admin features, opt-in)

The integration reads by default and never writes. If you tick **Use admin features** during setup, a second dialog asks for **separate admin credentials**: a local user with the admin role for the Network application, stored apart from the read-only user. Only then the gateway gets the switch **"Enforce backup uplink"**: on switches the preferred uplink off in the console (its WAN type becomes `disabled`), so the gateway fails over to its backup; off switches it on again. That is a way to test the failover without pulling a cable.

The switch guards itself:

- it switches back by itself after **15 minutes** (option *switch back after*, 1 to 1440 minutes), also after a restart of Home Assistant;
- it refuses while the backup already carries the traffic, and from **80 %** of the LTE data plan on (option *refuse from an LTE usage of*);
- errors of the console come back as readable messages.

Mind the data: in a test, about 60 MB per minute ran over LTE while the backup was enforced. The attributes `enforced_since` and `auto_off_at` show what is going on.

## Using the data

The integration reports; it decides nothing. What counts as slow, when you want to be told and what a dashboard shows is yours to set, and Home Assistant's own building blocks are enough for it. The examples use the entity IDs of a gateway named "Gateway" with uplinks named "WAN" and "LTE Failover WAN" (the names UniFi Network gives them) and an LTE device named "LTE backup"; yours will differ.

### A helper that knows which uplink is active

A fixed latency threshold does not work across a failover: a mobile backup is normally several times slower than a wired line, and that alone is no problem. Create a [template helper](https://www.home-assistant.io/integrations/template/) (Settings → Devices & services → Helpers → Create helper → Template → Binary sensor) with a threshold per uplink. Look at a week of the latency sensor's history on each uplink and take about three times the usual value:

```jinja
# State template
{% set uplink = states('sensor.gateway_active_uplink') %}
{% set latency = states('sensor.gateway_internet_latency') | float(0) %}
{{ (uplink == 'wan' and latency > 50) or (uplink == 'wan_lte_failover' and latency > 250) }}

# Availability template
{{ has_value('sensor.gateway_active_uplink') and has_value('sensor.gateway_internet_latency') }}
```

Give it the device class "Problem". The availability template keeps the helper from reporting a problem while the console is unreachable. Leave the helper's own delays empty and put the duration into the automation, where it is visible.

### Automations

**Slow for a while, and all clear again.** With the Companion app, a `tag` makes a new message replace the old one instead of stacking, and `clear_notification` with the same tag removes it once the problem is over:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.internet_slow
    to: "on"
    for: "00:05:00"
    id: slow
  - trigger: state
    entity_id: binary_sensor.internet_slow
    to: "off"
    for: "00:05:00"
    id: normal
actions:
  - choose:
      - conditions:
          - condition: trigger
            id: slow
        sequence:
          - action: notify.mobile_app_phone
            data:
              title: Internet is slow
              message: >-
                {{ states('sensor.gateway_internet_latency') }} ms for 5 minutes
                over {{ state_attr('sensor.gateway_active_uplink', 'uplink_name') }}.
              data:
                tag: internet-slow
      - conditions:
          - condition: trigger
            id: normal
        sequence:
          - action: notify.mobile_app_phone
            data:
              message: clear_notification
              data:
                tag: internet-slow
```

**No internet at all.** The active uplink is `unknown` while no uplink is online. The push reaches the phone once the internet is back; a dashboard in the house shows the state at once.

```yaml
triggers:
  - trigger: state
    entity_id: sensor.gateway_active_uplink
    to: "unknown"
    for: "00:03:00"
```

**Data plan nearly used up.** A `numeric_state` trigger fires once when the value crosses the line. The condition keeps it quiet when the sensor comes back from `unavailable` with a value that is already above it:

```yaml
triggers:
  - trigger: numeric_state
    entity_id: sensor.lte_backup_lte_data_used
    above: 80
conditions:
  - condition: template
    value_template: "{{ trigger.from_state is not none and is_number(trigger.from_state.state) }}"
actions:
  - action: notify.mobile_app_phone
    data:
      message: "LTE backup: {{ states('sensor.lte_backup_lte_data_used') | round(0) }} % of the data plan used."
```

A second trigger with `below: 80` and a `clear_notification` removes the message after the monthly reset.

### Dashboard

Native cards are enough. The tile for the active uplink shows the uplink's name from UniFi Network and how long it has carried the traffic. The conditional card appears only while the backup is active. The gauge uses the same limits as the automation. The history graph is what the latency sensor is for: one line across failovers, with the uplinks' online state underneath.

```yaml
- type: tile
  entity: sensor.gateway_active_uplink
  state_content:
    - uplink_name
    - last_changed
- type: tile
  entity: sensor.gateway_internet_latency
- type: conditional
  conditions:
    - condition: state
      entity: binary_sensor.gateway_backup_uplink_active
      state: "on"
  card:
    type: tile
    entity: sensor.lte_backup_lte_data_used
    name: Backup uplink active – data plan used
    color: orange
- type: gauge
  entity: sensor.lte_backup_lte_data_used
  needle: true
  severity:
    green: 0
    yellow: 80
    red: 95
- type: history-graph
  hours_to_show: 24
  entities:
    - entity: sensor.gateway_internet_latency
    - entity: binary_sensor.gateway_wan
    - entity: binary_sensor.gateway_lte_failover_wan
- type: tile
  entity: event.gateway_internet
  state_content:
    - event_type
    - state
```

With admin features, the switch asks before it acts:

```yaml
- type: tile
  entity: switch.gateway_enforce_backup_uplink
  tap_action:
    action: toggle
    confirmation:
      text: Switch the preferred uplink off in the console?
```

## Requirements

- A UniFi OS console with a gateway (Dream Machine, Cloud Gateway, UXG), UniFi Network 10
- A local user of the console; the read-only role is enough. Accounts of UI.com do not work.

Developed against a Dream Machine with UniFi Network 10.

## Installation

1. In HACS: **⋮ → Custom repositories**, add `https://github.com/n-schilling/ha-network-plus` as type **Integration**.
2. Install **Network Plus** and restart Home Assistant.
3. **Settings → Devices & services → Add integration → Network Plus.**

### Installation parameters

| Parameter | Description |
|---|---|
| Host | IP address or host name of the console, for example 192.168.1.1. |
| Username | A local user of the console. Create one under UniFi OS → Admins & Users with local access only and the read-only role for the Network application; accounts of UI.com do not work. |
| Password | The password of that user. |
| Verify the certificate | On by default. Turn it off only if the console uses its own self-signed certificate; the connection stays encrypted. |
| Use admin features | Off by default. On, a second dialog asks for the admin credentials for the switch "Enforce backup uplink"; see above. |

The entry is named after the gateway. Reconfigure changes the address, the user, the certificate check or the admin opt-in; when the read-only password changes, Home Assistant asks for the new one. The options hold the two limits of "Enforce backup uplink".

## How the data is updated

The console's websocket pushes the gateway's state about once a second; the integration applies a push at once when an uplink, the active uplink, an address or the LTE state changed. Numbers (latency, speed test, data usage, remote clients) are read every 60 seconds. If the console cannot be reached, the entities become unavailable until the next successful read.

## Known limitations

- Only uplinks the gateway monitors get entities; a configured but unused WAN port has none.
- The address sensors show the uplink port's address. Behind another router this is a private address.
- Speed tests are read, not started.
- The only write is the switch "Enforce backup uplink", and only with the admin opt-in.
- Remote clients are those the console lists as connected; of VPN servers only Teleport was tested.
- Events are derived from state changes, not from the console's event log; packet loss and "failed multiple times" are not reported.

## Troubleshooting

- **"The console does not answer"**: check the address, and whether the console has a self-signed certificate (then turn the certificate check off).
- **"The console rejected the username or the password"**: the user must be a local user of the console, not a UI.com account.
- **Entities unavailable**: the console was not reachable at the last read; look at Settings → System → Logs for `network_plus`.
- The diagnostics (⋮ on the integration's page → Download diagnostics) hold no address, user, password, MAC, uplink names or times and can be attached to an issue.

## Removal

Delete the integration under Settings → Devices & services. Nothing is left on the console; the integration never writes to it.

## For developers

[ARCHITECTURE.md](ARCHITECTURE.md) explains the data sources, the push/poll design, the decisions taken on purpose and what is not verified yet.

## License

[Apache 2.0](LICENSE)
