// Network Plus panel: the page in the sidebar where administrators see the
// uplinks of their consoles now, the switches between them, the speed tests,
// the backup drills and the remote sessions. Everything comes from the
// integration over the WebSocket API; the page is built from Home Assistant's
// own elements (ha-card, ha-alert, ha-icon-button) and its theme.
const PANEL_PATH = "/network-plus";
// The page asks again this often while it is open (ms)
const REFRESH = 30000;

if (!customElements.get("network-plus-panel")) {
  const MDI = {
    menu: "M3,6H21V8H3V6M3,11H21V13H3V11M3,16H21V18H3V16Z",
    uplinks: "M12,2A8,8 0 0,0 4,10C4,14.03 7,17.42 11,17.93V19H10A1,1 0 0,0 9,20H2V22H9A1,1 0 0,0 10,23H14A1,1 0 0,0 15,22H22V20H15A1,1 0 0,0 14,19H13V17.93C17,17.43 20,14.03 20,10A8,8 0 0,0 12,2M12,4C12,4 12.74,5.28 13.26,7H10.74C11.26,5.28 12,4 12,4M9.77,4.43C9.5,4.93 9.09,5.84 8.74,7H6.81C7.5,5.84 8.5,4.93 9.77,4.43M14.23,4.44C15.5,4.94 16.5,5.84 17.19,7H15.26C14.91,5.84 14.5,4.93 14.23,4.44M6.09,9H8.32C8.28,9.33 8.25,9.66 8.25,10C8.25,10.34 8.28,10.67 8.32,11H6.09C6.03,10.67 6,10.34 6,10C6,9.66 6.03,9.33 6.09,9M10.32,9H13.68C13.72,9.33 13.75,9.66 13.75,10C13.75,10.34 13.72,10.67 13.68,11H10.32C10.28,10.67 10.25,10.34 10.25,10C10.25,9.66 10.28,9.33 10.32,9M15.68,9H17.91C17.97,9.33 18,9.66 18,10C18,10.34 17.97,10.67 17.91,11H15.68C15.72,10.67 15.75,10.34 15.75,10C15.75,9.66 15.72,9.33 15.68,9M6.81,13H8.74C9.09,14.16 9.5,15.07 9.77,15.56C8.5,15.06 7.5,14.16 6.81,13M10.74,13H13.26C12.74,14.72 12,16 12,16C12,16 11.26,14.72 10.74,13M15.26,13H17.19C16.5,14.16 15.5,15.07 14.23,15.57C14.5,15.07 14.91,14.16 15.26,13Z",
    events: "M4 2V8H2V2H4M2 22V16H4V22H2M5 12C5 13.11 4.11 14 3 14C1.9 14 1 13.11 1 12C1 10.9 1.9 10 3 10C4.11 10 5 10.9 5 12M16 4C20.42 4 24 7.58 24 12C24 16.42 20.42 20 16 20C12.4 20 9.36 17.62 8.35 14.35L6 12L8.35 9.65C9.36 6.38 12.4 4 16 4M16 6C12.69 6 10 8.69 10 12C10 15.31 12.69 18 16 18C19.31 18 22 15.31 22 12C22 8.69 19.31 6 16 6M15 13V8H16.5V12.2L19.5 14L18.68 15.26L15 13Z",
    speedtests: "M12,16A3,3 0 0,1 9,13C9,11.88 9.61,10.9 10.5,10.39L20.21,4.77L14.68,14.35C14.18,15.33 13.17,16 12,16M12,3C13.81,3 15.5,3.5 16.97,4.32L14.87,5.53C14,5.19 13,5 12,5A8,8 0 0,0 4,13C4,15.21 4.89,17.21 6.34,18.65H6.35C6.74,19.04 6.74,19.67 6.35,20.06C5.96,20.45 5.32,20.45 4.93,20.07V20.07C3.12,18.26 2,15.76 2,13A10,10 0 0,1 12,3M22,13C22,15.76 20.88,18.26 19.07,20.07V20.07C18.68,20.45 18.05,20.45 17.66,20.06C17.27,19.67 17.27,19.04 17.66,18.65V18.65C19.11,17.2 20,15.21 20,13C20,12 19.81,11 19.46,10.1L20.67,8C21.5,9.5 22,11.18 22,13Z",
    drills: "M21,9L17,5V8H10V10H17V13M7,11L3,15L7,19V16H14V14H7V11Z",
    remote: "M9,5H15L12,8L9,5M10.5,14.66C10.2,15 10,15.5 10,16A2,2 0 0,0 12,18A2,2 0 0,0 14,16C14,15.45 13.78,14.95 13.41,14.59L14.83,13.17C15.55,13.9 16,14.9 16,16A4,4 0 0,1 12,20A4,4 0 0,1 8,16C8,14.93 8.42,13.96 9.1,13.25L9.09,13.24L16.17,6.17V6.17C16.89,5.45 17.89,5 19,5A4,4 0 0,1 23,9A4,4 0 0,1 19,13C17.9,13 16.9,12.55 16.17,11.83L17.59,10.41C17.95,10.78 18.45,11 19,11A2,2 0 0,0 21,9A2,2 0 0,0 19,7C18.45,7 17.95,7.22 17.59,7.59L10.5,14.66M6.41,7.59C6.05,7.22 5.55,7 5,7A2,2 0 0,0 3,9A2,2 0 0,0 5,11C5.55,11 6.05,10.78 6.41,10.41L7.83,11.83C7.1,12.55 6.1,13 5,13A4,4 0 0,1 1,9A4,4 0 0,1 5,5C6.11,5 7.11,5.45 7.83,6.17V6.17L10.59,8.93L9.17,10.35L6.41,7.59Z",
    refresh: "M17.65,6.35C16.2,4.9 14.21,4 12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20C15.73,20 18.84,17.45 19.73,14H17.65C16.83,16.33 14.61,18 12,18A6,6 0 0,1 6,12A6,6 0 0,1 12,6C13.66,6 15.14,6.69 16.22,7.78L13,11H20V4L17.65,6.35Z",
  };

  // Tab, icon, text
  const TABS = [
    ["uplinks", "uplinks", "tabUplinks"],
    ["events", "events", "tabEvents"],
    ["speedtests", "speedtests", "tabSpeedtests"],
    ["drills", "drills", "tabDrills"],
    ["remote", "remote", "tabRemote"],
  ];

  const TEXTS = {
    en: {
      tabUplinks: "Uplinks", tabEvents: "History", tabSpeedtests: "Speed tests", tabDrills: "Drills",
      tabRemote: "Remote", menu: "Menu", refresh: "Refresh", console: "Console",
      loading: "Loading …", noConsole: "No console is set up. Add one under Settings → Devices & services.",
      notLoaded: "This console is not loaded at the moment. Home Assistant tries again by itself.",
      failed: "The data could not be read", now: "Now", active: "active", preferred: "preferred", backup: "backup",
      online: "Online", offline: "Offline", unknown: "Unknown", activeUplink: "Active uplink", latency: "Latency",
      availability: "Availability", linkSpeed: "Link speed", expected: "expected", halfDuplex: "half duplex",
      fullDuplex: "full duplex", wanType: "Connection", port: "Port", ipv4: "Public IPv4", ipv6: "Public IPv6",
      backupEnforced: "Backup uplink enforced", yes: "yes", no: "no", lastSwitch: "Last switch", lastSpeedtest: "Last speed test",
      lastDrill: "Last drill", never: "none yet", lte: "LTE backup", connected: "Registered", inUse: "In use",
      state: "State", model: "Model", used: "Data used", limit: "Data plan", signal: "Signal", since: "since",
      failover: "Switched to {to}", failoverFrom: "from {from}", restored: "Back on {to}", after: "after {d}",
      brief: "Brief failover", briefNote: "the console switched for a moment", down: "{u} down", up: "{u} up again",
      ip: "New public {f} address", ipOf: "on {u}", noEvents: "Nothing happened in the kept period.",
      kept: "Kept for {n} days.", keptDrills: "Drills are kept for good.", download: "Download", upload: "Upload",
      ping: "Ping", uplink: "Uplink", time: "Time", noSpeedtests: "No speed test in the kept period.",
      running: "A speed test is running.", average: "Average", drillStart: "Started", failoverTime: "To the backup",
      restoreTime: "Back", data: "LTE data", noDrills: "No backup drill yet. The switch “Enforce backup uplink” starts one.",
      noAdmin: "Drills need the admin opt-in of the integration.", remoteNow: "Connected now", noRemote: "No remote client is connected.",
      remoteUnknown: "The console does not tell its remote clients.", sessions: "Sessions", name: "Name", kind: "Kind",
      from: "From", until: "Until", duration: "Duration", noSessions: "No finished session in the kept period.",
    },
    de: {
      tabUplinks: "Leitungen", tabEvents: "Verlauf", tabSpeedtests: "Speedtests", tabDrills: "Übungen",
      tabRemote: "Fernzugriff", menu: "Menü", refresh: "Aktualisieren", console: "Konsole",
      loading: "Lädt …", noConsole: "Keine Konsole eingerichtet. Unter Einstellungen → Geräte & Dienste hinzufügen.",
      notLoaded: "Diese Konsole ist gerade nicht geladen. Home Assistant versucht es selbst erneut.",
      failed: "Die Daten ließen sich nicht lesen", now: "Jetzt", active: "aktiv", preferred: "bevorzugt", backup: "Backup",
      online: "Verbunden", offline: "Getrennt", unknown: "Unbekannt", activeUplink: "Aktive Leitung", latency: "Latenz",
      availability: "Verfügbarkeit", linkSpeed: "Link-Geschwindigkeit", expected: "erwartet", halfDuplex: "Halbduplex",
      fullDuplex: "Vollduplex", wanType: "Anschluss", port: "Port", ipv4: "Öffentliche IPv4", ipv6: "Öffentliche IPv6",
      backupEnforced: "Backup-Leitung erzwungen", yes: "ja", no: "nein", lastSwitch: "Letzte Umschaltung", lastSpeedtest: "Letzter Speedtest",
      lastDrill: "Letzte Übung", never: "noch keine", lte: "LTE-Backup", connected: "Eingebucht", inUse: "In Benutzung",
      state: "Zustand", model: "Modell", used: "Verbraucht", limit: "Datenplan", signal: "Signal", since: "seit",
      failover: "Umgeschaltet auf {to}", failoverFrom: "von {from}", restored: "Zurück auf {to}", after: "nach {d}",
      brief: "Kurze Umschaltung", briefNote: "die Konsole hat kurz umgeschaltet", down: "{u} getrennt", up: "{u} wieder verbunden",
      ip: "Neue öffentliche {f}-Adresse", ipOf: "an {u}", noEvents: "Im aufbewahrten Zeitraum ist nichts passiert.",
      kept: "Aufbewahrt für {n} Tage.", keptDrills: "Übungen bleiben dauerhaft.", download: "Download", upload: "Upload",
      ping: "Ping", uplink: "Leitung", time: "Zeit", noSpeedtests: "Kein Speedtest im aufbewahrten Zeitraum.",
      running: "Ein Speedtest läuft gerade.", average: "Mittel", drillStart: "Beginn", failoverTime: "Umschaltung",
      restoreTime: "Rückkehr", data: "LTE-Daten", noDrills: "Noch keine Backup-Übung. Der Schalter „Backup-Leitung erzwingen“ startet eine.",
      noAdmin: "Übungen brauchen den Admin-Zugang der Integration.", remoteNow: "Gerade verbunden", noRemote: "Kein Fernzugriff verbunden.",
      remoteUnknown: "Die Konsole meldet ihre Fernzugriffe nicht.", sessions: "Sitzungen", name: "Name", kind: "Art",
      from: "Von", until: "Bis", duration: "Dauer", noSessions: "Keine beendete Sitzung im aufbewahrten Zeitraum.",
    },
  };

  // Colours of the uplinks in the chart: the theme's, in failover order
  const SERIES = ["var(--primary-color)", "var(--orange-color, #ff9800)", "var(--green-color, #4caf50)", "var(--purple-color, #9c27b0)"];

  const esc = (v) =>
    String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const icon = (name) => `<svg class="mdi" viewBox="0 0 24 24" aria-hidden="true"><path d="${MDI[name]}"/></svg>`;
  const fill = (text, values) => text.replace(/\{(\w+)\}/g, (_, k) => values[k] ?? "");
  const time = (iso) => (iso ? Date.parse(iso) : NaN);

  class NetworkPlusPanel extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._tab = "uplinks";
      this._entryId = null;
      this._data = null;
      this._error = null;
      this._frameKey = null;
      // Counts the requests: only the answer to the latest one counts
      this._request = 0;
      this._bindOnce();
    }

    set hass(hass) {
      const first = !this._hass;
      this._hass = hass;
      if (first) this._load();
      if (first || this._menuShown !== this._showMenu) this._render();
    }

    set narrow(narrow) {
      this._narrow = narrow;
      this._render();
    }

    set route(route) {
      const tab = (route?.path || "").split("/")[1] || "uplinks";
      this._tab = TABS.some(([id]) => id === tab) ? tab : "uplinks";
      this._render();
    }

    set panel(panel) {
      this._panelInfo = panel;
    }

    connectedCallback() {
      this._timer = window.setInterval(() => this._load(), REFRESH);
      this._onVisible = () => document.visibilityState === "visible" && this._load();
      document.addEventListener("visibilitychange", this._onVisible);
      if (this._hass) this._load();
    }

    disconnectedCallback() {
      window.clearInterval(this._timer);
      document.removeEventListener("visibilitychange", this._onVisible);
    }

    get _t() {
      const lang = (this._hass?.language || "en").split("-")[0];
      return TEXTS[lang] || TEXTS.en;
    }

    get _lang() {
      return this._hass?.language || "en";
    }

    get _showMenu() {
      return !!this._narrow || this._hass?.dockedSidebar === "always_hidden";
    }

    // An answer that arrives after a newer request was sent (another console
    // chosen meanwhile) is dropped, so it cannot bring back the old console
    async _load() {
      if (!this._hass || document.visibilityState === "hidden") return;
      const request = ++this._request;
      const msg = { type: "network_plus/overview" };
      if (this._entryId) msg.entry_id = this._entryId;
      let data;
      try {
        data = await this._hass.callWS(msg);
      } catch (err) {
        if (request !== this._request) return;
        // A console removed meanwhile: show the first one again
        if (err?.code === "not_found" && this._entryId) {
          this._entryId = null;
          return this._load();
        }
        this._error = err?.message || String(err);
        this._render();
        return;
      }
      if (request !== this._request) return;
      this._data = data;
      this._error = null;
      this._entryId = data.console?.entry_id || this._entryId;
      this._render();
    }

    // One listener each on the shadow root: the content under it is redrawn
    _bindOnce() {
      const root = this.shadowRoot;
      root.addEventListener("click", (e) => {
        const tab = e.target.closest?.("a[data-tab]");
        if (tab) {
          e.preventDefault();
          window.history.pushState(null, "", `${PANEL_PATH}/${tab.dataset.tab}`);
          window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
          return;
        }
        const act = e.target.closest?.("[data-act]")?.dataset.act;
        if (act === "menu") this.dispatchEvent(new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true }));
        if (act === "refresh") this._load();
      });
      root.addEventListener("change", (e) => {
        if (e.target.id !== "console") return;
        this._entryId = e.target.value;
        this._data = { ...this._data, console: null };
        this._render();
        this._load();
      });
    }

    _render() {
      if (!this._hass) return;
      const consoles = this._data?.consoles || [];
      const key = [this._tab, !!this._narrow, this._showMenu, this._lang, consoles.map((c) => `${c.entry_id}:${c.title}`).join(","), this._entryId].join("|");
      if (key !== this._frameKey) {
        this._frameKey = key;
        this._menuShown = this._showMenu;
        this._frame(consoles);
      }
      const sheet = this.shadowRoot.getElementById("sheet");
      const html = this._content();
      if (sheet && html !== this._sheetHtml) {
        this._sheetHtml = html;
        sheet.innerHTML = html;
      }
      // At the bottom, as on Home Assistant's own pages: the integration and its version
      const about = this.shadowRoot.getElementById("about");
      const version = this._data?.version;
      if (about) about.textContent = version ? `Network Plus ${version}` : "";
    }

    // The parts that stay while the page is open: toolbar, tabs, console choice
    _frame(consoles) {
      const t = this._t;
      const tabs = TABS.map(([id, ic, label]) =>
        `<a class="tab${this._tab === id ? " active" : ""}" data-tab="${id}" href="${PANEL_PATH}/${id}">` +
        `${this._narrow ? icon(ic) : ""}<span>${esc(t[label])}</span></a>`).join("");
      const menu = this._showMenu
        ? `<ha-icon-button class="menu-button" data-act="menu" label="${esc(t.menu)}" path="${MDI.menu}"></ha-icon-button>`
        : "";
      const choice = consoles.length > 1
        ? `<select id="console" aria-label="${esc(t.console)}">${consoles.map((c) =>
          `<option value="${esc(c.entry_id)}"${c.entry_id === this._entryId ? " selected" : ""}>${esc(c.title)}</option>`).join("")}</select>`
        : "";
      this.shadowRoot.innerHTML = `
        <style>${STYLE}</style>
        <div class="app${this._narrow ? " narrow" : ""}">
          <div class="toolbar">${menu}<div class="main-title">Network Plus</div>
            ${this._narrow ? "" : `<nav class="tabs">${tabs}</nav>`}
            <div class="tools">${choice}<ha-icon-button data-act="refresh" label="${esc(t.refresh)}" path="${MDI.refresh}"></ha-icon-button></div></div>
          <div class="content"><div class="sheet" id="sheet"></div><div class="about" id="about"></div></div>
          ${this._narrow ? `<nav class="bottom-tabs">${tabs}</nav>` : ""}
        </div>`;
      this._sheetHtml = null;
    }

    _content() {
      const t = this._t;
      if (this._error) return this._alert(`${t.failed}: ${this._error}`, "error");
      if (!this._data) return `<div class="muted">${esc(t.loading)}</div>`;
      if (!this._data.consoles.length) return this._alert(t.noConsole);
      const c = this._data.console;
      if (!c) {
        const known = this._data.consoles.find((x) => x.entry_id === this._entryId);
        return known && !known.loaded ? this._alert(t.notLoaded, "warning") : `<div class="muted">${esc(t.loading)}</div>`;
      }
      switch (this._tab) {
        case "events": return this._events(c);
        case "speedtests": return this._speedtests(c);
        case "drills": return this._drills(c);
        case "remote": return this._remote(c);
        default: return this._uplinks(c);
      }
    }

    // Formatting in the language of Home Assistant
    _num(v, d = 0) {
      return Number.isFinite(v) ? v.toLocaleString(this._lang, { minimumFractionDigits: d, maximumFractionDigits: d }) : "–";
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

    // Home Assistant's card; the header as a slotted .card-header takes its style
    // and can hold chips next to the title
    _card(title, body, extra = "") {
      return `<ha-card class="${extra.trim()}">${title ? `<h1 class="card-header">${title}</h1>` : ""}<div class="card-content">${body}</div></ha-card>`;
    }

    _alert(text, type = "info") {
      return `<ha-alert alert-type="${type}">${esc(text)}</ha-alert>`;
    }

    _kv(rows) {
      return `<table class="kv">${rows.filter(Boolean).map(([k, v]) => `<tr><th>${esc(k)}</th><td>${v}</td></tr>`).join("")}</table>`;
    }

    _chip(text, kind = "") {
      return `<span class="chip ${kind}">${esc(text)}</span>`;
    }

    _uplinks(c) {
      const t = this._t;
      const h = c.history;
      const lastSwitch = [...h.events].reverse().find((e) => e.type === "failover" || e.type === "restored");
      const lastTest = h.speedtests[h.speedtests.length - 1];
      const lastDrill = h.drills[h.drills.length - 1];
      const summary = this._kv([
        [t.activeUplink, `${esc(this._name(c, c.active))}${c.active && c.active !== c.primary ? ` ${this._chip(t.backup, "warn")}` : ""}`],
        [t.latency, Number.isFinite(c.latency) ? `${this._num(c.latency)} ms` : "–"],
        c.admin && [t.backupEnforced, c.backup_enforced == null ? "–" : esc(c.backup_enforced ? t.yes : t.no)],
        [t.lastSwitch, lastSwitch ? `${esc(this._when(lastSwitch.at))} · ${esc(fill(lastSwitch.type === "failover" ? t.failover : t.restored, { to: lastSwitch.to_uplink_name || this._name(c, lastSwitch.to_uplink) }))}` : esc(t.never)],
        [t.lastSpeedtest, lastTest ? `${esc(this._when(lastTest.at))} · ${this._speeds(lastTest)}` : esc(t.never)],
        [t.lastDrill, lastDrill ? `${esc(this._when(lastDrill.at))} · → ${esc(this._duration(lastDrill.failover_seconds))} · ← ${esc(this._duration(lastDrill.restore_seconds))}` : esc(t.never)],
      ]);
      const model = [c.gateway.model, c.gateway.version].filter(Boolean).join(" · ");
      let html = this._card(`${esc(c.title)} <span class="muted head-note">${esc(model)}</span>`, summary);
      html += `<div class="grid">`;
      for (const u of c.uplinks) {
        const chips = [
          u.online === true ? this._chip(t.online, "ok") : u.online === false ? this._chip(t.offline, "err") : this._chip(t.unknown),
          u.key === c.active ? this._chip(t.active, "on") : "",
          u.key === c.primary ? this._chip(t.preferred) : "",
        ].join("");
        const slow = Number.isFinite(u.expected_speed) && Number.isFinite(u.link_speed) && (u.link_speed < u.expected_speed || u.full_duplex === false);
        const speed = u.port
          ? `${Number.isFinite(u.link_speed) ? `${this._num(u.link_speed)} Mbit/s · ${esc(u.full_duplex === false ? t.halfDuplex : t.fullDuplex)}` : "–"}` +
            `${Number.isFinite(u.expected_speed) ? ` <span class="${slow ? "bad" : "muted"}">(${esc(t.expected)} ${this._num(u.expected_speed)})</span>` : ""}`
          : null;
        html += this._card(`${esc(u.name)} <span class="chips">${chips}</span>`, this._kv([
          [t.latency, Number.isFinite(u.latency) ? `${this._num(u.latency)} ms` : "–"],
          [t.availability, Number.isFinite(u.availability) ? `${this._num(u.availability, 1)} %` : "–"],
          speed && [t.linkSpeed, speed],
          u.port && [t.port, esc(u.port)],
          u.wan_type && [t.wanType, esc(u.wan_type)],
          u.ipv4 && [t.ipv4, `<span class="mono">${esc(u.ipv4)}</span>`],
          u.ipv6 && [t.ipv6, `<span class="mono">${esc(u.ipv6)}</span>`],
        ]));
      }
      for (const l of c.lte) {
        const share = Number.isFinite(l.used_percent) ? l.used_percent : null;
        const bar = share == null ? "" :
          `<div class="bar"><div style="width:${Math.min(share, 100)}%"></div>${Number.isFinite(l.warning_bytes) && l.limit_bytes ? `<i style="left:${(100 * l.warning_bytes / l.limit_bytes).toFixed(1)}%"></i>` : ""}</div>`;
        const chips = [
          l.connected === true ? this._chip(t.online, "ok") : l.connected === false ? this._chip(t.offline, "err") : this._chip(t.unknown),
          l.in_use ? this._chip(t.inUse, "warn") : "",
        ].join("");
        html += this._card(`${esc(l.name)} <span class="chips">${chips}</span>`, this._kv([
          [t.used, `${this._bytes(l.used_bytes)}${Number.isFinite(l.limit_bytes) ? ` / ${this._bytes(l.limit_bytes)} (${this._num(share, 1)} %)` : ""}${bar}`],
          l.period_start && [t.since, esc(this._when(l.period_start))],
          [t.signal, `${Number.isFinite(l.rssi) ? `RSSI ${this._num(l.rssi)} dBm` : "–"}${Number.isFinite(l.rsrp) ? ` · RSRP ${this._num(l.rsrp)} dBm` : ""}`],
          l.state && [t.state, esc(l.state)],
          [t.model, esc(l.model)],
        ]));
      }
      return html + `</div>`;
    }

    _speeds(s) {
      const parts = [];
      if (Number.isFinite(s.download)) parts.push(`↓ ${this._num(s.download)}`);
      if (Number.isFinite(s.upload)) parts.push(`↑ ${this._num(s.upload)} Mbit/s`);
      if (Number.isFinite(s.ping)) parts.push(`${this._num(s.ping)} ms`);
      if (s.uplink_name) parts.push(esc(s.uplink_name));
      return parts.join(" · ");
    }

    // The history, newest first and by day; a return names how long the backup carried
    _events(c) {
      const t = this._t;
      const h = c.history;
      const rows = [];
      const lastAt = {};
      for (const e of h.events) {
        let title = "";
        let note = "";
        let kind = "";
        if (e.type === "failover") {
          title = fill(t.failover, { to: e.to_uplink_name || this._name(c, e.to_uplink) });
          note = fill(t.failoverFrom, { from: e.from_uplink_name || this._name(c, e.from_uplink) });
          kind = "warn";
          lastAt.failover = e.at;
        } else if (e.type === "restored") {
          title = fill(t.restored, { to: e.to_uplink_name || this._name(c, e.to_uplink) });
          if (lastAt.failover) note = fill(t.after, { d: this._duration((time(e.at) - time(lastAt.failover)) / 1000) });
          lastAt.failover = null;
          kind = "ok";
        } else if (e.type === "brief_failover") {
          title = t.brief;
          note = t.briefNote;
          kind = "warn";
        } else if (e.type === "uplink_down") {
          title = fill(t.down, { u: e.uplink_name || this._name(c, e.uplink) });
          kind = "err";
          lastAt[`down:${e.uplink}`] = e.at;
        } else if (e.type === "uplink_up") {
          title = fill(t.up, { u: e.uplink_name || this._name(c, e.uplink) });
          const down = lastAt[`down:${e.uplink}`];
          if (down) note = fill(t.after, { d: this._duration((time(e.at) - time(down)) / 1000) });
          lastAt[`down:${e.uplink}`] = null;
          kind = "ok";
        } else if (e.type === "public_ip_changed") {
          title = fill(t.ip, { f: e.family === "ipv6" ? "IPv6" : "IPv4" });
          note = fill(t.ipOf, { u: e.uplink_name || this._name(c, e.uplink) });
        } else continue;
        rows.push({ at: e.at, title, note, kind });
      }
      const foot = `<div class="muted small">${esc(fill(t.kept, { n: h.days }))}</div>`;
      if (!rows.length) return this._card("", `<div class="muted">${esc(t.noEvents)}</div>${foot}`);
      let html = "";
      let day = null;
      let items = "";
      const flush = () => { if (day) html += this._card(esc(day), items, " list"); };
      for (const r of rows.reverse()) {
        const d = this._day(r.at);
        if (d !== day) { flush(); day = d; items = ""; }
        items += `<div class="item"><span class="dot ${r.kind}"></span><span class="clock">${esc(this._clock(r.at))}</span>` +
          `<div class="text"><span class="headline">${esc(r.title)}</span>${r.note ? `<span class="supporting">${esc(r.note)}</span>` : ""}</div></div>`;
      }
      flush();
      return html + foot;
    }

    _speedtests(c) {
      const t = this._t;
      const tests = c.history.speedtests;
      const running = c.speedtest_running ? this._alert(t.running) : "";
      const foot = `<div class="muted small">${esc(fill(t.kept, { n: c.history.days }))}</div>`;
      if (!tests.length) return running + this._card("", `<div class="muted">${esc(t.noSpeedtests)}</div>${foot}`);
      const keys = [...new Set(tests.map((s) => s.uplink || ""))];
      const order = c.uplinks.map((u) => u.key.toLowerCase());
      keys.sort((a, b) => (order.indexOf(a) + 99) % 99 - (order.indexOf(b) + 99) % 99);
      const color = (k) => SERIES[keys.indexOf(k) % SERIES.length];
      const chart = tests.length > 1 ? this._chart(tests, keys, color) : "";
      const legend = keys.map((k) => {
        const name = tests.find((s) => (s.uplink || "") === k)?.uplink_name || k || "–";
        return `<span class="legend"><i style="background:${color(k)}"></i>${esc(name)}</span>`;
      }).join("");
      const rows = [...tests].reverse().map((s) =>
        `<tr><td>${esc(this._when(s.at))}</td><td><span class="legend"><i style="background:${color(s.uplink || "")}"></i>${esc(s.uplink_name || "–")}</span></td>` +
        `<td class="n">${this._num(s.download)}</td><td class="n">${this._num(s.upload)}</td><td class="n">${this._num(s.ping)}</td></tr>`).join("");
      const table = `<div class="scroll"><table class="data"><tr><th>${esc(t.time)}</th><th>${esc(t.uplink)}</th>` +
        `<th class="n">${esc(t.download)} Mbit/s</th><th class="n">${esc(t.upload)} Mbit/s</th><th class="n">${esc(t.ping)} ms</th></tr>${rows}</table></div>`;
      return running + (chart ? this._card(esc(t.download), `${chart}<div class="legends">${legend}</div>`) : "") + this._card("", table + foot);
    }

    // Download over time, one line per uplink; the axis starts at 0
    _chart(tests, keys, color) {
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
      return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img">${grid}${labels}${lines}</svg>`;
    }

    _drills(c) {
      const t = this._t;
      const drills = c.history.drills;
      const hint = c.admin ? "" : this._alert(t.noAdmin);
      if (!drills.length) return hint + this._card("", `<div class="muted">${esc(t.noDrills)}</div>`);
      const avg = (key) => {
        const v = drills.map((d) => d[key]).filter(Number.isFinite);
        return v.length ? v.reduce((a, b) => a + b, 0) / v.length : NaN;
      };
      const rows = [...drills].reverse().map((d) =>
        `<tr><td>${esc(this._when(d.at))}</td><td class="n">${esc(this._duration(d.failover_seconds))}</td>` +
        `<td class="n">${esc(this._duration(d.restore_seconds))}</td><td class="n">${esc(this._bytes(d.data_used_bytes))}</td></tr>`).join("");
      const mean = drills.length > 1
        ? `<tr class="sum"><td>${esc(t.average)}</td><td class="n">${esc(this._duration(avg("failover_seconds")))}</td>` +
          `<td class="n">${esc(this._duration(avg("restore_seconds")))}</td><td class="n">${esc(this._bytes(avg("data_used_bytes")))}</td></tr>`
        : "";
      const table = `<div class="scroll"><table class="data"><tr><th>${esc(t.drillStart)}</th><th class="n">${esc(t.failoverTime)}</th>` +
        `<th class="n">${esc(t.restoreTime)}</th><th class="n">${esc(t.data)}</th></tr>${rows}${mean}</table></div>`;
      return hint + this._card("", table + `<div class="muted small">${esc(t.keptDrills)}</div>`);
    }

    _remote(c) {
      const t = this._t;
      const now = Date.now();
      let current;
      if (c.remote == null) current = `<div class="muted">${esc(t.remoteUnknown)}</div>`;
      else if (!c.remote.length) current = `<div class="muted">${esc(t.noRemote)}</div>`;
      else {
        current = `<table class="data"><tr><th>${esc(t.name)}</th><th>${esc(t.kind)}</th><th>${esc(t.from)}</th><th class="n">${esc(t.duration)}</th></tr>` +
          c.remote.map((r) => `<tr><td>${esc(r.name)}</td><td>${esc(r.type)}</td><td>${esc(this._when(r.since))}</td>` +
            `<td class="n">${esc(this._duration((now - time(r.since)) / 1000))}</td></tr>`).join("") + `</table>`;
      }
      const closed = c.history.sessions.filter((s) => s.until).reverse();
      const sessions = closed.length
        ? `<div class="scroll"><table class="data"><tr><th>${esc(t.name)}</th><th>${esc(t.kind)}</th><th>${esc(t.from)}</th><th>${esc(t.until)}</th><th class="n">${esc(t.duration)}</th></tr>` +
          closed.map((s) => `<tr><td>${esc(s.name)}</td><td>${esc(s.type)}</td><td>${esc(this._when(s.at))}</td><td>${esc(this._when(s.until))}</td>` +
            `<td class="n">${esc(this._duration((time(s.until) - time(s.at)) / 1000))}</td></tr>`).join("") + `</table></div>`
        : `<div class="muted">${esc(t.noSessions)}</div>`;
      return this._card(esc(t.remoteNow), current) +
        this._card(esc(t.sessions), sessions + `<div class="muted small">${esc(fill(t.kept, { n: c.history.days }))}</div>`);
    }
  }

  // Home Assistant's look: toolbar and tabs of the settings pages, cards, list
  // items; the colours of the active theme
  const STYLE = `
    /* The panel's own container has no height of its own: the page takes the window */
    :host { display: block; height: 100vh; height: 100dvh; background: var(--primary-background-color); color: var(--primary-text-color);
      font-family: var(--ha-font-family-body, Roboto, Noto, sans-serif); font-size: 14px; line-height: 1.5;
      -webkit-font-smoothing: antialiased; }
    * { box-sizing: border-box; }
    .app { display: flex; flex-direction: column; height: 100%; }
    .toolbar { display: flex; align-items: center; flex: none; height: var(--header-height, 56px); padding: 0 12px;
      background: var(--sidebar-background-color); color: var(--sidebar-text-color, var(--primary-text-color));
      border-bottom: 1px solid var(--divider-color); }
    .menu-button { color: var(--sidebar-icon-color, var(--secondary-text-color)); margin-right: 4px; }
    .main-title { font-size: 20px; font-weight: 400; line-height: 32px; margin-left: 12px; white-space: nowrap; }
    .menu-button + .main-title { margin-left: 0; }
    .tabs { display: flex; height: 100%; margin-left: 24px; overflow-x: auto; scrollbar-width: none; }
    .tab { display: flex; align-items: center; justify-content: center; height: 100%; padding: 0 20px; position: relative;
      color: var(--sidebar-text-color, var(--primary-text-color)); text-decoration: none; font-weight: 500; white-space: nowrap; opacity: 0.8; }
    .tab:hover { background: color-mix(in srgb, var(--primary-text-color) 5%, transparent); }
    .tab.active { color: var(--primary-color); opacity: 1; }
    .tabs .tab.active::after { content: ""; position: absolute; left: 0; right: 0; bottom: 0; height: 2px; background: var(--primary-color); }
    .tools { display: flex; align-items: center; gap: 4px; margin-left: auto; padding-left: 8px; }
    .tools select { height: 36px; max-width: 40vw; padding: 0 8px; border-radius: 8px; border: 1px solid var(--divider-color);
      background: var(--card-background-color); color: var(--primary-text-color); font: inherit; }
    .bottom-tabs { display: flex; flex: none; justify-content: space-around; background: var(--sidebar-background-color);
      border-top: 1px solid var(--divider-color); padding-bottom: env(safe-area-inset-bottom); }
    .bottom-tabs .tab { flex: 1 1 0; flex-direction: column; gap: 2px; height: 56px; padding: 6px 2px 4px; font-size: 11px; min-width: 0; }
    .bottom-tabs .tab span { overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
    .bottom-tabs .tab .mdi { padding: 2px 12px; border-radius: 14px; box-sizing: content-box; }
    .bottom-tabs .tab.active .mdi { background: color-mix(in srgb, var(--primary-color) 15%, transparent); }
    .content { flex: 1 1 auto; min-height: 0; overflow-y: auto; }
    .about { text-align: center; color: var(--secondary-text-color); font-size: var(--ha-font-size-s, 12px);
      padding: 8px 0 24px; }
    .sheet { max-width: 1040px; margin: 0 auto; padding: 16px; }
    .narrow .sheet { padding: 8px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 0 16px; }
    .narrow .grid { grid-template-columns: 1fr; }
    ha-card { display: block; margin-bottom: 16px; overflow: hidden; }
    .narrow ha-card { margin-bottom: 8px; }
    ha-card .card-header { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
    ha-card.list .card-content { padding: 0 0 8px; }
    ha-alert { display: block; margin-bottom: 16px; }
    .head-note { font-size: var(--ha-font-size-m, 14px); }
    .mdi { width: 24px; height: 24px; fill: currentColor; display: block; }
    .chips { display: inline-flex; gap: 4px; flex-wrap: wrap; }
    .chip { display: inline-block; padding: 0 8px; border-radius: 10px; font-size: 12px; line-height: 20px; font-weight: 500;
      background: color-mix(in srgb, var(--primary-text-color) 8%, transparent); color: var(--secondary-text-color); }
    .chip.ok { background: color-mix(in srgb, var(--success-color, #43a047) 16%, transparent); color: var(--success-color, #43a047); }
    .chip.err { background: color-mix(in srgb, var(--error-color, #db4437) 16%, transparent); color: var(--error-color, #db4437); }
    .chip.warn { background: color-mix(in srgb, var(--warning-color, #ffa600) 18%, transparent); color: var(--warning-color, #ffa600); }
    .chip.on { background: color-mix(in srgb, var(--primary-color) 16%, transparent); color: var(--primary-color); }
    .bad { color: var(--error-color, #db4437); }
    .item { display: flex; align-items: center; gap: 12px; min-height: 52px; padding: 6px 16px; }
    .item + .item { border-top: 1px solid var(--divider-color); }
    .clock { font-variant-numeric: tabular-nums; color: var(--secondary-text-color); flex: none; min-width: 72px; }
    .dot { width: 10px; height: 10px; border-radius: 50%; flex: none; background: var(--secondary-text-color); }
    .dot.ok { background: var(--success-color, #43a047); }
    .dot.err { background: var(--error-color, #db4437); }
    .dot.warn { background: var(--warning-color, #ffa600); }
    .text { display: flex; flex: 1; flex-direction: column; min-width: 0; }
    .headline { line-height: 20px; }
    .supporting { font-size: 12px; line-height: 16px; color: var(--secondary-text-color); margin-top: 2px; }
    .muted { color: var(--secondary-text-color); }
    .small { font-size: 12px; line-height: 16px; margin-top: 8px; }
    .mono { font-family: var(--ha-font-family-code, monospace); font-size: 13px; word-break: break-all; }
    table { border-collapse: collapse; width: 100%; }
    table.kv th { width: 40%; text-align: left; font-weight: 400; color: var(--secondary-text-color); padding: 8px 16px 8px 0; vertical-align: top; }
    table.kv td { padding: 8px 0; }
    table.kv tr + tr th, table.kv tr + tr td { border-top: 1px solid var(--divider-color); }
    table.data th { text-align: left; font-size: 12px; font-weight: 500; padding: 8px 12px 8px 0; white-space: nowrap; }
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
