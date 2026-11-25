"""Moonrift Observatory, from an archive beneath the ridge to an open meridian arch."""

from math import atan2, floor, hypot, tau

from src.core.building import Building


BEDROCK = "minecraft:deepslate_bricks"
BLACK = "minecraft:polished_blackstone_bricks"
SHALE = "quark:shale_bricks"
PAVED = "quark:polished_shale"
DUSK = "quark:duskbound_block"
QUARTZ = "minecraft:smooth_quartz"
PILLAR = "minecraft:quartz_pillar"
CARVED = "minecraft:chiseled_quartz_block"
COPPER = "minecraft:waxed_cut_copper"
VIOLET = "quark:purple_framed_glass"
CLEAR = "minecraft:glass"
AMETHYST = "chipped:amethyst_block_mini_tiles"
LIGHT = "minecraft:sea_lantern"
LANTERN = "minecraft:lantern"
WOOD = "minecraft:dark_oak_planks"
SHELF = "quark:dark_oak_bookshelf"

CARDINAL = ((-1, 0), (1, 0), (0, -1), (0, 1))
DIRECTIONS = dict(zip(("west", "east", "north", "south"), CARDINAL))
RING_X, RING_Z = 19, -5
DECK_Y = 17


class Observatory:
    def __init__(self):
        self.building = Building("月隙观星台 · Moonrift Observatory")
        self.parts = {}

    def set(self, part, x, y, z, block, **state):
        pos = (x, y, z)
        self.building.place(pos, block, **state)
        self.parts[pos] = part

    def stairs(self, part, x, y, z, block, facing, half="bottom"):
        self.set(part, x, y, z, block, facing=facing, half=half, shape="straight", waterlogged="false")

    def slab(self, part, x, y, z, block, kind="bottom"):
        self.set(part, x, y, z, block, type=kind, waterlogged="false")

    def lantern(self, part, x, y, z, hanging=False):
        self.set(part, x, y, z, LANTERN, hanging=str(hanging).lower(), waterlogged="false")


def oval(cx, cz, rx, rz):
    return {(x, z) for x in range(cx - rx, cx + rx + 1) for z in range(cz - rz, cz + rz + 1)
            if ((x - cx) / rx) ** 2 + ((z - cz) / rz) ** 2 <= 1.000000001}


def edge(shape):
    return {(x, z) for x, z in shape
            if any((x + dx, z + dz) not in shape for dx, dz in CARDINAL)}


def buffer(shape, thickness):
    return {(x + dx, z + dz) for x, z in shape
            for dx in range(-thickness, thickness + 1)
            for dz in range(-thickness, thickness + 1)
            if abs(dx) + abs(dz) <= thickness}


def guard_line(shape):
    """Join the diagonal steps in a footprint's edge through its inside corners."""
    outline = edge(shape)
    corners = {(x, z) for x, z in shape - outline for dx in (-1, 1) for dz in (-1, 1)
               if (x + dx, z) in outline and (x, z + dz) in outline and (x + dx, z + dz) not in shape}
    return outline | corners


def balusters(plan, part, positions, block, *, wall=False, join=()):
    neighbors = positions | set(join)
    for x, z in positions:
        state = {key: ("low" if wall else "true") if (x + dx, z + dz) in neighbors
                 else ("none" if wall else "false") for key, (dx, dz) in DIRECTIONS.items()}
        if wall:
            state["up"] = "true"
        plan.set(part, x, DECK_Y + 1, z, block, waterlogged="false", **state)


def plinth(plan, hall, shaft):
    foundations = "01 Plinth/Slate terraces"
    floor_part = "02 Archive/Compass pavement"
    pad = {(x, z) for x in range(-10, -1) for z in range(9, 16)}
    saddle = {(x, z) for x in range(5, 17) for z in range(-8, -3)}
    lower = buffer(hall | shaft | pad | saddle | oval(RING_X, RING_Z, 5, 5), 1)
    outer = edge(lower)
    for x, z in lower:
        plan.set(foundations, x, -2, z, BLACK)
        plan.set(foundations, x, -1, z, BEDROCK if (x, z) in outer else SHALE)
    for x, z in pad:
        if z == 15:
            plan.stairs("01 Plinth/Entry steps", x, 0, z, "quark:polished_shale_stairs", "north")
        else:
            plan.set("01 Plinth/Entry steps", x, 0, z, QUARTZ if x in (-8, -4) else PAVED)
    for x, z in hall | shaft:
        dx, dz = x + 7, z - 6
        radial = hypot(dx, dz)
        on_star = abs(dx) == abs(dz) and abs(dx) <= 3 or dx * dz == 0 and radial <= 4
        if (x, z) in shaft and (x, z) not in hall:
            tile = WOOD
        elif 3.75 < radial < 4.45 or on_star:
            tile = COPPER if (abs(dx), abs(dz)) in ((0, 4), (4, 0)) else QUARTZ
        else:
            tile = PAVED if radial <= 4.45 else SHALE
        plan.set(floor_part, x, 0, z, LIGHT if dx == dz == 0 else tile)

    for x in (-10, -2):
        z = 13
        plan.set("01 Plinth/Entry lamps", x, 0, z, BLACK)
        plan.set("01 Plinth/Entry lamps", x, 1, z, PILLAR, axis="y")
        plan.lantern("01 Plinth/Entry lamps", x, 2, z)


