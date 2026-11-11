# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.10.0] - 2026-11-11

### Added

- **The same header as on every page of these integrations and apps**: the toolbar of the page ends with the time the page last got its data ("As of 14:02:37", hidden on a phone), *Refresh* and *Settings*. Refresh reads the data again over the WebSocket without reloading the page, spins while it loads and moves the time; Settings opens Home Assistant's page of the integration (Settings → Devices & services → Network Plus) inside the app. Both on every tab.

### Changed

- The choice of the console (with more than one) moved from the toolbar to the top of the page, above the cards.
- The page check (CI job Page) tests the header on every tab: the order stamp, refresh, settings; that refresh sends the request again, shows the changed data and a new time; that settings opens the integration's page without reloading; the stamp hidden on a phone.
- CI job Page uses the runner's Google Chrome, like Crash Guard, and installs Playwright's Chromium with its apt packages only where there is none, so an unreachable Ubuntu mirror no longer fails it.

## [0.9.1] - 2026-11-11

### Fixed

- Tests: the config flow tests that end with a reauth or reconfigure wait for the reload it starts in the background, so the websocket listener no longer starts during teardown now and then (flaky failure "Lingering task … network_plus websocket"). No change to the integration.

## [0.9.0] - 2026-11-10

### Changed

- **Optional certificate pinning** for a console with a certificate of its own (self-signed): *Pin the certificate* shows its SHA-256 fingerprint in a second step and asks to enter it, compared with the console. From then on only that certificate is accepted; a changed one is never taken by itself: the entities become unavailable, the log names the new fingerprint and Home Assistant asks again. Off by default, and not for a certificate that is renewed regularly (Let's Encrypt every 90 days). *Verify the certificate* stays as it was (on by default) and now says what turning it off means. Existing entries keep their settings; nothing is pinned on the update.
- The switch "Enforce backup uplink" and the two speed test buttons are for administrators of Home Assistant only, like the page; automations and scripts may still use them. A user without administrator rights gets an error.
- The addresses of the event `public_ip_changed` (`address`, `previous`) reach automations but are no longer kept by the recorder, the logbook or backups.
- "Enforce backup uplink" refuses near the data plan also while the console's data limit is turned off, by the plan size the console still reports.
- The page is built from Home Assistant's own elements (cards, alerts, icon buttons) and shows the version of the integration at the bottom; its address carries a hash of the file, so a browser never shows an old page after an update.
- Requires Home Assistant 2026.11. Installation through the Marketplace.

### Fixed

- The uplink could stay off when the console took "Enforce backup uplink" but its answer or the check after it failed (a timeout, a server error): the timer to switch back is now armed before the write, and the speed test over the backup switches back in that case too.
- At the end of the set time the uplink stayed off when its state was not known at that moment; it is now switched on all the same.
- An uplink switched off in UniFi Network, not by Network Plus, was switched on again with a guessed connection type (DHCP) when the switch was turned off in Home Assistant, which cut a PPPoE line. The switch now refuses and says to switch it on in UniFi Network; switching on refuses an uplink that is off already for the same reason.
- Withdrawing the admin features while the backup was enforced left the uplink off for good; Reconfigure now refuses that until the switch is off.
- An attempt the console refused, or an uplink switched on again in UniFi Network, left the start behind: the switch showed old times, and a later restart could switch the uplink on against a setting made in the console. The start is now forgotten in both cases, and the switch shows its times only while on.
- A reload during a speed test over the backup switches the uplink on before the sessions close, instead of failing and waiting for the timer.
- A drill begins before the write, so a fast failover is not missed; an attempt the console refused keeps the last real drill.
- After a refusal before the first request, the button "Run speed test over backup uplink" no longer stays in progress.
- Choosing another console on the page while it was loading went back to the first one.
- Two remote clients of the same name were one session on the page; sessions now follow the client.

## [0.8.0] - 2026-11-02

### Added

- A page of its own, **Network Plus** in the sidebar, for administrators: the uplinks with their state, latency, availability, negotiated link speed against the expected one and public addresses, the LTE backup with its data plan and signal, the history of failovers, returns, brief failovers, uplinks going down and up and new public addresses, the speed tests per uplink with a chart, every backup drill with its times and data, and the remote sessions. It reads a WebSocket command `network_plus/overview` every 30 seconds while open.
- The history is kept per console in a store of its own, not in the recorder, for 365 days (new option *Keep history*); backup drills stay. It starts with this version.
- A completed backup drill is an entry in the activity, like a speed test, and fires the bus event `network_plus_drill`.

### Removed

- The sensors for the speed test (download, upload, ping, last speed test), the backup drill (last drill, failover time, restore time, data used), the last LTE failover, the VPN clients, the link speed per uplink, and the disabled sensors for latency, availability, IPv4 and IPv6 per uplink. Their values are on the page; the speed test result is also in the event `network_plus_speedtest`, the drill in `drill_completed`. Remove the leftover entities under Settings → Entities (they show as "not provided").

### Fixed

- Saving the options while the console could not be reached deleted the expected link speeds, because the form could not show them; they are now kept.

## [0.7.2] - 2026-10-24

### Changed

- A server error of the console (HTTP 5xx, for example while UniFi Network restarts) no longer makes the entities unavailable at once. The last state is kept and the read repeated up to five times, after 1, 2, 3, 4 and 5 minutes; pushes from the websocket still update the entities meanwhile. Only when the fifth repeat fails do they become unavailable. Seen live: a 502 of the Network application for a minute at night.

## [0.7.1] - 2026-10-23

### Fixed

- The entities no longer go unavailable for a minute every two hours. When the console's session token expired, a poll's parallel requests were rejected one by one; a rejection that arrived after the new login deleted the fresh token (the console deletes the cookie with every 401), so the repeated request failed as well. The client now keeps the token itself and sends it with every request and the websocket.

## [0.7.0] - 2026-10-18

### Added

- Option "LTE data plan: billing day" (1 to 28, default 1) for mobile plans that start a new data period on another day than the 1st ([#2](https://github.com/n-schilling/ha-network-plus/issues/2)). With another day, "LTE data this month" and "LTE data used" count the billing period, summed up from the console's daily reports; the usage sensor gets state class `total` with the period's start as `last_reset`, and the attributes `month_bytes` and `month_percent` keep the console's calendar-month count. The console's own limits still reset on the 1st, so "Enforce backup uplink" refuses as soon as either share reaches its limit. With the default nothing changes.

## [0.6.1] - 2026-10-17

### Fixed

- A single rejected login no longer asks for the password: the console's session token lives two hours, and a login that is rejected once (an expired session, maintenance) is retried; the reauthentication starts after three rejected polls in a row. The reason is in the log and in the message.
- A login carries no old session cookie, so the console judges the credentials, not a stale token.
- The console's lockout after too many login attempts is treated as a temporary error, not as wrong credentials.

## [0.6.0] - 2026-10-16

### Added

- Button "Run speed test over backup uplink" (admin features only): switches the preferred uplink off, waits for the failover, runs the speed test, waits for the result and switches the uplink on again, whatever happened in between. The press returns at once; the attribute `in_progress` shows the measurement. Refuses what the switch refuses, a running test and a second measurement. The README's script recipe is replaced by this button and an automation example.

### Changed

- The mechanics of "Enforce backup uplink" (switching, the restore type, the automatic switch-back, the drill) moved from the switch into the coordinator, shared with the new button. Behaviour of the switch is unchanged.

### Fixed

- A switch-back the console refused used to cancel the automatic switch-back; the timer now stays armed and brings the preferred uplink back later.

## [0.5.0] - 2026-10-16

### Added

- Every finished speed test is an entry in the activity (logbook) of its own, named with the result: gateway, download, upload, ping and the uplink. The four speed test sensors write the new result in that entry's context, so "What happened" in the activity details of a sensor names the test with its numbers.

## [0.4.0] - 2026-10-16

### Added

- Button "Run speed test" (admin features only): starts the gateway's speed test, the same one UniFi Network runs on its schedule, over the uplink that carries the traffic. Refuses while a test is running. With the switch "Enforce backup uplink" this measures the backup; the README has the recipe and the data warning.
- Attributes `uplink` and `uplink_name` on the four speed test sensors: the uplink the last test ran over, from the interface the gateway names or the uplink active when the result was first seen, kept across restarts. Values measured over the backup no longer pass for the main line's.
- Attribute `running` on "Last speed test".

### Changed

- A speed test that starts or finishes is applied from the websocket at once, and the result is taken from the gateway's status block, which is ahead of its uplink object; it no longer waits for the next poll.

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
