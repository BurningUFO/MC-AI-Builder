"""Mossreach Manor: a timber great hall on a stone landing above the lake."""

from math import cos

from src.core.building import Building


STONE = "twigs:polished_tuff_bricks"
MOSSY = "twigs:mossy_cobblestone_bricks"
BLOCK = "minecraft:stone_bricks"
PLASTER = "minecraft:white_terracotta"
TIMBER = "minecraft:stripped_dark_oak_log"
OAK = "minecraft:dark_oak_planks"
FLOOR = "minecraft:spruce_planks"
VERTICAL = "quark:vertical_spruce_planks"
TILE = "quark:cyan_shingles"
PALE = "minecraft:smooth_sandstone"
TERRACE = "minecraft:polished_andesite"
LANTERN = "minecraft:lantern"
SHELF = "quark:dark_oak_bookshelf"
EDGES = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}


class Manor:
    def __init__(self):
        self.building = Building("苔岬庄园 · Mossreach Manor")
        self.groups = {}

    def at(self, group, x, y, z, block, **properties):
        pos = x, y, z
        self.building.place(pos, block, **properties)
        self.groups[pos] = group

    def free(self, x, y, z):
        return (x, y, z) not in self.building.blocks

    def beam(self, group, x, y, z, axis="y"):
        self.at(group, x, y, z, TIMBER, axis=axis)

    def step(self, group, x, y, z, material, facing, half="bottom"):
        self.at(group, x, y, z, material, facing=facing, half=half, shape="straight", waterlogged="false")

    def slab(self, group, x, y, z, material, kind="bottom"):
        self.at(group, x, y, z, material, type=kind, waterlogged="false")

    def pane(self, group, x, y, z, axis):
        connections = {"north", "south"} if axis == "z" else {"west", "east"}
        self.at(group, x, y, z, "minecraft:glass_pane", waterlogged="false",
                **{direction: str(direction in connections).lower() for direction in EDGES})

    def lantern(self, group, x, y, z, hanging=False):
        self.at(group, x, y, z, LANTERN, hanging=str(hanging).lower(), waterlogged="false")

    def fence(self, group, x, y, z, neighbors, material="minecraft:spruce_fence"):
        self.at(group, x, y, z, material, waterlogged="false",
                **{direction: str((x + dx, z + dz) in neighbors).lower() for direction, (dx, dz) in EDGES.items()})

    def barrel(self, group, x, y, z, facing="up"):
        self.at(group, x, y, z, "minecraft:barrel", facing=facing, open="false")


def rectangle(x0, x1, z0, z1):
    return {(x, z) for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)}


MAIN = rectangle(-10, 10, -8, 7)
ORIEL = rectangle(-4, 4, 8, 10)
LODGE = rectangle(11, 21, -4, 11) | rectangle(13, 19, 12, 12) | rectangle(14, 18, 13, 13)
SERVICE = rectangle(-20, -11, -4, 4)
FOOTPRINT = MAIN | ORIEL | LODGE | SERVICE
STAIR = rectangle(-8, -3, -6, -5)
VOID = rectangle(-4, 5, -2, 3)


def outside(x, z, footprint):
    return [(side, dx, dz) for side, (dx, dz) in EDGES.items() if (x + dx, z + dz) not in footprint]


