"""Irisbank Station. Local axes are east X, up Y, downhill south Z."""

from math import cos, pi, sin, sqrt

from src.core.building import Building
from src.core.curves import cubic_cells
from src.core.roof import roof_courses


STONE = "minecraft:smooth_sandstone"
ASHLAR = "quark:polished_limestone"
BRICK = "quark:limestone_bricks"
PAVING = "quark:polished_tuff"
BASE = "twigs:polished_tuff_bricks"
DARK = "minecraft:polished_deepslate"
TEAL = "minecraft:dark_prismarine"
COPPER = "minecraft:waxed_oxidized_cut_copper"
BRONZE = "minecraft:waxed_exposed_cut_copper"
GLASS = "minecraft:glass"
MILK = "minecraft:white_stained_glass"
SMOKE = "minecraft:light_gray_stained_glass"
HONEY = "minecraft:orange_stained_glass"
BLUE = "minecraft:light_blue_stained_glass"
WOOD = "minecraft:spruce_planks"
OAK = "minecraft:stripped_dark_oak_wood"
SHINGLE = "quark:cyan_shingles"
CONCOURSE = "02 Concourse"
LOUNGE = "03 Upper waiting room"
PLATFORM = "04 Railway shed"


def arch(x, center, radius, spring, rise):
    u = (x - center) / radius
    return spring + round(rise * sqrt(1 - u * u)) if abs(u) <= 1 else spring - 1


def hall_course(x, z):
    u = (x + 2) / (18 if x < -2 else 17)
    crest = max(0, cos(pi * u / 2)) ** 1.45
    return round(2 * (10.1 + crest * (11.2 - 0.16 * (8 - z))))


def shed_course(z):
    return round(2 * (11.3 + 7.2 * sqrt(max(0, 1 - ((z + 16.5) / 10.7) ** 2))))


