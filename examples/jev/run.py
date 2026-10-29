"""Synthetic demo: each launch edits a new temporary copy, never the fixture."""
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from beancount_import import jev, webserver

args = sys.argv[1:]
if "--mock-jev" in args:
    args.remove("--mock-jev")
    def classify(context, model=None, instructions=None):
        time.sleep(0.4)
        probabilities = {account: 0 for account in context["categories"]}
        probabilities.update({"Expenses:Coffee": 0.75, "Expenses:Groceries": 0.25})
        return {"category": "Expenses:Coffee", "confidence": 0.6,
                "probabilities": probabilities}
    jev.classify = classify
    jev.configured = lambda: True
    print("Synthetic JEV responses enabled; no external requests.", flush=True)

with tempfile.TemporaryDirectory(prefix="beancount-jev-demo-") as directory:
    journal = Path(directory) / "demo.bean"
    shutil.copyfile(Path(__file__).with_name("demo.bean"), journal)
    print(f"Demo journal: {journal}", flush=True)
    ignored = Path(directory) / "ignored.bean"
    ignored.touch()
    webserver.main(args, ignored_journal=str(ignored), journal_input=str(journal), default_output=str(journal),
                   data_sources=[], port=8104)