def site(manor):
    masonry = "01 Site and plinth/Masonry"
    shore = "01 Site and plinth/Shore steps"
    landing = "01 Site and plinth/Lakeside terrace"
    for x, z in FOOTPRINT:
        for y in (-2, -1):
            block = MOSSY if z > 2 and (x * 3 + z * 5 + y) % 8 < 2 else STONE
            manor.at(masonry, x, y, z, block)
        surf = TERRACE if (x, z) in SERVICE or (-2 <= x <= 4 and z <= -5) else FLOOR
        if outside(x, z, FOOTPRINT):
            surf = STONE
        manor.at("02 Ground floor/Floors", x, 0, z, surf)

    for x in range(-21, 22):
        bank = 10 + int(2 * cos((x + 6) / 7))
        for z in range(5, bank + 1):
            if (x, z) in FOOTPRINT:
                continue
            manor.at(shore, x, -2, z, MOSSY if (x + 2 * z) % 7 < 2 else STONE)
            manor.at(shore, x, -1, z, "minecraft:gravel" if z == bank else "minecraft:coarse_dirt")
    patio = rectangle(-12, 10, 8, 14) | rectangle(11, 18, 12, 15) | rectangle(-15, -13, 8, 12)
    for x, z in patio - FOOTPRINT:
        for y in (-2, -1):
            manor.at(masonry, x, y, z, MOSSY if (x * 7 + z) % 9 == 0 else STONE)
        manor.at(landing, x, 0, z, PALE if x in (-12, 10) or z in (8, 14) else TERRACE)
    for x in range(-10, 9):
        if x in range(-7, -2):
            for y in (-2, -1):
                manor.at(shore, x, y, 15, BLOCK)
            manor.step(shore, x, 0, 15, "minecraft:stone_brick_stairs", "north")
        else:
            manor.slab(landing, x, 1, 14, "minecraft:stone_brick_slab")
            if x in (-10, -8, -2, 2, 6, 8):
                manor.at(landing, x, 1, 14, STONE)
                manor.lantern(landing, x, 2, 14)

    dock = "01 Site and plinth/Jetty and mooring"
    for x, z in rectangle(-7, -3, 16, 23):
        manor.at(dock, x, -1, z, VERTICAL if x in (-7, -3) else FLOOR)
    for z in (16, 20, 23):
        for x in (-7, -3):
            for y in range(-4, 1):
                manor.beam(dock, x, y, z)
            if z != 16:
                manor.lantern(dock, x, 1, z)
        for x in (-6, -5, -4):
            manor.beam(dock, x, -2, z, "x")
    dock_rail = {(x, z) for x in (-7, -3) for z in range(16, 24)}
    for x, z in dock_rail - {(x, z) for x in (-7, -3) for z in (16, 20, 23)}:
        manor.fence(dock, x, 0, z, dock_rail)

    ground = "01 Site and plinth/Land approach and herb beds"
    for x, z in rectangle(-7, -4, -14, -9):
        manor.at(ground, x, -1, z, MOSSY)
        manor.at(ground, x, 0, z, "minecraft:coarse_dirt" if (x + z) % 6 == 0 else TERRACE)
    for x, z in rectangle(-20, -12, -10, -5):
        manor.at(ground, x, -1, z, MOSSY)
        path = x in (-17, -16) or z == -5
        manor.at(ground, x, 0, z, TERRACE if path else "minecraft:grass_block", **({} if path else {"snowy": "false"}))
        if not path and (x + 3 * z) % 5 == 0:
            plant = "minecraft:fern" if x % 2 else "minecraft:flowering_azalea"
            manor.at(ground, x, 1, z, plant)
    for x, z in ((-12, 10), (-11, 10), (7, 11), (8, 11), (17, 14), (18, 14)):
        manor.at(ground, x, 1, z, "minecraft:moss_block")
        manor.at(ground, x, 2, z, "minecraft:fern" if x % 2 else "minecraft:flowering_azalea")


def doors():
    return {
        (-7, 1, 7): ("south", "left"), (-6, 1, 7): ("south", "right"),
        (-6, 1, -8): ("north", "right"), (-5, 1, -8): ("north", "left"),
        (-16, 1, -4): ("north", "right"),
        (15, 7, 11): ("south", "left"), (16, 7, 11): ("south", "right"),
    }


UPPER = MAIN | ORIEL | rectangle(11, 21, -4, 11)


def facade(manor):
    entries = doors()
    for outline, levels in ((FOOTPRINT, range(1, 6)), (UPPER, range(7, 10))):
        for x, z in outline:
            sides = outside(x, z, outline)
            if not sides:
                continue
            side, dx, dz = sides[0]
            axis = "z" if dx else "x"
            along = z if dx else x
            service = (x, z) in SERVICE
            if service and 5 in levels:
                wall_levels = range(1, 5)
            else:
                wall_levels = levels
            story = "03 Upper floor" if 7 in wall_levels else "02 Ground floor"
            frame = f"{story}/Half-timbered frame"
            wall = f"{story}/Wall infill"
            glass = f"{story}/Windows and sills"
            mullion = len(sides) > 1 or along % 5 == 0
            window = not mullion and along % 5 in (1, 2)
            if z == 10 and -3 <= x <= 3:
                window = x in (-3, -2, 0, 2, 3)
            if z == 13 and x in (15, 16, 17):
                window = True
            for y in wall_levels:
                door_base = y if (x, y, z) in entries else y - 1
                door = entries.get((x, door_base, z))
                if door:
                    facing, hinge = door
                    manor.at(f"{story}/Doors", x, y, z, "minecraft:spruce_door", facing=facing,
                             hinge=hinge, half="lower" if y == door_base else "upper",
                             open="false", powered="false")
                elif y == 1:
                    manor.at("01 Site and plinth/Masonry", x, y, z, STONE if along % 7 else MOSSY)
                elif y == wall_levels.stop - 1 or y == 4 and not service:
                    manor.beam(frame, x, y, z, axis)
                elif mullion or y == 7 and along % 5 == 4:
                    manor.beam(frame, x, y, z)
                elif window:
                    manor.pane(glass, x, y, z, axis)
                else:
                    manor.at(wall, x, y, z, PLASTER if not service else VERTICAL)
            sill = 1 if 1 in wall_levels else 6
            if window and (x, sill + 1, z) not in entries and manor.free(x + dx, sill, z + dz):
                manor.slab(glass, x + dx, sill, z + dz, "minecraft:spruce_slab", "top")

    for x in (-9, -4):
        for y in range(1, 4):
            manor.beam("02 Ground floor/Entry porch", x, y, 9)
    for x in range(-9, -3):
        manor.beam("02 Ground floor/Entry porch", x, 4, 9, "x")
        manor.step("04 Roofs/Entry hood", x, 5, 8, TILE + "_stairs", "north")
        manor.slab("04 Roofs/Entry hood", x, 5, 9, TILE + "_slab")
    for x in (-8, -5):
        manor.lantern("02 Ground floor/Entry porch", x, 3, 9, hanging=True)


