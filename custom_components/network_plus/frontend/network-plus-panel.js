// Network Plus panel: the page in the sidebar where administrators see the
// uplinks of their consoles now, the switches between them, the speed tests,
// the backup drills and the remote sessions. Everything comes from the
// integration over the WebSocket API.
//
// The page is built as STANDARD.md §7 describes it (reference: BLE Floorplan):
// Home Assistant's frame for pages with tabs (hass-tabs-subpage), with the
// page's three areas as tabs in the header, at the bottom on a narrow screen;
// the pages of an area as Home Assistant's tabs (ha-tab-group) below it. At the
// end of the header: the stamp, refresh and the button to Home Assistant's page
// of the integration. Explanations sit behind an (i) (ha-icon-button with
// ha-tooltip), errors read one line by their kind, numbers follow the number
// format of the user's profile. Colours, fonts and spacing only through Home
// Assistant's variables. The page has no settings of its own, so no setup area:
// the version line stays at the bottom of every page.

const DOMAIN = "network_plus";
const PANEL_PATH = "/network-plus";
// Home Assistant's own page of the integration, behind the button at the end of the header
const INTEGRATION_PATH = `/config/integrations/integration/${DOMAIN}`;
// The page asks again this often while it is open (ms)
const REFRESH = 30000;

if (!customElements.get("network-plus-panel")) {
  const MDI = {
    refresh: "M17.65,6.35C16.2,4.9 14.21,4 12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20C15.73,20 18.84,17.45 19.73,14H17.65C16.83,16.33 14.61,18 12,18A6,6 0 0,1 6,12A6,6 0 0,1 12,6C13.66,6 15.14,6.69 16.22,7.78L13,11H20V4L17.65,6.35Z",
    // mdi:cog-outline
    integration: "M12,8A4,4 0 0,1 16,12A4,4 0 0,1 12,16A4,4 0 0,1 8,12A4,4 0 0,1 12,8M12,10A2,2 0 0,0 10,12A2,2 0 0,0 12,14A2,2 0 0,0 14,12A2,2 0 0,0 12,10M10,22C9.75,22 9.54,21.82 9.5,21.58L9.13,18.93C8.5,18.68 7.96,18.34 7.44,17.94L4.95,18.95C4.73,19.03 4.46,18.95 4.34,18.73L2.34,15.27C2.21,15.05 2.27,14.78 2.46,14.63L4.57,12.97L4.5,12L4.57,11L2.46,9.37C2.27,9.22 2.21,8.95 2.34,8.73L4.34,5.27C4.46,5.05 4.73,4.96 4.95,5.05L7.44,6.05C7.96,5.66 8.5,5.32 9.13,5.07L9.5,2.42C9.54,2.18 9.75,2 10,2H14C14.25,2 14.46,2.18 14.5,2.42L14.87,5.07C15.5,5.32 16.04,5.66 16.56,6.05L19.05,5.05C19.27,4.96 19.54,5.05 19.66,5.27L21.66,8.73C21.79,8.95 21.73,9.22 21.54,9.37L19.43,11L19.5,12L19.43,13L21.54,14.63C21.73,14.78 21.79,15.05 21.66,15.27L19.66,18.73C19.54,18.95 19.27,19.04 19.05,18.95L16.56,17.95C16.04,18.34 15.5,18.68 14.87,18.93L14.5,21.58C14.46,21.82 14.25,22 14,22H10M11.25,4L10.88,6.61C9.68,6.86 8.62,7.5 7.85,8.39L5.44,7.35L4.69,8.65L6.8,10.2C6.4,11.37 6.4,12.64 6.8,13.8L4.68,15.36L5.43,16.66L7.86,15.62C8.63,16.5 9.68,17.14 10.87,17.38L11.24,20H12.76L13.13,17.39C14.32,17.14 15.37,16.5 16.14,15.62L18.57,16.66L19.32,15.36L17.2,13.81C17.6,12.64 17.6,11.37 17.2,10.2L19.31,8.65L18.56,7.35L16.15,8.39C15.38,7.5 14.32,6.86 13.12,6.62L12.75,4H11.25Z",
    // mdi:information-outline, the (i)
    info: "M11,9H13V7H11M12,20C7.59,20 4,16.41 4,12C4,7.59 7.59,4 12,4C16.41,4 20,7.59 20,12C20,16.41 16.41,20 12,20M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M11,17H13V11H11V17Z",
    // The areas: mdi:wan, mdi:speedometer, mdi:remote-desktop
    wan: "M12,2A8,8 0 0,0 4,10C4,14.03 7,17.42 11,17.93V19H10A1,1 0 0,0 9,20H2V22H9A1,1 0 0,0 10,23H14A1,1 0 0,0 15,22H22V20H15A1,1 0 0,0 14,19H13V17.93C17,17.43 20,14.03 20,10A8,8 0 0,0 12,2M12,4C12,4 12.74,5.28 13.26,7H10.74C11.26,5.28 12,4 12,4M9.77,4.43C9.5,4.93 9.09,5.84 8.74,7H6.81C7.5,5.84 8.5,4.93 9.77,4.43M14.23,4.44C15.5,4.94 16.5,5.84 17.19,7H15.26C14.91,5.84 14.5,4.93 14.23,4.44M6.09,9H8.32C8.28,9.33 8.25,9.66 8.25,10C8.25,10.34 8.28,10.67 8.32,11H6.09C6.03,10.67 6,10.34 6,10C6,9.66 6.03,9.33 6.09,9M10.32,9H13.68C13.72,9.33 13.75,9.66 13.75,10C13.75,10.34 13.72,10.67 13.68,11H10.32C10.28,10.67 10.25,10.34 10.25,10C10.25,9.66 10.28,9.33 10.32,9M15.68,9H17.91C17.97,9.33 18,9.66 18,10C18,10.34 17.97,10.67 17.91,11H15.68C15.72,10.67 15.75,10.34 15.75,10C15.75,9.66 15.72,9.33 15.68,9M6.81,13H8.74C9.09,14.16 9.5,15.07 9.77,15.56C8.5,15.06 7.5,14.16 6.81,13M10.74,13H13.26C12.74,14.72 12,16 12,16C12,16 11.26,14.72 10.74,13M15.26,13H17.19C16.5,14.16 15.5,15.07 14.23,15.57C14.5,15.07 14.91,14.16 15.26,13Z",
    speedometer: "M12,16A3,3 0 0,1 9,13C9,11.88 9.61,10.9 10.5,10.39L20.21,4.77L14.68,14.35C14.18,15.33 13.17,16 12,16M12,3C13.81,3 15.5,3.5 16.97,4.32L14.87,5.53C14,5.19 13,5 12,5A8,8 0 0,0 4,13C4,15.21 4.89,17.21 6.34,18.65H6.35C6.74,19.04 6.74,19.67 6.35,20.06C5.96,20.45 5.32,20.45 4.93,20.07V20.07C3.12,18.26 2,15.76 2,13A10,10 0 0,1 12,3M22,13C22,15.76 20.88,18.26 19.07,20.07V20.07C18.68,20.45 18.05,20.45 17.66,20.06C17.27,19.67 17.27,19.04 17.66,18.65V18.65C19.11,17.2 20,15.21 20,13C20,12 19.81,11 19.46,10.1L20.67,8C21.5,9.5 22,11.18 22,13Z",
    remote: "M3,2A2,2 0 0,0 1,4V16C1,17.11 1.9,18 3,18H10V20H8V22H16V20H14V18H21A2,2 0 0,0 23,16V4A2,2 0 0,0 21,2M3,4H21V16H3M15,5L11.5,8.5L15,12L16.4,10.6L14.3,8.5L16.4,6.4M9,8L7.6,9.4L9.7,11.5L7.6,13.6L9,15L12.5,11.5",
    // States, each with its own shape besides its colour: mdi:check-circle,
    // mdi:close-circle, mdi:help-circle, mdi:swap-horizontal, mdi:ip-network
    ok: "M12 2C6.5 2 2 6.5 2 12S6.5 22 12 22 22 17.5 22 12 17.5 2 12 2M10 17L5 12L6.41 10.59L10 14.17L17.59 6.58L19 8L10 17Z",
    err: "M12,2C17.53,2 22,6.47 22,12C22,17.53 17.53,22 12,22C6.47,22 2,17.53 2,12C2,6.47 6.47,2 12,2M15.59,7L12,10.59L8.41,7L7,8.41L10.59,12L7,15.59L8.41,17L12,13.41L15.59,17L17,15.59L13.41,12L17,8.41L15.59,7Z",
    unknown: "M15.07,11.25L14.17,12.17C13.45,12.89 13,13.5 13,15H11V14.5C11,13.39 11.45,12.39 12.17,11.67L13.41,10.41C13.78,10.05 14,9.55 14,9C14,7.89 13.1,7 12,7A2,2 0 0,0 10,9H8A4,4 0 0,1 12,5A4,4 0 0,1 16,9C16,9.88 15.64,10.67 15.07,11.25M13,19H11V17H13M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12C22,6.47 17.5,2 12,2Z",
    warn: "M21,9L17,5V8H10V10H17V13M7,11L3,15L7,19V16H14V14H7V11Z",
    ip: "M15,9H13V7H15V9M22,20V22H15A1,1 0 0,1 14,23H10A1,1 0 0,1 9,22H2V20H9A1,1 0 0,1 10,19H11V17H7A2,2 0 0,1 5,15V5A2,2 0 0,1 7,3H17A2,2 0 0,1 19,5V15A2,2 0 0,1 17,17H13V19H14A1,1 0 0,1 15,20H22M9,5H7V15H9V5M11,15H13V11H15A2,2 0 0,0 17,9V7A2,2 0 0,0 15,5H11V15Z",
  };

  // The three areas and their pages: area, icon, text, pages (page, text)
  const AREAS = [
    ["uplinks", "wan", "tabUplinks", [["status", "subStatus"], ["history", "subHistory"]]],
    ["tests", "speedometer", "tabTests", [["speedtests", "subSpeedtests"], ["drills", "subDrills"]]],
    ["remote", "remote", "tabRemote", [["now", "subNow"], ["sessions", "subSessions"]]],
  ];
  // The addresses of the pages before the areas, as bookmarks still have them;
  // /uplinks and /remote are areas now and lead to their first page
  const OLD_PAGES = { events: "uplinks/history", speedtests: "tests/speedtests", drills: "tests/drills" };
  const areaOf = (page) => AREAS.find((a) => a[3].some(([p]) => p === page)) || AREAS[0];

  const TEXTS = {
    en: {
      title: "Network Plus", tabUplinks: "Uplinks", tabTests: "Tests", tabRemote: "Remote", subStatus: "Status",
      subHistory: "History", subSpeedtests: "Speed tests", subDrills: "Drills", subNow: "Connected now", subSessions: "Sessions",
      refresh: "Refresh", integration: "Integration in Home Assistant", info: "Explanation", asOf: "As of {t}",
      about: "Network Plus {version}", errDetail: "Message", e_not_loaded: "This console is not loaded at the moment.",
      e_not_found: "This console is not set up (any more).", e_invalid_format: "The input does not fit.",
      e_unauthorized: "This needs an administrator.", e_other: "That did not work. Please try again.", console: "Console",
      loading: "Loading …", noConsole: "No console is set up.", noConsoleWhy: "Add one under Settings → Devices & services.",
      notLoaded: "This console is not loaded at the moment.",
      notLoadedWhy: "Home Assistant tries again by itself; its page of the integration says what went wrong.",
      active: "active", preferred: "preferred", backup: "backup", online: "Online", offline: "Offline", unknown: "Unknown",
      activeUplink: "Active uplink", latency: "Latency", availability: "Availability", linkSpeed: "Link speed",
      expected: "expected", slow: "slower than expected", halfDuplex: "half duplex", fullDuplex: "full duplex",
      wanType: "Connection", port: "Port", ipv4: "Public IPv4", ipv6: "Public IPv6", backupEnforced: "Backup uplink enforced",
      yes: "yes", no: "no", lastSwitch: "Last switch", lastSpeedtest: "Last speed test", lastDrill: "Last drill",
      never: "none yet", inUse: "In use", state: "State", model: "Model", used: "Data used", signal: "Signal", since: "since",
      lteUsed: "{p} % of the data plan used", lteWarn: "{p} % of the data plan used, warning at {w} %",
      failover: "Switched to {to}", failoverFrom: "from {from}", restored: "Back on {to}", after: "after {d}",
      brief: "Brief failover", briefNote: "the console switched for a moment", down: "{u} down", up: "{u} up again",
      ip: "New public {f} address", ipOf: "on {u}", evSwitch: "Switch", evBack: "Back", evDown: "Outage", evChange: "Change",
      noEvents: "Nothing happened in the kept period.", kept: "Kept for {n} days.",
      keptHistory: "Kept for {n} days. Of a new public address only the uplink and the family are kept, not the address.",
      keptDrills: "Drills are kept for good.", download: "Download", upload: "Upload", ping: "Ping", uplink: "Uplink",
      time: "Time", results: "Results", chart: "Download in Mbit/s over time, one line per uplink: {names}",
      noSpeedtests: "No speed test in the kept period.", running: "A speed test is running.", average: "Average",
      drillStart: "Started", failoverTime: "To the backup", restoreTime: "Back", data: "LTE data",
      noDrills: "No backup drill yet.", noDrillsHow: "The switch “Enforce backup uplink” starts one.",
      noAdmin: "No drills without the admin features.",
      noAdminWhy: "A drill switches the preferred uplink off and on again, which needs the admin opt-in: turn on “Use admin features” when reconfiguring the console in Home Assistant.",
      noRemote: "No remote client is connected.", remoteUnknown: "The console does not tell its remote clients.",
      name: "Name", kind: "Kind", from: "From", until: "Until", duration: "Duration",
      noSessions: "No finished session in the kept period.",
      networks: "Networks", network: "Network", internet: "Internet now", clients: "Clients",
      carries: "carries it", notCarries: "does not carry it",
      networksHelp: "Whether each local network reaches the internet, now and over each uplink. A wired uplink carries every network; a cellular backup only the networks assigned to it in UniFi Network. A traffic route with kill switch binds a network to its target uplink. Clients: connected now.",
    },
    de: {
      title: "Network Plus", tabUplinks: "Leitungen", tabTests: "Tests", tabRemote: "Fernzugriff", subStatus: "Status",
      subHistory: "Verlauf", subSpeedtests: "Speedtests", subDrills: "Übungen", subNow: "Gerade verbunden", subSessions: "Sitzungen",
      refresh: "Aktualisieren", integration: "Integration in Home Assistant", info: "Erklärung", asOf: "Stand {t}",
      about: "Network Plus {version}", errDetail: "Meldung", e_not_loaded: "Diese Konsole ist gerade nicht geladen.",
      e_not_found: "Diese Konsole ist nicht (mehr) eingerichtet.", e_invalid_format: "Die Eingabe passt nicht.",
      e_unauthorized: "Dafür braucht es einen Administrator.", e_other: "Das hat nicht geklappt. Bitte erneut versuchen.",
      console: "Konsole", loading: "Lädt …", noConsole: "Keine Konsole eingerichtet.",
      noConsoleWhy: "Unter Einstellungen → Geräte & Dienste hinzufügen.", notLoaded: "Diese Konsole ist gerade nicht geladen.",
      notLoadedWhy: "Home Assistant versucht es selbst erneut; seine Seite der Integration sagt, was schiefging.",
      active: "aktiv", preferred: "bevorzugt", backup: "Backup", online: "Verbunden", offline: "Getrennt", unknown: "Unbekannt",
      activeUplink: "Aktive Leitung", latency: "Latenz", availability: "Verfügbarkeit", linkSpeed: "Link-Geschwindigkeit",
      expected: "erwartet", slow: "langsamer als erwartet", halfDuplex: "Halbduplex", fullDuplex: "Vollduplex",
      wanType: "Anschluss", port: "Port", ipv4: "Öffentliche IPv4", ipv6: "Öffentliche IPv6", backupEnforced: "Backup-Leitung erzwungen",
      yes: "ja", no: "nein", lastSwitch: "Letzte Umschaltung", lastSpeedtest: "Letzter Speedtest", lastDrill: "Letzte Übung",
      never: "noch keine", inUse: "In Benutzung", state: "Zustand", model: "Modell", used: "Verbraucht", signal: "Signal", since: "seit",
      lteUsed: "{p} % des Datenplans verbraucht", lteWarn: "{p} % des Datenplans verbraucht, Warnung bei {w} %",
      failover: "Umgeschaltet auf {to}", failoverFrom: "von {from}", restored: "Zurück auf {to}", after: "nach {d}",
      brief: "Kurze Umschaltung", briefNote: "die Konsole hat kurz umgeschaltet", down: "{u} getrennt", up: "{u} wieder verbunden",
      ip: "Neue öffentliche {f}-Adresse", ipOf: "an {u}", evSwitch: "Umschaltung", evBack: "Zurück", evDown: "Ausfall",
      evChange: "Änderung", noEvents: "Im aufbewahrten Zeitraum ist nichts passiert.", kept: "Aufbewahrt für {n} Tage.",
      keptHistory: "Aufbewahrt für {n} Tage. Von einer neuen öffentlichen Adresse bleiben nur Leitung und Familie, nicht die Adresse.",
      keptDrills: "Übungen bleiben dauerhaft.", download: "Download", upload: "Upload", ping: "Ping", uplink: "Leitung",
      time: "Zeit", results: "Ergebnisse", chart: "Download in Mbit/s über die Zeit, eine Linie je Leitung: {names}",
      noSpeedtests: "Kein Speedtest im aufbewahrten Zeitraum.", running: "Ein Speedtest läuft gerade.", average: "Mittel",
      drillStart: "Beginn", failoverTime: "Umschaltung", restoreTime: "Rückkehr", data: "LTE-Daten",
      noDrills: "Noch keine Backup-Übung.", noDrillsHow: "Der Schalter „Backup-Leitung erzwingen“ startet eine.",
      noAdmin: "Keine Übungen ohne die Admin-Funktionen.",
      noAdminWhy: "Eine Übung schaltet die bevorzugte Leitung aus und wieder ein, dafür braucht es den Admin-Zugang: beim Neukonfigurieren der Konsole in Home Assistant „Admin-Funktionen verwenden“ einschalten.",
      noRemote: "Kein Fernzugriff verbunden.", remoteUnknown: "Die Konsole meldet ihre Fernzugriffe nicht.",
      name: "Name", kind: "Art", from: "Von", until: "Bis", duration: "Dauer",
      noSessions: "Keine beendete Sitzung im aufbewahrten Zeitraum.",
      networks: "Netze", network: "Netz", internet: "Internet jetzt", clients: "Clients",
      carries: "versorgt es", notCarries: "versorgt es nicht",
      networksHelp: "Ob jedes lokale Netz ins Internet kommt, jetzt und über jede Leitung. Eine Kabel-Leitung versorgt alle Netze, eine Mobilfunk-Reserve nur die Netze, die ihr in UniFi Network zugewiesen sind. Eine Traffic Route mit Kill Switch bindet ein Netz an ihre Ziel-Leitung. Clients: gerade verbunden.",
    },
  };

  // Colours of the uplinks in the chart: the theme's, in failover order
  const SERIES = ["var(--primary-color)", "var(--orange-color, #ff9800)", "var(--green-color, #4caf50)", "var(--purple-color, #9c27b0)"];
  // The colour of each state, besides its icon and its word
  const STATE_COLOR = {
    ok: "var(--success-color, #43a047)", err: "var(--error-color, #db4437)", warn: "var(--warning-color, #ffa600)",
    on: "var(--primary-color)", unknown: "var(--secondary-text-color)", ip: "var(--secondary-text-color)",
  };

  const esc = (text) =>
    String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const time = (iso) => (iso ? Date.parse(iso) : NaN);

  // Numbers as Home Assistant writes them: the number format of the user's profile
  const numberLocale = (hass) => {
    const format = hass?.locale?.number_format;
    if (format === "system") return undefined;
    return { comma_decimal: "en-US", decimal_comma: "de", space_comma: "fr", none: "en-US" }[format] ||
      hass?.locale?.language || hass?.language || "en";
  };
  // A number for reading, with d decimals
  const formatNumber = (hass, v, d = 1) => (Number.isFinite(v)
    ? v.toLocaleString(numberLocale(hass), { minimumFractionDigits: d, maximumFractionDigits: d, useGrouping: hass?.locale?.number_format !== "none" })
    : "–");

  // In-app navigation like Home Assistant's own links, no page reload
  const navigate = (url, replace = false) => {
    if (replace) window.history.replaceState(null, "", url);
    else window.history.pushState(null, "", url);
    window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace } }));
  };

  class NetworkPlusPanel extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._tab = AREAS[0][3][0][0];
      // The console chosen (?console=<entry_id>); without one the first is shown
      this._chosen = null;
      this._data = null;
      this._consoles = [];
      // An error of the last request: {text, detail}
      this._note = null;
      // Shown at the bottom: {version}, asked for apart from the data, so it stays on an error
      this._about = null;
      this._frameKey = null;
      // Counts the requests: only the answer to the latest one counts
      this._request = 0;
      // Requests on their way (the refresh button spins) and when the data last came
      this._pending = 0;
      this._loadedAt = null;
      this._hid = 0;
      this._bindOnce();
    }

    set hass(hass) {
      const first = !this._hass;
      this._hass = hass;
      for (const el of this.shadowRoot.querySelectorAll("hass-tabs-subpage")) el.hass = hass;
      if (first) this._load();
      this._render();
    }

    set narrow(narrow) {
      this._narrow = narrow;
      this._render();
    }

    // The address: /<area>/<page>?console=<entry_id>; an old address goes to its new
    // place, an area alone to its first page, nothing to Uplinks
    set route(route) {
      const parts = (route?.path || "").split("/").filter(Boolean);
      const search = window.location.search;
      if (OLD_PAGES[parts[0]]) {
        navigate(`${PANEL_PATH}/${OLD_PAGES[parts[0]]}${search}`, true);
        return;
      }
      const area = AREAS.find((a) => a[0] === parts[0]);
      if (!area) {
        navigate(`${PANEL_PATH}/${AREAS[0][0]}/${AREAS[0][3][0][0]}${search}`, true);
        return;
      }
      if (!area[3].some(([p]) => p === parts[1])) {
        navigate(`${PANEL_PATH}/${area[0]}/${area[3][0][0]}${search}`, true);
        return;
      }
      const wanted = new URLSearchParams(search).get("console");
      // Another area chosen in the header (its address has no query): the console stays
      if (!wanted && this._chosen) {
        window.history.replaceState(null, "", `${window.location.pathname}?console=${encodeURIComponent(this._chosen)}`);
      }
      const href = window.location.pathname + window.location.search;
      // The same address again (Home Assistant after the query was read): nothing to do
      if (href === this._href && this._tab === parts[1]) return;
      this._href = href;
      this._route = { prefix: PANEL_PATH, path: `/${area[0]}/${parts[1]}` };
      this._tab = parts[1];
      if (wanted && wanted !== this._chosen) {
        this._chosen = wanted;
        this._data = null;
        this._note = null;
        this._load();
      }
      this._render();
    }

    set panel(panel) {
      this._panelInfo = panel;
    }

    connectedCallback() {
      this._timer = window.setInterval(() => this._load(), REFRESH);
      this._onVisible = () => document.visibilityState === "visible" && this._load();
      document.addEventListener("visibilitychange", this._onVisible);
      // Home Assistant passes a new route only when the path changes: an address
      // that changes only its query (another console) is read here
      this._onLocation = () => {
        const { pathname, search } = window.location;
        if (!pathname.startsWith(`${PANEL_PATH}/`) || pathname + search === this._href) return;
        this.route = { prefix: PANEL_PATH, path: pathname.slice(PANEL_PATH.length) };
      };
      window.addEventListener("location-changed", this._onLocation);
      window.addEventListener("popstate", this._onLocation);
      if (this._hass) this._load();
    }

    disconnectedCallback() {
      window.clearInterval(this._timer);
      document.removeEventListener("visibilitychange", this._onVisible);
      window.removeEventListener("location-changed", this._onLocation);
      window.removeEventListener("popstate", this._onLocation);
    }

    get _lang() {
      return this._hass?.language || "en";
    }

    get _texts() {
      return TEXTS[this._lang.split("-")[0]] || TEXTS.en;
    }

    _t(key, values = {}) {
      const text = this._texts[key] ?? TEXTS.en[key];
      return text.replace(/\{(\w+)\}/g, (_, name) => values[name] ?? "");
    }

    // The address of a page, with the console chosen
    _path(area, page) {
      return `${PANEL_PATH}/${area}/${page}${this._chosen ? `?console=${encodeURIComponent(this._chosen)}` : ""}`;
    }

    // Fetches the page's data over the WebSocket; refresh calls it again. An answer
    // that arrives after a newer request was sent (another console chosen
    // meanwhile) is dropped, so it cannot bring back the old console.
    async _load() {
      if (!this._hass || document.visibilityState === "hidden") return;
      const request = ++this._request;
      const wanted = this._chosen;
      const msg = { type: `${DOMAIN}/overview` };
      if (wanted) msg.entry_id = wanted;
      if (!this._about) {
        // The version comes from manifest.json at run time (async_get_integration)
        this._hass.callWS({ type: `${DOMAIN}/about` })
          .then((about) => { this._about = about; this._render(); })
          .catch(() => {});
      }
      this._pending += 1;
      this._busy();
      try {
        const data = await this._hass.callWS(msg);
        if (request !== this._request) return;
        this._data = data;
        this._consoles = data.consoles;
        this._note = null;
        this._loadedAt = Date.now();
      } catch (err) {
        if (request !== this._request) return;
        this._data = null;
        this._note = this._error(err);
        // A console the address asks for that is not there: the line says so, and
        // the choice offers the others (not one of them silently in its place)
        if (err?.code === "not_found" && wanted) {
          try {
            const all = await this._hass.callWS({ type: `${DOMAIN}/overview` });
            if (request === this._request) this._consoles = all.consoles;
          } catch {
            // The line above already says that it did not work
          }
        }
      } finally {
        this._pending -= 1;
        this._busy();
      }
      if (request === this._request) this._render();
    }

    // ---- Building blocks ----

    // The (i): Home Assistant's icon button with its tooltip; a tap or Enter shows it, as hovering does
    _help(text) {
      if (!text) return "";
      const id = `help${++this._hid}`;
      return `<ha-icon-button class="help" id="${id}" path="${MDI.info}" label="${esc(this._t("info"))}"></ha-icon-button>` +
        `<ha-tooltip for="${id}" placement="top" trigger="hover click">${esc(text)}</ha-tooltip>`;
    }

    // A card, its title with an (i) when it has something to explain; the title is HTML
    _card(title, body, help = "", cls = "") {
      const head = title ? `<h1 class="card-header">${title}${this._help(help)}</h1>` : "";
      return `<ha-card${cls ? ` class="${cls}"` : ""}>${head}<div class="card-content${title ? "" : " top"}">${body}</div></ha-card>`;
    }

    // One line of state, the why behind its (i)
    _alert(kind, text, help = "") {
      const end = help ? `<span slot="action" class="alertact">${this._help(help)}</span>` : "";
      return `<ha-alert alert-type="${kind}">${esc(text)}${end}</ha-alert>`;
    }

    // An error of a call: one line by its kind, what Home Assistant said behind the (i)
    _error(err) {
      const text = (err?.code && this._texts[`e_${err.code}`]) || this._t("e_other");
      const said = err?.message || (typeof err === "string" ? err : "");
      return { text, detail: said && said !== text ? `${this._t("errDetail")}: ${said}` : "" };
    }

    // A state as Home Assistant's badge: icon, word and colour
    _badge(text, kind = "", icon = "") {
      const color = STATE_COLOR[kind] || "var(--secondary-text-color)";
      return `<ha-badge style="--badge-color:${color}">${icon ? `<ha-svg-icon slot="icon" path="${MDI[icon]}"></ha-svg-icon>` : ""}${esc(text)}</ha-badge>`;
    }

    // The refresh button spins while a request is on its way
    _busy() {
      this.shadowRoot.getElementById("refresh")?.classList.toggle("spin", this._pending > 0);
    }

    // One listener each on the shadow root: the content under it is redrawn
    _bindOnce() {
      const root = this.shadowRoot;
      root.addEventListener("click", (e) => {
        const act = e.target.closest?.("[data-act]")?.dataset.act;
        if (act === "refresh") this._load();
        if (act === "integration") navigate(INTEGRATION_PATH);
      });
      // Home Assistant's tabs of the pages of an area
      root.addEventListener("wa-tab-show", (e) => {
        const page = e.detail?.name;
        if (e.target?.id === "subtabs" && page && page !== this._tab) navigate(this._path(areaOf(page)[0], page));
      });
      // Another console: into the address, so reload and back keep it
      root.addEventListener("selected", (e) => {
        if (e.target?.id !== "console") return;
        const value = e.detail?.value;
        if (!value || value === this._shownConsole) return;
        this._chosen = value;
        navigate(this._path(areaOf(this._tab)[0], this._tab));
      });
    }

    // ---- Drawing ----

    get _shownConsole() {
      return this._chosen || this._data?.console?.entry_id || this._consoles[0]?.entry_id || null;
    }

    _render() {
      if (!this._hass) return;
      const key = [this._tab, !!this._narrow, this._lang].join("|");
      if (key !== this._frameKey) {
        this._frameKey = key;
        this._frame();
      }
      this._choice();
      this._hid = 0;
      const html = (this._note ? this._alert("error", this._note.text, this._note.detail) : "") + this._content();
      const sheet = this.shadowRoot.getElementById("sheet");
      // Drawn anew only when it changed, so an open (i) stays open through a refresh
      if (html !== this._sheetHtml) {
        this._sheetHtml = html;
        sheet.innerHTML = html;
      }
      this.shadowRoot.getElementById("about").textContent = this._about?.version
        ? this._t("about", { version: this._about.version })
        : "";
      // When the page last got its data
      this.shadowRoot.getElementById("stamp").textContent = this._loadedAt
        ? this._t("asOf", { t: new Date(this._loadedAt).toLocaleTimeString(this._lang, { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }) })
        : "";
      this._busy();
    }

    // The parts that stay while the page is open: Home Assistant's frame with the
    // areas as tabs, the pages of the area, the console choice, the sheet and the version line
    _frame() {
      const area = areaOf(this._tab);
      const pages = `<ha-tab-group id="subtabs" class="subtabs">${area[3].map(([page, label]) =>
        `<ha-tab-group-tab slot="nav" panel="${page}"${page === this._tab ? " active" : ""}>${esc(this._t(label))}</ha-tab-group-tab>`).join("")}</ha-tab-group>`;
      this.shadowRoot.innerHTML = `
        <style>${STYLE}</style>
        <hass-tabs-subpage id="frame" main-page>
          <div slot="toolbar-icon" class="actions"><span class="stamp" id="stamp"></span>
            <ha-icon-button id="refresh" data-act="refresh" label="${esc(this._t("refresh"))}" path="${MDI.refresh}"></ha-icon-button>
            <ha-icon-button id="integration" data-act="integration" label="${esc(this._t("integration"))}" path="${MDI.integration}"></ha-icon-button></div>
          <div class="page${this._narrow ? " narrow" : ""}">${pages}
            <div class="sheet"><div class="choice" id="choice" hidden></div><div class="body" id="sheet"></div></div>
            <div class="about" id="about"></div></div>
        </hass-tabs-subpage>`;
      const frame = this.shadowRoot.getElementById("frame");
      frame.hass = this._hass;
      frame.tabs = AREAS.map(([id, ic, label]) => ({ path: `${PANEL_PATH}/${id}`, name: this._t(label), iconPath: MDI[ic] }));
      frame.route = this._route || { prefix: PANEL_PATH, path: `/${area[0]}/${this._tab}` };
      this._sheetHtml = null;
    }

    // The console choice, Home Assistant's select, with more than one console
    _choice() {
      const box = this.shadowRoot.getElementById("choice");
      const consoles = this._consoles;
      box.hidden = consoles.length < 2;
      if (box.hidden) return;
      let select = box.querySelector("ha-select");
      if (!select) {
        select = document.createElement("ha-select");
        select.id = "console";
        box.appendChild(select);
      }
      select.label = this._t("console");
      const options = consoles.map((c) => ({ value: c.entry_id, label: c.title }));
      if (JSON.stringify(options) !== JSON.stringify(select.options)) select.options = options;
      // A console the address asks for that is not there is shown as nothing chosen
      const shown = consoles.some((c) => c.entry_id === this._shownConsole) ? this._shownConsole : undefined;
      if (select.value !== shown) select.value = shown;
    }

    _content() {
      if (this._note) return "";
      if (!this._data) return `<div class="muted">${esc(this._t("loading"))}</div>`;
      if (!this._data.consoles.length) return this._alert("info", this._t("noConsole"), this._t("noConsoleWhy"));
      const c = this._data.console;
      if (!c) {
        const known = this._data.consoles.find((x) => x.entry_id === this._shownConsole);
        return known && !known.loaded
          ? this._alert("warning", this._t("notLoaded"), this._t("notLoadedWhy"))
          : `<div class="muted">${esc(this._t("loading"))}</div>`;
      }
      switch (this._tab) {
        case "history": return this._history(c);
        case "speedtests": return this._speedtests(c);
        case "drills": return this._drills(c);
        case "now": return this._remoteNow(c);
        case "sessions": return this._sessions(c);
        default: return this._status(c);
      }
    }

    // Formatting in the number format and language of Home Assistant
    _num(v, d = 0) {
      return formatNumber(this._hass, v, d);
    }

    _when(iso) {
      const ms = time(iso);
      if (!Number.isFinite(ms)) return "–";
      return new Date(ms).toLocaleString(this._lang, { dateStyle: "medium", timeStyle: "short" });
    }

    _clock(iso) {
      const ms = time(iso);
      return Number.isFinite(ms) ? new Date(ms).toLocaleTimeString(this._lang, { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "–";
    }

    _day(iso) {
      return new Date(time(iso)).toLocaleDateString(this._lang, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    }

    _duration(seconds) {
      if (!Number.isFinite(seconds) || seconds < 0) return "–";
      const s = Math.round(seconds);
      if (s < 60) return `${s} s`;
      const m = Math.floor(s / 60);
      if (m < 60) return `${m} min${s % 60 ? ` ${s % 60} s` : ""}`;
      const h = Math.floor(m / 60);
      if (h < 48) return `${h} h${m % 60 ? ` ${m % 60} min` : ""}`;
      return `${Math.floor(h / 24)} d${h % 24 ? ` ${h % 24} h` : ""}`;
    }

    _bytes(b) {
      if (!Number.isFinite(b)) return "–";
      return b >= 1e9 ? `${this._num(b / 1e9, 2)} GB` : `${this._num(b / 1e6, 0)} MB`;
    }

    _name(c, key) {
      return c.uplinks.find((u) => u.key === key)?.name || key || "–";
    }

    _kv(rows) {
      return `<table class="kv">${rows.filter(Boolean).map(([k, v]) => `<tr><th>${esc(k)}</th><td>${v}</td></tr>`).join("")}</table>`;
    }

    _online(state) {
      return state === true ? this._badge(this._t("online"), "ok", "ok")
        : state === false ? this._badge(this._t("offline"), "err", "err")
          : this._badge(this._t("unknown"), "unknown", "unknown");
    }

    // Uplinks › Status: the console's summary, every uplink, the LTE backup
    _status(c) {
      const h = c.history;
      const lastSwitch = [...h.events].reverse().find((e) => e.type === "failover" || e.type === "restored");
      const lastTest = h.speedtests[h.speedtests.length - 1];
      const lastDrill = h.drills[h.drills.length - 1];
      const summary = this._kv([
        [this._t("activeUplink"), `${esc(this._name(c, c.active))}${c.active && c.active !== c.primary ? ` ${this._badge(this._t("backup"), "warn")}` : ""}`],
        [this._t("latency"), Number.isFinite(c.latency) ? `${this._num(c.latency)} ms` : "–"],
        c.admin && [this._t("backupEnforced"), c.backup_enforced == null ? "–" : esc(this._t(c.backup_enforced ? "yes" : "no"))],
        [this._t("lastSwitch"), lastSwitch ? `${esc(this._when(lastSwitch.at))} · ${esc(this._t(lastSwitch.type === "failover" ? "failover" : "restored", { to: lastSwitch.to_uplink_name || this._name(c, lastSwitch.to_uplink) }))}` : esc(this._t("never"))],
        [this._t("lastSpeedtest"), lastTest ? `${esc(this._when(lastTest.at))} · ${this._speeds(lastTest)}` : esc(this._t("never"))],
        [this._t("lastDrill"), lastDrill ? `${esc(this._when(lastDrill.at))} · → ${esc(this._duration(lastDrill.failover_seconds))} · ← ${esc(this._duration(lastDrill.restore_seconds))}` : esc(this._t("never"))],
      ]);
      const model = [c.gateway.model, c.gateway.version].filter(Boolean).join(" · ");
      let html = this._card(`<span>${esc(c.title)}</span> <span class="muted head-note">${esc(model)}</span>`, summary);
      html += this._networks(c);
      html += `<div class="grid">`;
      for (const u of c.uplinks) {
        const badges = [
          this._online(u.online),
          u.key === c.active ? this._badge(this._t("active"), "on") : "",
          u.key === c.primary ? this._badge(this._t("preferred")) : "",
        ].join("");
        const slow = Number.isFinite(u.expected_speed) && Number.isFinite(u.link_speed) && (u.link_speed < u.expected_speed || u.full_duplex === false);
        const speed = u.port
          ? `${Number.isFinite(u.link_speed) ? `${this._num(u.link_speed)} Mbit/s · ${esc(this._t(u.full_duplex === false ? "halfDuplex" : "fullDuplex"))}` : "–"}` +
            `${Number.isFinite(u.expected_speed) ? ` <span class="${slow ? "bad" : "muted"}">(${esc(this._t("expected"))} ${this._num(u.expected_speed)}${slow ? `, ${esc(this._t("slow"))}` : ""})</span>` : ""}`
          : null;
        html += this._card(`<span>${esc(u.name)}</span><span class="badges">${badges}</span>`, this._kv([
          [this._t("latency"), Number.isFinite(u.latency) ? `${this._num(u.latency)} ms` : "–"],
          [this._t("availability"), Number.isFinite(u.availability) ? `${this._num(u.availability, 1)} %` : "–"],
          speed && [this._t("linkSpeed"), speed],
          u.port && [this._t("port"), esc(u.port)],
          u.wan_type && [this._t("wanType"), esc(u.wan_type)],
          u.ipv4 && [this._t("ipv4"), `<span class="mono">${esc(u.ipv4)}</span>`],
          u.ipv6 && [this._t("ipv6"), `<span class="mono">${esc(u.ipv6)}</span>`],
        ]));
      }
      for (const l of c.lte) {
        const share = Number.isFinite(l.used_percent) ? l.used_percent : null;
        const warnAt = Number.isFinite(l.warning_bytes) && l.limit_bytes ? (100 * l.warning_bytes) / l.limit_bytes : null;
        // The bar and its warning mark say in words what they show
        const label = share == null ? "" : warnAt == null
          ? this._t("lteUsed", { p: this._num(share, 1) })
          : this._t("lteWarn", { p: this._num(share, 1), w: this._num(warnAt, 0) });
        const bar = share == null ? "" :
          `<div class="bar" role="img" aria-label="${esc(label)}"><div style="width:${Math.min(share, 100)}%"></div>${warnAt == null ? "" : `<i style="left:${warnAt.toFixed(1)}%"></i>`}</div>`;
        const badges = [this._online(l.connected), l.in_use ? this._badge(this._t("inUse"), "warn") : ""].join("");
        html += this._card(`<span>${esc(l.name)}</span><span class="badges">${badges}</span>`, this._kv([
          [this._t("used"), `${this._bytes(l.used_bytes)}${Number.isFinite(l.limit_bytes) ? ` / ${this._bytes(l.limit_bytes)} (${this._num(share, 1)} %)` : ""}${bar}`],
          l.period_start && [this._t("since"), esc(this._when(l.period_start))],
          [this._t("signal"), `${Number.isFinite(l.rssi) ? `RSSI ${this._num(l.rssi)} dBm` : "–"}${Number.isFinite(l.rsrp) ? ` · RSRP ${this._num(l.rsrp)} dBm` : ""}`],
          l.state && [this._t("state"), esc(l.state)],
          [this._t("model"), esc(l.model)],
        ]));
      }
      return html + `</div>`;
    }

    // Uplinks › Status: every local network, whether it reaches the internet now and over which uplink
    _networks(c) {
      if (!c.networks?.length) return "";
      // Each state with its own shape, word and colour; a cell's word is its label
      const mark = (value, yes, no) => {
        const [kind, word] = value === true ? ["ok", yes] : value === false ? ["err", no] : ["unknown", this._t("unknown")];
        return `<ha-svg-icon class="kind" style="color:${STATE_COLOR[kind]}" path="${MDI[kind]}" role="img" aria-label="${esc(word)}" title="${esc(word)}"></ha-svg-icon>`;
      };
      const head = `<tr><th>${esc(this._t("network"))}</th><th>${esc(this._t("internet"))}</th>` +
        c.uplinks.map((u) => `<th>${esc(u.name)}</th>`).join("") + `<th class="n">${esc(this._t("clients"))}</th></tr>`;
      const rows = c.networks.map((n) => `<tr><td>${esc(n.name)}</td><td>${this._online(n.internet)}</td>` +
        c.uplinks.map((u) => `<td>${mark(n.uplinks[u.key], `${u.name}: ${this._t("carries")}`, `${u.name}: ${this._t("notCarries")}`)}</td>`).join("") +
        `<td class="n">${Number.isFinite(n.clients) ? this._num(n.clients) : "–"}</td></tr>`).join("");
      return this._card(esc(this._t("networks")), `<div class="scroll"><table class="data">${head}${rows}</table></div>`, this._t("networksHelp"));
    }

    _speeds(s) {
      const parts = [];
      if (Number.isFinite(s.download)) parts.push(`↓ ${this._num(s.download)}`);
      if (Number.isFinite(s.upload)) parts.push(`↑ ${this._num(s.upload)} Mbit/s`);
      if (Number.isFinite(s.ping)) parts.push(`${this._num(s.ping)} ms`);
      if (s.uplink_name) parts.push(esc(s.uplink_name));
      return parts.join(" · ");
    }

    // Uplinks › History, newest first and by day; a return names how long the backup carried
    _history(c) {
      const h = c.history;
      const rows = [];
      const lastAt = {};
      for (const e of h.events) {
        let title = "";
        let note = "";
        let kind = "";
        if (e.type === "failover") {
          title = this._t("failover", { to: e.to_uplink_name || this._name(c, e.to_uplink) });
          note = this._t("failoverFrom", { from: e.from_uplink_name || this._name(c, e.from_uplink) });
          kind = "warn";
          lastAt.failover = e.at;
        } else if (e.type === "restored") {
          title = this._t("restored", { to: e.to_uplink_name || this._name(c, e.to_uplink) });
          if (lastAt.failover) note = this._t("after", { d: this._duration((time(e.at) - time(lastAt.failover)) / 1000) });
          lastAt.failover = null;
          kind = "ok";
        } else if (e.type === "brief_failover") {
          title = this._t("brief");
          note = this._t("briefNote");
          kind = "warn";
        } else if (e.type === "uplink_down") {
          title = this._t("down", { u: e.uplink_name || this._name(c, e.uplink) });
          kind = "err";
          lastAt[`down:${e.uplink}`] = e.at;
        } else if (e.type === "uplink_up") {
          title = this._t("up", { u: e.uplink_name || this._name(c, e.uplink) });
          const down = lastAt[`down:${e.uplink}`];
          if (down) note = this._t("after", { d: this._duration((time(e.at) - time(down)) / 1000) });
          lastAt[`down:${e.uplink}`] = null;
          kind = "ok";
        } else if (e.type === "public_ip_changed") {
          title = this._t("ip", { f: e.family === "ipv6" ? "IPv6" : "IPv4" });
          note = this._t("ipOf", { u: e.uplink_name || this._name(c, e.uplink) });
          kind = "ip";
        } else continue;
        rows.push({ at: e.at, title, note, kind });
      }
      const help = this._t("keptHistory", { n: this._num(h.days) });
      if (!rows.length) return this._card(esc(this._t("subHistory")), `<div class="muted">${esc(this._t("noEvents"))}</div>`, help);
      // The kind of each entry: its icon and its word, besides the colour
      const word = { warn: "evSwitch", ok: "evBack", err: "evDown", ip: "evChange" };
      let items = "";
      let day = null;
      for (const r of rows.reverse()) {
        const d = this._day(r.at);
        if (d !== day) {
          day = d;
          items += `<h2 class="day">${esc(d)}</h2>`;
        }
        items += `<div class="item"><ha-svg-icon class="kind" style="color:${STATE_COLOR[r.kind]}" path="${MDI[r.kind]}" role="img" aria-label="${esc(this._t(word[r.kind]))}"></ha-svg-icon>` +
          `<span class="clock">${esc(this._clock(r.at))}</span>` +
          `<div class="text"><span class="headline">${esc(r.title)}</span>${r.note ? `<span class="supporting">${esc(r.note)}</span>` : ""}</div></div>`;
      }
      return this._card(esc(this._t("subHistory")), items, help, "list");
    }

    // Tests › Speed tests: download over time per uplink, and every result
    _speedtests(c) {
      const tests = c.history.speedtests;
      const running = c.speedtest_running ? this._alert("info", this._t("running")) : "";
      const help = this._t("kept", { n: this._num(c.history.days) });
      if (!tests.length) return running + this._card(esc(this._t("results")), `<div class="muted">${esc(this._t("noSpeedtests"))}</div>`, help);
      const keys = [...new Set(tests.map((s) => s.uplink || ""))];
      const order = c.uplinks.map((u) => u.key.toLowerCase());
      keys.sort((a, b) => (order.indexOf(a) + 99) % 99 - (order.indexOf(b) + 99) % 99);
      const color = (k) => SERIES[keys.indexOf(k) % SERIES.length];
      const nameOf = (k) => tests.find((s) => (s.uplink || "") === k)?.uplink_name || k || "–";
      const chart = tests.length > 1 ? this._chart(tests, keys, color, keys.map(nameOf).join(", ")) : "";
      const legend = keys.map((k) => `<span class="legend"><i style="background:${color(k)}"></i>${esc(nameOf(k))}</span>`).join("");
      const rows = [...tests].reverse().map((s) =>
        `<tr><td>${esc(this._when(s.at))}</td><td><span class="legend"><i style="background:${color(s.uplink || "")}"></i>${esc(s.uplink_name || "–")}</span></td>` +
        `<td class="n">${this._num(s.download)}</td><td class="n">${this._num(s.upload)}</td><td class="n">${this._num(s.ping)}</td></tr>`).join("");
      const table = `<div class="scroll"><table class="data"><tr><th>${esc(this._t("time"))}</th><th>${esc(this._t("uplink"))}</th>` +
        `<th class="n">${esc(this._t("download"))} Mbit/s</th><th class="n">${esc(this._t("upload"))} Mbit/s</th><th class="n">${esc(this._t("ping"))} ms</th></tr>${rows}</table></div>`;
      return running + (chart ? this._card(esc(this._t("download")), `${chart}<div class="legends">${legend}</div>`) : "") +
        this._card(esc(this._t("results")), table, help);
    }

    // Download over time, one line per uplink; the axis starts at 0
    _chart(tests, keys, color, names) {
      const W = 800, H = 220, L = 48, R = 12, T = 12, B = 28;
      const xs = tests.map((s) => time(s.at));
      const x0 = Math.min(...xs), x1 = Math.max(...xs);
      const top = Math.max(...tests.map((s) => s.download || 0)) || 1;
      const step = [1, 2, 5, 10, 20, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000].find((v) => top / v <= 5) || 10000;
      const yMax = Math.ceil(top / step) * step;
      const px = (ms) => L + (x1 === x0 ? (W - L - R) / 2 : ((ms - x0) / (x1 - x0)) * (W - L - R));
      const py = (v) => T + (1 - v / yMax) * (H - T - B);
      let grid = "";
      for (let v = 0; v <= yMax; v += step) {
        grid += `<line x1="${L}" x2="${W - R}" y1="${py(v)}" y2="${py(v)}" class="grid"/>` +
          `<text x="${L - 6}" y="${py(v) + 4}" text-anchor="end">${esc(this._num(v))}</text>`;
      }
      const labels = [x0, (x0 + x1) / 2, x1].map((ms, i) =>
        `<text x="${px(ms)}" y="${H - 8}" text-anchor="${["start", "middle", "end"][i]}">${esc(new Date(ms).toLocaleDateString(this._lang, { day: "numeric", month: "short" }))}</text>`).join("");
      let lines = "";
      for (const k of keys) {
        const pts = tests.filter((s) => (s.uplink || "") === k && Number.isFinite(s.download)).map((s) => [px(time(s.at)), py(s.download)]);
        if (pts.length > 1) lines += `<polyline points="${pts.map((p) => p.map((n) => n.toFixed(1)).join(",")).join(" ")}" style="stroke:${color(k)}"/>`;
        lines += pts.map(([x, y]) => `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3" style="fill:${color(k)}"/>`).join("");
      }
      return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(this._t("chart", { names }))}">${grid}${labels}${lines}</svg>`;
    }

    // Tests › Drills: every drill with failover time, return time and LTE data
    _drills(c) {
      const drills = c.history.drills;
      const hint = c.admin ? "" : this._alert("info", this._t("noAdmin"), this._t("noAdminWhy"));
      if (!drills.length) {
        return hint + this._card(esc(this._t("subDrills")), `<div class="muted">${esc(this._t("noDrills"))}</div>`,
          `${this._t("noDrillsHow")} ${this._t("keptDrills")}`);
      }
      const avg = (key) => {
        const v = drills.map((d) => d[key]).filter(Number.isFinite);
        return v.length ? v.reduce((a, b) => a + b, 0) / v.length : NaN;
      };
      const rows = [...drills].reverse().map((d) =>
        `<tr><td>${esc(this._when(d.at))}</td><td class="n">${esc(this._duration(d.failover_seconds))}</td>` +
        `<td class="n">${esc(this._duration(d.restore_seconds))}</td><td class="n">${esc(this._bytes(d.data_used_bytes))}</td></tr>`).join("");
      const mean = drills.length > 1
        ? `<tr class="sum"><td>${esc(this._t("average"))}</td><td class="n">${esc(this._duration(avg("failover_seconds")))}</td>` +
          `<td class="n">${esc(this._duration(avg("restore_seconds")))}</td><td class="n">${esc(this._bytes(avg("data_used_bytes")))}</td></tr>`
        : "";
      const table = `<div class="scroll"><table class="data"><tr><th>${esc(this._t("drillStart"))}</th><th class="n">${esc(this._t("failoverTime"))}</th>` +
        `<th class="n">${esc(this._t("restoreTime"))}</th><th class="n">${esc(this._t("data"))}</th></tr>${rows}${mean}</table></div>`;
      return hint + this._card(esc(this._t("subDrills")), table, this._t("keptDrills"));
    }

    // Remote › Connected now
    _remoteNow(c) {
      const now = Date.now();
      let current;
      if (c.remote == null) current = `<div class="muted">${esc(this._t("remoteUnknown"))}</div>`;
      else if (!c.remote.length) current = `<div class="muted">${esc(this._t("noRemote"))}</div>`;
      else {
        current = `<div class="scroll"><table class="data"><tr><th>${esc(this._t("name"))}</th><th>${esc(this._t("kind"))}</th><th>${esc(this._t("from"))}</th><th class="n">${esc(this._t("duration"))}</th></tr>` +
          c.remote.map((r) => `<tr><td>${esc(r.name)}</td><td>${esc(r.type)}</td><td>${esc(this._when(r.since))}</td>` +
            `<td class="n">${esc(this._duration((now - time(r.since)) / 1000))}</td></tr>`).join("") + `</table></div>`;
      }
      return this._card(esc(this._t("subNow")), current);
    }

    // Remote › Sessions: the finished ones
    _sessions(c) {
      const closed = c.history.sessions.filter((s) => s.until).reverse();
      const sessions = closed.length
        ? `<div class="scroll"><table class="data"><tr><th>${esc(this._t("name"))}</th><th>${esc(this._t("kind"))}</th><th>${esc(this._t("from"))}</th><th>${esc(this._t("until"))}</th><th class="n">${esc(this._t("duration"))}</th></tr>` +
          closed.map((s) => `<tr><td>${esc(s.name)}</td><td>${esc(s.type)}</td><td>${esc(this._when(s.at))}</td><td>${esc(this._when(s.until))}</td>` +
            `<td class="n">${esc(this._duration((time(s.until) - time(s.at)) / 1000))}</td></tr>`).join("") + `</table></div>`
        : `<div class="muted">${esc(this._t("noSessions"))}</div>`;
      return this._card(esc(this._t("subSessions")), sessions, this._t("kept", { n: this._num(c.history.days) }));
    }
  }

  const STYLE = `
    /* The panel's own container has no height of its own: the page takes the window */
    :host { display: block; height: 100vh; height: 100dvh; background: var(--primary-background-color);
      color: var(--primary-text-color); font-family: var(--ha-font-family-body, Roboto, Noto, sans-serif);
      font-size: var(--ha-font-size-m, 14px); line-height: 1.5; -webkit-font-smoothing: antialiased; }
    * { box-sizing: border-box; }
    hass-tabs-subpage { height: 100%; }
    .actions { display: flex; align-items: center; }
    .stamp { color: var(--secondary-text-color); font-size: var(--ha-font-size-m, 14px); white-space: nowrap; margin-inline-end: 4px; font-variant-numeric: tabular-nums; }
    @media (max-width: 600px) { .stamp { display: none; } }
    #refresh.spin { animation: spin 1s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .page { display: flex; flex-direction: column; min-height: 100%; }
    .subtabs { flex: none; padding: 0 16px; }
    .sheet { width: 100%; max-width: 1040px; margin: 0 auto; padding: 16px; display: flex; flex-direction: column; gap: 16px; }
    .narrow .sheet { padding: 8px; gap: 8px; }
    .body { display: flex; flex-direction: column; gap: 16px; }
    .narrow .body { gap: 8px; }
    .choice[hidden] { display: none; }
    .choice ha-select { display: block; max-width: 360px; }
    /* The title of a card as Home Assistant draws it, with room for the (i) at its end */
    .card-header { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; margin: 0; padding: 12px 8px 12px 16px; min-height: 48px;
      color: var(--ha-card-header-color, var(--primary-text-color)); font-size: var(--ha-card-header-font-size, var(--ha-font-size-2xl, 24px));
      font-weight: var(--ha-font-weight-normal, 400); line-height: var(--ha-line-height-condensed, 1.2); }
    .card-header ha-icon-button.help { margin-inline-start: auto; }
    .card-content { padding: 0 16px 16px; }
    .card-content.top { padding-top: 16px; }
    ha-icon-button.help { --mdc-icon-button-size: 32px; --mdc-icon-size: 18px; --ha-icon-button-size: 32px; color: var(--secondary-text-color); flex: none; }
    ha-tooltip { --max-width: min(360px, 90vw); }
    .alertact { display: flex; align-items: center; gap: 4px; }
    /* Name and version at the bottom, centred */
    .about { text-align: center; color: var(--secondary-text-color); font-size: var(--ha-font-size-s, 12px); padding: 8px 0 24px; margin-top: auto; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
    .narrow .grid { grid-template-columns: 1fr; gap: 8px; }
    ha-card { display: block; overflow: hidden; }
    ha-card.list .card-content { padding: 0 0 8px; }
    .head-note { font-size: var(--ha-font-size-m, 14px); }
    .badges { display: inline-flex; gap: 4px; flex-wrap: wrap; }
    ha-badge { --ha-badge-size: 26px; --ha-badge-icon-size: 16px; vertical-align: middle; }
    .bad { color: var(--error-color, #db4437); }
    .day { margin: 0; padding: 12px 16px 4px; font-size: var(--ha-font-size-s, 12px); font-weight: var(--ha-font-weight-medium, 500);
      color: var(--secondary-text-color); }
    .item { display: flex; align-items: center; gap: 12px; min-height: 52px; padding: 6px 16px; }
    .item + .item { border-top: 1px solid var(--divider-color); }
    .kind { flex: none; --mdc-icon-size: 18px; width: 18px; height: 18px; }
    .clock { font-variant-numeric: tabular-nums; color: var(--secondary-text-color); flex: none; min-width: 72px; }
    .text { display: flex; flex: 1; flex-direction: column; min-width: 0; }
    .headline { line-height: 20px; }
    .supporting { font-size: var(--ha-font-size-s, 12px); line-height: 16px; color: var(--secondary-text-color); margin-top: 2px; }
    .muted { color: var(--secondary-text-color); }
    .mono { font-family: var(--ha-font-family-code, monospace); font-size: 13px; word-break: break-all; }
    table { border-collapse: collapse; width: 100%; }
    table.kv th { width: 40%; text-align: left; font-weight: 400; color: var(--secondary-text-color); padding: 8px 16px 8px 0; vertical-align: top; }
    table.kv td { padding: 8px 0; }
    table.kv tr + tr th, table.kv tr + tr td { border-top: 1px solid var(--divider-color); }
    table.data th { text-align: left; font-size: var(--ha-font-size-s, 12px); font-weight: 500; padding: 8px 12px 8px 0; white-space: nowrap; }
    table.data td { padding: 6px 12px 6px 0; border-top: 1px solid var(--divider-color); white-space: nowrap; }
    table.data td:last-child, table.data th:last-child { padding-right: 0; }
    table.data .n { text-align: right; font-variant-numeric: tabular-nums; }
    table.data tr.sum td { font-weight: 500; border-top: 2px solid var(--divider-color); }
    .scroll { overflow-x: auto; }
    .bar { position: relative; height: 6px; margin-top: 6px; border-radius: 3px; background: color-mix(in srgb, var(--primary-text-color) 10%, transparent); }
    .bar div { height: 100%; border-radius: 3px; background: var(--primary-color); }
    .bar i { position: absolute; top: -2px; bottom: -2px; width: 2px; background: var(--warning-color, #ffa600); }
    .chart { width: 100%; height: auto; display: block; }
    .chart .grid { stroke: var(--divider-color); stroke-width: 1; vector-effect: non-scaling-stroke; }
    .chart text { fill: var(--secondary-text-color); font-size: 12px; }
    .chart polyline { fill: none; stroke-width: 2; vector-effect: non-scaling-stroke; }
    .legends { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 8px; }
    .legend { display: inline-flex; align-items: center; gap: 6px; }
    .legend i { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
  `;

  customElements.define("network-plus-panel", NetworkPlusPanel);
}
