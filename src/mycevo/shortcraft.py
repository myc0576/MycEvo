"""Candidate-first Shortcraft card validation and discovery."""
from pathlib import Path
import json

REQUIRED_FIELDS = ("id", "version", "title", "category", "purpose", "renderer", "qa", "reuse_policy")

def load_cards(root: Path) -> list[dict[str, object]]:
    cards = []
    for path in sorted(root.rglob("*.json")):
        card = json.loads(path.read_text(encoding="utf-8"))
        missing = [field for field in REQUIRED_FIELDS if field not in card]
        if missing:
            raise ValueError(f"{path}: missing card fields: {', '.join(missing)}")
        cards.append(card)
    return cards
