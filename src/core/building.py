"""Sparse building data in Minecraft coordinates: x east, y up, z south."""

from collections import Counter
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile


Position = tuple[int, int, int]
RESOURCE_ID = re.compile(r"[a-z0-9_.-]+:[a-z0-9_./-]+")
STATE_VALUE = re.compile(r"[a-z0-9_-]+")
SCHEMA_VERSION = 1


def resource_id(value: str) -> str:
    if not isinstance(value, str) or RESOURCE_ID.fullmatch(value) is None:
        raise ValueError(f"Invalid Minecraft resource ID: {value!r}")
    namespace, path = value.split(":", 1)
    if namespace in {".", ".."} or any(part in {"", ".", ".."} for part in path.split("/")):
        raise ValueError(f"Invalid Minecraft resource path: {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class BlockState:
    block_id: str
    properties: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        resource_id(self.block_id)
        if len(dict(self.properties)) != len(self.properties):
            raise ValueError(f"Duplicate state property for {self.block_id}")
        for name, value in self.properties:
            if not all(isinstance(part, str) and STATE_VALUE.fullmatch(part) for part in (name, value)):
                raise ValueError(f"Invalid state property: {name!r}={value!r}")
        object.__setattr__(self, "properties", tuple(sorted(self.properties)))

    @property
    def key(self) -> str:
        if not self.properties:
            return self.block_id
        values = ",".join(f"{name}={value}" for name, value in self.properties)
        return f"{self.block_id}[{values}]"

    @classmethod
    def parse(cls, key: str) -> "BlockState":
        """Read the ID[property=value,...] notation emitted by key."""
        block_id, bracket, suffix = key.partition("[")
        if not bracket:
            return cls(block_id)
        if not suffix.endswith("]"):
            raise ValueError(f"Missing closing bracket in {key!r}")
        properties = []
        for entry in suffix[:-1].split(","):
            name, _, value = entry.partition("=")
            properties.append((name, value))
        return cls(block_id, tuple(properties))


@dataclass(slots=True)
class Block:
    state: BlockState
    block_entity: dict | None = None


@dataclass
class Building:
    name: str
    blocks: dict[Position, Block] = field(default_factory=dict)
    minecraft_version: str = "1.20.1"
    data_version: int = 3465

    def place(
        self,
        position: Position,
        block_id: str,
        *,
        block_entity: dict | None = None,
        **properties: str,
    ) -> None:
        if len(position) != 3 or any(type(axis) is not int for axis in position):
            raise ValueError(f"Expected three integer Minecraft coordinates: {position!r}")
        if block_entity is not None and not isinstance(block_entity, dict):
            raise ValueError(f"Expected a block entity object at {position!r}")
        self.blocks[position] = Block(BlockState(block_id, tuple(properties.items())), block_entity)

    def bounds(self) -> tuple[Position, Position]:
        if not self.blocks:
            raise ValueError("An empty building has no bounds")
        axes = tuple(zip(*self.blocks))
        return tuple(map(min, axes)), tuple(map(max, axes))

    def state_counts(self) -> Counter:
        return Counter(block.state for block in self.blocks.values())

    def write(self, path: Path) -> None:
        minimum, maximum = self.bounds()
        blocks = []
        for (x, y, z), block in sorted(self.blocks.items(), key=lambda entry: (entry[0][1], entry[0][2], entry[0][0])):
            row = {"x": x, "y": y, "z": z, "id": block.state.block_id, "properties": dict(block.state.properties)}
            if block.block_entity is not None:
                row["block_entity"] = block.block_entity
            blocks.append(row)
        write_json(path, {
            "schema_version": SCHEMA_VERSION,
            "name": self.name,
            "minecraft_version": self.minecraft_version,
            "data_version": self.data_version,
            "bounds": {"min": list(minimum), "max": list(maximum)},
            "blocks": blocks,
        })

    @classmethod
    def read(cls, path: Path) -> "Building":
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["schema_version"] != SCHEMA_VERSION:
            raise ValueError(f"Unsupported building schema: {data['schema_version']}")
        building = cls(data["name"], minecraft_version=data["minecraft_version"], data_version=data["data_version"])
        for row in data["blocks"]:
            position = row["x"], row["y"], row["z"]
            if position in building.blocks:
                raise ValueError(f"Duplicate block at {position}")
            building.place(position, row["id"], block_entity=row.get("block_entity"), **row["properties"])
        minimum, maximum = building.bounds()
        if data["bounds"] != {"min": list(minimum), "max": list(maximum)}:
            raise ValueError(f"Stored bounds do not match the blocks in {path}")
        return building


def write_json(path: Path, value: dict, *, compact: bool = False) -> None:
    """Replace a JSON file only after its full contents have been written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as stream:
            scratch = Path(stream.name)
            if compact:
                json.dump(value, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            else:
                json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
        scratch.replace(path)
    finally:
        if scratch is not None:
            scratch.unlink(missing_ok=True)

