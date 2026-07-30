from pathlib import Path

from mycevo.corpus import CrossrefProvider, LocalPrivateProvider, WorkRecord
from mycevo.shortcraft import load_cards
from mycevo.shortcraft_cli import main

def test_private_provider_is_non_networking_and_shortcraft_card_validates():
    assert LocalPrivateProvider().discover(type("Q", (), {"text": "x"})()) == []
    cards = load_cards(Path(__file__).parents[1] / "shortcraft" / "cards")
    assert cards[0]["qa"]["status"] == "candidate"

def test_crossref_is_metadata_only():
    work = WorkRecord("10.1/example", "Example", "https://example.test")
    assert CrossrefProvider().resolve(work).source_url == "https://example.test"
    try:
        CrossrefProvider().fetch(CrossrefProvider().resolve(work), Path("."))
    except RuntimeError as exc:
        assert "metadata-only" in str(exc)
    else:
        raise AssertionError("content fetch must fail closed")

def test_shortcraft_cli_validate(capsys):
    assert main(["validate"]) == 0
    assert '"status": "valid"' in capsys.readouterr().out
