"""Oxbow Herbarium, with an oval specimen hall and an oblique reading wing."""

from math import atan2, floor, tau

from src.core.building import Building


STONE = "twigs:polished_calcite_bricks"
PILASTER = "quark:calcite_pillar"
DETAIL_STONE = "quark:calcite_bricks"
FLOOR = "minecraft:smooth_sandstone"
PAVING = "minecraft:polished_andesite"
BIRCH = "quark:vertical_birch_planks"
TIMBER = "minecraft:stripped_spruce_log"
GLASS = "minecraft:glass"
WEATHERED = "minecraft:waxed_weathered_cut_copper"
PATINA = "minecraft:waxed_oxidized_cut_copper"
WARM_COPPER = "minecraft:waxed_exposed_cut_copper"
MOSS = "minecraft:moss_block"
LANTERN = "minecraft:lantern"
SHELF = "quark:dark_oak_bookshelf"

CENTER = -5
MAIN_STAIR = {(x, z) for x in range(-14, -8) for z in (-4, -3)}
ARCHIVE_STAIR = {(x, z) for x in range(16, 21) for z in (-4, -3)}
SOUTH_READING_WINDOW = "quark:cyan_framed_glass"
NEIGHBORS = ((-1, 0), (1, 0), (0, -1), (0, 1))


class Plan:
    def __init__(self):
        self.building = Building("折岸植物标本馆 · Oxbow Herbarium")
        self.parts = {}

    def set(self, part, x, y, z, block_id, **properties):
        pos = (x, y, z)
        self.building.place(pos, block_id, **properties)
        self.parts[pos] = part

    def step(self, part, x, y, z, facing, block_id="minecraft:spruce_stairs"):
        self.set(part, x, y, z, block_id, facing=facing, half="bottom", shape="straight", waterlogged="false")

    def slab(self, part, x, y, z, block_id, kind="bottom"):
        self.set(part, x, y, z, block_id, type=kind, waterlogged="false")


def ellipse(rx, rz):
    return {(x, z) for x in range(CENTER - rx, CENTER + rx + 1) for z in range(-rz, rz + 1)
            if (x - CENTER) ** 2 * rz ** 2 + z ** 2 * rx ** 2 <= rx ** 2 * rz ** 2}


