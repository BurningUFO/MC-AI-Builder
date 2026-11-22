"""Take a player's local edit on an existing blueprint through the ordinary plan diff."""

from collections import deque
from itertools import product

from src.architect.design import DesignError, VoxelDesign
from src.architect.grammar import LIGHT_BLOCKS, ROOF_MATERIALS
from src.construction.plan import VANILLA_BLOCKS


def local_gaze(gaze, plan):
    if not gaze:
        return None
    x, y, z = (value - origin for value, origin in zip(gaze["position"], plan["origin"], strict=True))
    rotation = plan["rotation"] // 90
    x, z = ((x, z), (z, -x), (-x, -z), (-z, x))[rotation]
    face = gaze["face"]
    sides = ("north", "east", "south", "west")
    if face in sides:
        face = sides[(sides.index(face) - rotation) % 4]
    return {"position": [x, y, z], "face": face}


def _box(module):
    return tuple(map(min, module["from"], module["to"])), tuple(map(max, module["from"], module["to"]))


def _inside(pos, bounds):
    return all(low <= axis <= high for axis, low, high in zip(pos, *bounds))


def roof_cells(spec, building):
    if spec["kind"] == "cottage":
        return {pos for pos in building.blocks if pos[1] == 3}
    if spec["kind"] == "window_wall":
        height = spec["parameters"]["height"] - 1
        return {pos for pos in building.blocks if pos[1] == height}
    modules = spec["parameters"].get("modules", ())
    cells = set()
    for module in modules:
        mode = module.get("mode")
        bounds = _box(module)
        if mode == "roof":
            overhang = module.get("overhang")
            cells.update(VoxelDesign.generate_roof(module["style"], bounds, 1 if overhang is None else overhang,
                                                  "minecraft:oak_planks", module.get("ridge_axis")))
        elif mode == "framing":
            cells.difference_update(VoxelDesign.generate_framing(bounds, "minecraft:oak_log", module.get("spacing") or 4))
        elif cells:
            cells = {pos for pos in cells if not _inside(pos, bounds) or mode == "shell"
                     and all(lo < v < hi for v, lo, hi in zip(pos, *bounds))}
    if cells:
        return cells & building.blocks.keys()

    # Legacy voxel roofs can have plain layers instead of a roof module. Look for a solid
    # interior patch above the walls, and keep only the roof material above that patch.
    for module in reversed(modules):
        if module["mode"] == "fill":
            lo, hi = _box(module)
            if lo[1] == hi[1] and lo[1] >= 3 and hi[0] - lo[0] >= 2 and hi[2] - lo[2] >= 2:
                base = module.get("block")
                layers = [_box(row) for row in modules if row["mode"] == "fill" and row.get("block") == base]
                compatible = {base}
                if base in ROOF_MATERIALS:
                    compatible.update(ROOF_MATERIALS[base] + suffix for suffix in ("_stairs", "_slab"))
                bottom = min(a[1] for a, b in layers if a[1] == b[1] and a[1] >= 3
                             and b[2] - a[2] >= 2 and b[0] - a[0] >= 2)
                return {pos for pos, block in building.blocks.items() if pos[1] >= bottom
                        and any(_inside(pos, layer) for layer in layers) and block.state.block_id in compatible}
    return set()


def _wall_material(block):
    if block is None or block.block_entity is not None:
        return False
    kind = block.state.block_id
    return kind not in LIGHT_BLOCKS and not kind.endswith(("_stairs", "_slab"))


