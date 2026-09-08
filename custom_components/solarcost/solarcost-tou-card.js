/* SolarCost's dependency-free time-of-use card. */
export function bandRows(bands, names = {}) {
  const grouped = new Map();
  for (const [key, band] of Object.entries(bands)) {
    const name = String(names[key] ?? band.name ?? key);
    const cost = Number(band.import_cost);
    const kwh = Number(band.import_kwh);
    if (!Number.isFinite(cost) || !Number.isFinite(kwh) || kwh < 0) {
      throw new Error("Invalid time-band reading.");
    }
    const row = grouped.get(name) ?? { key, name, cost: 0, kwh: 0 };
    row.cost += cost;
    row.kwh += kwh;
    grouped.set(name, row);
  }
  return [...grouped.values()].sort((a, b) => Math.abs(b.cost) - Math.abs(a.cost));
}

class SolarCostTouCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 20px; color: var(--primary-text-color); }
        h2 { font-size: 18px; font-weight: 500; margin: 0 0 16px; }
        nav { display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 20px; }
        button { flex: 1; min-height: 40px; border: 0; border-radius: 10px;
          padding: 8px; background: var(--secondary-background-color);
          color: var(--secondary-text-color); font: inherit; cursor: pointer; }
        button[aria-pressed="true"] { background: var(--primary-color); color: var(--text-primary-color, white); }
        button:focus-visible, summary:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 3px; }
        .total { font-size: 32px; font-weight: 600; line-height: 1.2; font-variant-numeric: tabular-nums; }
        .muted { color: var(--secondary-text-color); font-size: 13px; line-height: 1.5; }
        .row { margin-top: 18px; }
        .label, .energy-row { display: flex; justify-content: space-between; gap: 16px; }
        .label span, .energy-row span { overflow-wrap: anywhere; min-width: 0; }
        strong { white-space: nowrap; font-variant-numeric: tabular-nums; }
        .track { height: 9px; border-radius: 5px; margin-top: 8px; background: var(--divider-color); overflow: hidden; }
        .fill { height: 100%; border-radius: inherit; background: var(--primary-color); }
        details { margin-top: 20px; }
        summary { color: var(--secondary-text-color); cursor: pointer; padding: 6px 0; }
        .energy-row { font-size: 13px; padding: 6px 0; }
        .note:empty { display: none; }
        .note { margin: 14px 0 0; }
      </style>
      <ha-card>
        <h2>Import costs by time band</h2>
        <nav aria-label="Reporting period"></nav>
        <div class="total"></div>
        <div class="muted status"></div>
        <div class="bars" role="list" aria-label="Cost by time band"></div>
        <p class="muted note"></p>
        <details><summary>Energy details</summary><div class="energy"></div>
          <p class="muted">Import costs include configured discounts and VAT. Fixed charges and export credit are separate.</p>
        </details>
      </ha-card>`;
  }

  static getStubConfig() {
    return {
      entities: [
        { entity: "sensor.solarcost_this_month_import_cost", name: "This month" },
        { entity: "sensor.solarcost_today_import_cost", name: "Today" },
        { entity: "sensor.solarcost_last_month_import_cost", name: "Last month" },
      ],
    };
  }

  setConfig(config) {
    if (!Array.isArray(config.entities) || !config.entities.length ||
        config.entities.some(item => !item || typeof item.entity !== "string" || !item.entity.startsWith("sensor."))) {
      throw new Error("Choose the SolarCost import-cost sensors to display.");
    }
    this.config = config;
    this.selected = 0;
    this.lastState = undefined;
    this.rendered = false;
    const nav = this.shadowRoot.querySelector("nav");
    nav.replaceChildren();
    config.entities.forEach((item, index) => {
      const button = document.createElement("button");
      button.textContent = item.name || item.entity;
      button.addEventListener("click", () => {
        this.selected = index;
        this.render();
      });
      nav.append(button);
    });
    this.render();
  }

  set hass(hass) {
    this._hass = hass;
    const state = this.config && hass.states[this.config.entities[this.selected].entity];
    if (!this.rendered || state !== this.lastState) this.render();
  }

  getCardSize() { return 7; }
  getGridOptions() { return { columns: 12, min_columns: 6 }; }

  render() {
    if (!this.config || !this._hass) return;
    this.rendered = true;
    const root = this.shadowRoot;
    root.querySelectorAll("nav button").forEach((button, index) => {
      button.setAttribute("aria-pressed", String(index === this.selected));
    });
    const state = this._hass.states[this.config.entities[this.selected].entity];
    this.lastState = state;
    const total = root.querySelector(".total");
    const status = root.querySelector(".status");
    const bars = root.querySelector(".bars");
    const energy = root.querySelector(".energy");
    const details = root.querySelector("details");
    const note = root.querySelector(".note");
    bars.replaceChildren();
    energy.replaceChildren();
    note.textContent = "";
    details.hidden = true;
    total.textContent = "—";
    if (!state || state.state === "unavailable") {
      status.textContent = "Import sensor unavailable.";
      return;
    }
    if (state.state === "unknown" || state.attributes.has_history === false) {
      status.textContent = "No recorded history for this period.";
      return;
    }
    if (!state.attributes.time_of_use || !Number.isFinite(Number(state.state))) {
      status.textContent = "Time-band details are not available yet.";
      return;
    }
    try {
      const money = new Intl.NumberFormat(this._hass.locale?.language || undefined, {
        style: "currency", currency: state.attributes.unit_of_measurement,
      });
      const number = new Intl.NumberFormat(this._hass.locale?.language || undefined, { maximumFractionDigits: 3 });
      const rows = bandRows(state.attributes.time_of_use, this.config.band_names);
      const active = rows.filter(row => row.cost !== 0 || row.kwh !== 0);
      const scale = Math.max(...active.map(row => Math.abs(row.cost)), 0.000001);
      total.textContent = money.format(Number(state.state));
      status.textContent = active.length ? "Imported electricity" : "No imports recorded for this period.";
      details.hidden = false;
      for (const row of rows) {
        const line = document.createElement("div");
        line.className = "energy-row";
        const name = document.createElement("span");
        name.textContent = row.name;
        const value = document.createElement("strong");
        value.textContent = `${number.format(row.kwh)} kWh`;
        line.append(name, value);
        energy.append(line);
      }
      for (const row of active) {
        const item = document.createElement("div");
        item.className = "row";
        item.setAttribute("role", "listitem");
        item.innerHTML = '<div class="label"><span></span><strong></strong></div><div class="track" aria-hidden="true"><div class="fill"></div></div>';
        item.querySelector("span").textContent = row.name;
        item.querySelector("strong").textContent = money.format(row.cost);
        const fill = item.querySelector(".fill");
        fill.style.width = `${Math.abs(row.cost) / scale * 100}%`;
        const palette = ["#60a5fa", "#a78bfa", "#34d399", "#fb923c"];
        const color = [...row.name].reduce((sum, char) => sum + char.codePointAt(0), 0);
        fill.style.background = row.key === "unallocated" ? "var(--secondary-text-color)"
          : row.key === "base" ? "#fbbf24" : palette[color % palette.length];
        bars.append(item);
      }
      note.textContent = [
        state.attributes.partial_history ? "Includes recorded history only." : "",
        active.some(row => row.key === "unallocated") ? "Unallocated: recorded before time-band tracking." : "",
        active.some(row => row.cost < 0) ? "Negative amounts are import credits." : "",
      ].filter(Boolean).join(" ");
    } catch (error) {
      total.textContent = "—";
      status.textContent = error.message;
    }
  }
}

if (!customElements.get("solarcost-tou-card")) {
  customElements.define("solarcost-tou-card", SolarCostTouCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "solarcost-tou-card", name: "SolarCost time-of-use", description: "Compare import costs by time band, one period at a time." });
}