def wing_limits(x):
    shift = max(0, (x - 8) // 4)
    chamfer = max(0, x - 22)
    return -4 - shift + chamfer, 7 - shift - chamfer


def perimeter(region):
    return {(x, z) for x, z in region if any((x + dx, z + dz) not in region for dx, dz in NEIGHBORS)}


def foundations(plan, oval, wing):
    footprint = oval | wing
    apron = ellipse(13, 10) | footprint | {
        (x + dx, z + dz) for x, z in wing for dx, dz in NEIGHBORS
    } | {(x, z) for x in range(-10, 1) for z in range(8, 14)}
    for x, z in apron:
        if (x, z) not in ARCHIVE_STAIR:
            plan.set("01 Site/Stone plinth", x, -1, z, STONE if (x, z) in footprint else PAVING)
    for x, z in footprint - ARCHIVE_STAIR:
        use_wood = (x, z) in wing and x >= 8
        centerline = -6 <= x <= -4 or -1 <= z <= 1
        block = BIRCH if use_wood else DETAIL_STONE if centerline else FLOOR
        plan.set("03 Ground floor/Floors", x, 0, z, block)
    for x in range(-9, 0):
        for z in range(8, 12):
            plan.set("01 Site/Entry terrace", x, 0, z, FLOOR if -7 <= x <= -3 else STONE)
        plan.step("01 Site/Entry terrace", x, 0, 12, "north", "minecraft:polished_andesite_stairs")
    for x, z in ((-10, 11), (-1, 12), (0, 12), (1, 11), (-14, 7), (9, 7), (12, 7), (15, 6), (20, 6)):
        if (x, -1, z) not in plan.building.blocks:
            plan.set("01 Site/Stone plinth", x, -1, z, PAVING)
        plan.set("01 Site/Planting", x, 0, z, MOSS)
        plan.set("01 Site/Planting", x, 1, z, "minecraft:fern")


def specimen_shell(plan, oval, wing, outside):
    structural = "03 Ground floor/Specimen hall walls"
    high_walls = "04 Mezzanine/Clerestory walls"
    high_sash = "04 Mezzanine/Clerestory glazing"
    for x, z in perimeter(oval):
        phase = (atan2(z / 9, (x - CENTER) / 12) % tau) * 15 / tau
        pier = abs(phase - round(phase)) < 0.26
        entrance = -7 <= x <= -3 and z >= 7
        for y in range(1, 11):
            terrace_exit = (x, z) in wing and x >= 6 and -1 <= z <= 1 and 7 <= y <= 9
            if (y <= 6 and (x, z) in wing and (x, z) not in outside) or terrace_exit or (entrance and y < 4):
                continue
            elif y in (5, 10):
                plan.set(high_walls if y == 10 else structural, x, y, z, TIMBER,
                         axis="x" if abs(z) >= 5 else "z")
            elif y in (1, 6) or pier or entrance:
                plan.set(high_walls if y >= 6 else structural, x, y, z,
                         PILASTER if pier else STONE, **({"axis": "y"} if pier else {}))
            else:
                plan.set(high_sash if y >= 7 else "03 Ground floor/Specimen glazing", x, y, z, GLASS)


def entry_portico(plan, oval):
    canopy = "05 Roof/Entry canopy"
    supports = "03 Ground floor/Entry portico"
    for dx in range(-4, 5):
        x = CENTER + dx
        span = abs(dx)
        wall_z = max(z for px, z in oval if px == x)
        front = 11 if span == 4 else 12
        for z in range(wall_z + 1, front + 1):
            if span >= 3:
                plan.slab(canopy, x, 4, z, WEATHERED + "_slab", "top")
                if span == 3:
                    plan.slab(canopy, x, 5, z, WEATHERED + "_slab")
            elif span == 2:
                plan.step(canopy, x, 5, z, "east" if dx < 0 else "west", WEATHERED + "_stairs")
            elif z == front:
                plan.slab(canopy, x, 5, z, WEATHERED + "_slab", "top")
            else:
                plan.set(canopy, x, 5, z, "chipped:windowed_birch_trapdoor", facing="north",
                         half="top", open="false", powered="false", waterlogged="false")

    for side, inward in ((-1, "east"), (1, "west")):
        pier = CENTER + 3 * side
        bracket = CENTER + 2 * side
        plan.set(supports, pier, 1, 11, PILASTER, axis="y")
        for y in (2, 3):
            plan.set(supports, pier, y, 11, "twigs:polished_calcite_brick_wall", up="true",
                     north="none", south="none", east="none", west="none", waterlogged="false")
        plan.set(supports, pier, 4, 11, "twigs:polished_calcite_brick_stairs", facing=inward,
                 half="top", shape="straight", waterlogged="false")
        plan.set(supports, bracket, 4, 11, "decorative_blocks:spruce_support", facing=inward,
                 up="true", horizontal="small", vertical="big")
        plan.set("03 Ground floor/Lighting", bracket, 4, 10, LANTERN, hanging="true", waterlogged="false")


def reading_wing(plan, oval, wing, outside):
    walls = "03 Ground floor/Reading room walls"
    for x, z in (wing & outside) - oval:
        north, south = wing_limits(x)
        ribbon = (16 <= x <= 19 and z == south)
        bay = (z in (north, south) and x not in {11, 15, 20, 23, 24})
        for y in range(1, 6):
            if 2 <= y <= 4 and (ribbon or bay):
                part = "03 Ground floor/South reading window" if ribbon else "03 Ground floor/Reading glazing"
                plan.set(part, x, y, z, SOUTH_READING_WINDOW if ribbon else GLASS)
            else:
                plan.set(walls, x, y, z, WARM_COPPER if y == 5 else STONE)
    railings = perimeter(wing) - oval
    for x, z in wing:
        if (x, z) in oval:
            continue
        block = WEATHERED if (x, z) in railings else PAVING if -1 <= z <= 0 else FLOOR
        plan.set("04 Mezzanine/East terrace deck", x, 6, z, block)
        if (x, z) in railings and x >= 8:
            plan.slab("04 Mezzanine/East terrace rail", x, 7, z, "minecraft:waxed_weathered_cut_copper_slab")
    for positions, z in ((range(11, 16), 3), (range(18, 23), -3)):
        for x in positions:
            part = "04 Mezzanine/Terrace planters"
            plan.set(part, x, 7, z, MOSS)
            if x in (positions.start, positions.stop - 1):
                plan.set(part, x, 8, z, "minecraft:birch_leaves", persistent="true", distance="7", waterlogged="false")
            else:
                plan.set(part, x, 8, z, "minecraft:flowering_azalea" if x % 2 else "minecraft:fern")
            plan.slab("04 Mezzanine/Terrace seating", x, 7, z - (1 if z > 0 else -1), "minecraft:spruce_slab")
    for x, z in ((10, 2), (16, 2), (17, -2), (23, -2)):
        plan.set("04 Mezzanine/Terrace lights", x, 7, z, LANTERN, hanging="false", waterlogged="false")


def mezzanine(plan, oval, wing):
    ring = oval - ellipse(9, 6)
    landing = {(x, z) for x in range(-9, 1) for z in (-5, -4, -3)}
    landing |= {(x, z) for x in (-9, -8) for z in range(-7, -4)}
    platform = (ring | landing) - MAIN_STAIR
    for x, z in platform:
        plan.set("04 Mezzanine/Gallery walk", x, 6, z, BIRCH)
    rail = perimeter(platform) & ellipse(11, 8)
    for x, z in rail:
        if (x, z) in wing or (x, z) in {(-8, -4), (-8, -3)} or x < -12 and -5 <= z <= -2:
            continue
        plan.slab("04 Mezzanine/Atrium rail", x, 7, z, "minecraft:spruce_slab")
    for x, z in MAIN_STAIR:
        height = x + 15
        for y in range(1, height):
            plan.set("04 Mezzanine/Main stair", x, y, z, BIRCH)
        plan.step("04 Mezzanine/Main stair", x, height, z, "east")


def canopy(plan):
    roof = ellipse(13, 10)
    heights = {(x, z): 11 + floor(4.4 * max(0, 1 - (x - CENTER) ** 2 / 169 - z ** 2 / 100) ** 1.1)
               for x, z in roof}
    for (x, z), top in heights.items():
        edges = {facing: heights.get((x + dx, z + dz), 10)
                 for facing, (dx, dz) in zip(("west", "east", "north", "south"), NEIGHBORS)}
        higher = max(edges, key=edges.get)
        skylight = (x - CENTER) ** 2 / 23.04 + (z + 1) ** 2 / 52 <= 1 and z not in (-5, -1, 3)
        for y in range(min(top, *edges.values()), top + 1):
            block = GLASS if skylight else PATINA if y >= 13 else WEATHERED
            part = "05 Roof/Skylight" if skylight else "05 Roof/Copper shell"
            if y == 10:
                plan.slab("05 Roof/Cornice", x, y, z, WEATHERED + "_slab", "top")
            elif y == top and not skylight and edges[higher] > top:
                plan.step(part, x, y, z, higher, block + "_stairs")
            else:
                plan.set(part, x, y, z, block)


def interior_furniture(plan):
    benches = "03 Ground floor/Specimen displays"
    for start in (-12, -3):
        for x in range(start, start + 4):
            for z in range(2, 5):
                plan.set(benches, x, 1, z, MOSS if z == 3 else WEATHERED)
            plant = ("minecraft:fern", "minecraft:flowering_azalea", "minecraft:potted_azalea_bush")[x % 3]
            if plant.startswith("minecraft:potted"):
                plan.set(benches, x, 1, 3, TIMBER, axis="x")
            plan.set(benches, x, 2, 3, plant)
    for x in (-11, -2):
        plan.set("03 Ground floor/Lighting", x, 4, 6, LANTERN, hanging="true", waterlogged="false")
        plan.set("03 Ground floor/Lighting", x, 5, 6, TIMBER, axis="x")
        plan.set("03 Ground floor/Lighting", x, 6, 6, BIRCH)
    for x in (-9, -3, 3):
        for z in range(-2, 1):
            plan.set(benches, x, 1, z, STONE)
        plan.set(benches, x, 2, -1, "minecraft:potted_azalea_bush")
        plan.set(benches, x, 2, 0, LANTERN, hanging="false", waterlogged="false")

    room = "03 Ground floor/Reading furniture"
    for x in range(12, 16):
        for y in (1, 2):
            plan.set(room, x, y, -4, SHELF)
    for x in range(13, 21):
        plan.set(room, x, 1, 1, "twigs:spruce_table", table_leg1="true", table_leg2="true",
                 table_leg3="true", table_leg4="true")
        if x in (13, 16, 20):
            plan.set(room, x, 1, 0, "handcrafted:spruce_chair", color="none", facing="south", waterlogged="false")
            plan.set("03 Ground floor/Lighting", x, 2, 1, LANTERN, hanging="false", waterlogged="false")
        elif x in (14, 17, 19):
            plan.set(room, x, 1, 2, "handcrafted:spruce_chair", color="none", facing="north", waterlogged="false")
    for x in (12, 21):
        plan.set(room, x, 2, 1, "minecraft:potted_azalea_bush")
        plan.set(room, x, 1, 1, TIMBER, axis="y")
    for z in (-2, -1, 0):
        for y in (1, 2):
            plan.set(room, 23, y, z, SHELF)
    for x in (10, 14, 18, 22):
        lo, hi = wing_limits(x)
        for z in range(lo + 1, hi):
            plan.set("03 Ground floor/Reading rafters", x, 5, z, TIMBER, axis="z")
        plan.set("03 Ground floor/Lighting", x, 4, 0, LANTERN, hanging="true", waterlogged="false")


def archive(plan):
    shell = "02 Basement/Archive shell"
    archive_area = {(x, z) for x in range(12, 23) for z in range(-5, 4)}
    for x, z in archive_area:
        plan.set(shell, x, -5, z, PAVING)
    for x, z in perimeter(archive_area):
        for y in range(-4, -1):
            plan.set(shell, x, y, z, STONE)
    for x, z in ARCHIVE_STAIR:
        height = x - 20
        for y in range(-4, height):
            plan.set("02 Basement/Archive stair", x, y, z, STONE)
        plan.step("02 Basement/Archive stair", x, height, z, "east", "minecraft:stone_brick_stairs")
    for x in list(range(13, 17)) + list(range(18, 22)):
        for y in (-4, -3):
            plan.set("02 Basement/Collections", x, y, 2, SHELF)
    for x in (14, 18, 21):
        plan.set("02 Basement/Lighting", x, -2, 1, LANTERN, hanging="true", waterlogged="false")


def build():
    plan = Plan()
    oval = ellipse(12, 9)
    wing = {(x, z) for x in range(4, 25) for z in range(wing_limits(x)[0], wing_limits(x)[1] + 1)}
    outside = perimeter(oval | wing)
    foundations(plan, oval, wing)
    specimen_shell(plan, oval, wing, outside)
    entry_portico(plan, oval)
    reading_wing(plan, oval, wing, outside)
    mezzanine(plan, oval, wing)
    canopy(plan)
    interior_furniture(plan)
    archive(plan)
    return plan.building, plan.parts