def archive(plan, hall, shaft, outer):
    shell = "02 Archive/Curved masonry"
    leadwork = "02 Archive/Leaded windows"
    for x, z in edge(hall) & outer:
        if (x, z) in shaft:
            continue
        segment = atan2((z - 4) / 8, (x + 6) / 10) * 16 / tau
        pier = abs(segment - round(segment)) < 0.29
        at_door = -8 <= x <= -4 and z >= 10
        for y in range(1, 6):
            if at_door and y < 5:
                continue
            if pier or y in (1, 5) or at_door:
                if 2 <= y <= 4 and pier:
                    plan.set(shell, x, y, z, PILLAR, axis="y")
                else:
                    plan.set(shell, x, y, z, DUSK if y == 5 else SHALE)
            else:
                plan.set(leadwork, x, y, z, VIOLET if y == 4 else CLEAR)

    portico = "02 Archive/Entry canopy"
    for x in range(-10, -1):
        plan.set(portico, x, 5, 12, QUARTZ)
        plan.slab(portico, x, 5, 13, "minecraft:smooth_quartz_slab", "top")
        plan.slab(portico, x, 6, 12, "quark:duskbound_block_slab")
    for x in (-10, -2):
        for y in range(1, 5):
            plan.set(portico, x, y, 12, PILLAR, axis="y")
    for x in (-8, -4):
        plan.lantern(portico, x, 4, 12, hanging=True)

    # The roof is a shallow skewed vault. Its high glass slot runs into the stair tower.
    roof = buffer(hall, 1) - shaft
    heights = {(x, z): 6 + floor(3.3 * (1 - abs(z - 3) / 11) + (x + 8) / 12) for x, z in roof}
    for (x, z), top in heights.items():
        adjacent = [heights.get((x + dx, z + dz), 6) for dx, dz in CARDINAL]
        for y in range(min(top, *adjacent), top + 1):
            if z in (2, 3) and -12 <= x <= -4 and x not in (-9, -5):
                plan.set("03 Archive roof/Clerestory", x, y, z, CLEAR)
            else:
                skin = AMETHYST if y == top and z in (1, 4) and -14 <= x <= -3 else DUSK
                plan.set("03 Archive roof/Vault profile", x, y, z, QUARTZ if y == 6 else skin)

    for x, z in ({(x, -1) for x in range(-12, -4)} | {(-14, z) for z in range(2, 7)}):
        for y in (1, 2):
            plan.set("02 Archive/Atlas shelves", x, y, z, SHELF)
        plan.slab("02 Archive/Atlas shelves", x, 3, z, "minecraft:dark_oak_slab")
    for x in (-12, -11, -10):
        plan.set("02 Archive/Charting desks", x, 1, 3, "handcrafted:dark_oak_desk", facing="south",
                 color="none", waterlogged="false")
    for x in (-12, -10):
        plan.set("02 Archive/Charting desks", x, 1, 4, "handcrafted:dark_oak_chair", facing="north",
                 color="gray", waterlogged="false")
    plan.set("02 Archive/Reference lectern", -9, 1, 5, "minecraft:lectern", facing="east")
    for x, z in ((-13, 3), (-4, 8), (-9, 1)):
        plan.set("02 Archive/Lamp stands", x, 1, z, CARVED)
        plan.lantern("02 Archive/Lamp stands", x, 2, z)


