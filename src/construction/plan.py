"""Versioned plan envelope for the existing sparse Minecraft building model."""

from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4
import re

from src.core.building import Building, Position, write_json


SCHEMA_VERSION = 1
MAX_ACTIONS = 4096
VANILLA_BLOCKS = frozenset(f"minecraft:{name}" for name in (
    "stone", "cobblestone", "stone_bricks", "oak_planks", "spruce_planks", "dark_oak_planks",
    "dirt", "sandstone", "smooth_sandstone", "deepslate_bricks", "bricks", "mossy_cobblestone",
    "andesite", "polished_andesite", "terracotta", "white_terracotta", "red_terracotta", "mud_bricks",
    "birch_planks", "jungle_planks", "acacia_planks", "mangrove_planks", "cherry_planks",
    "oak_log", "spruce_log", "dark_oak_log", "stripped_oak_log", "stripped_spruce_log", "stripped_dark_oak_log",
    "spruce_stairs", "dark_oak_stairs", "brick_stairs", "spruce_slab", "dark_oak_slab", "brick_slab",
    "white_stained_glass", "green_stained_glass", "brown_stained_glass",
    "glass", "light_blue_stained_glass", "blue_stained_glass", "oak_stairs", "stone_brick_stairs", "oak_slab", "stone_brick_slab",
))


def make_plan(
    building: Building,
    *,
    building_id: str,
    version: int,
    origin: Position,
    rotation: int = 0,
) -> dict:
    """Carry explicit states and optional block-entity data to the backend."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", building_id):
        raise ValueError("Building ID must be 1–64 lowercase letters, digits, dots, underscores or hyphens.")
    if type(version) is not int or not 1 <= version <= 2_147_483_647:
        raise ValueError("Building version must be a positive 32-bit integer.")
    if len(origin) != 3 or any(type(axis) is not int or abs(axis) > 30_000_000 for axis in origin):
        raise ValueError("Origin must be three Minecraft coordinates.")
    if type(rotation) is not int or rotation not in (0, 90, 180, 270):
        raise ValueError("Rotation must be 0, 90, 180 or 270 degrees clockwise.")
    if (building.minecraft_version, building.data_version) != ("1.20.1", 3465):
        raise ValueError("The M1 backend expects Minecraft 1.20.1 data (3465).")
    if not 1 <= len(building.blocks) <= MAX_ACTIONS:
        raise ValueError(f"A construction plan needs 1–{MAX_ACTIONS} target blocks.")

    minimum, maximum = building.bounds()
    actions = []
    # Place lower layers first so foundations and support faces are available.
    for (x, y, z), block in sorted(building.blocks.items(), key=lambda entry: (entry[0][1], entry[0][2], entry[0][0])):
        block_id = block.state.block_id
        action = {
            "local": [x, y, z],
            "id": block_id,
            "properties": dict(block.state.properties),
            "item_id": block_id if block_id in VANILLA_BLOCKS else None,
        }
        if block.block_entity is not None:
            action["block_entity"] = block.block_entity
        actions.append(action)

    model = [{key: value for key, value in action.items() if key != "item_id"} for action in actions]
    canonical = json.dumps(model, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return {
        "schema_version": SCHEMA_VERSION,
        "model_sha256": sha256(canonical.encode("utf-8")).hexdigest(),
        "plan_id": str(uuid4()),
        "building_id": building_id,
        "building_version": version,
        "minecraft_version": building.minecraft_version,
        "data_version": building.data_version,
        "origin": list(origin),
        "rotation": rotation,
        "local_bounds": {"min": list(minimum), "max": list(maximum)},
        "actions": actions,
    }


def demo_wall() -> Building:
    wall = Building("M1 window wall")
    for x in range(8):
        wall.place((x, 0, 0), "minecraft:stone_bricks")
        material = "spruce_planks" if x in (0, 3, 4, 7) else "glass"
        wall.place((x, 1, 0), f"minecraft:{material}")
        wall.place((x, 2, 0), "minecraft:stone_brick_slab", type="bottom", waterlogged="false")
    for x in range(2, 6):
        wall.place((x, 0, 1), "minecraft:stone_bricks")
    for x in (1, 6):
        wall.place((x, 0, 1), "minecraft:oak_stairs",
                   facing="north", half="bottom", shape="straight", waterlogged="false")
    return wall


def export_demo(directory: Path, origin: Position, rotation: int = 0) -> dict:
    building = demo_wall()
    model_path = directory / "building.json"
    building.write(model_path)
    plan = make_plan(Building.read(model_path), building_id="m1_wall", version=1, origin=origin, rotation=rotation)
    write_json(directory / "construction-plan.json", plan)
    return plan
