from __future__ import annotations
import argparse
import json
from pathlib import Path
from .shortcraft import load_cards

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="paperframes shortcraft")
    parser.add_argument("command", choices=("list", "inspect", "validate", "gallery", "candidate"))
    parser.add_argument("card_id", nargs="?")
    parser.add_argument("--root", type=Path, default=Path("shortcraft/cards"))
    args = parser.parse_args(argv)
    cards = load_cards(args.root)
    if args.command == "list":
        print(json.dumps([c["id"] for c in cards]))
    elif args.command == "inspect":
        matches = [c for c in cards if c["id"] == args.card_id]
        if not matches:
            parser.error("unknown card")
        print(json.dumps(matches[0], indent=2))
    elif args.command == "validate":
        print(json.dumps({"status": "valid", "count": len(cards)}))
    elif args.command in {"gallery", "candidate"}:
        # Gallery and candidate output remain explicit artifacts; promotion is never implicit.
        print(json.dumps({"status": "candidate", "count": len(cards), "promotion": "human_required"}))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
