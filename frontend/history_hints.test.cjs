const { test } = require("node:test");
const assert = require("node:assert/strict");
const esbuild = require("esbuild");
const Module = require("node:module");
const path = require("node:path");
const compiled = esbuild.buildSync({
  entryPoints: [path.join(__dirname, "history_hints.tsx")],
  bundle: true,
  platform: "node",
  format: "cjs",
  write: false,
  external: ["react", "react-dom"],
});
const mod = new Module(path.join(__dirname, "hints-test-bundle.cjs"), module);
mod.filename = path.join(__dirname, "hints-test-bundle.cjs");
mod.paths = module.paths;
mod._compile(compiled.outputFiles[0].text, mod.filename);
const { HistoryHintsComponent, percent } = mod.exports;
const storage = new Map();
global.window = {
  location: { pathname: "/" },
  clearTimeout,
  setTimeout,
  addEventListener() {},
  removeEventListener() {},
  localStorage: {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
  },
};

const fakeResult = {
  category: "Expenses:Coffee",
  confidence: 0.8,
  probabilities: { "Expenses:Coffee": 0.8, "Expenses:Groceries": 0.2 },
};
function component(groups = [0]) {
  const candidate = {
    hints: {
      token: "first",
      payee: "Coffee+Bar",
      transaction:
        '2026-01-06 * "Coffee+Bar" "Shopping"\n  Assets:Bank -5 USD\n  Expenses:FIXME 5 USD',
      groups: groups.map((number) => ({
        number,
        posting_indices: [number + 1],
        postings: ["5 USD"],
      })),
    },
    substituted_accounts: groups.map((n) => [
      "unique" + n,
      "Expenses:FIXME",
      n,
      "Expenses:FIXME",
      "Expenses:FIXME",
    ]),
    new_entries: [
      {
        payee: "Coffee+Bar",
        narration: "Shopping",
        tags: ["test"],
        links: [],
        postings: [],
      },
    ],
  };
  const c = new HistoryHintsComponent({
    candidate,
    candidateIndex: 0,
    generation: 1,
    configured: true,
    toolbar: null,
  });
  c.setState = (update, callback) => {
    c.state = { ...c.state, ...update };
    if (callback) callback();
  };
  c.mounted = true;
  return c;
}
function deferred() {
  let resolve;
  const promise = new Promise((r) => (resolve = r));
  return { promise, resolve };
}