def floor_plan(manor):
    floor = "03 Upper floor/Gallery and suite floors"
    stairs = "03 Upper floor/Main stair"
    rails = "03 Upper floor/Gallery balustrade"
    for x, z in UPPER - VOID - STAIR:
        rim = bool(outside(x, z, UPPER)) or any((x + dx, z + dz) in VOID for dx, dz in EDGES.values())
        manor.at(floor, x, 6, z, OAK if rim else FLOOR)
    for x, z in STAIR:
        tread = x + 9
        for y in range(1, tread):
            manor.at(stairs, x, y, z, VERTICAL)
        manor.step(stairs, x, tread, z, "minecraft:spruce_stairs", "east")
    gallery_rails = (
        {(x, z) for x, z in UPPER - VOID - STAIR if any((x + dx, z + dz) in VOID for dx, dz in EDGES.values())}
        | {(-9, -6), (-9, -5)} | {(x, -7) for x in range(-8, -3)} | {(x, -4) for x in range(-8, -3)}
    )
    for x, z in gallery_rails:
        manor.fence(rails, x, 7, z, gallery_rails, "minecraft:dark_oak_fence")
    for x in (-6, 7):
        for z in (-3, 4):
            for y in range(1, 6):
                manor.beam("02 Ground floor/Truss structure", x, y, z)
    for z in (-3, 4):
        for x in range(-10, 11):
            manor.beam("03 Upper floor/Roof tie beams", x, 10, z, "x")
        for x in (-6, 7):
            manor.beam("03 Upper floor/Roof tie beams", x, 11, z)
            manor.lantern("03 Upper floor/Lighting", x, 9, z, hanging=True)

    partitions = "02 Ground floor/Service and workshop walls"
    for x, zs in ((-10, range(-4, 5)), (10, range(-4, 8))):
        for z in zs:
            for y in range(1, 5):
                if z in (0, 1) and y <= 3:
                    continue
                manor.at(partitions, x, y, z, VERTICAL if y < 4 else OAK)
    suites = "03 Upper floor/Bedroom partitions"
    for x in range(14, 21):
        for y in range(7, 10):
            manor.at(suites, x, y, 3, TIMBER if x == 14 else PLASTER,
                     **({"axis": "y"} if x == 14 else {}))
    for z in range(-3, 11):
        for y in range(7, 10):
            if z in (-1, 0, 7, 8) and y <= 8:
                continue
            manor.at(suites, 13, y, z, VERTICAL if y != 9 else OAK)

    balcony = "03 Upper floor/Lakeside balcony"
    balcony_deck = rectangle(13, 19, 12, 15)
    for x, z in balcony_deck:
        manor.at(balcony, x, 6, z, VERTICAL if x in (13, 19) or z == 15 else FLOOR)
    balcony_rails = {(x, z) for x, z in balcony_deck if x in (13, 19) or z == 15}
    for x, z in balcony_rails:
        manor.fence(balcony, x, 7, z, balcony_rails, "minecraft:dark_oak_fence")
    for x in (14, 18):
        for y, z in ((2, 13), (3, 14), (4, 14), (5, 15)):
            manor.beam(balcony, x, y, z, "z")
        manor.beam(balcony, x, 7, 15)
        manor.lantern(balcony, x, 8, 15)


