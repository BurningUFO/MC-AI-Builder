"""Moonrift Observatory, with a tower-mounted armillary and an open sighting court."""

from math import atan2, hypot, sqrt, tau

from src.core.building import Building
from src.core.roof import roof_courses


BEDROCK = "minecraft:deepslate_bricks"
BLACK = "minecraft:polished_blackstone_bricks"
SHALE = "quark:shale_bricks"
PAVED = "quark:polished_shale"
DUSK = "quark:duskbound_block"
QUARTZ = "minecraft:smooth_quartz"
PILLAR = "minecraft:quartz_pillar"
CARVED = "minecraft:chiseled_quartz_block"
COPPER = "minecraft:waxed_cut_copper"
AGED = "minecraft:waxed_exposed_cut_copper"
PATINA = "minecraft:waxed_oxidized_cut_copper"
GLAZING = "quark:cyan_framed_glass"
VIOLET = "quark:purple_framed_glass"
CLEAR = "minecraft:glass"
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
        self.building = Building("月隙观星台 · Moonrift Observatory / Architecture V1")
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
                    plan.set(shell, x, y, z, BEDROCK if y < 4 else CARVED)
                else:
                    plan.set(shell, x, y, z, DUSK if y == 5 else SHALE)
            else:
                plan.set(leadwork, x, y, z, GLAZING if y == 4 else CLEAR)

    portico = "02 Archive/Entry canopy"
    for x in range(-10, -1):
        plan.set(portico, x, 5, 12, SHALE)
        plan.slab(portico, x, 5, 13, AGED + "_slab", "top")
        plan.slab(portico, x, 6, 12, PATINA + "_slab")
    for x in (-10, -2):
        for y in range(1, 5):
            plan.set(portico, x, y, 12, SHALE if y < 4 else CARVED)
        plan.stairs(portico, x + (1 if x < -6 else -1), 4, 12,
                    "minecraft:smooth_quartz_stairs", "east" if x < -6 else "west", "top")
    for x in (-8, -4):
        plan.lantern(portico, x, 4, 12, hanging=True)

    roof = buffer(hall, 1) - shaft
    outline = edge(roof)
    heights = {}
    for x, z in roof:
        radius = ((x + 6) / 12) ** 2 + ((z - 4) / 9.5) ** 2
        heights[x, z] = 12 + round(6 * sqrt(max(0, 1 - radius)))
    for (x, y, z), form, state in roof_courses(heights):
        if (x, z) in outline:
            for fill in range(6, y):
                plan.set("03 Archive roof/Cornice and vault closure", x, fill, z, SHALE)
        if 3 <= z <= 4 and -13 <= x <= -5 and x not in (-10, -7):
            plan.set("03 Archive roof/Clerestory", x, y, z, GLAZING)
        else:
            skin = AGED if x in (-13, -10, -7, -4) or (x, z) in outline else PATINA
            part = "03 Archive roof/Vault ribs" if skin == AGED else "03 Archive roof/Patinated vault"
            plan.set(part, x, y, z, skin if form in ("block", "backing") else skin + "_" + form, **state)

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
    outline = edge(shaft)
    for x, z in outline:
        face = abs(z + 2) if x in (-4, 7) else abs(x - 1)
        pier = face >= 3
        for y in range(1, DECK_Y + 1):
            if y <= 5 and (x, z) in hall and (x, z) not in outer:
                continue
            under_point = y in (4, 9, 14)
            point = y in (5, 10, 15)
            if y in (1, 6, 11, 17):
                plan.set(shell, x, y, z, BEDROCK if y in (1, 17) else DUSK)
            elif face <= (0 if point else 1 if under_point else 2) and y < 16:
                plan.set(glazing, x, y, z, VIOLET if point else GLAZING if abs(face) == 2 else CLEAR)
            elif pier and y in (7, 12, 16):
                plan.set(shell, x, y, z, CARVED if face == 3 else PAVED)
            else:
                plan.set(shell, x, y, z, SHALE if face <= 3 else BEDROCK)

    for x, z in ((-4, -5), (-4, 2), (7, -5), (7, 2), (-2, -7), (5, -7), (-2, 4), (5, 4)):
        dx, dz = ((1 if x > 0 else -1), 0) if x in (-4, 7) else (0, (1 if z > 0 else -1))
        if (x + dx, z + dz) in hall:
            continue
        for y in range(5, 17):
            material = AGED if y in (6, 11, 16) else PAVED if y >= 8 else BEDROCK
            plan.set("04 Stair tower/External buttresses", x + dx, y, z + dz, material)
        for y in range(-1, 5):
            plan.set("04 Stair tower/External buttresses", x + dx, y, z + dz, BEDROCK)

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
        plan.set("05 Arrival terrace/Deck", x, DECK_Y, z, AGED if abs(z + 2) == 0 else PAVED)
    crown_guard = guard_line(shaft) - {(7, z) for z in range(-4, 0)} - aperture
    balusters(plan, "05 Arrival terrace/Parapet", crown_guard, "twigs:calcite_wall", wall=True)
    inside_stair = {(x, -3) for x in (-2, -1, 0)}
    balusters(plan, "05 Arrival terrace/Stair guard", inside_stair, "minecraft:iron_bars")


