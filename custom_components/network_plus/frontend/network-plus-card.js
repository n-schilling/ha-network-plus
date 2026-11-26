// Network Plus card: the internet of the home for every user. It only reads
// entities, changes nothing and uses none of the administrators' commands.
//
// Written for a resident, not for an administrator: the uplinks are called by what
// they are for (main line, mobile backup), not by their names in UniFi Network.
//
// The parts from most to least important, which is also the order in which they
// give way when the card gets less room: the overall state, the networks without
// internet, the connections, the mobile data used; off by default the response
// time, the last change and the problems.
(() => {
  const DOMAIN = "network_plus";
  const TAG = "network-plus-card";
  // The card from most to least: what does not fit is left out in this order
  const SIZES = ["large", "medium", "small"];
  // By the space the card gets, the medium card first gives up the backup's data
  const STEPS = ["large", "medium", "brief", "small"];

  const TEXTS = {
    en: {
      name: "Network Plus",
      description: "The internet of the home at a glance: connections, backup and networks.",
      heading: "Internet",
      missing: "Network Plus is not set up.",
      online: "Internet is working",
      backupMobile: "Running on mobile data",
      backupSecond: "Running on the second line",
      drillMobile: "Test: running on mobile data",
      drillSecond: "Test: running on the second line",
      offline: "No internet",
      since: "since {time}",
      active: "in use",
      without: "{names} without internet",
      roleMain: "Main line",
      roleMobile: "Mobile backup",
      roleSecond: "Second line",
      uplinks: "Connections",
      networks: "Networks",
      data: "Mobile data",
      dataOf: "{used} of {limit} used",
      dataUsed: "{used} used",
      latency: "Response time",
      lastSwitch: "Last change",
      title: "Title",
      device: "Gateway (with more than one)",
      size: "Size",
      sizeAuto: "By the space the card gets",
      sizeSmall: "Small",
      sizeMedium: "Medium",
      sizeLarge: "Large",
      section: "What to show",
      show_status: "Overall state",
      show_networks: "Networks",
      show_uplinks: "Connections",
      show_names: "Names from UniFi Network instead of main line and backup",
      show_data: "Mobile data used",
      show_latency: "Response time",
      show_last_switch: "Last change",
      show_problems: "Problems (SIM, slow link)",
    },
    de: {
      name: "Network Plus",
      description: "Das Internet des Zuhauses auf einen Blick: Anschlüsse, Ersatz und Netzwerke.",
      heading: "Internet",
      missing: "Network Plus ist nicht eingerichtet.",
      online: "Internet läuft",
      backupMobile: "Läuft über Mobilfunk",
      backupSecond: "Läuft über Zweitanschluss",
      drillMobile: "Test: läuft über Mobilfunk",
      drillSecond: "Test: läuft über Zweitanschluss",
      offline: "Kein Internet",
      since: "seit {time}",
      active: "in Benutzung",
      without: "{names} ohne Internet",
      roleMain: "Hauptanschluss",
      roleMobile: "Mobilfunk-Ersatz",
      roleSecond: "Zweitanschluss",
      uplinks: "Anschlüsse",
      networks: "Netzwerke",
      data: "Mobilfunk-Datenvolumen",
      dataOf: "{used} von {limit} verbraucht",
      dataUsed: "{used} verbraucht",
      latency: "Reaktionszeit",
      lastSwitch: "Letzter Wechsel",
      title: "Titel",
      device: "Gateway (bei mehreren)",
      size: "Größe",
      sizeAuto: "Nach dem Platz, den die Karte bekommt",
      sizeSmall: "Klein",
      sizeMedium: "Mittel",
      sizeLarge: "Groß",
      section: "Was gezeigt wird",
      show_status: "Gesamtzustand",
      show_networks: "Netzwerke",
      show_uplinks: "Anschlüsse",
      show_names: "Namen aus UniFi Network statt Hauptanschluss und Ersatz",
      show_data: "Verbrauchtes Mobilfunk-Datenvolumen",
      show_latency: "Reaktionszeit",
      show_last_switch: "Letzter Wechsel",
      show_problems: "Probleme (SIM, langsamer Link)",
    },
  };

  // What informs is on, what only matters in a problem or to an administrator is off
  const DEFAULTS = {
    size: "auto",
    show_status: true,
    show_networks: true,
    show_uplinks: true,
    show_names: false,
    show_data: true,
    show_latency: false,
    show_last_switch: false,
    show_problems: false,
  };
  const PARTS = [
    "show_status",
    "show_networks",
    "show_uplinks",
    "show_names",
    "show_data",
    "show_latency",
    "show_last_switch",
    "show_problems",
  ];

  const ICONS = { ok: "mdi:web", backup: "mdi:swap-horizontal", offline: "mdi:web-off", unknown: "mdi:web-off" };
  // Bytes per unit, to compare the data limit with the console's warning limit
  const BYTES = { B: 1, kB: 1e3, MB: 1e6, GB: 1e9, TB: 1e12, KiB: 2 ** 10, MiB: 2 ** 20, GiB: 2 ** 30, TiB: 2 ** 40 };

  // The language of the last card that got its data, for the editor's labels
  let language = "en";
  const texts = (lang) => TEXTS[(lang || "en").split("-")[0]] || TEXTS.en;
  const fill = (text, values = {}) => text.replace(/\{(\w+)\}/g, (_, key) => String(values[key] ?? ""));
  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
  const known = (entity) => entity && !["unavailable", "unknown"].includes(entity.state);

  const STYLE = `
    :host { display: block; height: 100%; }
    ha-card { height: 100%; box-sizing: border-box; overflow: hidden; padding: 14px 16px; }
    .content.fill { height: 100%; }
    .head { display: flex; align-items: center; gap: 8px; }
    .head ha-icon, .small ha-icon { color: var(--secondary-text-color); --mdc-icon-size: 20px; flex: none; }
    .warning ha-icon.state, .line.warning { color: var(--warning-color); }
    .error ha-icon.state { color: var(--error-color); }
    .title { flex: 1; font-size: 16px; font-weight: 500; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .pill { font-size: 12px; padding: 2px 10px; border-radius: 999px; white-space: nowrap;
      background: rgba(var(--rgb-primary-text-color), 0.06); color: var(--secondary-text-color); }
    .warning .pill { background: rgba(var(--rgb-warning-color), 0.15); color: var(--primary-text-color); }
    .error .pill { background: rgba(var(--rgb-error-color), 0.15); color: var(--primary-text-color); }
    .label { margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--divider-color);
      font-size: 12px; color: var(--secondary-text-color); }
    .row { display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 14px; }
    .row[data-entity] { cursor: pointer; }
    .row .name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .row .tag { font-size: 11px; padding: 1px 8px; border-radius: 999px; white-space: nowrap;
      background: rgba(var(--rgb-primary-color), 0.15); }
    .row .value { margin-left: auto; padding-left: 8px; white-space: nowrap; color: var(--secondary-text-color); }
    .row .value.warning { color: var(--warning-color); }
    .details { margin-top: 12px; padding-top: 6px; border-top: 1px solid var(--divider-color); }
    .bar + .row { margin-top: 6px; }
    .bar { height: 4px; border-radius: 2px; margin: 0 0 2px; background: rgba(var(--rgb-primary-text-color), 0.1); overflow: hidden; }
    .bar div { height: 100%; background: var(--primary-color); }
    .bar.warning div { background: var(--warning-color); }
    .line { display: flex; align-items: center; margin-top: 10px; font-size: 13px; }
    .line ha-icon { --mdc-icon-size: 16px; margin-right: 6px; flex: none; }
    .small { display: flex; align-items: center; gap: 10px; height: 100%; }
    .who { flex: 1; min-width: 0; }
    .small .name { font-size: 14px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .small .sub { font-size: 12px; color: var(--secondary-text-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .missing { color: var(--secondary-text-color); font-size: 14px; }
  `;

  class Card extends HTMLElement {
    static getStubConfig() {
      return {};
    }

    // A field for every option, the parts in one expandable group
    static getConfigForm() {
      const t = texts(language);
      const bool = (name) => ({ name, selector: { boolean: {} } });
      return {
        schema: [
          { name: "title", selector: { text: {} } },
          {
            name: "size",
            selector: {
              select: {
                mode: "dropdown",
                options: [["auto", t.sizeAuto], ["small", t.sizeSmall], ["medium", t.sizeMedium], ["large", t.sizeLarge]].map(
                  ([value, label]) => ({ value, label }),
                ),
              },
            },
          },
          { name: "device", selector: { device: { integration: DOMAIN } } },
          { type: "expandable", name: "parts", title: t.section, flatten: true, schema: PARTS.map(bool) },
        ],
        computeLabel: (field) => texts(language)[field.name] ?? field.name,
      };
    }

    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._config = { ...DEFAULTS };
      this._key = null;
    }

    setConfig(config) {
      this._config = { ...DEFAULTS, ...config };
      this._key = null;
      this._render();
    }

    set hass(hass) {
      this._hass = hass;
      language = hass?.language || language;
      this._render();
    }

    // Home Assistant sets it: "grid" in a sections view, where the card has a fixed height
    set layout(layout) {
      this._layout = layout;
      this._key = null;
      this._render();
    }

    getCardSize() {
      return 4;
    }

    // As high as what it shows, until someone gives it fewer rows
    getGridOptions() {
      return { columns: 6, rows: "auto", min_columns: 3, min_rows: 1 };
    }

    connectedCallback() {
      this._observer = new ResizeObserver(() => {
        this._key = null;
        this._render();
      });
      this._observer.observe(this);
      // A tap on a row opens Home Assistant's own dialog of the entity: reading, no action
      this.shadowRoot.addEventListener("click", (event) => {
        const row = event.target.closest?.("[data-entity]");
        if (!row) return;
        this.dispatchEvent(
          new CustomEvent("hass-more-info", { detail: { entityId: row.dataset.entity }, bubbles: true, composed: true }),
        );
      });
      this._render();
    }

    disconnectedCallback() {
      this._observer?.disconnect();
    }

    get _t() {
      return texts(this._hass?.language);
    }

    // What the entities say, found by the integration's own translation keys so that
    // a renamed entity or another language changes nothing. The uplinks and the
    // networks come as lists from the summary entity ("Active uplink").
    _data() {
      const hass = this._hass;
      const own = Object.values(hass.entities || {}).filter((entry) => entry.platform === DOMAIN);
      const summaries = own
        .filter((entry) => entry.translation_key === "active_uplink" && hass.states[entry.entity_id])
        .sort((a, b) => a.entity_id.localeCompare(b.entity_id));
      if (!summaries.length) return null;
      // The gateway that was chosen (an LTE device stands for its gateway), else the first
      const devices = hass.devices || {};
      const wanted = devices[this._config.device]?.via_device_id || this._config.device;
      const gateway = (summaries.find((entry) => entry.device_id === wanted) || summaries[0]).device_id;
      const below = Object.values(devices)
        .filter((device) => device.via_device_id === gateway)
        .map((device) => device.id);
      const states = (key, of = [gateway]) =>
        own
          .filter((entry) => entry.translation_key === key && of.includes(entry.device_id))
          .map((entry) => hass.states[entry.entity_id])
          .filter(Boolean);
      const active = states("active_uplink")[0];
      const listed = (name) => (Array.isArray(active.attributes[name]) ? active.attributes[name] : []);
      const withState = (item) => ({ ...item, state: hass.states[item.entity_id] });
      const usage = states("lte_usage_percent", below).find(known);
      const limit = usage && states("lte_limit", below).find((entity) => known(entity));
      return {
        active,
        since: states("active_uplink_since").find(known),
        backup: states("backup_active")[0]?.state === "on",
        enforced: states("enforce_backup")[0]?.state === "on",
        latency: states("internet_latency").find(known),
        uplinks: listed("uplinks").map(withState).filter((item) => item.state),
        networks: listed("networks").map(withState).filter((item) => item.state),
        usage,
        limit,
        problems: [...states("link_degraded"), ...states("sim_problem", below)].filter((entity) => entity.state === "on"),
      };
    }

    _render() {
      if (!this._hass || !this.isConnected) return;
      const data = this._data();
      const fixed = this._layout === "grid";
      const key = JSON.stringify([data, this._config, this._hass.language, fixed, this.clientWidth, this.clientHeight]);
      if (key === this._key) return;
      this._key = key;
      const root = this.shadowRoot;
      let card = root.querySelector("ha-card");
      if (!card) {
        // The frame stays: a new ha-card shows its content only once it has drawn
        // itself, so what is measured right after creating it would be nothing
        root.innerHTML = `<style>${STYLE}</style><ha-card><div class="content"></div></ha-card>`;
        card = root.querySelector("ha-card");
        card.updateComplete?.then(() => {
          this._key = null;
          this._render();
        });
      }
      const content = root.querySelector(".content");
      // Draws the card and says whether its content is higher than the card
      const draw = (html, whole = false) => {
        content.className = `content${whole ? " fill" : ""}`;
        content.innerHTML = html;
        const style = getComputedStyle(card);
        const room = card.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
        return content.scrollHeight > room + 1;
      };
      if (!data) {
        draw(`<div class="missing">${esc(this._t.missing)}</div>`);
        return;
      }
      const wanted = SIZES.includes(this._config.size) ? [this._config.size] : STEPS;
      for (const size of wanted) {
        const overflows = draw(this._view(data, size), size === "small");
        // Only a card with a fixed height can be too small for what it shows
        if (!fixed || !overflows) break;
      }
      // A second line that is cut off gives way to a shorter one. Measured beside the
      // line, to the fraction of a pixel: scrollWidth and clientWidth are whole
      // numbers and call a text that is a hair too wide a fit
      const line = content.querySelector(".sub[data-lines]");
      if (line) {
        const probe = document.createElement("span");
        probe.className = line.className;
        probe.style.cssText = "position: absolute; visibility: hidden; overflow: visible; width: auto; display: inline-block;";
        line.parentElement.appendChild(probe);
        const room = line.getBoundingClientRect().width;
        for (const text of JSON.parse(line.dataset.lines)) {
          line.textContent = text;
          probe.textContent = text;
          if (probe.getBoundingClientRect().width <= room) break;
        }
        probe.remove();
      }
    }

    // A time as the user's profile writes it; with the day unless it is today
    _time(value) {
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return "";
      const locale = this._hass.locale || {};
      const timeZone = locale.time_zone === "server" ? this._hass.config?.time_zone : undefined;
      const day = (when) => new Intl.DateTimeFormat("en-CA", { timeZone, dateStyle: "short" }).format(when);
      const options = { hour: "numeric", minute: "2-digit", timeZone };
      if (locale.time_format === "12") options.hour12 = true;
      if (locale.time_format === "24") options.hour12 = false;
      if (day(date) !== day(new Date())) Object.assign(options, { day: "numeric", month: "short" });
      return new Intl.DateTimeFormat(locale.language || this._hass.language || "en", options).format(date);
    }

    // What an uplink is called: by what it is for, or by its name in UniFi Network
    _label(data, item) {
      const t = this._t;
      if (this._config.show_names) return item.name;
      if (item.preferred) return t.roleMain;
      if (item.cellular) return t.roleMobile;
      const second = data.uplinks.filter((other) => !other.preferred && !other.cellular);
      return second.length > 1 ? `${t.roleSecond} ${second.indexOf(item) + 1}` : t.roleSecond;
    }

    // The overall state in words, with its level: only "warning" and "error" get a colour
    _status(data) {
      const t = this._t;
      const active = data.active;
      const item = data.uplinks.find((uplink) => uplink.key === active.state);
      if (active.state === "unavailable") {
        return { level: "unknown", text: this._hass.formatEntityState?.(active) ?? active.state };
      }
      if (data.uplinks.length && data.uplinks.every((uplink) => uplink.state.state === "off")) {
        return { level: "offline", text: t.offline };
      }
      if (data.backup) {
        // A backup switched on by hand is a test: as intended, not a warning
        const kind = item && !item.cellular ? "Second" : "Mobile";
        return data.enforced
          ? { level: "drill", text: t[`drill${kind}`], item }
          : { level: "backup", text: t[`backup${kind}`], item };
      }
      if (!known(active)) {
        return { level: "unknown", text: this._hass.formatEntityState?.(active) ?? active.state };
      }
      return { level: "ok", text: t.online, item };
    }

    // The card in one size. Every part asks its option first; a state reads as Home
    // Assistant writes it (formatEntityState), in the user's language and formats.
    _view(data, size) {
      const t = this._t;
      const c = this._config;
      const title = c.title || t.heading;
      const state = (entity) => this._hass.formatEntityState?.(entity) ?? entity.state;
      const status = this._status(data);
      const tone = { backup: "warning", offline: "error" }[status.level] || "";
      const icon = ICONS[status.level] || ICONS.backup;
      const onBackup = status.level === "backup" || status.level === "drill";
      const since = onBackup && data.since ? fill(t.since, { time: this._time(data.since.state) }) : "";
      // A network nothing would ever carry is as intended; one that lost its uplink is not
      const cut = data.networks.filter(
        (item) => item.state.state === "off" && (item.state.attributes.uplinks || []).length,
      );
      const without = c.show_networks && cut.length ? fill(t.without, { names: cut.map((item) => item.name).join(", ") }) : "";

      if (size === "small") {
        // The second line from most to least: the first that fits is shown
        // The state names the kind of connection already; its UniFi name only where wanted
        const named = c.show_uplinks && c.show_names && status.item ? status.item.name : "";
        const pieces = [c.show_status ? status.text : "", without, named, c.show_status ? since : ""].filter(Boolean);
        const lines = pieces.map((_, index) => pieces.slice(0, pieces.length - index).join(" · "));
        if (!lines.length) lines.push("");
        return `<div class="small ${tone}"><ha-icon class="state" icon="${icon}"></ha-icon><div class="who"><div class="name">${esc(title)}</div>` +
          `<div class="sub" data-lines="${esc(JSON.stringify(lines))}">${esc(lines[0])}</div></div></div>`;
      }

      const large = size === "large";
      const row = (name, value, entity, extra = "", warn = false) =>
        `<div class="row"${entity ? ` data-entity="${esc(entity)}"` : ""}><span class="name">${esc(name)}</span>${extra}` +
        `<span class="value${warn ? " warning" : ""}">${esc(value)}</span></div>`;
      let html = `<div class="head ${tone}"><ha-icon class="state" icon="${icon}"></ha-icon><div class="title">${esc(title)}</div>` +
        (c.show_status ? `<span class="pill">${esc([status.text, since].filter(Boolean).join(" · "))}</span>` : "") + `</div>`;
      if (without && !large) {
        html += `<div class="line warning"><ha-icon icon="mdi:lan-disconnect"></ha-icon>${esc(without)}</div>`;
      }
      if (c.show_uplinks && data.uplinks.length) {
        html += `<div class="label">${esc(t.uplinks)}</div>`;
        for (const item of data.uplinks) {
          const tag = item.key === data.active.state ? `<span class="tag">${esc(t.active)}</span>` : "";
          // A connection that is down needs someone, unless it was switched off for a test
          html += row(this._label(data, item), state(item.state), item.entity_id, tag, item.state.state === "off" && !data.enforced);
        }
      }
      if (large && c.show_networks && data.networks.length) {
        html += `<div class="label">${esc(t.networks)}</div>`;
        for (const item of data.networks) {
          html += row(item.name, state(item.state), item.entity_id, "", cut.includes(item));
        }
      }
      // The backup's data: always in the large card, in the medium one while it is used up
      const cellular = data.uplinks.find((item) => item.key === data.active.state)?.cellular;
      const details = [];
      if (c.show_data && data.usage && (large || (size === "medium" && onBackup && cellular))) {
        const percent = Number(data.usage.state);
        const limit = Number(data.limit?.state) * (BYTES[data.limit?.attributes.unit_of_measurement] || NaN);
        const warning = Number(data.limit?.attributes.warning_bytes);
        // The console's own warning limit decides the colour, not a threshold of the card
        const warn = limit > 0 && warning > 0 && percent >= (warning / limit) * 100;
        const used = data.limit
          ? fill(t.dataOf, { used: state(data.usage), limit: state(data.limit) })
          : fill(t.dataUsed, { used: state(data.usage) });
        details.push(
          row(t.data, used, data.usage.entity_id, "", warn) +
            `<div class="bar${warn ? " warning" : ""}"><div style="width: ${Math.max(0, Math.min(100, percent)) || 0}%"></div></div>`,
        );
      }
      if (large && c.show_latency && data.latency) details.push(row(t.latency, state(data.latency), data.latency.entity_id));
      if (large && c.show_last_switch && data.since) {
        details.push(row(t.lastSwitch, this._time(data.since.state), data.since.entity_id));
      }
      if (details.length) html += `<div class="details">${details.join("")}</div>`;
      if (large && c.show_problems) {
        for (const problem of data.problems) {
          html += `<div class="line warning" data-entity="${esc(problem.entity_id)}"><ha-icon icon="mdi:alert-outline"></ha-icon>` +
            `${esc(problem.attributes.friendly_name || problem.entity_id)}</div>`;
        }
      }
      return html;
    }
  }

  if (!customElements.get(TAG)) customElements.define(TAG, Card);
  window.customCards = window.customCards || [];
  if (!window.customCards.some((card) => card.type === TAG)) {
    window.customCards.push({ type: TAG, name: TEXTS.en.name, description: TEXTS.en.description, preview: true });
  }
})();