def _wall(building, gaze, roof, spec):
    if not gaze or gaze["face"] not in ("north", "south", "east", "west"):
        raise DesignError("请用准心对着要改的那面墙，再跟我说一声。")
    start = tuple(gaze["position"])
    axis = 0 if gaze["face"] in ("east", "west") else 2
    along = 2 - axis
    offset = 1 if gaze["face"] in ("east", "south") else -1
    if start[1] <= 0:
        raise DesignError("请把准心稍微抬高，对准地板上方的墙体。")

    def allowed(pos):
        front = list(pos)
        front[axis] += offset
        return (pos[1] > 0 and pos not in roof and _wall_material(building.blocks.get(pos))
                and not _wall_material(building.blocks.get(tuple(front))))

    if not allowed(start):
        raise DesignError("准心里的位置不属于当前方案的可改墙面。")
    found = {start}
    pending = deque(found)
    while pending:
        pos = pending.popleft()
        for dimension, direction in product((along, 1), (-1, 1)):
            neighbor = list(pos)
            neighbor[dimension] += direction
            neighbor = tuple(neighbor)
            if neighbor not in found and allowed(neighbor):
                found.add(neighbor)
                pending.append(neighbor)
    # Keep shared corner columns whenever the adjoining wall returns into the building.
    border = (min(pos[along] for pos in found), max(pos[along] for pos in found))
    if border[1] - border[0] >= 2 and spec["kind"] != "window_wall":
        shared = set()
        for pos in found:
            inward = list(pos)
            inward[axis] -= offset
            if pos[along] in border and allowed(pos) and _wall_material(building.blocks.get(tuple(inward))):
                shared.add(pos)
        found -= shared
    return found, along


def apply_edit(building, spec, edit, gaze):
    roof = roof_cells(spec, building)
    surface, material = edit["surface"], edit["material"]
    if material not in VANILLA_BLOCKS:
        raise DesignError("请选择一种可施工的原版材料。")
    if surface == "roof":
        if material not in ROOF_MATERIALS or not roof:
            raise DesignError("我需要能辨认出原屋顶的范围，并用木板或石材保留它的坡面。")
        changes = {}
        for pos in roof:
            block = building.blocks[pos]
            old = block.state.block_id
            if old in LIGHT_BLOCKS or block.block_entity is not None:
                continue
            suffix = next((form for form in ("_stairs", "_slab") if old.endswith(form)), "")
            changes[pos] = (ROOF_MATERIALS[material] + suffix if suffix else material, dict(block.state.properties) if suffix else {})
    else:
        wall, along = _wall(building, gaze, roof, spec)
        if surface == "wall":
            if material in LIGHT_BLOCKS or material.endswith(("_stairs", "_slab")) or "glass" in material:
                raise DesignError("整面墙需要整块墙材。想加玻璃时，告诉我窗户的位置和大小。")
            properties = {"axis": "y"} if material.endswith("_log") else {}
            changes = {pos: (material, properties) for pos in wall
                       if "glass" not in building.blocks[pos].state.block_id}
        else:
            if "glass" not in material:
                raise DesignError("窗洞可用全块玻璃，请告诉我想要的颜色。")
            width, height = edit.get("width", 2), edit.get("height", 2)
            x, y, z = gaze["position"]
            position = [x, y, z]
            floor = min(pos[1] for pos in wall)
            top = max(pos[1] for pos in wall)
            height = min(height, top - floor + 1) if edit.get("floor_to_ceiling") else height
            bottom = floor if edit.get("floor_to_ceiling") else min(y, top - height + 1)
            changes = {}
            anchor = position[along]
            for left in dict.fromkeys((anchor - (width - 1) // 2, anchor - width // 2, anchor - width + 1)):
                window = set()
                for column, row in product(range(left, left + width), range(bottom, bottom + height)):
                    position[along], position[1] = column, row
                    window.add(tuple(position))
                if window and window <= wall:
                    changes = {pos: (material, {}) for pos in window}
                    break
            if not changes:
                raise DesignError("这里放不下这组窗户。请对准一段宽一点的墙，或告诉我缩小尺寸。")
    if not changes:
        raise DesignError("这处方案里没有需要替换的方块。")
    for pos, (block, properties) in changes.items():
        building.place(pos, block, **properties)
    return building
