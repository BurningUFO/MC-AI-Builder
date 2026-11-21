"""Generate the two versions of the existing M1 window wall for M2."""

from pathlib import Path

from src.construction.plan import demo_wall, make_plan
from src.core.building import Building, Position, write_json


def revised_wall() -> Building:
    wall = demo_wall()
    wall.name = "M2 blue-glass window wall"
    for x in (1, 2, 5, 6):
        wall.place((x, 1, 0), "minecraft:light_blue_stained_glass")
    wall.place((0, 1, 0), "minecraft:oak_planks")
    del wall.blocks[(3, 0, 1)]
    wall.place((8, 0, 0), "minecraft:stone_bricks")
    return wall


def export_revision_demo(directory: Path, origin: Position = (6, -60, 6), rotation: int = 0) -> tuple[dict, dict]:
    originals = (demo_wall(), revised_wall())
    plans = []
    for version, model in enumerate(originals, start=1):
        folder = directory / f"v{version}"
        model_path = folder / "building.json"
        model.write(model_path)
        plan = make_plan(Building.read(model_path), building_id="m2_wall", version=version,
                         origin=origin, rotation=rotation)
        write_json(folder / "construction-plan.json", plan)
        plans.append(plan)
    return plans[0], plans[1]