test("mount, history search and request preview never classify", async () => {
  const c = component();
  const operations = [];
  c.request = async (operation) => {
    operations.push(operation);
    return operation === "hints/history"
      ? { accounts: [], transaction_count: 0 }
      : { request: { state: {} } };
  };
  c.componentDidMount();
  await c.loadHistory();
  await c.togglePreview();
  assert.ok(!operations.includes("hints/classify"));
  assert.equal(c.state.aiOpen, false);
  assert.equal(c.state.previewOpen, true);
  c.componentWillUnmount();
});
test("double click makes one call; retry after completion makes a new call", async () => {
  const c = component();
  const pending = deferred();
  let calls = 0;
  c.request = () => {
    calls++;
    return pending.promise;
  };
  const first = c.classify();
  await c.classify();
  assert.equal(calls, 1);
  assert.equal(c.state.loading, true);
  pending.resolve(fakeResult);
  await first;
  assert.deepEqual(c.state.result, fakeResult);
  await c.classify();
  assert.equal(calls, 2);
});
test("late reply after group/context change is discarded", async () => {
  const c = component([0, 1]);
  c.state.group = 0;
  const pending = deferred();
  c.request = (operation) =>
    operation === "hints/classify"
      ? pending.promise
      : Promise.resolve({ accounts: [], transaction_count: 0 });
  const work = c.classify();
  c.selectGroup({ target: { value: "1" } });
  pending.resolve(fakeResult);
  await work;
  assert.equal(c.state.group, 1);
  assert.equal(c.state.result, undefined);
  assert.equal(c.state.aiOpen, false);
});
test("multiple groups require selection; no key and no groups never call JEV", async () => {
  for (const c of [component([0, 1]), component([]), component()]) {
    if (c.state.group === 0) c.props = { ...c.props, configured: false };
    c.request = () => {
      throw Error("unexpected request");
    };
    await c.classify();
    assert.equal(c.state.aiOpen, false);
  }
});
test("apply changes only the chosen group and retains the answer and text", async () => {
  const c = component([0, 1]);
  c.state.group = 1;
  c.state.result = fakeResult;
  c.state.aiOpen = true;
  let sent;
  c.request = async (operation, body) => {
    sent = { operation, body };
  };
  await c.apply("Expenses:Coffee");
  assert.deepEqual(sent.body.changes.accounts, [
    "Expenses:FIXME",
    "Expenses:Coffee",
  ]);
  assert.equal(sent.body.changes.payee, "Coffee+Bar");
  assert.equal(sent.body.hint_token, "first");
  assert.equal(sent.operation, "change_candidate");
  assert.equal(c.state.result, fakeResult);
});
test("category-only update preserves result; changed text clears it and refreshes history", () => {
  const c = component();
  c.state.aiOpen = true;
  c.state.result = fakeResult;
  let refreshes = 0;
  c.loadHistory = () => refreshes++;
  let previous = c.props;
  c.props = { ...c.props, candidate: { ...c.props.candidate } };
  c.componentDidUpdate(previous);
  assert.equal(c.state.result, fakeResult);
  assert.equal(refreshes, 0);
  previous = c.props;
  c.props = {
    ...c.props,
    candidate: {
      ...c.props.candidate,
      hints: {
        ...c.props.candidate.hints,
        token: "edited",
        payee: "New payee",
      },
    },
  };
  c.componentDidUpdate(previous);
  assert.equal(c.state.aiOpen, false);
  assert.equal(c.state.query, "New payee");
  assert.equal(refreshes, 1);
});
test("historical narration preserves accounts and other text without requiring a posting group", async () => {
  for (const groups of [[0, 1], []]) {
    const c = component(groups);
    c.props.candidate.substituted_accounts.forEach((s) => {
      s[1] = "Expenses:Coffee";
    });
    const before = structuredClone(c.props.candidate);
    let sent;
    c.request = async (operation, body) => {
      sent = { operation, body };
    };
    await c.applyNarration('Historical "description"');
    assert.equal(c.state.group, undefined);
    assert.equal(sent.operation, "change_candidate");
    assert.equal(sent.body.hint_token, "first");
    assert.deepEqual(sent.body.changes, {
      accounts: groups.map(() => "Expenses:Coffee"),
      payee: "Coffee+Bar",
      narration: 'Historical "description"',
      tags: ["test"],
      links: [],
    });
    assert.deepEqual(c.props.candidate, before);
  }
});
test("failed request is displayed without retry, and unmounted results are ignored", async () => {
  const c = component();
  let calls = 0;
  c.request = async () => {
    calls++;
    throw Error("timeout");
  };
  await c.classify();
  assert.match(c.state.aiError, /timeout/);
  assert.equal(calls, 1);
  const d = component();
  const pending = deferred();
  d.request = () => pending.promise;
  const work = d.classify();
  d.componentWillUnmount();
  pending.resolve(fakeResult);
  await work;
  assert.equal(d.state.result, undefined);
});
test("probability formatting matches Fava", () => {
  assert.equal(percent(0.75), "75%");
  assert.equal(percent(0.000001), "<0.01%");
  assert.equal(percent(0.0001), "0.01%");
});
test("skip patterns disable AI classification but not the preview", async () => {
  const c = component();
  const operations = [];
  c.request = async (operation) => {
    operations.push(operation);
    return { request: { state: {} } };
  };
  storage.set("jevSkipTransactions", JSON.stringify(["(", "^\\s+Assets:Bank"]));
  try {
    assert.match(c.aiDisabledReason, /Assets:Bank/);
    await c.classify();
    assert.deepEqual(operations, []);
    await c.togglePreview();
    assert.deepEqual(operations, ["hints/request"]);
    storage.set("jevSkipTransactions", JSON.stringify(["^Assets:Bank"]));
    assert.equal(c.aiDisabledReason, "");
  } finally {
    storage.delete("jevSkipTransactions");
  }
});
test("answers at or above the valid confidence are marked valid", () => {
  const settings = esbuild.buildSync({
    entryPoints: [path.join(__dirname, "jev_settings.ts")],
    platform: "node",
    format: "cjs",
    write: false,
  });
  const settingsModule = new Module(path.join(__dirname, "jev-settings-test.cjs"), module);
  settingsModule._compile(settings.outputFiles[0].text, settingsModule.id);
  const { isJevAnswerValid, loadJevValidConfidencePercent } =
    settingsModule.exports;
  assert.equal(loadJevValidConfidencePercent(), 90);
  assert.equal(isJevAnswerValid(0.9), true);
  assert.equal(isJevAnswerValid(0.89), false);
  storage.set("jevValidConfidence", "75");
  try {
    assert.equal(isJevAnswerValid(0.8), true);
    storage.set("jevValidConfidence", "57");
    assert.equal(isJevAnswerValid(0.57), true);
    storage.set("jevValidConfidence", "abc");
    assert.equal(loadJevValidConfidencePercent(), 90);
  } finally {
    storage.delete("jevValidConfidence");
  }
});
