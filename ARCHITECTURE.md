# Architecture

How Network Plus is built, which decisions were taken on purpose, and what is
not verified yet. Written for a reviewer; the README says what the integration
does for its users.

## Scope

One config entry is one UniFi OS console with its UniFi Network application.
The integration reads; it never writes to the console. It complements the
UniFi integration that ships with Home Assistant and does not touch its
entities or devices.

## Data sources

All endpoints are the ones the console's own web interface uses; none is a
published API. Every request goes through `NetworkClient._get` in `api.py`, so
a changed interface fails in one place.

| Source | Used for | How often |
|---|---|---|
| `POST /api/auth/login` | Session cookie; a 401 later logs in again once | On demand |
| `GET …/stat/device` | Runtime objects of all devices: the gateway (uplink status, addresses, speed test, monitors) and the LTE device (data usage, limits, signal) | Every 60 s |
| `GET …/v2/api/site/<site>/wan/enriched-configuration` | Names and failover priorities of the uplinks | Every 60 s |
| `GET …/v2/api/site/<site>/clients/active?includeTeleport=true` | Remote clients (Teleport, VPN) | Every 60 s |
| `wss://…/wss/s/<site>/events?clients=v2` | `device:sync` messages with the same runtime objects, about once a second | Continuous |

The LTE data usage is `rx_bytes + tx_bytes` of the LTE device's runtime object.
On the console this was developed against, that sum equals the sum of the
daily values of the console's monthly report to the byte, also across a
reboot of the device, so it is the usage in the current calendar month.

## Data flow

```
websocket ──device:sync──▶ NetworkCoordinator.handle_message ──▶ parse_state ──┐
                                                                               ├─▶ essentials changed? ──▶ async_set_updated_data + events
poll (60 s) ─────────────▶ NetworkCoordinator._async_update_data ──▶ parse_state ──▶ always ──────────────▶ coordinator data + events
```

- `model.py` turns raw objects into frozen dataclasses (`NetworkState`,
  `Uplink`, `Speedtest`, `LteBackup`, `VpnClient`). Only named fields are
  taken over; raw objects never leave the coordinator.
- `detect_events(old, new, switch_seen_at)` derives the events of the event
  entity from two states: uplink down/up, failover/restored, and a brief
  failover (the gateway's transition timestamp moved while the active uplink
  stayed the preferred one; a timestamp older than the last seen switch is
  ignored, because it belongs to a failover that was seen).
  `detect_address_events` compares the public addresses against the last
  address known per uplink and family, so a change that passes through a
  state without an address (PPPoE reconnect, outage) still fires.
- **Push gate:** the websocket sends the gateway about once a second with
  latency values that differ nearly every time. A push updates the state only
  when `essentials()` changed: an uplink's online state or address, the active
  uplink, the transition timestamp, or the LTE connected/in-use flags. All
  numbers (latency, speed test, data usage, remote clients) come from the
  60-second poll. This keeps failovers immediate and the recorder quiet.
- A failed poll makes the entities unavailable (`UpdateFailed`); the next
  poll that succeeds brings them back. Pushes are ignored while the last poll
  failed: the poll decides whether the console is healthy, so a failing poll
  is not hidden behind push data. A rejected login starts the reauth flow. A
  missing clients endpoint only makes the VPN sensor unknown.
- The websocket is reconnected with a delay that doubles per failed attempt
  (5 s to 5 min) and resets as soon as a connection stood, however it ended.

## Entities and devices

- The gateway's device is created in `async_setup_entry` so its registry ID is
  known; the LTE device hangs below it through `via_device_id` (`via_device`
  is deprecated since Home Assistant 2026.10). Both carry their MAC as
  connection, like the devices of the built-in UniFi integration; since
  2026.10 a device belongs to one integration, so they are separate devices.
- Uplink entities exist for every uplink the gateway monitors
  (`last_wan_status`), not for every configured WAN port. Uplinks and the LTE
  device that appear later get their entities without a reload
  (`add_uplink_entities`, `add_lte_entities`).