def roof_field():
    roofs = (
        ("Great hall", -11, 11, -9, 8, 10, ("z",)),
        ("Lakeside oriel", -5, 5, 3, 11, 10, ("x",)),
        ("Bedroom wing", 10, 22, -5, 14, 10, ("x",)),
        ("Hipped service roof", -21, -10, -5, 5, 5, ("x", "z")),
    )
    field = {}
    for name, x0, x1, z0, z1, eave, axes in roofs:
        for x, z in rectangle(x0, x1, z0, z1):
            run = ((x - x0, "east"), (x1 - x, "west")) if "x" in axes else ()
            run += ((z - z0, "south"), (z1 - z, "north")) if "z" in axes else ()
            pitch, facing = min(run)
            slope = max(min(x1 - x0 if axis == "x" else z1 - z0 for axis in axes) // 2, 1)
            ridge = pitch == slope
            record = eave + pitch, facing, ridge, name, eave
            if (x, z) not in field or record[0] > field[x, z][0]:
                field[x, z] = record
    return field


def roofs(manor):
    field = roof_field()
    for (x, z), (top, facing, ridge, name, eave) in field.items():
        tiles = f"04 Roofs/{name}"
        local_heights = [field[x + dx, z + dz][0] for dx, dz in EDGES.values() if (x + dx, z + dz) in field]
        underside = max(min(local_heights), min(top, eave + 1))
        for y in range(min(underside, top), top):
            manor.at(tiles, x, y, z, TILE)
        if ridge:
            manor.slab(tiles, x, top, z, TILE + "_slab")
        else:
            manor.step(tiles, x, top, z, TILE + "_stairs", facing)
        if top == eave and (x, z) not in FOOTPRINT:
            manor.slab("04 Roofs/Eave boards", x, top - 1, z, "minecraft:dark_oak_slab", "top")

    for envelope, bottom in ((UPPER, 10), (SERVICE, 5)):
        for edge in envelope:
            x, z = edge
            sides = outside(x, z, envelope)
            if not sides:
                continue
            top = field[edge][0]
            normal, dx, dz = sides[0]
            axis = "z" if dx else "x"
            shift = (z if dx else x) - (16 if x >= 11 and dz else 0)
            for y in range(bottom, top):
                if not manor.free(x, y, z):
                    continue
                timber = y == bottom or y == 13 or shift % 5 == 0 or abs(shift) == y - bottom
                if not timber and y in (11, 12) and normal in ("north", "south") and abs(shift) <= 2:
                    manor.pane("04 Roofs/Gable lights", x, y, z, axis)
                elif timber:
                    manor.beam("04 Roofs/Gable timber", x, y, z, axis if y in (bottom, 13) else "y")
                else:
                    manor.at("04 Roofs/Gable infill", x, y, z, PLASTER if edge in UPPER else VERTICAL)
    for x, z in ((-11, -1), (11, -1), (0, 11), (16, 14)):
        top = field[x, z][0]
        manor.beam("04 Roofs/Ridge ends", x, top, z)
        manor.slab("04 Roofs/Ridge ends", x, top + 1, z, TILE + "_slab")


def hearth(manor):
    stonework = "05 Hearth and chimney/Masonry"
    mantel = "05 Hearth and chimney/Mantel"
    for y in range(1, 21):
        width = range(-1, 4) if y <= 4 else range(0, 3)
        for x in width:
            for z in range(-8, -5):
                if x == 1 and z == -7:
                    continue
                if y <= 2 and z == -6 and x in range(0, 3):
                    continue
                facing = y <= 3 and z == -6
                manor.at(stonework, x, y, z, "minecraft:bricks" if facing else BLOCK)
    for x in range(-1, 4):
        manor.slab(mantel, x, 3, -5, "minecraft:spruce_slab", "top")
    for y in range(2, 22):
        manor.building.blocks.pop((1, y, -7), None)
        manor.groups.pop((1, y, -7), None)
    manor.at("05 Hearth and chimney/Campfire", 1, 1, -7, "minecraft:campfire",
             facing="south", lit="true", waterlogged="false")
    for x, z in rectangle(0, 2, -8, -6) - {(1, -7)}:
        manor.slab(stonework, x, 21, z, "minecraft:stone_brick_slab")


def chair(manor, group, x, y, z, facing, color="none"):
    manor.at(group, x, y, z, "handcrafted:spruce_chair", facing=facing, color=color, waterlogged="false")


def table(manor, group, x, y, z):
    manor.at(group, x, y, z, "twigs:spruce_table",
             table_leg1="true", table_leg2="true", table_leg3="true", table_leg4="true")


def great_hall(manor):
    dining = "02 Ground floor/Dining table"
    living = "02 Ground floor/Hearth lounge"
    for x in range(-6, -1):
        table(manor, dining, x, 1, 2)
    for x, z, facing in ((-6, 1, "south"), (-4, 1, "south"), (-2, 1, "south"),
                         (-6, 3, "north"), (-4, 3, "north"), (-2, 3, "north")):
        chair(manor, dining, x, 1, z, facing)
    manor.lantern(dining, -4, 2, 2)
    manor.at(dining, -2, 2, 2, "minecraft:potted_cornflower")
    carpet = rectangle(1, 6, -4, -1)
    for x, z in carpet:
        if manor.free(x, 1, z):
            color = "brown" if x in (1, 6) or z in (-4, -1) else "red"
            manor.at(living, x, 1, z, f"minecraft:{color}_carpet")
    for z in (-3, -2, -1):
        half = {-3: "left", -2: "middle", -1: "right"}[z]
        manor.at(living, 6, 1, z, "handcrafted:spruce_bench",
                 facing="west", shape=half, color="brown", waterlogged="false")
    chair(manor, living, 2, 1, -1, "north", "brown")
    table(manor, living, 4, 1, -2)
    manor.lantern(living, 4, 2, -2)
    for x in (-3, 0, 3):
        table(manor, living, x, 1, 9)
        manor.at(living, x, 2, 9, "minecraft:potted_fern")


def kitchen_and_workshop(manor):
    kitchen = "02 Ground floor/Kitchen"
    pantry = "02 Ground floor/Pantry"
    workshop = "02 Ground floor/Workshop"
    for x in range(-19, -11):
        if x in (-18, -17, -16, -15):
            block = "minecraft:smoker" if x in (-18, -17) else "minecraft:furnace"
            manor.at(kitchen, x, 1, 3, block, facing="north", lit="false")
        else:
            manor.at(kitchen, x, 1, 3, VERTICAL)
        manor.slab(kitchen, x, 2, 3, "minecraft:smooth_stone_slab")
    for x in (-19, -18, -14, -13, -12):
        for y in (1, 2):
            manor.barrel(pantry, x, y, -3, facing="south")
    manor.at(kitchen, -19, 1, 1, "minecraft:cauldron")
    for x in (-16, -15, -14):
        table(manor, kitchen, x, 1, 0)
    manor.lantern(kitchen, -15, 2, 0)
    manor.at(kitchen, -16, 2, 0, "minecraft:potted_fern")

    for x in range(13, 20):
        for y in (1, 2):
            manor.barrel(workshop, x, y, -3, facing="south")
    for z in range(-1, 5):
        block = "minecraft:crafting_table" if z in (0, 1) else VERTICAL
        manor.at(workshop, 20, 1, z, block)
    manor.at(workshop, 20, 1, 3, "minecraft:stonecutter", facing="west")
    for x in (14, 15, 16, 17, 18):
        manor.at(workshop, x, 1, 10, SHELF)
    for x in (16, 17, 18):
        table(manor, workshop, x, 1, 8)
    chair(manor, workshop, 16, 1, 7, "south")
    chair(manor, workshop, 18, 1, 7, "south")
    manor.lantern(workshop, 17, 2, 8)


def bed(manor, group, x, z):
    for xx, part in ((x, "foot"), (x + 1, "head")):
        manor.at(group, xx, 7, z, "aether:skyroot_bed", facing="east", part=part)


def private_rooms(manor):
    guest = "03 Upper floor/Guest bedroom"
    suite = "03 Upper floor/Lake bedroom"
    nook = "03 Upper floor/Gallery study"
    for x, z, name in ((18, -2, guest), (18, 7, suite), (18, 8, suite)):
        bed(manor, name, x, z)
    for x, z, name in ((19, -1, guest), (19, 6, suite), (19, 9, suite)):
        manor.barrel(name, x, 7, z)
        manor.lantern(name, x, 8, z)
    for x in (15, 16, 17):
        for z in (5, 6, 7, 8, 9):
            manor.at(suite, x, 7, z, "minecraft:green_carpet")
    for x, z in ((15, -2), (16, -2), (17, 4), (18, 4), (-8, 6), (-7, 6), (-6, 6)):
        group = nook if x < 0 else (guest if z < 0 else suite)
        manor.at(group, x, 7, z, SHELF)
        if x < 0 or z != 4:
            manor.at(group, x, 8, z, SHELF)
    for x in (-8, -7):
        table(manor, nook, x, 7, 4)
    chair(manor, nook, -8, 7, 3, "south")
    manor.lantern(nook, -7, 8, 4)


def build():
    manor = Manor()
    site(manor)
    facade(manor)
    floor_plan(manor)
    roofs(manor)
    hearth(manor)
    great_hall(manor)
    kitchen_and_workshop(manor)
    private_rooms(manor)
    return manor.building, manor.groups
