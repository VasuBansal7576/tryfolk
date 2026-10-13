"""Launch the real demo using its private shared configuration."""

import argparse
import os
from pathlib import Path
import runpy

from dotenv import load_dotenv


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("local", "worker"))
    parser.add_argument("--env", type=Path, default=Path("runtime/demo.env"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    if not args.env.is_file():
        parser.error("The private demo configuration is missing; see README.md.")
    load_dotenv(args.env, override=False)
    runpy.run_path(str(root / "scripts" / (args.role + ".py" if args.role == "local" else "run_worker.py")),
                   run_name="__main__")
