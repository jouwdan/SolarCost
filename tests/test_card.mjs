// Run with node tests/test_card.mjs; no DOM or third-party packages needed.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

globalThis.HTMLElement = class {};
globalThis.customElements = { get: () => true };
const source = await readFile(new URL("../custom_components/solarcost/solarcost-tou-card.js", import.meta.url));
const { bandRows } = await import(`data:text/javascript;base64,${source.toString("base64")}`);

const bands = {
  base: { name: "Base rate", import_cost: 10.5, import_kwh: 20 },
  "band:EV": { name: "EV", import_cost: -2, import_kwh: 10 },
  "band:Night before EV": { name: "Night before EV", import_cost: 3, import_kwh: 8 },
  "band:Night after EV": { name: "Night after EV", import_cost: 4, import_kwh: 12 },
  "band:Peak": { name: "Peak", import_cost: 0, import_kwh: 0 },
};
const original = structuredClone(bands);
const rows = bandRows(bands, {
  base: "Day", "band:Night before EV": "Night", "band:Night after EV": "Night",
});
assert.deepEqual(rows.map(row => [row.name, row.cost, row.kwh]), [
  ["Day", 10.5, 20], ["Night", 7, 20], ["EV", -2, 10], ["Peak", 0, 0],
]);
assert.equal(rows.reduce((total, row) => total + row.cost, 0), 15.5);
assert.deepEqual(bands, original);
assert.deepEqual(bandRows({}), []);
assert.equal(bandRows({ base: { name: "Free", import_cost: 0, import_kwh: 2 } })[0].kwh, 2);
assert.equal(bandRows({ base: { name: "<b>Literal name</b>", import_cost: 1, import_kwh: 1 } })[0].name, "<b>Literal name</b>");
assert.throws(() => bandRows({ bad: { import_cost: Infinity, import_kwh: 1 } }));
assert.throws(() => bandRows({ bad: { import_cost: 1, import_kwh: -1 } }));
console.log("PASS: time-band grouping, ordering, credits, zero costs and invalid readings");

const cardSource = (await readFile(new URL("../custom_components/solarcost/solarcost-card.js", import.meta.url), "utf8"))
  .replace('"./solarcost-tou-card.js"', JSON.stringify(`data:text/javascript;base64,${source.toString("base64")}`));
const { reportGrouping, relatedStates } = await import(`data:text/javascript;base64,${Buffer.from(cardSource).toString("base64")}`);
assert.equal(reportGrouping("2026-07-01", "2026-08-31"), "day");
assert.equal(reportGrouping("2026-06-30", "2026-08-31"), "month");
assert.equal(reportGrouping("2024-01-01", "2026-08-31"), "year");
const bill = { entity_id: "sensor.renamed_bill", attributes: { entry_id: "one", metric: "net_cost", period: "month" } };
const rate = { attributes: { entry_id: "one", metric: "import_rate", period: "current" } };
const foreign = { attributes: { entry_id: "two", metric: "net_cost", period: "month" } };
const energy = { attributes: { entry_id: "one", metric: "solar_kwh", period: "month" } };
assert.deepEqual(relatedStates({ bill, rate, foreign, energy }, bill), [bill, rate]);
assert.deepEqual(relatedStates({ bill }, undefined), []);
assert.deepEqual(relatedStates({ bill }, energy), []);
console.log("PASS: scalable report grouping, renamed sensors and separate SolarCost setups");