- An uplink entity is unavailable while the gateway stops reporting that
  uplink; "offline" is reported only when the gateway says so.
- Disabled by default: latency and availability per uplink, the public
  addresses, the LTE signal. Enabled: what a dashboard or automation needs.

## Decisions taken on purpose

- **Own client, not `aiounifi`.** The built-in UniFi integration pins
  `aiounifi`; a second requirement on it could conflict, and this integration
  needs only a login, two GETs and the websocket.
- **Own aiohttp session** via `async_create_clientsession` with
  `CookieJar(unsafe=True)`: the console's login is a cookie, and consoles are
  usually addressed by IP, which the default jar rejects. Home Assistant
  detaches a session created during entry setup when the entry unloads; the
  config flow detaches its own after each check.
- **Certificate check is an option, on by default.** Consoles ship with a
  self-signed certificate; the option exists so nobody has to turn off
  verification globally.
- **No thresholds in code.** "Internet latency" is one series across
  failovers; a threshold helper or an automation decides what is slow. The
  integration sends no notifications.
- **No actions.** Starting a speed test or anything else that writes would
  need more than a read-only user; out of scope for now.
- **Events are derived from state**, not read from the console's event log:
  the log is polled, states arrive by push. Event types the log knows and the
  state does not (packet loss, "failed multiple times") are therefore not
  offered yet.
- **Public addresses** live in disabled diagnostic sensors and in the
  `public_ip_changed` event, so storing an address in the recorder is the
  user's choice. An address that appears or vanishes is no change event.
- **LTE usage** is the console's monthly counter, reported with state class
  `total_increasing`: the statistics take the console's monthly drop as the
  reset, whatever its time zone or billing day. The share of the data plan is
  unknown while the limit is turned off in UniFi Network, even if the console
  keeps the old limit value.
- **Diagnostics** hold no address, user, password, MAC, uplink or client name
  and no times; tests assert it. The LTE runtime object contains the SIM PIN,
  APN credentials, IMEI and ICCID, which is why only named fields are parsed.
- **Fixtures use placeholder data only** (192.0.2.x, 198.51.100.x, 2001:db8::,
  02:… MACs, neutral model names).

## Verified against a provoked outage

The WAN cable of the development console was pulled for about three minutes
while the websocket was recorded. Observed:

- `last_wan_status` goes `online` → `offline` → `online`; the monitors of the
  down uplink keep `availability: 0` each, the summary values disappear.
- `uplink.comment` names `WAN_LTE_FAILOVER` while the gateway routes over
  LTE (interface `gre1`), and `WAN` again afterwards. `uplink.latency` does
  not describe the LTE path then (32 ms next to 156 ms from the monitor), so
  the monitor's value of the active uplink is used and `uplink.latency` is
  only a fallback.
- The LTE device's `lte_failover` flips to `true` with the failover and back
  to `false` about 100 s after the return; it is not exposed as an entity.
- `last_wan_interfaces.<group>.alive` lags and even flips while the port
  comes back; it is not used. Its address stays during the outage, which is
  what change detection across an outage needs. The port's own IPv4 and
  global IPv6 vanish while down and return unchanged, so no address event.
- The state over the websocket showed the failover about 25 s after the
  console logged it, and the return at the same second.
- The console's event log entries also arrive over the websocket as
  `events` messages (for example the WAN interface transitions); a later
  version could use them for event types the state does not carry.

## Not verified yet

- Whether a local API key would work instead of a user; developed and tested
  with user name and password.
- Clients of VPN servers (WireGuard, OpenVPN, L2TP): only Teleport was
  available for a live check; types other than `WIRED`/`WIRELESS` are counted.

## Tests

`tests/conftest.py` simulates the console (`FakeConsole`) and provides the
runtime objects (`gateway()`, `lte_device()`, `teleport_client()`). The tests
cover the client against a fake aiohttp session, the model, the config flow,
the entities with websocket pushes and polls, and the diagnostics. CI runs
ruff, mypy in strict mode, hassfest, the HACS check (once the repository is
public) and pytest with a coverage floor of 99 %.
