const { test } = require("node:test");
const assert = require("node:assert/strict");
const esbuild = require("esbuild");
const Module = require("node:module");
const path = require("node:path");
const compiled = esbuild.buildSync({
  entryPoints: [path.join(__dirname, "jev_batch.ts")],
  platform: "node",
  format: "cjs",
  write: false,
});
const mod = new Module(path.join(__dirname, "jev-batch-test.cjs"), module);
mod._compile(compiled.outputFiles[0].text, mod.id);
const {
  batchConfirmText,
  batchCount,
  batchDisabledReason,
  batchLabel,
  isJevAnswerValid,
  percent,
} = mod.exports;

const status = {
  rulesPending: 2,
  aiPending: 3,
  jevEnabled: true,
  jevSettingsError: null,
  run: null,
};
const running = (mode) => ({
  ...status,
  run: { mode, i: 2, n: 3, accepted: 1, below: 0, errors: 0, skipped: 0, running: true },
});

test("counts and labels", () => {
  assert.equal(batchCount("ai", status), 3);
  assert.equal(batchCount("rules_ai", status), 5);
  assert.equal(batchLabel("ai", status), "Accept with AI (3)");
  assert.equal(batchLabel("rules_ai", status), "Accept rules + AI (5)");
  assert.equal(batchLabel("ai", running("ai")), "Stop (2/3)");
  assert.equal(batchLabel("rules_ai", running("ai")), "Accept rules + AI (5)");
  const finished = running("ai");
  finished.run = { ...finished.run, running: false };
  assert.equal(batchLabel("ai", finished), "Accept with AI (3)");
});

test("disabled reasons", () => {
  assert.equal(batchDisabledReason("ai", status), "");
  assert.equal(batchDisabledReason("ai", running("ai")), "");
  assert.match(batchDisabledReason("rules_ai", running("ai")), /in progress/);
  assert.match(
    batchDisabledReason("ai", { ...status, jevEnabled: false }),
    /OPENROUTER_API_KEY/
  );
  assert.match(
    batchDisabledReason("ai", {
      ...status,
      jevEnabled: false,
      jevSettingsError: "settings.yaml: broken",
    }),
    /JEV disabled: settings.yaml: broken/
  );
  assert.match(batchDisabledReason("ai", { ...status, aiPending: 0 }), /Nothing/);
  assert.equal(batchDisabledReason("rules_ai", { ...status, aiPending: 0 }), "");
});

test("confirmation text names the count, cost and threshold", () => {
  const text = batchConfirmText(3, 0.9);
  assert.match(text, /3 transactions to OpenRouter \(JEV\)/);
  assert.match(text, /separate paid request/);
  assert.match(text, /at least 90%/);
  assert.match(batchConfirmText(1, 0.57), /1 transaction to .*at least 57%/s);
});

test("answers at or above the valid confidence are valid", () => {
  assert.equal(isJevAnswerValid(0.9, 0.9), true);
  assert.equal(isJevAnswerValid(0.89, 0.9), false);
  assert.equal(isJevAnswerValid(0.57, 57 / 100), true);
});

test("probability formatting matches Fava", () => {
  assert.equal(percent(0.75), "75%");
  assert.equal(percent(0.000001), "<0.01%");
  assert.equal(percent(0.0001), "0.01%");
});
