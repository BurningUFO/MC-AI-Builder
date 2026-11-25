"""Check pedestrian connections on the generated Minecraft blueprint."""

from collections import deque
from itertools import pairwise

from src.core.building import Building
from src.design import digest, read_json


DIRECTIONS = {"east": (1, 0), "west": (-1, 0), "south": (0, 1), "north": (0, -1)}
AIR = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}
NON_FLOORS = AIR | {"minecraft:water", "minecraft:lava", "minecraft:powder_snow", "minecraft:bubble_column"}


def floor_edges(state, assets, y):
    """Height in half blocks at each edge of a supported floor block."""
    if state.block_id in NON_FLOORS:
        return None
    if assets[state.key]["full_cube"]:
        return dict.fromkeys(DIRECTIONS.values(), 2 * y + 2)
    props = dict(state.properties)
    if state.block_id.endswith("_slab") and props.get("type") in {"bottom", "top", "double"}:
        return dict.fromkeys(DIRECTIONS.values(), 2 * y + (1 if props["type"] == "bottom" else 2))
    if state.block_id.endswith("_stairs") and props.get("shape") == "straight":
        if props.get("half") == "top":
            return dict.fromkeys(DIRECTIONS.values(), 2 * y + 2)
        if props.get("half") == "bottom" and props.get("facing") in DIRECTIONS:
            dx, dz = DIRECTIONS[props["facing"]]
            edges = dict.fromkeys(DIRECTIONS.values(), 2 * y + 2)
            edges[(-dx, -dz)] -= 1
            return edges
    return None


def shortest_walk(surfaces, start, goal):
    previous = {start: None}
    queue = deque([start])
    while queue and goal not in previous:
        pos = queue.popleft()
        x, y, z = pos
        for (dx, dz), height in surfaces[pos].items():
            for ny in (y, y + 1, y - 1):
                neighbor = x + dx, ny, z + dz
                if neighbor in surfaces and neighbor not in previous:
                    entry = surfaces[neighbor][(-dx, -dz)]
                    if abs(height - entry) <= 1:
                        previous[neighbor] = pos
                        queue.append(neighbor)
    if goal not in previous:
        return None
    path = [goal]
    while path[-1] != start:
        path.append(previous[path[-1]])
    return path[::-1]


def trace_walk(project, stops):
    blueprint = project / "building.json"
    before = digest(blueprint)
    building = Building.read(blueprint)
    index = read_json(project / "material-index.json")
    if before != index["building_sha256"] or before != digest(blueprint):
        raise ValueError("The blueprint and material index differ. Generate them again.")
    assets = index["states"]
    fallbacks = index.get("render_fallbacks", {})
    occupied = {pos for pos, block in building.blocks.items() if block.state.block_id not in AIR}
    floors = {}
    for (x, y, z), block in building.blocks.items():
        if block.state.key not in assets or block.state.key in fallbacks:
            continue
        if (x, y + 1, z) in occupied or (x, y + 2, z) in occupied:
            continue
        if edges := floor_edges(block.state, assets, y):
            floors[(x, y, z)] = edges

    segments = []
    for start, end in pairwise(stops):
        missing = [p for p in (start, end) if p not in floors]
        path = None if missing else shortest_walk(floors, start, end)
        blocked = [{"stop": p, "blockers": [(p[0], p[1] + dy, p[2]) for dy in (1, 2)
                                           if (p[0], p[1] + dy, p[2]) in occupied]} for p in missing]
        segments.append({"from": start, "to": end, "invalid_stops": blocked, "path": path})
    return {"building_sha256": before, "segments": segments}
