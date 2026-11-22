"""Procedural building pieces and a conservative lighting layout for voxel designs."""

from collections import deque
from itertools import product

from src.construction.plan import MAX_ACTIONS, WOOD_FAMILIES

ROOF_STYLES = ("gable", "hip", "parapet")
LIGHTING = ("torch", "lantern", "none")
ROOF_MATERIALS = {f"minecraft:{family}_planks": f"minecraft:{family}" for family in WOOD_FAMILIES}
ROOF_MATERIALS.update({f"minecraft:{base}": f"minecraft:{piece}" for base, piece in (
    ("stone", "stone"), ("cobblestone", "cobblestone"), ("stone_bricks", "stone_brick"), ("bricks", "brick"),
)})
HORIZONTAL = {"west": (-1, 0), "east": (1, 0), "north": (0, -1), "south": (0, 1)}
OPPOSITE = {"west": "east", "east": "west", "north": "south", "south": "north"}
LIGHT_BLOCKS = {"minecraft:torch", "minecraft:wall_torch", "minecraft:lantern"}

GRAMMAR_PROMPT = """For a house, prefer a roof module and a framing module over hand-writing stairs.
A roof module has mode "roof", style "gable", "hip", or "parapet", from/to the two corners of the
wall footprint at the roof's eave Y, block a plank or masonry block, optional overhang (default 1),
and optional ridge_axis "x" or "z". A modded roof material needs its companion stairs and slabs in
available_mod_blocks. The generator seals the ceiling, orients the stairs and makes
the ridge and corners symmetric. To add a chimney, place it after the roof module.
A framing module has mode "framing", from/to the diagonal corners of the frame, block an overworld
log, optional spacing (default 4). It creates corner posts, repeated bays, and overhead beams.
Cut doors/windows with later erase modules before adding glazing. Set changes.lighting to "torch"
(default for roof modules), "lantern", or "none". The planner places entry lights and lights dark
sections of floors and roofs. Lighting is included in the material list."""


def roof_family(material, available_mod_blocks=()):
    if material in ROOF_MATERIALS:
        return ROOF_MATERIALS[material]
    if material in available_mod_blocks:
        for prefix in dict.fromkeys((material.removesuffix("_planks"), material, material.removesuffix("s"))):
            if {prefix + "_stairs", prefix + "_slab"} <= set(available_mod_blocks):
                return prefix
    return None


def generate_roof(style, bounds, overhang=1, material="minecraft:spruce_planks", ridge_axis=None, *, available_mod_blocks=()):
    """Return block states for a sealed roof with one continuous ceiling deck."""
    prefix = roof_family(material, available_mod_blocks)
    if style not in ROOF_STYLES or prefix is None or type(overhang) is not int or not 0 <= overhang <= 2:
        raise ValueError("屋顶需要人字顶、四面坡或女儿墙，以及一种受支持的木板或石材。")
    (x0, y, z0), (x1, upper, z1) = bounds
    if y != upper or x1 - x0 < 2 or z1 - z0 < 2:
        raise ValueError("屋顶范围需要同高且宽深至少 3 格。")
    ridge_axis = ridge_axis or ("x" if x1 - x0 >= z1 - z0 else "z")
    if ridge_axis not in ("x", "z"):
        raise ValueError("屋脊只能沿 x 或 z。")
    x0, x1, z0, z1 = x0 - overhang, x1 + overhang, z0 - overhang, z1 + overhang
    if (x1 - x0 + 1) * (z1 - z0 + 1) > MAX_ACTIONS:
        raise ValueError("屋顶出檐后的占地太大，请缩小屋顶。")
    full, slab = (material, {}), (prefix + "_slab", {"type": "top", "waterlogged": "false"})
    cells = {}
    for x, z in product(range(x0, x1 + 1), range(z0, z1 + 1)):
        edges = {"east": x - x0, "west": x1 - x, "south": z - z0, "north": z1 - z}
        perimeter = min(edges.values()) == 0
        if style == "parapet":
            cells[x, y, z] = full
            if perimeter:
                cells[x, y + 1, z] = full
                corner = x in (x0, x1) and z in (z0, z1)
                cells[x, y + 2, z] = full if corner else slab
            continue
        cells[x, y - 1, z] = full
        cross = ("south", "north") if ridge_axis == "x" else ("east", "west")
        slope = tuple(edges) if style == "hip" else cross
        rise = min(edges[direction] for direction in slope)
        faces = [direction for direction in slope if edges[direction] == rise]
        for step in range(rise):
            cells[x, y + step, z] = full
        peak = (edges[cross[0]] + edges[cross[1]]) // 2 if style == "gable" else min(x1 - x0, z1 - z0) // 2
        if rise == peak:
            along = ("east", "west") if ridge_axis == "x" else ("south", "north")
            if style == "hip":
                along = ("east", "west") if x1 - x0 >= z1 - z0 else ("south", "north")
            end = min(edges[direction] for direction in along) == (peak if style == "hip" else 0)
            # A top slab needs a same-level face to click. The two solid verge caps seed the row.
            cells[x, y + rise, z] = full if end else slab
        elif len(faces) == 1:
            cells[x, y + rise, z] = (prefix + "_stairs", {
                "facing": faces[0], "half": "bottom", "shape": "straight", "waterlogged": "false",
            })
        else:
            cells[x, y + rise, z] = full
        if len(cells) > MAX_ACTIONS:
            raise ValueError("坡屋顶展开后超出施工容量，请缩小屋顶或分段设计。")
    if len(cells) > MAX_ACTIONS:
        raise ValueError("屋顶展开后超出施工容量，请缩小屋顶或分段设计。")
    return cells


