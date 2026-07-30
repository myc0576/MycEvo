"""Read-only plugin metadata inspection for MycEvo Core.

This module deliberately never calls an entry point's ``load`` method. Plugin
implementation code belongs to ``mycevo-runner`` and must not run in Core.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any


PLUGIN_GROUP = "mycevo.plugins"


def inspect_entry_points() -> list[dict[str, str]]:
    """Return discoverable plugin metadata without importing implementations."""

    discovered = entry_points()
    selected = discovered.select(group=PLUGIN_GROUP) if hasattr(discovered, "select") else discovered.get(PLUGIN_GROUP, ())
    return [
        {
            "name": item.name,
            "value": item.value,
            "group": PLUGIN_GROUP,
            "implementation_loaded": "false",
        }
        for item in selected
    ]