def court(plan):
    pier = "06 Sky court/Footed pier"
    ribs = "06 Sky court/Corbel ribs"
    deck = oval(RING_X, RING_Z, 7, 7)
    for y in range(17):
        core = oval(RING_X, RING_Z, 3 if y <= 2 else 2, 3 if y <= 2 else 2)
        for x, z in core:
            block = BEDROCK if y <= 2 else AGED if y == 5 and (x, z) in edge(core) else SHALE
            plan.set(pier, x, y, z, block)
    for ex, ez in ((14, -7), (17, -10), (23, -8), (23, -2), (17, 0)):
        previous_x, previous_z = 19, -5
        for y in range(7, 17):
            t = (y - 7) / 9
            x, z = round(19 + (ex - 19) * t), round(-5 + (ez + 5) * t)
            if (x, z) != (previous_x, previous_z):
                plan.set(ribs, previous_x, y, previous_z, SHALE)
                plan.set(ribs, x, y, previous_z, SHALE)
            for dx, dz in ((0, 0), (0, 1)):
                block = AGED if y == 15 else PAVED if dz else SHALE
                plan.set(ribs, x + dx, y, z + dz, block)
            previous_x, previous_z = x, z
        plan.set(ribs, ex, 16, ez, CARVED)

    bridge_path = {(x, z) for x in range(7, 15) for z in range(-4, 0)}
    lamps = {(x, z) for x in (8, 13) for z in (-4, -1)}
    bridge_rails = {(x, z) for x in range(7, 15) for z in (-4, -1)} - lamps
    for x, z in bridge_path:
        tile = PAVED if z in (-3, -2) else AGED
        plan.set("06 Sky court/Suspended passage", x, DECK_Y, z, tile)
    for x, z in lamps:
        plan.set("06 Sky court/Bridge rail", x, DECK_Y + 1, z, CARVED)
        plan.lantern("06 Sky court/Bridge rail", x, DECK_Y + 2, z)
    balusters(plan, "06 Sky court/Bridge rail", bridge_rails, "minecraft:iron_bars", join=lamps)
    for x in range(7, 15):
        for z in (-4, -1):
            plan.slab("06 Sky court/Bridge girders", x, 16, z, AGED + "_slab", "top")
            if x <= 9 or x >= 13:
                for y in range(15 - min(abs(x - 10), abs(x - 12)), 16):
                    plan.set("06 Sky court/Bridge corbels", x, y, z, SHALE)

    bowl = "06 Sky court/Recessed sighting dial"
    rim = edge(deck)
    for x, z in deck:
        dx, dz = x - RING_X, z - RING_Z
        radius = hypot(dx, dz)
        if radius < 2.75:
            tile = LIGHT if not radius else QUARTZ if dx * dz == 0 else DUSK
            plan.set(bowl, x, DECK_Y - 1, z, tile)
        else:
            tick = 2.75 <= radius < 4.2 and (dx * dz == 0 or abs(dx) == abs(dz))
            tile = AGED if tick else SHALE if (x, z) in rim else PAVED
            plan.set("06 Sky court/Open observation deck", x, DECK_Y, z, tile)
    for x in (16, 22):
        for z in (-6, -5, -4):
            plan.stairs(bowl, x, DECK_Y, z, "quark:polished_shale_stairs", "west" if x < 19 else "east")
    parapet = guard_line(deck) - bridge_path
    balusters(plan, "06 Sky court/Parapet", parapet, "minecraft:iron_bars")
    for x, z, facing in ((19, -10, "north"), (24, -5, "east"), (19, 0, "south")):
        dx, dz = DIRECTIONS[facing]
        plan.set("06 Sky court/Sight vanes", x, 18, z, CARVED)
        plan.set("06 Sky court/Sight vanes", x, 19, z, AGED)
        plan.stairs("06 Sky court/Sight vanes", x - dx, 18, z - dz, AGED + "_stairs", facing)
        plan.slab("06 Sky court/Sight vanes", x, 20, z, "minecraft:smooth_quartz_slab")
    for x, z in ((15, -8), (23, -2)):
        for bx in (x, x + 1):
            if (bx, z) in deck - parapet:
                plan.slab("06 Sky court/Observer benches", bx, 18, z, "quark:duskbound_block_slab")


def armillary(plan):
    centre_x, centre_y, centre_z = 2, 27, -2
    support = "07 Armillary/Cantilevered stone bearings"
    for sign in (-1, 1):
        x = centre_x + sign * 4
        z = centre_z - sign
        for y in range(18, 22):
            reach = x + sign * (y == 21)
            for dz in (0, 1):
                material = CARVED if y in (18, 21) else SHALE
                plan.set(support, reach, y, z + dz, material)
            if y == 20:
                plan.set(support, x + sign, y, z, AGED)
                plan.stairs(support, x + sign, y - 1, z, "quark:polished_shale_stairs",
                            "west" if sign > 0 else "east", "top")

    trim = "07 Armillary/Oblique equatorial scale"
    for u in range(-8, 9):
        for w in range(-8, 9):
            if 6.4 <= hypot(u, w) <= 7.8:
                y = round(centre_y - 0.40 * u + 0.36 * w)
                x, z = centre_x + u, centre_z + w
                block = PATINA if abs(u) % 3 == 0 or not w % 3 else AGED
                plan.set(trim, x, y, z, block)
    for u in range(-8, 9):
        for v in range(-8, 9):
            if 6.4 <= hypot(u, v) <= 8.35:
                x, y = centre_x + u, centre_y + v
                z = centre_z - round(u / 3)
                key = abs(u), abs(v)
                mark = key in {(0, 7), (7, 0), (5, 5)}
                rim = "07 Armillary/Meridian ring"
                plan.set(rim, x, y, z, AGED if hypot(u, v) > 7.6 else PATINA)
                plan.set(rim, x, y, z + 1, QUARTZ if mark else COPPER)
                if key == (0, 8):
                    plan.set("07 Armillary/Pole lamps", x, y, z + 1, LIGHT)


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
    armillary(plan)
    return plan.building, plan.parts