def generate_framing(bounds, log_type, spacing=4, *, available_mod_blocks=()):
    supported = log_type in {f"minecraft:{wood}_log" for wood in WOOD_FAMILIES}
    supported |= log_type in available_mod_blocks and log_type.endswith(("_log", "_beam", "_stem"))
    if not supported or type(spacing) is not int or not 2 <= spacing <= 8:
        raise ValueError("立柱需要原木，柱距可设为 2–8 格。")
    (x0, y0, z0), (x1, y1, z1) = bounds
    if x1 - x0 < 2 or z1 - z0 < 2 or y1 <= y0:
        raise ValueError("框架需要立柱高度和至少 3×3 的跨度。")

    def posts(low, high):
        distance = high - low
        bays = (distance + spacing - 1) // spacing
        if distance % 2 and bays % 2 == 0:
            bays += 1
        return {low + round(index * distance / bays) for index in range(bays + 1)}

    cells = {(x, y1, z): (log_type, {"axis": "x"})
             for x, z in product(range(x0, x1 + 1), (z0, z1))}
    cells.update({(x, y1, z): (log_type, {"axis": "z"})
                  for x, z in product((x0, x1), range(z0 + 1, z1))})
    columns = {(x, z) for x, z in product(posts(x0, x1), (z0, z1))}
    columns.update(product((x0, x1), posts(z0, z1)))
    for x, z in columns:
        for y in range(y0, y1 + 1):
            cells[x, y, z] = log_type, {"axis": "y"}
    return cells


def walkable_cells(building):
    for (x, y, z), support in building.blocks.items():
        kind = support.state.block_id
        if (y < 0 or kind in LIGHT_BLOCKS or kind.endswith("_stairs") or "glass" in kind
                or kind.endswith("_slab") and dict(support.state.properties).get("type") != "top"):
            continue
        feet = x, y + 1, z
        head = x, y + 2, z
        if all(cell not in building.blocks or building.blocks[cell].state.block_id in LIGHT_BLOCKS for cell in (feet, head)):
            yield feet


def illuminate(building, light="torch"):
    """Space lamps along walkable surfaces, with a lamp on each side of an open entry."""
    if light == "none":
        return
    floors = set(walkable_cells(building))
    if not floors:
        return
    blocks = building.blocks

    def solid(pos):
        block = blocks.get(pos)
        return (block is not None and block.state.block_id not in LIGHT_BLOCKS
                and "glass" not in block.state.block_id and not block.state.block_id.endswith(("_stairs", "_slab")))

    baseline = [(x, z) for x, y, z in blocks if y == 0]
    if baseline:
        x0, z0 = tuple(map(min, zip(*baseline)))
        x1, z1 = tuple(map(max, zip(*baseline)))
        edges = (
            ("north", range(x0 + 1, x1), lambda n, y: (n, y, z0)),
            ("south", range(x0 + 1, x1), lambda n, y: (n, y, z1)),
            ("west", range(z0 + 1, z1), lambda n, y: (x0, y, n)),
            ("east", range(z0 + 1, z1), lambda n, y: (x1, y, n)),
        )
        for face, columns, wall_point in edges:
            dx, dz = HORIZONTAL[face]
            for column in columns:
                x, y, z = wall_point(column, 1)
                if (x, y, z) not in floors or (x - dx, y, z - dz) not in floors:
                    continue
                pair = []
                for direction in (-1, 1):
                    support = next((wall_point(column + direction * distance, height)
                                    for distance, height in product((1, 2), (2, 1))
                                    if solid(wall_point(column + direction * distance, height))), None)
                    if support is not None:
                        sx, sy, sz = support
                        pair.append((sx + dx, sy, sz + dz))
                if len(pair) == 2:
                    for point in pair:
                        if point not in blocks:
                            building.place(point, "minecraft:wall_torch", facing=face)

    covered = set()

    def cover(start):
        pending = deque([(start, 0)])
        seen = {start}
        while pending:
            point, distance = pending.popleft()
            covered.add(point)
            if distance == 5:
                continue
            x, y, z = point
            for dx, dz in HORIZONTAL.values():
                other = x + dx, y, z + dz
                if other in floors and other not in seen:
                    pending.append((other, distance + 1))
                    seen.add(other)

    for pos in sorted(floors):
        if pos in covered:
            continue
        x, y, z = pos
        if pos not in blocks:
            wall = next((direction for direction, (dx, dz) in HORIZONTAL.items()
                         if solid((x + dx, y + 1, z + dz))), None)
            if wall and light == "torch" and (x, y + 1, z) not in blocks:
                building.place((x, y + 1, z), "minecraft:wall_torch", facing=OPPOSITE[wall])
            elif light == "lantern":
                hanger = x, y + 1, z
                if solid((x, y + 2, z)) and hanger not in blocks:
                    building.place(hanger, "minecraft:lantern", hanging="true", waterlogged="false")
                else:
                    building.place(pos, "minecraft:lantern", hanging="false", waterlogged="false")
            else:
                building.place(pos, "minecraft:torch")
        cover(pos)
