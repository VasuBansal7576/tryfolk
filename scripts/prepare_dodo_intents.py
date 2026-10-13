"""Infer and save genuine purchase intentions for four real saved TinyPeople.

Run with integrations/tinytroupe/.venv/bin/python and PYTHONPATH=src. No browser
or payment is performed. Original saved customers and diagnostic history remain.
"""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import runpy
import sys

from tryfolk.purchase_intents import prepare_intents
from tryfolk.codex_bridge import generate
from tryfolk.scenarios import digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population", type=Path, required=True)
    parser.add_argument("--brief", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("runtime/dodo-intent-population.json"))
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Saved intention cohort already exists; restore it without changing the customers.")
    source = json.loads(args.population.read_text())
    population = deepcopy(source if isinstance(source, list) else source["population"])
    brief = json.loads(args.brief.read_text())
    if len(population) != 4 or len({person["id"] for person in population}) != 4:
        raise ValueError("Four distinct actual saved customers are required.")
    if not brief.get("observed") or brief.get("provider") != "dodo" or brief.get("measured_payment") is not False:
        raise ValueError("Actual Dodo BrowserUse discovery is required; it cannot establish payment outcomes.")
    original_stdout = sys.stdout
    try:
        adapter = runpy.run_path("integrations/tinytroupe/adapter.py", run_name="intent_preparation")
        population = prepare_intents(population, brief, adapter["build"], adapter["TinyPerson"], generate)
    finally:
        sys.stdout = original_stdout
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({
        "source_population_file": str(args.population.resolve()),
        "source_population_sha256": digest(source if isinstance(source, list) else source["population"]),
        "purpose": "Genuine intentions prepared before the four-customer baseline; no dataset provenance claimed",
        "population": population,
    }, indent=2, default=str))
    args.output.chmod(0o600)
    print(json.dumps({"population_file": str(args.output.resolve()), "count": len(population),
                      "population_sha256": digest(population), "browser_actions": 0, "payment_attempts": 0}))


if __name__ == "__main__":
    main()
