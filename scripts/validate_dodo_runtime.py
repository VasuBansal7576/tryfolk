"""Bind actual unpaid browser/Entire preflights and independent healthy evidence."""
import argparse
import json
from pathlib import Path
from tryfolk.dodo_runtime_validation import write_validation


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("preflight", "capture", "association", "healthy"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    result = write_validation(args.preflight, args.capture, args.association, args.healthy,
                              output=Path("runtime/dodo-execution-validation.json"),
                              root=Path(__file__).resolve().parents[1])
    print(json.dumps({key:result[key] for key in ("status", "scope", "source_digest", "population_validation")}))
