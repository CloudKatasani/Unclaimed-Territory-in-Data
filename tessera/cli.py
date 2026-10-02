"""Command line entry points used by the Makefile."""

from __future__ import annotations

import argparse
import json

from tessera.config import get_settings
from tessera.seed.generate import install


def main() -> None:
    ap = argparse.ArgumentParser(prog="tessera")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("reset", help="drop and reseed the demo warehouse")
    sub.add_parser("drift", help="apply the vendor schema change and run the Drift Healer")
    sub.add_parser("record-fixtures", help="run the client demo and record mock LLM fixtures")
    sub.add_parser("verify", help="verify the provenance ledger")
    args = ap.parse_args()
    settings = get_settings()
    if args.cmd == "reset":
        print(f"reset -> {install(settings)}")
        return
    from tessera.platform import Tessera

    app = Tessera()
    try:
        if args.cmd == "drift":
            print(json.dumps(app.simulate_drift(), indent=2, default=str))
        elif args.cmd == "verify":
            print(json.dumps(app.ledger.verify(), indent=2))
        elif args.cmd == "record-fixtures":
            from tessera.demo.conductor import record_fixtures

            print(json.dumps(record_fixtures(app), indent=2, default=str))
    finally:
        app.close()


if __name__ == "__main__":
    main()