def stair_tower(plan, hall, shaft, outer):
    shell = "04 Stair tower/Piers and spandrels"
    glazing = "04 Stair tower/Stair glazing"
    for x, z in edge(shaft):
        pier = (x in (-4, 7) and z in (-5, -1, 2)) or (z in (-7, 4) and x in (-2, 1, 5))
        for y in range(1, DECK_Y + 1):
            if y <= 5 and (x, z) in hall and (x, z) not in outer:
                continue
            if y in (1, 6, 11, 17):
                plan.set(shell, x, y, z, DUSK if y in (6, 11) else SHALE)
            elif pier:
                if y in (7, 12, 16):
                    plan.set(shell, x, y, z, CARVED)
                else:
                    plan.set(shell, x, y, z, PILLAR, axis="y")
            elif (x in (-4, 7) and -5 < z < 2 or z in (-7, 4) and -2 < x < 5) and y <= 15:
                plan.set(glazing, x, y, z, VIOLET if y in (5, 10, 15) else CLEAR)
            else:
                plan.set(shell, x, y, z, SHALE)

    first = [(x, x + 3, z) for x in range(-2, 4) for z in (1, 2)]
    second = [(x, 7 - z, z) for z in range(0, -5, -1) for x in (4, 5)]
    third = [(x, 15 - x, z) for x in range(3, -3, -1) for z in (-5, -4)]
    stair_part = "04 Stair tower/Three flights"
    for steps, facing in ((first, "east"), (second, "north"), (third, "west")):
        for x, y, z in steps:
            if y > 1:
                plan.stairs(stair_part, x, y - 1, z, "quark:duskbound_block_stairs", facing, "top")
            plan.stairs(stair_part, x, y, z, "quark:duskbound_block_stairs", facing)

    for x in (4, 5):
        for z in (1, 2):
            plan.set("04 Stair tower/Intermediate landings", x, 6, z, WOOD)
        for z in (-6, -5):
            plan.set("04 Stair tower/Intermediate landings", x, 11, z, WOOD)
    balcony = {(x, -6) for x in range(-2, 4)}
    balcony |= {(x, z) for x in (-3, -2, -1) for z in (-5, -4, -3, -2)}
    for x, z in balcony:
        plan.set("04 Stair tower/Survey balcony", x, 11, z, WOOD)
    plan.set("04 Stair tower/Survey balcony", -2, 12, -3, "minecraft:lectern", facing="east")
    plan.lantern("04 Stair tower/Survey balcony", -1, 12, -2)
    for x, y, z in ((6, 8, 1), (6, 13, -5), (-3, 16, -6)):
        plan.set("04 Stair tower/Lamp corbels", x, y - 1, z, CARVED)
        plan.lantern("04 Stair tower/Lamp corbels", x, y, z)

    aperture = {(x, z) for x, y, z in third if y >= 15}
    for x, z in shaft - aperture:
        plan.set("05 Arrival terrace/Deck", x, DECK_Y, z, QUARTZ if (x, z) in edge(shaft) else PAVED)
    for x, z in edge(shaft):
        if x == 7 and -3 <= z <= -1:
            continue
        plan.slab("05 Arrival terrace/Parapet", x, DECK_Y + 1, z, "minecraft:smooth_quartz_slab")

    shelter = "05 Arrival terrace/Folded stair canopy"
    canopy = {(x, z): 23 - (x + 4) // 2 + max(0, 2 - abs(z + 3) // 2)
              for x in range(-4, 3) for z in range(-5 + (x == 2), 2 - (x + 4) // 3)}
    for (x, z), top in canopy.items():
        adjacent = [canopy.get((x + dx, z + dz), top) for dx, dz in CARDINAL]
        for y in range(min(top, *adjacent), top + 1):
            if y == top and x == -4:
                plan.slab(shelter, x, y, z, "minecraft:smooth_quartz_slab", "top")
            else:
                plan.set(shelter, x, y, z, DUSK if y == top else QUARTZ)
    for x, z in ((-4, -5), (-4, 1), (2, -4)):
        for y in range(DECK_Y + 1, canopy[x, z]):
            plan.set(shelter, x, y, z, CARVED if y == DECK_Y + 1 else PILLAR, **({} if y == DECK_Y + 1 else {"axis": "y"}))
    for z in (-4, -3, -2):
        plan.slab(shelter, -4, canopy[-4, z] + 1, z, "minecraft:waxed_cut_copper_slab")


def court(plan):
    pier = "06 Sky court/Footed pier"
    ribs = "06 Sky court/Corbel ribs"
    deck = oval(RING_X, RING_Z, 7, 7)
    balustrade = edge(deck)
    for y in range(17):
        for x in range(18, 21):
            for z in range(-6, -3):
                plan.set(pier, x, y, z, SHALE if y >= 4 else BEDROCK)
        if y in (3, 8, 13):
            for x, z in ((17, -5), (21, -5), (19, -7), (19, -3)):
                plan.set(pier, x, y, z, DUSK)
    for ex, ez in ((15, -9), (24, -5), (19, 0), (15, -1)):
        previous_x, previous_z = 19, -5
        for y in range(7, 17):
            t = (y - 7) / 9
            x, z = round(19 + (ex - 19) * t), round(-5 + (ez + 5) * t)
            if (x, z) != (previous_x, previous_z):
                plan.set(ribs, previous_x, y, previous_z, SHALE)
                plan.set(ribs, x, y, previous_z, SHALE)
            for dx, dz in ((0, 0), (0, 1), (1, 0)):
                block = DUSK if (dx, dz) == (0, 0) else PAVED if dz and y >= 10 else SHALE
                plan.set(ribs, x + dx, y, z + dz, block)
            previous_x, previous_z = x, z
        plan.set(ribs, ex, 16, ez, QUARTZ)

    bridge_path = {(x, z) for x in range(7, 15) for z in (-3, -2, -1)}
    lamps = {(x, z) for x in (8, 13) for z in (-3, -1)}
    bridge_rails = {(x, z) for x in range(7, 15) for z in (-3, -1)} - lamps
    for x, z in bridge_path:
        plan.set("06 Sky court/Suspended passage", x, DECK_Y, z, QUARTZ if z != -2 else DUSK)
    for x, z in lamps:
        plan.set("06 Sky court/Bridge rail", x, DECK_Y + 1, z, CARVED)
        plan.lantern("06 Sky court/Bridge rail", x, DECK_Y + 2, z)
    balusters(plan, "06 Sky court/Bridge rail", bridge_rails, "minecraft:iron_bars", join=lamps)
    for x in range(7, 15):
        for z in (-3, -1):
            if x <= 10 or x >= 13:
                for y in range(16 - abs(x - 10), 17):
                    plan.set("06 Sky court/Bridge corbels", x, y, z, DUSK if y == 16 else SHALE)

    for x, z in deck:
        dx, dz = x - RING_X, z - RING_Z
        compass = (dx * dz == 0 and abs(dx) + abs(dz) <= 5
                   or abs(dx) == abs(dz) <= 2)
        tile = QUARTZ if compass or (x, z) in balustrade else PAVED
        if (abs(dx), abs(dz)) in ((0, 5), (5, 0)):
            tile = COPPER
        plan.set("06 Sky court/Open observation deck", x, DECK_Y, z,
                 LIGHT if dx == dz == 0 else tile)
    parapet = guard_line(deck) - bridge_path
    balusters(plan, "06 Sky court/Parapet", parapet, "twigs:calcite_wall", wall=True)
    for x, z in ((20, 0), (17, -10)):
        plan.set("06 Sky court/Sight pedestals", x, DECK_Y + 1, z, CARVED)
        plan.slab("06 Sky court/Sight pedestals", x, DECK_Y + 2, z, "minecraft:smooth_quartz_slab")
    for x, z in ((15, -8), (23, -2)):
        for dx in (-1, 0, 1):
            if (x + dx, z) not in parapet:
                plan.slab("06 Sky court/Observer benches", x + dx, DECK_Y + 1, z, "quark:duskbound_block_slab")


def meridian_arch(plan):
    rim = "07 Meridian arch/Stone voussoirs"
    inlay = "07 Meridian arch/Graduated inlay"
    back = "07 Meridian arch/Ribbed backing"
    def world_z(u):
        return RING_Z + (u + 1) // 4

    cells = {(u, v) for u in range(-8, 9) for v in range(-6, 10)
             if (u / 8) ** 2 + (v / 9) ** 2 <= 1.0
             and (u / 6.15) ** 2 + (v / 7.1) ** 2 >= 1.0}
    for u, v in cells:
        x, y, z = RING_X + u, 25 + v, world_z(u)
        is_mark = (abs(u), v) in {(7, -2), (7, 2), (6, 4), (5, 6), (3, 8), (0, 9)}
        plan.set(back, x, y, z - 1, DUSK)
        plan.set(back, x, y, z, QUARTZ)
        plan.set(inlay if is_mark else rim, x, y, z + 1, COPPER if is_mark else QUARTZ)
        if (u, v + 1) not in cells and v > 0:
            plan.slab(rim, x, y + 1, z, "minecraft:smooth_quartz_slab")
    for u in (-6, -5, -4, 4, 5, 6):
        x, z = RING_X + u, world_z(u)
        for layer in (-1, 0, 1):
            plan.set(rim, x, DECK_Y + 1, z + layer, CARVED if layer == 1 else DUSK)
    for u, y in ((-7, 25), (7, 25), (-4, 32), (4, 32)):
        plan.set(inlay, RING_X + u, y, world_z(u) + 1, LIGHT)
    plan.set(inlay, RING_X, 34, RING_Z + 1, LIGHT)
    plan.set(inlay, RING_X, 35, RING_Z, COPPER)
    plan.slab(inlay, RING_X, 36, RING_Z, "minecraft:smooth_quartz_slab")


def build():
    plan = Observatory()
    hall = oval(-6, 4, 10, 8)
    shaft = {(x, z) for x in range(-4, 8) for z in range(-7, 5)
             if min(x + 4, 7 - x) + min(z + 7, 4 - z) >= 2}
    outer = edge(hall | shaft)
    plinth(plan, hall, shaft)
    archive(plan, hall, shaft, outer)
    stair_tower(plan, hall, shaft, outer)
    court(plan)
    meridian_arch(plan)
    return plan.building, plan.parts
