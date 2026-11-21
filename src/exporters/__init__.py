"""Contract for future exporters of the shared block data."""

from pathlib import Path
from typing import Protocol

from src.core.building import Building


class BlueprintExporter(Protocol):
    def write(self, building: Building, destination: Path) -> None:
        """Serialize blocks and their states to a blueprint file."""
        ...
