# Architecture

How Network Plus is built, which decisions were taken on purpose, and what is
not verified yet. Written for a reviewer; the README says what the integration
does for its users.

## Scope

One config entry is one UniFi OS console with its UniFi Network application.
The integration reads. The one exception is the switch "Enforce backup uplink",
which exists only with the admin opt-in (separate admin credentials) and
changes exactly one thing: the WAN type of the preferred uplink. It
complements the UniFi integration that ships with Home Assistant and does not
touch its entities or devices.

## Data sources

All endpoints are the ones the console's own web interface uses; none is a
published API. Every request goes through `NetworkClient._get` in `api.py`, so
a changed interface fails in one place.

| Source | Used for | How often |
|---|---|---|
| `POST /api/auth/login` | Session cookie; a 401 later logs in again once | On demand |
| `GET …/stat/device` | Runtime objects of all devices: the gateway (uplink status, addresses, speed test, monitors, the WAN ports' `speed`, `full_duplex` and `up`) and the LTE device (data usage, limits, signal, SIM flags `lte_is_sim_pin_required`, `lte_is_sim_pin_verified`, `lte_sim_pin_tries_left`) | Every 60 s |
| `GET …/v2/api/site/<site>/wan/enriched-configuration` | Names and failover priorities of the uplinks | Every 60 s |
| `GET …/v2/api/site/<site>/clients/active?includeTeleport=true` | Remote clients (Teleport, VPN) | Every 60 s |
| `wss://…/wss/s/<site>/events?clients=v2` | `device:sync` messages with the same runtime objects, about once a second | Continuous |
| `GET /api/users/self` | Whether the admin user may change the Network configuration (`permissions["network.management"]` holds `admin`) | Config flow |
| `GET` + `PUT …/rest/networkconf/<id>` | The switch "Enforce backup uplink": read the uplink network, write it back with `wan_type` `disabled` or the previous type. Needs the CSRF token the console hands out at login and with answers | On switching |

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
- The events of the event entity are derived from state changes:
  `detect_online_events`, `detect_active_events` and `detect_address_events`
  compare against the last known status, active uplink or address, so a
  change that passes through an unknown state in between (PPPoE reconnect,
  outage, an unmapped status value) still fires.
- A grown transition timestamp while the active uplink stays the preferred
  one is only a candidate for a brief failover: the gateway may report the
  timestamp a push before the switch itself. The candidate is reported once
  `SWITCH_TOLERANCE` (30 s) passed without a switch, and dropped when a
  switch follows.
- **Push gate:** the websocket sends the gateway about once a second with
  latency values that differ nearly every time. A push updates the state only
  when `essentials()` changed: an uplink's online state or address, the active
  uplink, the transition timestamp, or the LTE connected/in-use flags. All
  numbers (latency, speed test, data usage, remote clients) come from the
  60-second poll. This keeps failovers immediate and the recorder quiet.
- `device:sync` carries whole objects and replaces what is known;
  `device:update` carries only the changed fields (seen live: a single key)
  and is merged into the known object.
- The poll's three requests run in parallel. A device pushed while the poll
  is in flight keeps its pushed object, which is newer than the poll's
  snapshot; otherwise a snapshot from before a failover would turn it back
  and fire a false `restored`. Devices not pushed meanwhile take the poll's
  object, and the poll decides which LTE devices exist.
- The websocket listener survives anything a push can contain: parsing
  tolerates Infinity, NaN, timestamps beyond any clock and odd field types,
  and an unexpected exception is logged, not fatal. A rejected login is
  retried only after the longest delay (5 min): wrong credentials go through
  the poll's reauth flow and a reload, while a console that is starting up
  rejects logins for a while and then works again.
- The console answers wrong credentials with 403 and an `AUTHENTICATION_FAILED…`
  error code; a 400 or 403 without that code (a console still starting, a
  blocked request) is a network error, not a reason for reauthentication.
- A push that changes the state sets the coordinator's data and notifies the
  listeners directly, so the poll timer stays untouched; `async_set_updated_data`
  would reschedule the poll and a flapping uplink could postpone it for good.
- The reconnect delay resets only after a connection that delivered a
  message; connections the console closes at once back off like failures.
- Uplink names are part of entity names (translation placeholders). When a
  poll finds an uplink renamed in UniFi Network, the entry is reloaded. Only
  names from the uplink configuration count; an uplink missing from one
  incomplete answer falls back to its key, which is no rename.
- Parsing a push costs about 20 µs on a desktop CPU (measured), a few times
  that on a Raspberry Pi; at one push per second that is not worth a cache.
- Concurrent requests that find the session rejected share one new login:
  each request remembers the login generation it was sent with, and a 401
  only marks the session expired if nobody logged in since.
- The active-client list is fetched whole every minute (the console offers
  no filter for remote clients); on a LAN with a few dozen clients that is a
  few kilobytes.
- A failed poll makes the entities unavailable (`UpdateFailed`); the next
  poll that succeeds brings them back. Pushes are ignored while the last poll
  failed: the poll decides whether the console is healthy, so a failing poll
  is not hidden behind push data. A rejected login starts the reauth flow. A
  missing clients endpoint only makes the VPN sensor unknown.
- The websocket is reconnected with a delay that doubles per failed attempt
  (5 s to 5 min) and resets as soon as a connection stood, however it ended.

## Entities and devices

- The gateway's device is created in `async_setup_entry` so its registry ID is
  known; every LTE device (keyed by MAC) hangs below it through
  `via_device_id` (`via_device` is deprecated since Home Assistant 2026.10).
  All carry their MAC as connection, like the devices of the built-in UniFi
  integration; since 2026.10 a device belongs to one integration, so they are
  separate devices.
- Uplink entities exist for every uplink the gateway monitors
  (`last_wan_status`), not for every configured WAN port. Uplinks and the LTE
  device that appear later get their entities without a reload
  (`add_uplink_entities`, `add_lte_entities`).
- An uplink entity is unavailable while the gateway stops reporting that
  uplink; "offline" is reported only when the gateway says so.
- Disabled by default: latency and availability per uplink, the public
  addresses, the LTE signal. Enabled: what a dashboard or automation needs.
- **Link speed:** every uplink with a port object (`wan1`, `wan2`, …; the LTE
  backup has none) gets a diagnostic sensor with what the port negotiated.
  The expected speed is an option per network group (`expected_speed_wan`,
  … `_wan4`: static keys, so they have translations; the options flow shows
  only the ports the gateway reports). The problem sensor "link slower than
  expected" exists only for ports with an expectation and goes with it: the
  options flow reloads the entry, and setup removes sensors without one. A
  link that is down is unknown there, not slow. Speed and duplex are part of
  `essentials()`, so a renegotiated link shows through the websocket.
- **SIM problem:** PIN required but not verified, or no PIN tries left. The
  console's `lte_state` is an attribute, not interpreted: only the healthy
  values were seen live.

## The admin opt-in and the switch "Enforce backup uplink"

- Admin features are an explicit choice at setup: a checkbox, and only then
  a second dialog for separate admin credentials. The read-only user stays
  what it is; the admin user gets its own client and session and is used
  for nothing but the write. Without the opt-in the switch does not exist.
- The switch's state is read, not assumed: on means the preferred uplink's
  `wan_type` is `disabled` in the console's configuration. Switching on
  remembers the previous type and the start in the entry's data, so a
  restart neither forgets what to restore nor when.
- Guards: it refuses while the backup already carries the traffic and from a
  configurable share of the LTE data plan on; it switches back by itself
  after a configurable time (default 15 min), also after a restart, and
  retries a failed switch-back every minute. Live, a provoked failover ran
  about 60 MB per minute over LTE.
- Verified live: setting `wan_type` to `disabled` fails the gateway over
  within about 15 s and the integration reports `failover`; restoring the
  previous type brings it back as fast and reports `restored`. The port's
  link stays up during that, unlike with a pulled cable.
- **Drill protocol:** the switch tells the coordinator when it switched the
  uplink off (`start_drill`) and when it switches it on again (`end_drill`,
  before the write, undone if the write fails). The coordinator advances the
  drill from the `failover` and `restored` events it detects anyway
  (`drill_progress` in `model.py`): the first failover marks the switch to
  the backup, the return completes the drill with the LTE usage of that
  moment. Times are Home Assistant's clock, so drills compare. A return
  before the switch-back (someone switched the uplink on in the console)
  completes the drill without a restore time. The drill is stored in the
  entry's data, so a restart in the middle of one does not lose it; the
  entry's data is otherwise the switch's own store already.

## Repair issues

Three conditions need a person and get an issue in Settings → Repairs, none
fixable from Home Assistant. Issue IDs end with the entry ID, so two consoles
do not share them, and `async_remove_entry` drops them with the entry.

- **Console not reachable:** after `UNREACHABLE_POLLS` (15) consecutive
  failed polls, deleted with the next successful one. Fewer failures are
  normal (a reboot of the console) and only make the entities unavailable.
- **LTE data limit turned off:** per LTE device, from the poll; the console
  keeps the old limit value, which is why the integration does not use it.
- **Admin user cannot write:** checked once per setup with
  `can_manage_network()` (lacking role, or login rejected), and raised at
  once when a write of the switch comes back with 403. A console that does
  not answer the check leaves the issue as it is.

## Decisions taken on purpose

- **Own client, not `aiounifi`.** The built-in UniFi integration pins
  `aiounifi`; a second requirement on it could conflict, and this integration
  needs only a login, two GETs and the websocket.
- **Own aiohttp session** via `async_create_clientsession` with
  `CookieJar(unsafe=True)`: the console's login is a cookie, and consoles are
  usually addressed by IP, which the default jar rejects. Home Assistant
  detaches a session created during entry setup when the entry unloads, also
  after a failed setup; the entry registers the detach as well so it is
  visible, and the config flow detaches its own after each check.
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
- **Addresses** are the uplink port's addresses: what the provider assigned,
  or a private address behind another router (double NAT); only link-local,
  unspecified, loopback and multicast are left out. They live in disabled
  diagnostic sensors and in the `public_ip_changed` event, so storing an
  address in the recorder is the user's choice. An address that appears or
  vanishes is no change event.
- **Brief failover tolerance:** a transition timestamp within 30 s of a seen
  switch belongs to that switch. This compares the console's clock with Home
  Assistant's; both are expected to run on NTP.
- **Active uplink fallback:** without `uplink.comment` the first uplink in
  failover order that is online counts, but an uplink of unknown status before
  it leaves the active uplink unknown rather than reporting a false failover.
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
- The SIM flags in a fault: live, only a healthy SIM was seen (PIN required
  and verified, three tries left). What the console reports with a wrong PIN
  or a missing SIM, and which `lte_state` values exist, is unknown.
- The port fields on consoles other than the development one; there, the
  2.5G WAN port reported `speed` 2500, `full_duplex` true, `max_speed` 10000.
- Clients of VPN servers (WireGuard, OpenVPN, L2TP): only Teleport was
  available for a live check; types other than `WIRED`/`WIRELESS` are counted.

## Tests

`tests/conftest.py` simulates the console (`FakeConsole`) and provides the
runtime objects (`gateway()`, `lte_device()`, `teleport_client()`). The tests
cover the client against a fake aiohttp session, the model, the config flow,
the entities with websocket pushes and polls, and the diagnostics. CI runs
ruff, mypy in strict mode, hassfest, the HACS check (once the repository is
public) and pytest with a coverage floor of 99 %.
