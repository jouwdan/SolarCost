/* One SolarCost card, using the integration's existing sensors and reports. */
import { bandRows } from "./solarcost-tou-card.js";

const PERIODS = ["today", "week", "month", "last_month", "last_months", "year", "last_years", "all_time"];
const ENERGY = [
  ["solar_kwh", "Solar generated", "mdi:white-balance-sunny", "#e5ad27"],
  ["usage_kwh", "Home usage", "mdi:home-lightning-bolt", "#60a5fa"],
  ["import_kwh", "Grid import", "mdi:transmission-tower-import", "#fb923c"],
  ["export_kwh", "Grid export", "mdi:transmission-tower-export", "#34bfa5"],
];
const el = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
};

export function reportGrouping(start, end) {
  const days = (Date.parse(end) - Date.parse(start)) / 86400000 + 1;
  return days <= 62 ? "day" : days <= 730 ? "month" : "year";
}

export function relatedStates(states, anchor) {
  if (!anchor?.attributes.entry_id || anchor.attributes.metric !== "net_cost") return [];
  return Object.values(states).filter(state => state.attributes.entry_id === anchor.attributes.entry_id &&
    (state.attributes.metric === "net_cost" || state.attributes.period === "current"));
}

class SolarCostCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.view = "overview";
    this.chart = "bill";
    this.requestId = 0;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; --sc-green: #34bfa5; }
        * { box-sizing: border-box; }
        [hidden] { display: none !important; }
        ha-card { padding: 20px; color: var(--primary-text-color); overflow: hidden; }
        header { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
        h2 { display: flex; align-items: center; gap: 8px; font-size: 18px; font-weight: 600; margin: 0; }
        h2 ha-icon { color: #e5ad27; }
        button, select, input { font: inherit; color: var(--primary-text-color); background: var(--secondary-background-color); }
        button, select { cursor: pointer; }
        button, select, input { border: 1px solid var(--divider-color); border-radius: 10px; padding: 8px 10px; min-height: 40px; min-width: 0; }
        select { max-width: 100%; font-size: 13px; }
        button:focus-visible, select:focus-visible, input:focus-visible, summary:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
        .dates { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 16px; align-items: end; }
        .dates label { flex: 1; min-width: 110px; font-size: 12px; color: var(--secondary-text-color); }
        input { width: 100%; margin-top: 4px; font-size: 13px; }
        .hero { padding: 24px 0 20px; }
        .eyebrow { font-size: 13px; color: var(--secondary-text-color); }
        .amount { font-size: clamp(32px, 8vw, 42px); line-height: 1.3; font-weight: 600; letter-spacing: -1px; font-variant-numeric: tabular-nums; }
        .credit { color: var(--sc-green); }
        .muted { font-size: 12px; color: var(--secondary-text-color); line-height: 1.5; }
        .context { margin-top: 4px; }
        nav { display: flex; padding: 4px; gap: 4px; border-radius: 12px; background: var(--secondary-background-color); margin-bottom: 20px; }
        nav button { flex: 1; border: 0; background: transparent; color: var(--secondary-text-color); padding: 8px 4px; font-size: 13px; }
        nav button[aria-pressed="true"] { color: var(--primary-text-color); background: var(--card-background-color); box-shadow: 0 1px 4px #0002; }
        .energy { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
        .tile { padding: 14px; border: 1px solid var(--divider-color); border-radius: 12px; }
        .tile ha-icon { margin-bottom: 8px; --mdc-icon-size: 22px; }
        .tile .value { font-size: 21px; font-weight: 500; margin-top: 3px; font-variant-numeric: tabular-nums; }
        .unit { font-size: 12px; color: var(--secondary-text-color); font-weight: 400; margin-left: 4px; }
        .flows { margin-top: 18px; }
        .line { display: flex; justify-content: space-between; gap: 12px; padding: 7px 0; font-size: 13px; }
        .line span { min-width: 0; overflow-wrap: anywhere; }
        strong { white-space: nowrap; font-variant-numeric: tabular-nums; font-weight: 500; }
        h3 { font-size: 14px; margin: 0 0 4px; font-weight: 600; }
        .band { margin-top: 16px; }
        .band.export { border-top: 1px solid var(--divider-color); padding-top: 16px; margin-top: 20px; }
        .band .line { padding: 0; }
        .track { height: 8px; border-radius: 4px; margin-top: 7px; background: var(--divider-color); overflow: hidden; }
        .fill { height: 100%; border-radius: inherit; }
        .band small { display: block; font-size: 11px; color: var(--secondary-text-color); margin-top: 4px; }
        details { border-top: 1px solid var(--divider-color); margin-top: 18px; padding-top: 12px; }
        summary { cursor: pointer; font-size: 13px; padding: 5px 0; }
        .note { margin: 12px 0 0; }
        .note:empty { display: none; }
        footer { margin-top: 18px; }
        .history-head { display: flex; justify-content: space-between; gap: 12px; align-items: center; }
        .history-head button { font-size: 12px; min-height: 34px; padding: 5px 10px; }
        .legend { display: flex; gap: 8px 12px; flex-wrap: wrap; margin: 14px 0 8px; font-size: 11px; }
        .legend span { display: inline-flex; align-items: center; gap: 5px; }
        .dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
        .chart-area { display: flex; height: 174px; gap: 8px; margin-top: 12px; }
        .axis { position: relative; display: flex; flex-direction: column; justify-content: space-between; font-size: 10px; color: var(--secondary-text-color); padding-bottom: 4px; }
        .plot { position: relative; flex: 1; min-width: 0; display: flex; gap: 2px; border-top: 1px solid var(--divider-color); border-bottom: 1px solid var(--divider-color); }
        .baseline { position: absolute; left: 0; right: 0; height: 1px; background: var(--divider-color); pointer-events: none; }
        .column { position: relative; flex: 1; min-width: 0; min-height: 0; border: 0; border-radius: 3px; padding: 0; background: transparent; }
        .column:hover, .column[aria-pressed="true"] { background: var(--secondary-background-color); }
        .column i { position: absolute; display: block; min-height: 1px; border-radius: 2px; pointer-events: none; }
        .xlabels { display: flex; justify-content: space-between; gap: 4px; margin-top: 7px; font-size: 10px; color: var(--secondary-text-color); }
        .selection { margin-top: 16px; }
        .selection .energy { gap: 6px 16px; margin-top: 8px; }
        .selection .line { display: block; padding: 3px 0; }
        .selection strong { display: block; margin-top: 3px; }
        .error { color: var(--error-color, #db4437); }
      </style>
      <ha-card>
        <header><h2><ha-icon icon="mdi:solar-power"></ha-icon><span>SolarCost</span></h2><select class="period" aria-label="Reporting period"></select></header>
        <form class="dates" hidden><label>From<input type="date" name="start" required></label><label>To<input type="date" name="end" required></label><button>Apply</button></form>
        <div class="hero"><div class="eyebrow">Estimated bill</div><div class="amount">—</div><div class="muted range"></div><div class="muted context" role="status"></div></div>
        <nav aria-label="Card view"><button data-view="overview" aria-pressed="true">Overview</button><button data-view="costs" aria-pressed="false">Costs</button><button data-view="history" aria-pressed="false">History</button></nav>
        <section class="overview"><div class="energy"></div><div class="flows"></div></section>
        <section class="costs" hidden><h3>Energy costs and credits</h3><div class="bars" role="list" aria-label="Costs by time band and export credit"></div><p class="muted band-note note"></p><details class="bill-details" open><summary>Bill breakdown</summary><div class="breakdown"></div></details><details><summary>Current rates</summary><div class="rates"></div><p class="muted">Effective prices now, including configured discounts and VAT. Historical costs retain their recorded prices.</p></details></section>
        <section class="history" hidden><div class="history-head"><h3>Bill history</h3><button class="chart-toggle">Show energy</button></div><p class="muted history-status" role="status"></p><div class="history-chart"></div></section>
        <footer class="muted">Estimates include configured discounts, tax and fixed charges.</footer>
      </ha-card>`;
    const root = this.shadowRoot;
    root.querySelector(".period").addEventListener("change", event => {
      this.period = event.target.value;
      this.clearReport();
      this.render();
    });
    root.querySelector(".dates").addEventListener("submit", event => {
      event.preventDefault();
      const start = root.querySelector('[name="start"]').value;
      const end = root.querySelector('[name="end"]').value;
      if (start > end) {
        root.querySelector(".context").textContent = "Choose an end date on or after the start date.";
        return;
      }
      this.clearReport();
      this.customRange = { start, end };
      this.render();
    });
    root.querySelectorAll("nav button").forEach(button => button.addEventListener("click", () => {
      this.view = button.dataset.view;
      this.render();
    }));
    root.querySelector(".chart-toggle").addEventListener("click", () => {
      this.chart = this.chart === "bill" ? "energy" : "bill";
      this.renderHistory();
    });
  }

  static getConfigElement() { return document.createElement("solarcost-card-editor"); }

  static getStubConfig(hass) {
    const state = Object.values(hass?.states || {}).find(item => item.attributes.metric === "net_cost" && item.attributes.period === "month");
    return { entity: state?.entity_id || "sensor.solarcost_this_month_estimated_bill" };
  }

  setConfig(config) {
    if (typeof config.entity !== "string" || !config.entity.startsWith("sensor.")) {
      throw new Error("Choose one SolarCost estimated-bill sensor. Its other periods are found automatically.");
    }
    this.config = config;
    this.period = undefined;
    this.states = [];
    this.clearReport();
    this.shadowRoot.querySelector("h2 span").textContent = config.title || "SolarCost";
    this.render();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this.config) return;
    const anchor = hass.states[this.config.entity];
    const states = relatedStates(hass.states, anchor);
    const changed = anchor !== this.anchor || states.length !== this.states.length || states.some((state, i) => state !== this.states[i]);
    this.anchor = anchor;
    this.states = states;
    if (changed || !this.rendered) this.render();
  }

  getCardSize() { return 9; }
  getGridOptions() { return { columns: 12, min_columns: 6 }; }

  clearReport() {
    this.requestId++;
    this.report = undefined;
    this.reportKey = undefined;
    this.reportError = "";
    this.loading = false;
    this.customRange = undefined;
    this.selectedDate = undefined;
  }

  money(value, digits) {
    return new Intl.NumberFormat(this._hass.locale?.language || undefined, {
      style: "currency", currency: this.currency, minimumFractionDigits: digits ?? 2,
      maximumFractionDigits: digits ?? (value !== 0 && Math.abs(value) < 0.01 ? 6 : 2),
    }).format(value);
  }

  number(value) {
    return new Intl.NumberFormat(this._hass.locale?.language || undefined, { maximumFractionDigits: 3 }).format(value);
  }

  date(value) {
    if (!value) return "";
    const parts = value.split("-");
    return new Intl.DateTimeFormat(this._hass.locale?.language || undefined, {
      ...(parts.length > 1 ? { month: "short" } : {}), ...(parts.length > 2 ? { day: "numeric" } : {}), year: "numeric", timeZone: "UTC",
    }).format(new Date(`${value}${parts.length === 1 ? "-01-01" : parts.length === 2 ? "-01" : ""}T12:00:00Z`));
  }

  line(label, value) {
    const line = el("div", undefined, "line");
    line.append(el("span", label), el("strong", value));
    return line;
  }

  render() {
    if (!this.config || !this._hass) return;
    this.rendered = true;
    // setConfig can run after hass, for example while editing the card.
    this.anchor = this._hass.states[this.config.entity];
    this.states = relatedStates(this._hass.states, this.anchor);
    const root = this.shadowRoot;
    const bills = new Map(this.states.filter(state => state.attributes.metric === "net_cost").map(state => [state.attributes.period, state]));
    this.period ??= this.anchor?.attributes.period || "month";
    const select = root.querySelector(".period");
    const choices = PERIODS.filter(period => bills.has(period)).map(period => [period, bills.get(period).attributes.period_name]);
    choices.push(["custom", "Custom dates"]);
    const signature = JSON.stringify(choices);
    if (this.choices !== signature) {
      this.choices = signature;
      select.replaceChildren(...choices.map(([value, name]) => {
        const option = el("option", name); option.value = value; return option;
      }));
    }
    select.value = this.period;
    const custom = this.period === "custom";
    root.querySelector(".dates").hidden = !custom;
    const today = bills.get("today")?.attributes.end;
    root.querySelectorAll("input").forEach(input => { if (today) input.max = today; });
    if (!root.querySelector('[name="start"]').value && this.anchor) {
      root.querySelector('[name="start"]').value = this.anchor.attributes.start || "";
      root.querySelector('[name="end"]').value = this.anchor.attributes.end || "";
    }
    const state = bills.get(this.period);
    this.currency = custom ? this.report?.currency || this.anchor?.attributes.unit_of_measurement : state?.attributes.unit_of_measurement;
    this.data = custom ? (this.report ? { ...this.report, ...this.report.totals, has_history: this.report.end >= this.report.tracking_since.slice(0, 10) } : undefined)
      : state ? { ...state.attributes, net_cost: Number(state.state) } : undefined;
    const data = this.data;
    this.available = !!data && data.has_history !== false && Number.isFinite(data.net_cost) && (custom || !["unknown", "unavailable"].includes(state.state));
    let message = "";
    if (!this.anchor || !this.states.length) message = "Choose a SolarCost bill sensor from version 0.1.5 or later.";
    else if (custom && !this.report) message = this.reportError || (this.loading ? "Loading your report…" : "Choose your billing dates and select Apply.");
    else if (data?.has_history === false) message = "No recorded history for this period.";
    else if (!this.available) message = "This period's bill sensor is unavailable.";
    else if (data.partial_history) message = `Recorded history only · tracking since ${this.date(data.tracking_since?.slice(0, 10))}`;
    else message = custom ? "Date-range snapshot · select Apply to refresh." : "";
    root.querySelector(".eyebrow").textContent = this.available && data.net_cost < 0 ? "Estimated credit" : "Estimated bill";
    const amount = root.querySelector(".amount");
    amount.textContent = this.available ? this.money(Math.abs(data.net_cost)) : "—";
    amount.classList.toggle("credit", this.available && data.net_cost < 0);
    root.querySelector(".range").textContent = data ? `${this.date(data.start)} – ${this.date(data.end)}` : "";
    root.querySelector(".context").textContent = message;
    root.querySelectorAll("nav button").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.view === this.view)));
    for (const view of ["overview", "costs", "history"]) root.querySelector(`.${view}`).hidden = view !== this.view || !this.available;
    if (this.available) {
      if (this.view === "overview") this.renderOverview();
      if (this.view === "costs") this.renderCosts();
      if (this.view === "history") this.renderHistory();
    }
    if (this.anchor?.attributes.entry_id && ((custom && this.customRange) || (!custom && this.available && this.view === "history"))) {
      const range = custom ? this.customRange : data;
      const key = JSON.stringify([this.anchor.attributes.entry_id, range.start, range.end, custom ? "snapshot" : state.attributes.updated_at]);
      if (key !== this.reportKey) this.loadReport(range, key);
    }
  }

  renderOverview() {
    const root = this.shadowRoot;
    root.querySelector(".overview .energy").replaceChildren(...ENERGY.map(([key, name, icon, color]) => {
      const tile = el("div", undefined, "tile");
      const symbol = el("ha-icon"); symbol.setAttribute("icon", icon); symbol.style.color = color;
      const value = el("div", this.number(this.data[key]), "value"); value.append(el("span", "kWh", "unit"));
      tile.append(symbol, el("div", name, "muted"), value);
      return tile;
    }));
    root.querySelector(".flows").replaceChildren(
      this.line("Imported electricity", this.money(this.data.import_cost)),
      this.line("Export credit", this.money(this.data.export_credit)),
    );
  }

  renderCosts() {
    const root = this.shadowRoot;
    const bars = root.querySelector(".bars");
    bars.replaceChildren();
    try {
      const rows = bandRows(this.data.time_of_use || {}, this.config.band_names);
      const active = rows.filter(row => row.cost !== 0 || row.kwh !== 0);
      const plotted = [...active, {
        key: "export", name: this.data.export_credit < 0 ? "Export charge" : "Export credit",
        cost: -this.data.export_credit, kwh: this.data.export_kwh,
      }];
      const max = Math.max(...plotted.map(row => Math.abs(row.cost)), 0.000001);
      for (const row of plotted) {
        const item = el("div", undefined, row.key === "export" ? "band export" : "band"); item.setAttribute("role", "listitem");
        const track = el("div", undefined, "track"); track.setAttribute("aria-hidden", "true");
        const fill = el("div", undefined, "fill");
        fill.style.width = `${Math.abs(row.cost) / max * 100}%`;
        fill.style.background = row.key === "unallocated" ? "var(--secondary-text-color)" : row.cost < 0 || (row.key === "export" && row.cost === 0) ? "var(--sc-green)" : "var(--primary-color)";
        track.append(fill);
        item.append(this.line(row.name, this.money(row.cost)), track, el("small", `${this.number(row.kwh)} kWh`));
        bars.append(item);
      }
      root.querySelector(".band-note").textContent = [
        !active.length ? "No imports recorded for this period." : "",
        active.some(row => row.key === "unallocated") ? "Unallocated: recorded before time-band tracking." : "",
        active.some(row => row.cost < 0) ? "Negative amounts are import credits." : "",
      ].filter(Boolean).join(" ");
    } catch (error) { root.querySelector(".band-note").textContent = error.message; }
    root.querySelector(".breakdown").replaceChildren(
      ...[["import_cost", "Imported electricity", 1], ["standing_charge", "Standing charge", 1], ["fixed_charge", "Monthly levy / fee", 1], ["export_credit", "Export credit", -1], ["net_cost", "Estimated total", 1]]
        .map(([key, name, sign]) => this.line(name, this.money(this.data[key] * sign))),
    );
    root.querySelector(".rates").replaceChildren(...["import_rate", "export_rate"].map(metric => {
      const rate = this.states.find(state => state.attributes.metric === metric);
      return this.line(metric === "import_rate" ? "Import now" : "Export now", rate && Number.isFinite(Number(rate.state)) ? `${this.money(Number(rate.state), 4)} / kWh` : "Unavailable");
    }));
  }

  async loadReport(range, key) {
    const id = ++this.requestId;
    this.reportKey = key;
    this.report = undefined;
    this.reportError = "";
    this.loading = true;
    this.render();
    try {
      const result = await this._hass.callWS({
        type: "call_service", domain: "solarcost", service: "get_report", return_response: true,
        service_data: { config_entry_id: this.anchor.attributes.entry_id, start: range.start, end: range.end, group_by: reportGrouping(range.start, range.end) },
      });
      if (id !== this.requestId) return;
      if (!result.response?.totals || !Array.isArray(result.response.periods)) throw new Error("The report did not contain billing data.");
      this.report = result.response;
    } catch (error) {
      if (id !== this.requestId) return;
      this.reportError = error.message || "Could not load the report. Select the period again to retry.";
    }
    this.loading = false;
    this.render();
  }

  renderHistory() {
    const root = this.shadowRoot;
    root.querySelector(".history-head h3").textContent = this.chart === "bill" ? "Bill history" : "Energy history";
    root.querySelector(".chart-toggle").textContent = this.chart === "bill" ? "Show energy" : "Show bill";
    const status = root.querySelector(".history-status");
    const container = root.querySelector(".history-chart");
    container.replaceChildren();
    status.classList.toggle("error", !!this.reportError);
    status.textContent = this.reportError || (this.loading || !this.report ? "Loading history…" : "");
    if (!this.report || this.loading) return;
    const periods = this.report.periods;
    if (!periods.length) { status.textContent = "No recorded history for this period."; return; }
    const bill = this.chart === "bill";
    const series = bill ? [["net_cost", "Bill / credit", "", "var(--primary-color)"]] : ENERGY;
    const values = periods.flatMap(period => series.map(([key]) => period[key]));
    const min = Math.min(0, ...values), max = Math.max(0, ...values);
    const span = max - min || 1;
    const zero = -min / span * 100;
    const format = value => bill ? this.money(value) : `${this.number(value)} kWh`;
    const legend = el("div", undefined, "legend");
    for (const [, name, , color] of series) {
      const label = el("span"); const dot = el("i", undefined, "dot"); dot.style.background = color;
      label.append(dot, document.createTextNode(name)); legend.append(label);
    }
    const area = el("div", undefined, "chart-area");
    const axis = el("div", undefined, "axis"); axis.append(el("span", format(max)), el("span", format(min)));
    if (min < 0 && max > 0) {
      const zeroLabel = el("span", "0"); zeroLabel.style.cssText = `position:absolute;bottom:calc(${zero}% - 6px)`;
      axis.append(zeroLabel);
    }
    const plot = el("div", undefined, "plot"); plot.setAttribute("aria-label", bill ? "Bill by period; negative values are credits" : "Energy by period");
    const baseline = el("div", undefined, "baseline"); baseline.style.bottom = `${zero}%`; plot.append(baseline);
    const selection = el("div", undefined, "selection");
    selection.setAttribute("aria-live", "polite");
    const selectPeriod = period => {
      this.selectedDate = period.period;
      plot.querySelectorAll("button").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.date === period.period)));
      selection.replaceChildren(el("h3", this.date(period.period)));
      if (bill) selection.append(this.line(period.net_cost < 0 ? "Estimated credit" : "Estimated bill", this.money(Math.abs(period.net_cost))));
      else {
        const grid = el("div", undefined, "energy");
        grid.append(...ENERGY.map(([key, name]) => this.line(name, `${this.number(period[key])} kWh`)));
        selection.append(grid);
      }
    };
    for (const period of periods) {
      const column = el("button", undefined, "column"); column.dataset.date = period.period;
      column.title = `${this.date(period.period)} · ${series.map(([key, name]) => `${name}: ${format(period[key])}`).join(" · ")}`;
      column.setAttribute("aria-label", column.title);
      series.forEach(([key, , , color], index) => {
        const value = period[key];
        const bar = el("i");
        bar.style.cssText = `left:${(index * 100 + 5) / series.length}%;width:${90 / series.length}%;height:${Math.abs(value) / span * 100}%;bottom:${value < 0 ? zero - Math.abs(value) / span * 100 : zero}%;background:${value < 0 ? "var(--sc-green)" : color}`;
        bar.hidden = value === 0;
        column.append(bar);
      });
      column.addEventListener("click", () => selectPeriod(period));
      plot.append(column);
    }
    area.append(axis, plot);
    const labels = el("div", undefined, "xlabels");
    labels.append(el("span", this.date(periods[0].period)), el("span", periods.length > 1 ? this.date(periods.at(-1).period) : ""));
    container.append(legend, area, labels, selection);
    selectPeriod(periods.find(period => period.period === this.selectedDate) || periods.at(-1));
    const unit = { 1: "year", 2: "month", 3: "day" }[periods[0].period.split("-").length];
    status.textContent = periods.length === 1
      ? `One recorded ${unit}. Its details are shown below.`
      : "Select a date’s bars to update the details below.";
    if (bill && min < 0) status.textContent += " Negative amounts are credits.";
  }
}

class SolarCostCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" }).innerHTML = `
      <style>
        :host { display: block; color: var(--primary-text-color); }
        label { display: block; margin: 16px 0; font-size: 14px; }
        input, select { display: block; box-sizing: border-box; width: 100%; min-height: 44px;
          margin-top: 6px; padding: 10px; font: inherit; color: var(--primary-text-color);
          background: var(--secondary-background-color); border: 1px solid var(--divider-color); border-radius: 8px; }
        input:focus-visible, select:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
        h3 { font-size: 15px; margin: 24px 0 0; }
        p { font-size: 13px; line-height: 1.5; color: var(--secondary-text-color); }
      </style>
      <label>SolarCost bill sensor<select class="entity" required></select></label>
      <p>Choose the setup and period to show initially. All its other periods are available in the card.</p>
      <label>Card title (optional)<input class="title" type="text" placeholder="SolarCost"></label>
      <h3>Time-band labels</h3><p>Use the same label to combine bands in this card. Leave a field empty to use its original name.</p>
      <div class="bands"></div>`;
    this.shadowRoot.querySelector(".entity").addEventListener("change", event => {
      this.config = { ...this.config, entity: event.target.value };
      this.changed(); this.render();
    });
    this.shadowRoot.querySelector(".title").addEventListener("change", event => {
      this.config = { ...this.config, title: event.target.value };
      if (!this.config.title) delete this.config.title;
      this.changed();
    });
  }

  setConfig(config) { this.config = { ...config }; this.render(); }
  set hass(hass) { this._hass = hass; this.render(); }
  changed() {
    this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this.config }, bubbles: true, composed: true }));
  }

  render() {
    if (!this.config || !this._hass) return;
    const root = this.shadowRoot;
    const bills = Object.values(this._hass.states).filter(state => state.attributes.metric === "net_cost" && state.attributes.entry_id)
      .sort((a, b) => (a.attributes.friendly_name || a.entity_id).localeCompare(b.attributes.friendly_name || b.entity_id));
    const choices = bills.map(state => [state.entity_id, state.attributes.friendly_name || `${state.entity_id} · ${state.attributes.period_name}`]);
    if (this.config.entity && !bills.some(state => state.entity_id === this.config.entity)) choices.unshift([this.config.entity, `${this.config.entity} (unavailable)`]);
    const signature = JSON.stringify(choices);
    const select = root.querySelector(".entity");
    if (signature !== this.choices) {
      this.choices = signature;
      select.replaceChildren(...[["", "Choose a SolarCost bill sensor"], ...choices].map(([value, name]) => {
        const option = el("option", name); option.value = value; option.disabled = !value; return option;
      }));
    }
    select.value = this.config.entity || "";
    const title = root.querySelector(".title");
    if (root.activeElement !== title) title.value = this.config.title || "";
    const bands = Object.entries(this._hass.states[this.config.entity]?.attributes.time_of_use || {}).filter(([key]) => key !== "unallocated");
    const bandSignature = JSON.stringify(bands.map(([key, band]) => [key, band.name]));
    if (bandSignature !== this.bandSignature) {
      this.bandSignature = bandSignature;
      const fields = bands.map(([key, band]) => {
        const label = el("label", band.name || key);
        const input = el("input"); input.type = "text"; input.dataset.band = key; input.placeholder = band.name || key;
        input.addEventListener("change", () => {
          const names = { ...this.config.band_names };
          if (input.value.trim()) names[key] = input.value.trim(); else delete names[key];
          this.config = { ...this.config, band_names: names };
          if (!Object.keys(names).length) delete this.config.band_names;
          this.changed();
        });
        label.append(input); return label;
      });
      root.querySelector(".bands").replaceChildren(...fields);
    }
    root.querySelectorAll("[data-band]").forEach(input => {
      if (root.activeElement !== input) input.value = this.config.band_names?.[input.dataset.band] || "";
    });
  }
}

if (!customElements.get("solarcost-card-editor")) customElements.define("solarcost-card-editor", SolarCostCardEditor);

if (!customElements.get("solarcost-card")) {
  customElements.define("solarcost-card", SolarCostCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "solarcost-card", name: "SolarCost", description: "Energy, bill estimates, time-of-use costs and history in one card." });
}
