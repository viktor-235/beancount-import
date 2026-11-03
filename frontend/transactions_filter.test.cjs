const { test } = require("node:test");
const assert = require("node:assert/strict");
const esbuild = require("esbuild");
const Module = require("node:module");
const path = require("node:path");
const compiled = esbuild.buildSync({
  entryPoints: [path.join(__dirname, "transactions_filter.ts")],
  platform: "node",
  format: "cjs",
  write: false,
});
const mod = new Module(path.join(__dirname, "transactions-filter-test.cjs"), module);
mod._compile(compiled.outputFiles[0].text, mod.id);
const {
  activePreset,
  datePreset,
  defaultFilter,
  filterArgs,
  filterQuery,
  localIsoDate,
  restoreFilter,
  reviewMoves,
  storedFilter
} = mod.exports;

const day = (y, m, d) => new Date(y, m - 1, d, 23, 30);

test("local ISO date does not shift to UTC", () => {
  assert.equal(localIsoDate(day(2026, 3, 9)), "2026-03-09");
  assert.equal(localIsoDate(new Date(2026, 0, 1, 0, 5)), "2026-01-01");
});

test("presets", () => {
  const today = day(2026, 11, 3);
  assert.deepEqual(datePreset("thisMonth", today), { start: "2026-11-01", end: "2026-11-03" });
  assert.deepEqual(datePreset("lastMonth", today), { start: "2026-10-01", end: "2026-10-31" });
  assert.deepEqual(datePreset("sinceLastMonth", today), { start: "2026-10-01", end: "2026-11-03" });
  assert.deepEqual(datePreset("thisYear", today), { start: "2026-01-01", end: "2026-11-03" });
  assert.deepEqual(datePreset("all", today), { start: "", end: "" });
});

test("January rolls over to December", () => {
  const today = day(2027, 1, 15);
  assert.deepEqual(datePreset("lastMonth", today), { start: "2026-12-01", end: "2026-12-31" });
  assert.deepEqual(datePreset("sinceLastMonth", today), { start: "2026-12-01", end: "2027-01-15" });
});

test("month ends and leap years", () => {
  assert.equal(datePreset("lastMonth", day(2024, 3, 31)).end, "2024-02-29");
  assert.equal(datePreset("lastMonth", day(2025, 3, 1)).end, "2025-02-28");
  assert.equal(datePreset("lastMonth", day(2026, 5, 31)).end, "2026-04-30");
});

test("default filter and active preset", () => {
  const today = day(2026, 11, 3);
  const filter = defaultFilter(today);
  assert.deepEqual(filter, {
    start: "2026-10-01", end: "2026-11-03", categorizer: null, search: "",
  });
  assert.equal(activePreset(filter, today), "sinceLastMonth");
  assert.equal(activePreset({ ...filter, start: "", end: "" }, today), "all");
  assert.equal(activePreset({ ...filter, start: "2026-10-02" }, today), undefined);
  assert.equal(activePreset({ ...filter, ...datePreset("lastMonth", today) }, today), "lastMonth");
});

test("query encodes All, (none) and a value", () => {
  const filter = { start: "2026-10-01", end: "", categorizer: null, search: "Café & co" };
  const all = new URLSearchParams(filterQuery(filter, 7, 10, 20));
  assert.equal(all.has("categorizer"), false);
  assert.equal(all.get("generation"), "7");
  assert.equal(all.get("start"), "2026-10-01");
  assert.equal(all.get("end"), "");
  assert.equal(all.get("search"), "Café & co");
  assert.equal(all.get("offset"), "10");
  assert.equal(all.get("limit"), "20");
  const none = new URLSearchParams(filterQuery({ ...filter, categorizer: "" }, 7, 0, 0));
  assert.equal(none.get("categorizer"), "");
  const jev = new URLSearchParams(filterQuery({ ...filter, categorizer: "jev" }, 7, 0, 0));
  assert.equal(jev.get("categorizer"), "jev");
});

test("filter arguments for review requests", () => {
  const filter = { start: "2026-10-01", end: "", categorizer: null, search: "x" };
  assert.deepEqual(filterArgs(filter), { start: "2026-10-01", end: "", search: "x" });
  assert.equal(filterArgs({ ...filter, categorizer: "" }).categorizer, "");
});

test("review moves through the list", () => {
  assert.deepEqual(reviewMoves(0, 3), { first: false, previous: false, next: true, last: true });
  assert.deepEqual(reviewMoves(1, 3), { first: true, previous: true, next: true, last: true });
  assert.deepEqual(reviewMoves(2, 3), { first: true, previous: true, next: false, last: false });
  assert.deepEqual(reviewMoves(0, 1), { first: false, previous: false, next: false, last: false });
  // Not in the list: only jumps to its ends.
  assert.deepEqual(reviewMoves(null, 2), { first: true, previous: false, next: false, last: true });
  assert.deepEqual(reviewMoves(null, 0), { first: false, previous: false, next: false, last: false });
});

test("stored filter keeps presets by name and custom dates as is", () => {
  const november = day(2026, 11, 3);
  const december = day(2026, 12, 5);
  const roundTrip = (filter) =>
    restoreFilter(JSON.parse(JSON.stringify(storedFilter(filter, november))), december);
  const thisMonth = { ...datePreset("thisMonth", november), categorizer: "jev", search: "shop" };
  assert.deepEqual(roundTrip(thisMonth), {
    start: "2026-12-01", end: "2026-12-05", categorizer: "jev", search: "shop",
  });
  const custom = { start: "2026-03-02", end: "2026-04-01", categorizer: "", search: "" };
  assert.deepEqual(roundTrip(custom), custom);
});

test("invalid stored filter falls back to the default", () => {
  const today = day(2026, 11, 3);
  for (const value of [null, 5, [], { start: "2026-01-01" }, { start: "", end: "", categorizer: 1, search: "" }]) {
    assert.deepEqual(restoreFilter(value, today), defaultFilter(today));
  }
});
