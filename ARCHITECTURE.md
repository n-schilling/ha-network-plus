# Architecture

How Network Plus is built, which decisions were taken on purpose, and what is
not verified yet. Written for a reviewer; the README says what the integration
does for its users.

## Scope

One config entry is one UniFi OS console with its UniFi Network application.
The integration reads. The exceptions exist only with the admin opt-in
(separate admin credentials): the switch "Enforce backup uplink" changes
exactly one thing, the WAN type of the preferred uplink; the button "Run
speed test" sends one command to the gateway; the button "Run speed test
over backup uplink" combines the two. It complements the UniFi integration
that ships with Home Assistant and does not touch its entities or devices.

## Data sources

All endpoints are the ones the console's own web interface uses; none is a
published API. Every request goes through `NetworkClient._request` in
`api.py`, so a changed interface fails in one place.

| Source | Used for | How often |
|---|---|---|
| `POST /api/auth/login` | Session cookie; a 401 later logs in again once | On demand |
| `GET …/stat/device` | Runtime objects of all devices: the gateway (uplink status, addresses, speed test progress, result and interface in `speedtest-status`, the result once more in `uplink`, monitors, the WAN ports' `ifname`, `speed`, `full_duplex` and `up`) and the LTE device (data usage, limits, signal, SIM flags `lte_is_sim_pin_required`, `lte_is_sim_pin_verified`, `lte_sim_pin_tries_left`) | Every 60 s |
| `GET …/v2/api/site/<site>/wan/enriched-configuration` | Names and failover priorities of the uplinks | Every 60 s |
| `GET …/v2/api/site/<site>/clients/active?includeTeleport=true` | Remote clients (Teleport, VPN) | Every 60 s |
| `wss://…/wss/s/<site>/events?clients=v2` | `device:sync` messages with the same runtime objects, about once a second | Continuous |
| `GET …/stat/sysinfo` | The console's time zone (`timezone`), which the days of its reports are in. Falls back to Home Assistant's | Once, with a billing day |
| `POST …/stat/report/{daily,monthly}.ap` `{"attrs": ["lte_rxbytes", "lte_txbytes", "time"], "macs": [...], "start", "end"}` | The LTE bytes per day and per month, for a billing day other than the 1st. Rows carry `ap` (MAC) and `time` (ms, midnight in the console's time zone); bytes are floats. Retention by default: daily 90 days, monthly a year (`data_retention_time_in_hours_for_*` in `stat/sysinfo`) | Hourly and on a new day, with a billing day |
| `GET /api/users/self` | Whether the admin user may change the Network configuration (`permissions["network.management"]` holds `admin`) | Config flow |
| `GET` + `PUT …/rest/networkconf/<id>` | The switch "Enforce backup uplink": read the uplink network, write it back with `wan_type` `disabled` or the previous type. Needs the CSRF token the console hands out at login and with answers | On switching |
| `POST …/cmd/devmgr` `{"mac": <gateway>, "cmd": "speedtest"}` | The button "Run speed test". The console answers at once with `meta.rc`; progress and result arrive in the gateway's runtime object. (`"cmd": "speedtest-status"` returns the same `speedtest-status` block on demand; not needed with the push.) | On pressing |

The LTE data usage is `rx_bytes + tx_bytes` of the LTE device's runtime object.
On the console this was developed against, that sum equals the sum of the
daily values of the console's monthly report to the byte, also across a
reboot of the device, so it is the usage in the current calendar month.
The monthly report's row equals the counter as well, and today's daily row
is rolled up within minutes.

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
  uplink, the transition timestamp, the LTE connected/in-use flags, or a
  speed test that started or finished (`running`, `last_run`). All other
  numbers (latency, data usage, remote clients) come from the 60-second
  poll. This keeps failovers and speed test results immediate and the
  recorder quiet.
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
- **Session lifetime:** the console's session token (cookie `TOKEN`, a JWT)
  lives exactly two hours (seen live); afterwards every request answers
  401 and the client logs in again. Every 401 carries a `Set-Cookie` that
  deletes `TOKEN`; a poll's parallel requests come back one by one, and a
  late 401 after the new login deleted the fresh token from a cookie jar, so
  the repeat failed and the poll's entities went unavailable for a minute
  every two hours. The client therefore keeps the token itself and sends it
  as a `Cookie` header; a login carries no token. A rejected
  login asks for the password only after `AUTH_FAILURES_FOR_REAUTH` (3)
  polls in a row: a reauthentication flow needs a person, and the console
  may reject a login once in a while (maintenance). The console's lockout after too many attempts
  (`AUTHENTICATION_FAILED_LIMIT_REACHED`) is a temporary error, not wrong
  credentials.
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
- **Speed test result:** `speedtest-status` is the gateway's status block.
  While a test runs it carries `status_summary` 1 and no result (`rundate`
  0, throughputs 0, no interface; the sub-statuses go 0 → 1 → 2); finished,
  it carries 2, `rundate`, the throughputs, `latency` and the interface. The
  `uplink` object (`speedtest_lastrun`, `xput_down`, …) follows about 40 s
  later (seen live). A finished status block that is at least as new as the
  uplink object is therefore the result; otherwise the uplink object is, so
  the last result stays visible while a test runs.
- **Speed test uplink:** the status block names the interface the test ran
  over (`interface_name`, also `source_interface` as `if!<name>`), but only
  for a while: live it was there right after the test and hours later, and
  empty in between. A WAN port's `ifname` (`wan1.ifname` and so on) maps it
  to that port's uplink; any other interface is the uplink without a port,
  the LTE backup, which routes over a tunnel (`gre1` live). The coordinator
  notes the uplink when it first sees a run time (`_place_speedtest`): from
  the interface if named, else the uplink that carries the traffic at that
  moment, which the backup recipe keeps active until the result arrived. It
  is kept in the entry's data (`speedtest_uplink`), so a restart does not
  reassign a result to whatever uplink is active then; an interface the
  gateway names later wins. The four speed test sensors carry the key and
  the name as attributes, so values measured over the backup do not pass for
  the main line's.
- **Speed test in the activity:** a result that arrives while the integration
  watches (not the one found at setup or after a restart) fires the bus event
  `network_plus_speedtest` with the numbers and the uplink, in a fresh
  `Context`. `logbook.py` describes the event for the logbook; the result is
  in the entry's *name*, because the activity details render only the name
  of a cause, not its message. The speed test sensors write the new result
  in that context (`SpeedtestResultEntity`), so the logbook names the test
  as the cause of their state change. The context is used once per run
  time; Home Assistant drops it from later writes after a few seconds.

## The admin opt-in, the switch "Enforce backup uplink" and the speed test buttons

- Admin features are an explicit choice at setup: a checkbox, and only then
  a second dialog for separate admin credentials. The read-only user stays
  what it is; the admin user gets its own client and session and is used
  for nothing but the writes. Without the opt-in neither the switch nor the
  buttons exist, and entities from an earlier opt-in are removed from the
  registry at setup. All writes share one path in the coordinator
  (`_admin_write`) that turns a rejected login, missing rights (also raising
  the repair issue) and other errors of the console into translated errors.
- The mechanics of enforcing the backup live in the coordinator
  (`async_enforce_backup`, `async_release_backup`, the auto-off timer, the
  guards), shared by the switch and the backup speed test; the switch is a
  thin entity over them. A switch-back the console refuses leaves the timer
  armed, so the uplink still comes back later.
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
- **Speed test:** the button sends the device manager command the console's
  own interface uses. The test runs over the uplink that carries the traffic;
  the console offers no way to pick one. The button refuses while
  `speedtest-status.status_summary` says a test is in progress. Verified
  live over the cable: the command is accepted with `meta.rc` "ok", the
  status block shows the run within a few seconds, and the result with the
  interface about 15 s after the start.
- **Speed test over the backup uplink:** a capability of the gateway that
  the console does not offer as one command, so the integration composes
  it (`async_start_backup_speedtest`): the guards of the switch and of the
  test are checked at the press, then a background task of the entry
  enforces the backup, waits for `backup_active` (`FAILOVER_TIMEOUT`, 90 s;
  live about 15 s), starts the test, waits for a new run time
  (`RESULT_TIMEOUT`, 3 min; live well under one), and releases the backup
  in a `finally`, so the uplink comes back whatever happened. Waiting uses
  the coordinator's own listeners (`_async_wait`), not Home Assistant's
  state machine. A timeout or a refused command is a warning in the log; the
  drill protocol and the speed test event report what did happen. The
  policy, when to measure and whom to tell, stays in automations. If Home
  Assistant stops in the middle, the task is cancelled; the switch-back is
  then up to the auto-off timer, re-armed from the entry's data at the next
  setup.

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
  `DummyCookieJar()`: the client sends the session token itself (see
  above), so the session keeps no cookies; that also works for consoles
  addressed by IP, which the default jar would reject. Home Assistant
  detaches a session created during entry setup when the entry unloads, also
  after a failed setup; the entry registers the detach as well so it is
  visible, and the config flow detaches its own after each check.
- **Certificate check is an option, on by default.** Consoles ship with a
  self-signed certificate; the option exists so nobody has to turn off
  verification globally.
- **No thresholds in code.** "Internet latency" is one series across
  failovers; a threshold helper or an automation decides what is slow. The
  integration sends no notifications.
- **No service actions.** The two writes are entities (the switch, the
  button), so they appear on the gateway's device, carry its availability and
  work with the usual tiles and automations. The command takes no uplink: a
  test over a chosen one is not offered (see "Not verified yet").
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
  reset, whatever its time zone.
- **Billing day** (option, 2 to 28): the period's usage is the console's
  counter minus the reported days of the month before the billing day, or,
  before the billing day, the counter plus the previous month's total minus
  its days before the billing day (`model.period_usage`). Only closed days
  come from the reports, today from the counter. Subtracting the days that do
  not count, instead of adding the ones that do, makes a missing day count as
  used: a warning comes early, never late. Without the previous month's total
  its reported days since the billing day are added instead. Within the first
  hour of the 1st a counter that is still at least last month's total has not
  been reset yet and is not added twice. The sensor then has state class
  `total` with the period's start (midnight in the console's time zone) as
  `last_reset`, so a value that drops a little within the period (a report
  rolled up late) is a correction, not a reset. Reports of an earlier day give
  unknown, not a guess. The console's own limits still reset on the 1st, so
  the switch refuses by the higher of the two shares. The share of the data plan is
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
- What `speedtest-status` shows for a failed test; live, 1 (running) and 2
  (finished) were seen. The gateway also carries a list
  `speedtest-pending-interfaces` (empty live), which hints at tests per
  interface in a later console version.
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
the entities with websocket pushes and polls, the switch, the button, the
repair issues and the diagnostics. CI runs
ruff, mypy in strict mode, hassfest, the HACS check (once the repository is
public) and pytest with a coverage floor of 99 %.
