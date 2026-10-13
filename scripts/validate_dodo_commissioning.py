"""Create a readiness declaration only from a fully verified real healthy checkout."""
import argparse
from pathlib import Path

from tryfolk.dodo_commissioning_validation import write_validation
from tryfolk.process import ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("commissioning", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "runtime/dodo-execution-validation.json")
    args = parser.parse_args()
    result = write_validation(args.commissioning, output=args.output, root=ROOT)
    print(f"Validated {result['completed_diagnostic_personas']} real healthy diagnostic checkout(s); "
          f"population coverage remains pending. Evidence: {args.output}")


if __name__ == "__main__":
    main()
