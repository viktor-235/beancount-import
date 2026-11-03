# Synthetic history and JEV demo

From the repository root, in an environment with this package's dependencies
installed and the frontend built (`npm --prefix frontend run build`):

```sh
python examples/jev/run.py --mock-jev --port 8106
```

Open http://127.0.0.1:8106/. Each launch copies `demo.bean` to a new temporary
directory. Confirm and Ignore affect only that disposable copy. No real ledger
or credentials are used with `--mock-jev`; JEV replies are deterministic local
fixtures. Stop the process to remove the temporary directory.

The demo covers one unknown group, two independently editable groups, and a
transfer that merges into one transaction without unknown accounts. History
includes punctuation variants to distinguish substring search from exact-payee
statistics sent to JEV.

JEV settings come from `settings.yaml` (the defaults). **Accept with AI (3)**
sends the three single-group transactions; the mock answers the bakery and the
grocer with 95% and 97% and the coffee bar with 60%, so two are confirmed and
one stays pending.

Omit `--mock-jev` to exercise the real client using `OPENROUTER_API_KEY` from the
environment. Only clicking Classify with AI, pressing `c` or confirming an AI
batch dialog sends requests.