def build():
    station = Building("鸢坡站 / Irisbank Station")
    groups = {}
    bars = {}

    def p(part, x, y, z, block, **state):
        pos = x, y, z
        station.place(pos, block, **state)
        groups[pos] = part

    def box(part, x0, x1, y0, y1, z0, z1, block, **state):
        for x in range(x0, x1 + 1):
            for z in range(z0, z1 + 1):
                for y in range(y0, y1 + 1):
                    p(part, x, y, z, block, **state)

    def stair(part, x, y, z, material, facing, half="bottom"):
        p(part, x, y, z, material + "_stairs", facing=facing, half=half,
          shape="straight", waterlogged="false")

    def slab(part, x, y, z, material, kind="bottom"):
        p(part, x, y, z, material + "_slab", type=kind, waterlogged="false")

    def bar(part, x, y, z):
        bars[x, y, z] = part

    def lamp(part, x, y, z, hanging=False):
        p(part, x, y, z, "minecraft:lantern", hanging=str(hanging).lower(), waterlogged="false")

    def skin(part, heights, material, *, single_glazing=False):
        for (x, y, z), form, state in roof_courses(heights, connect_steps=True):
            finish = material(x, z)
            if finish in (GLASS, MILK, SMOKE, BLUE, HONEY):
                if form != "backing" or not single_glazing:
                    p(part, x, y, z, finish)
            else:
                suffix = {"backing": "", "block": "", "slab": "_slab", "stairs": "_stairs"}[form]
                p(part, x, y, z, finish + suffix, **state)

    # The cutting climbs from the city street to a single railway shelf.
    terrain = "01 Site/Rock cutting"
    clay = "minecraft:coarse_dirt"
    rock = "minecraft:andesite"
    grass = "minecraft:grass_block"
    for x in range(-35, 36):
        for z in range(-33, 23):
            if (x / 36) ** 4 + ((z + 5) / 28) ** 4 > 1.19:
                continue
            ridge = sin(0.19 * x) + 0.7 * cos(0.43 * x + 0.24 * z)
            if z <= -28:
                top = round(7 + (-z - 27) * 0.62 + ridge)
            elif z <= -7:
                top = 5
            elif abs(x) >= 27:
                top = min(5, round((6 - z) * 0.28 + ridge))
            else:
                top = -1 if z <= 17 else -3
            face = abs(x) >= 32 and z < -6 or (z <= -28 and (x + 2 * z) % 11 < 3)
            for y in range(-4, top + 1):
                surface = y == top and (z <= -28 or z > -6)
                if surface and not face:
                    p(terrain, x, y, z, grass, snowy="false")
                else:
                    p(terrain, x, y, z, rock if face or y < top - 1 else clay)

    court = "01 Site/Street terraces"
    for z in range(9, 18):
        for x in range(-28, 29):
            if abs(x) > 25 and z > 17 - (abs(x) - 25):
                continue
            material = STONE if abs(x + 2) in (5, 6) else ASHLAR if abs(x + 2) < 5 else PAVING
            p(court, x, 0, z, material)
    box(court, -25, 25, -3, -3, 20, 22, PAVING)
    for z in range(18, 21):
        y = 18 - z
        for x in range(-9, 6):
            box(court, x, x, -3, y - 1, z, z, BASE)
            stair(court, x, y, z, "quark:polished_tuff", "north")
    for x in (*range(-24, -9), *range(6, 24)):
        box(court, x, x, -2, 0, 17, 17, BASE)
        slab(court, x, 1, 17, "quark:polished_limestone")
        if (x + 2) % 6 == 0:
            p(court, x, 1, 16, BRICK)
            lamp(court, x, 2, 16)
    for x0, x1, z0, z1 in [(-25, -17, 12, 14), (12, 22, 13, 15), (-31, -28, 1, 8), (22, 25, -2, 3)]:
        for x in range(x0, x1 + 1):
            for z in range(z0, z1 + 1):
                rim = x in (x0, x1) or z in (z0, z1)
                if rim:
                    p(court, x, 1, z, BRICK)
                else:
                    p(court, x, 1, z, grass, snowy="false")
                if not rim and (2 * x + z) % 4 != 0:
                    p(court, x, 2, z, "minecraft:flowering_azalea_leaves" if x % 3 == 0
                      else "minecraft:azalea_leaves", persistent="true", distance="7", waterlogged="false")

    planting = "01 Site/Planting"
    for x, z, height in [(-31, 7, 5), (30, 11, 6), (-27, -30, 5), (24, -31, 5), (3, -32, 4)]:
        # Start on the actual grade if this tree is on the rock cutting.
        y = max(yy + 1 for xx, yy, zz in station.blocks if (xx, zz) == (x, z))
        box(planting, x, x, y, y + height - 1, z, z, "minecraft:oak_log", axis="y")
        for dx in range(-3, 4):
            for dz in range(-3, 4):
                for dy in range(-2, 3):
                    if dx * dx + dz * dz + 2 * dy * dy <= 10 + (dx - dz) % 3:
                        pos = x + dx, y + height + dy, z + dz
                        if pos not in station.blocks:
                            p(planting, *pos, "minecraft:oak_leaves", persistent="true",
                              waterlogged="false", distance="7")

    arcade = "01 Site/Railway retaining wall"
    for x in range(-34, 35):
        for y in range(0, 6):
            p(arcade, x, y, -7, BRICK if y == 5 or x % 8 in (0, 1) else BASE)
        if abs(x + 2) > 16:
            slab(arcade, x, 6, -6, "quark:polished_limestone")

    # The principal flight fits inside the double-height lobby.
    floor = CONCOURSE + "/Mosaic and steps"
    for x in range(-24, 26):
        for z in range(-6, 9):
            if (x < -17 or x > 14) and z > 6:
                continue
            dx = x + 2
            border = (dx / 9) ** 2 + ((z - 5) / 3.2) ** 2
            leaf = abs(abs(dx) - (6 - 0.85 * (z - 5) ** 2)) < 0.55 and 3 <= z <= 7
            material = TEAL if leaf else STONE if 0.79 <= border <= 1.16 else ASHLAR
            p(floor, x, 0, z, material)
    steps = [(1, 1, 7), (0, 2, 6), (-1, 3, 5), (-2, 3, 5), (-3, 3, 5),
             (-4, 4, 4), (-5, 5, 4), (-6, 6, 4)]
    for z, y, radius in steps:
        left, right = -2 - radius, -2 + radius
        for x in range(left, right + 1):
            box(floor, x, x, 1, y - 1, z, z, BASE)
            if -3 <= z <= -2:
                p(floor, x, y, z, PAVING if x in (left, right) or z == -2 else ASHLAR)
            else:
                finish = ASHLAR if z <= -4 or x in (left, right) else STONE
                stair(floor, x, y, z, finish, "north")
        for x in (left - 1, right + 1):
            box(floor, x, x, 1, y, z, z, BRICK)
            if z in (1, -3, -6):
                p(floor, x, y + 1, z, TEAL)
                lamp(floor, x, y + 2, z)
            else:
                bar(floor, x, y + 1, z)

    shell = CONCOURSE + "/Street fanlight and piers"
    stems = set(cubic_cells((-2, 5), (-2, 9), (-2, 12), (-2, 16)))
    stems.update(cubic_cells((-2, 8), (-7, 8), (-9, 11), (-9, 14)))
    stems.update(cubic_cells((-2, 9), (3, 9), (5, 12), (4, 14)))
    blossoms = {(-10, 13), (-9, 15), (-8, 14), (-3, 15), (-1, 15), (3, 14), (4, 15), (5, 13)}
    for x in range(-17, 15):
        cap = (hall_course(x, 8) - 1) // 2
        outside = arch(x, -2, 12, 9, 9)
        inside = arch(x, -2, 10, 8, 8)
        for y in range(1, cap + 1):
            if -7 <= x <= 3 and y <= 4:
                continue
            if abs(x + 2) <= 10 and 5 <= y <= inside:
                dx = x + 2
                if (x, y) in stems:
                    finish = TEAL
                elif (x, y) in blossoms:
                    finish = HONEY
                elif abs(dx) >= 8 and 6 <= y <= 10:
                    finish = BLUE
                else:
                    finish = MILK if abs(dx) <= 7 else SMOKE
                p(shell, x, y, 8, finish)
            elif y <= outside and abs(x + 2) <= 12:
                p(shell, x, y, 8, BRICK)
                if y >= 5:
                    p(shell, x, y, 9, STONE if y < outside else COPPER)
            elif abs(x + 2) in (14, 15) and 3 <= y <= 8:
                p(shell, x, y, 8, "quark:framed_glass")
            else:
                p(shell, x, y, 8, ASHLAR if y < 3 else STONE)
        if abs(x + 2) in (6, 7, 8) and cap >= 10:
            for y in range(1, 5):
                if abs(x + 2) == 8 or y >= 4:
                    p(shell, x, y, 9, BRICK)
    for x in (-14, 10):
        box(shell, x, x, 1, 7, 9, 9, "quark:limestone_pillar", axis="y")
        stair(shell, x, 8, 9, "minecraft:smooth_sandstone", "north", "top")
        p(shell, x, 6, 10, COPPER)
        lamp(shell, x, 5, 10, True)

    side = CONCOURSE + "/Side walls and gallery"
    for x in (-17, 14):
        for z in range(-7, 8):
            cap = (hall_course(x, z) - 1) // 2
            for y in range(1, cap + 1):
                if y <= 4 and 0 <= z <= 3:
                    continue
                if x == 14 and 7 <= y <= 10 and -6 <= z <= -2:
                    continue
                finish = ("quark:framed_glass" if z % 4 != 0 and 5 <= y < cap - 1
                          else BRICK if y in (1, 4) else STONE)
                p(side, x, y, z, finish)
    for x in range(-17, 15):
        top = (hall_course(x, -7) - 1) // 2
        opening = arch(x, -2, 11, 9, 7) - 1
        for y in range(7, top + 1):
            if abs(x + 2) <= 10 and y <= opening:
                continue
            trim = abs(x + 2) <= 11 and opening < y <= opening + 2
            p(side, x, y, -7, ASHLAR if trim else STONE)
        if abs(x + 2) in (8, 9) and 11 <= opening < top - 1:
            stair(side, x, opening, -7, "minecraft:smooth_sandstone",
                  "east" if x < -2 else "west", "top")
    for z in range(-6, 1):
        for x in range(6, 15):
            p(side, x, 6, z, WOOD if x != 6 else BRICK)
        bar(side, 6, 7, z)
    for x in range(7, 14):
        bar(side, x, 7, 0)

    roof = CONCOURSE + "/Copper-edged crown"
    heights = {}
    for x in range(-18, 16):
        lip = 9 + int(2 * (1 - ((x + 2) / 16) ** 2))
        for z in range(-8, lip + 1):
            heights[x, z] = hall_course(x, z)
    skin(roof, heights, lambda x, z: COPPER if (x, z + 1) not in heights or z == -8 else
         GLASS if -7 <= x <= 3 and -4 <= z <= 5 and z not in (0, 4) else SHINGLE)

    canopy = CONCOURSE + "/Splayed street canopy"
    leaf = {}
    ribs = set(cubic_cells((-2, 9), (-2, 11), (-2, 12), (-2, 14)))
    for direction in (-1, 1):
        for controls in [((3, 9), (4, 10), (5, 11), (6, 12)),
                         ((6, 12), (6, 13), (5, 14), (4, 14)),
                         ((6, 12), (7, 12), (8, 12), (9, 13))]:
            ribs.update(cubic_cells(*[(-2 + direction * x, z) for x, z in controls]))
    for z, radius in [(9, 5), (10, 6), (11, 7), (12, 8), (13, 9), (14, 7)]:
        for x in range(-2 - radius, -1 + radius):
            u = abs(x + 2) / radius
            leaf[x, z] = round(2 * (5.5 + 0.36 * sin(pi * (z - 9) / 5) + 1.5 * u ** 2.2))
    skin(canopy, leaf, lambda x, z: COPPER if (x, z) in ribs else SMOKE, single_glazing=True)
    for direction in (-1, 1):
        x = -2 + direction * 6
        p(canopy, x, 1, 12, ASHLAR)
        for reach in (4, 8):
            anchor = -2 + direction * reach
            top = (leaf[anchor, 12] - 1) // 2
            for offset, y in cubic_cells((6, 2), (6, 4), (reach, 4), (reach, top)):
                if (y * 2 + 2) < leaf[-2 + direction * offset, 12]:
                    p(canopy, -2 + direction * offset, y, 12, TEAL)
        slab(canopy, x, 4, 13, COPPER, "top")
        slab(canopy, x, 4, 12, COPPER, "top")
        lamp(canopy, x, 3, 13, True)

    # Side-pavilions flank the lobby; the rounded east bay looks down the valley.
    west = CONCOURSE + "/West ticket annex"
    for x in range(-24, -17):
        for z in range(-5, 7):
            if x == -24 or z in (-5, 6):
                for y in range(1, 8):
                    finish = "quark:framed_glass" if 2 <= y <= 5 and (x + z) % 4 != 0 else STONE
                    p(west, x, y, z, finish)
    skin(west, {(x, z): 17 + max(0, 5 - abs(z) * 2)
                for x in range(-25, -16) for z in range(-6, 8)}, lambda x, z: SHINGLE)

    bay = {(x, z) for x in range(15, 27) for z in range(-6, 10)
           if z <= 4 and x <= 24 or ((x - 19.5) / 6.5) ** 2 + ((z - 2) / 7.5) ** 2 <= 1}
    for x, z in sorted(bay):
        p(LOUNGE + "/Floor", x, 6, z, WOOD)
        edge = any((x + dx, z + dz) not in bay for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)))
        if edge and not (x == 15 and -6 <= z <= -2):
            for y in range(1, 13):
                finish = STONE
                if y in (1, 5, 6, 12):
                    finish = BRICK
                elif x % 3 != 0 and z % 3 != 0 and (2 <= y <= 4 or 8 <= y <= 11):
                    finish = "quark:framed_glass" if y != 11 else HONEY
                p(LOUNGE + "/Bay windows", x, y, z, finish)
    bay_roof = {(x + dx, z + dz) for x, z in bay for dx, dz in
                ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1))}
    skin(LOUNGE + "/Hipped roof",
         {(x, z): 27 + max(0, 9 - round(1.6 * sqrt((x - 20) ** 2 + (z - 0.5) ** 2)))
          for x, z in sorted(bay_roof)}, lambda x, z: COPPER if abs(x - 20) <= 1 and z == 0 else SHINGLE)

    landing = "01 Site/East platform approach"
    for z in range(-6, 6):
        y = max(0, 6 - (z + 6) // 2)
        for x in range(27, 30):
            box(landing, x, x, -1, y - 1, z, z, BASE)
            if (z + 6) % 2:
                stair(landing, x, y, z, "quark:polished_tuff", "north")
            else:
                p(landing, x, y, z, PAVING)
        p(landing, 30, y, z, BRICK)
        bar(landing, 30, y + 1, z)
    box(landing, 26, 29, 0, 0, 6, 9, PAVING)
    box(landing, 24, 29, 6, 6, -8, -6, ASHLAR)

    platform = PLATFORM + "/Track bed and boarding edge"
    for x in range(-34, 35):
        for z in range(-25, -6):
            if -20 <= z <= -16:
                p(platform, x, 5, z, "minecraft:gravel" if (x + z) % 5 else DARK)
            else:
                p(platform, x, 6, z, STONE if z == -15 else PAVING if z in (-21, -7) else ASHLAR)
        p(platform, x, 6, -18, "minecraft:rail", shape="east_west", waterlogged="false")
    rib_x = (-28, -21, -14, -7, 0, 7, 14, 21, 28)
    for x in rib_x:
        for z in (-26, -7):
            if z == -7 and -13 < x < 12:
                continue
            box(PLATFORM + "/Springing columns", x, x, 7, 13, z, z, TEAL)
            p(PLATFORM + "/Springing columns", x, 7, z, BRICK)
            p(PLATFORM + "/Springing columns", x, 11, z, COPPER)
            inset = z + (1 if z == -26 else -1)
            p(PLATFORM + "/Springing columns", x, 11, inset, TEAL)
            lamp(PLATFORM + "/Springing columns", x, 10, inset, True)
    skin(PLATFORM + "/Vault glazing",
         {(x, z): shed_course(z) for x in range(-29, 30) for z in range(-26, -6)},
         lambda x, z: COPPER if x in rib_x else SHINGLE if z in (-26, -7) else SMOKE)
    for x in rib_x:
        for z in range(-26, -6):
            y = (shed_course(z) - 1) // 2 - 1
            if z in (-26, -7) or (x, y, z) not in station.blocks:
                p(PLATFORM + "/Vault ribs", x, y, z, TEAL)
    for x in range(-28, 29):
        for y in range(7, 13):
            finish = BRICK if y in (7, 12) or x % 7 in (0, 1) else "quark:framed_glass"
            p(PLATFORM + "/Uphill screen", x, y, -26, finish)

    # The west footbridge connects the two passenger faces over the rail.
    crossing = PLATFORM + "/West footbridge"
    for z in range(-23, -12):
        if -19 <= z <= -16:
            y, facing = 9, None
        elif z <= -20:
            y, facing = z + 29, "south"
        else:
            y, facing = -z - 6, "north"
        for x in range(-26, -23):
            if facing:
                stair(crossing, x, y, z, "minecraft:spruce", facing)
            else:
                p(crossing, x, y, z, WOOD)
        for x in (-27, -23):
            if z in (-23, -20, -16, -13):
                p(crossing, x, y, z, TEAL)
            else:
                p(crossing, x, y, z, OAK, axis="z")
            if facing and y > 6:
                p(crossing, x, y - 1, z, OAK, axis="z")
            if z in (-23, -13):
                p(crossing, x, y + 1, z, BRONZE)
                lamp(crossing, x, y + 2, z)
            else:
                bar(crossing, x, y + 1, z)
    for x in (-27, -23):
        for z, facing in ((-20, "north"), (-16, "south")):
            box(crossing, x, x, 6, 7, z, z, TEAL)
            stair(crossing, x, 8, z, TEAL, facing)

    # Furnish the route at the edges, leaving a clear arrival axis.
    tickets = CONCOURSE + "/Tickets and lighting"
    for x in range(-15, -9):
        p(tickets, x, 1, 2, OAK, axis="x")
        slab(tickets, x, 2, 2, "minecraft:spruce")
        p(tickets, x, 1, -3, "minecraft:bookshelf")
    for x in (-16, 13):
        for z in (4, -2):
            p(tickets, x, 5, z, TEAL)
            p(tickets, x + (1 if x < 0 else -1), 5, z, COPPER)
            lamp(tickets, x + (1 if x < 0 else -1), 4, z, True)
    for x in (-20, -19, -18):
        p(west, x, 1, 4, "handcrafted:spruce_bench", facing="north", color="none",
          shape="single", waterlogged="false")
    furniture = LOUNGE + "/Oak seats and lamps"
    for x, z in [(17, 3), (20, 5), (23, 2), (20, -3)]:
        p(furniture, x, 7, z, "twigs:spruce_table", table_leg1="true", table_leg2="true",
          table_leg3="true", table_leg4="true")
        for offset, facing in ((-1, "east"), (1, "west")):
            p(furniture, x + offset, 7, z, "handcrafted:spruce_bench",
              facing=facing, color="none", shape="single", waterlogged="false")
        lamp(furniture, x, 8, z)
    for x in (-22, -17, 12, 17, 22):
        for dx in (-1, 0, 1):
            p(PLATFORM + "/Waiting benches", x + dx, 7, -10, "handcrafted:spruce_bench",
              facing="north", color="none", shape="single", waterlogged="false")
        if x in (-22, 12, 22):
            box(PLATFORM + "/Waiting benches", x, x, 7, 9, -9, -9, TEAL)
            lamp(PLATFORM + "/Waiting benches", x, 10, -9)

    clock = CONCOURSE + "/Platform clock"
    for x in range(-3, 0):
        for y in range(16, 19):
            ornament = BRONZE if (x, y) in ((-2, 17), (-2, 18), (-1, 17)) else STONE
            p(clock, x, y, -6, ornament if x == -2 or y == 17 else TEAL)

    neighbors = {"east": (1, 0), "west": (-1, 0), "south": (0, 1), "north": (0, -1)}
    for (x, y, z), part in bars.items():
        if (x, y, z) in station.blocks:
            continue
        state = {name: str((x + dx, y, z + dz) in bars).lower()
                 for name, (dx, dz) in neighbors.items()}
        p(part, x, y, z, "minecraft:iron_bars", **state, waterlogged="false")
    return station, groups
