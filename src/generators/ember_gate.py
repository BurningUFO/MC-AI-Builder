"""A double-sided Nether portal with a corbeled arch and recessed lamps."""

from src.core.building import Building
from src.generators.medieval import slab, stairs


WIDTH, DEPTH = 19, 15
PORTAL_X = range(7, 12)
PORTAL_Y = range(2, 10)
PORTAL_Z = DEPTH // 2

BRICK = "minecraft:polished_blackstone_bricks"
STAIR = "minecraft:polished_blackstone_brick_stairs"
SLAB = "minecraft:polished_blackstone_brick_slab"
STONE = "minecraft:polished_blackstone"
TILES = "minecraft:deepslate_tiles"
BASALT = "minecraft:polished_basalt"
COPPER = "minecraft:waxed_cut_copper"
COPPER_SLAB = "minecraft:waxed_cut_copper_slab"
LAMP = "minecraft:shroomlight"
LANTERN = "minecraft:lantern"
GATE_VANILLA = {"rune": "minecraft:chiseled_polished_blackstone"}
GATE_MODDED = {"rune": ("chipped:runic_carved_blackstone", [{}])}


def lay_terrace(gate):
    for z in range(1, DEPTH - 1):
        inset = min(z, DEPTH - 1 - z)
        margin = {1: 4, 2: 2, 3: 1}.get(inset, 0)
        for x in range(margin, WIDTH - margin):
            edge = x in (margin, WIDTH - 1 - margin)
            material = STONE if edge else TILES if x in PORTAL_X else BRICK
            if inset == 2 and x in (6, 12):
                material = COPPER
            gate.place((x, 0, z), material)

    for side, uphill in ((range(DEPTH), "south"), (range(DEPTH - 1, -1, -1), "north")):
        for x in range(5, 14):
            gate.place((x, 0, side[0]), STAIR, **stairs(uphill))
            gate.place((x, 1, side[3]), STAIR, **stairs(uphill))
    for z in range(4, 11):
        margin = 4 if z in (4, 10) else 3
        for x in range(margin, WIDTH - margin):
            material = TILES if x in PORTAL_X else BRICK
            if z in (4, 10) and x in (6, 12):
                material = COPPER
            gate.place((x, 1, z), material)


def build_piers(gate, rune):
    place = gate.place
    for side, inward, outward in (
        (range(WIDTH), "east", "west"), (range(WIDTH - 1, -1, -1), "west", "east"),
    ):
        toe, foot, wing, hearth, shoulder, pier, jamb, spring, arch = side[:9]
        place((toe, 1, PORTAL_Z), STAIR, **stairs(inward))
        place((foot, 2, PORTAL_Z), STAIR, **stairs(inward))
        for z in range(5, 10):
            for y in (1, 2):
                place((wing, y, z), BRICK)
        for z in (6, 7, 8):
            place((foot, 1, z), BRICK)
            if z == PORTAL_Z:
                place((wing, 3, z), STAIR, **stairs(inward))
            else:
                place((wing, 3, z), BRICK)
                place((wing, 4, z), TILES)
                place((wing, 5, z), STAIR, **stairs(inward))
            for y in range(2, 6):
                place((hearth, y, z), LAMP if y == 4 and z != PORTAL_Z else BRICK)
            place((hearth, 6, z), STAIR, **stairs(inward))
            for y in range(2, 11):
                place((pier, y, z), BRICK)
            for y in range(2, 10):
                place((shoulder, y, z), TILES if y == 9 and z == PORTAL_Z else BRICK)
            place((shoulder, 10, z), SLAB, **slab("top"))
            if z != PORTAL_Z:
                for y in range(2, 10):
                    place((jamb, y, z), STONE)
                place((spring, 9, z), STAIR, **(stairs(outward) | {"half": "top"}))

        place((hearth, 7, PORTAL_Z), rune)
        place((hearth, 8, PORTAL_Z), SLAB, **slab("bottom"))

        for face, front, uphill in ((5, 4, "south"), (9, 10, "north")):
            place((hearth, 2, face), BRICK)
            place((hearth, 3, face), SLAB, **slab("top"))
            place((hearth, 5, face), COPPER_SLAB, **slab("bottom"))
            for y in range(2, 8):
                place((shoulder, y, face), TILES if y in (4, 5) else BRICK)
            place((shoulder, 8, face), STAIR, **stairs(inward))

            place((pier, 2, front), STAIR, **stairs(uphill))
            place((pier, 2, face), BRICK)
            place((pier, 3, face), rune)
            for y in range(4, 9):
                place((pier, y, face), BASALT, axis="y")
            place((pier, 9, face), rune)
            place((pier, 9, front), COPPER_SLAB, **slab("top"))

            for y in range(2, 10):
                place((jamb, y, face), rune if y == 8 else STONE)
            place((spring, 8, face), STAIR, **(stairs(outward) | {"half": "top"}))
            place((spring, 9, face), BRICK)
            place((arch, 9, face), STAIR, **(stairs(outward) | {"half": "top"}))

        for z in (2, 12):
            place((shoulder, 1, z), rune)
            place((shoulder, 2, z), LANTERN, hanging="false", waterlogged="false")


def cap_arch(gate, rune):
    place = gate.place
    for x in range(5, 14):
        for z in range(5, 10):
            place((x, 10, z), rune if x == 9 and z in (5, 9) else BRICK)
        for z in (4, 10):
            place((x, 10, z), SLAB, **slab("top"))
        for z in (5, 9):
            place((x, 11, z), COPPER_SLAB if x in (6, 9, 12) else SLAB, **slab("top"))
        for z in (6, 7, 8):
            place((x, 11, z), BRICK)
    for z in (6, 7, 8):
        for x, uphill in ((4, "east"), (14, "west")):
            place((x, 11, z), STAIR, **stairs(uphill))
        for x, uphill in ((6, "east"), (12, "west")):
            place((x, 12, z), STAIR, **stairs(uphill))
        for x in range(7, 12):
            place((x, 12, z), rune if x == 9 and z != PORTAL_Z else BRICK)
    for x, uphill in ((7, "east"), (11, "west")):
        place((x, 13, PORTAL_Z), STAIR, **stairs(uphill))
    for x in range(8, 11):
        place((x, 13, PORTAL_Z), BRICK)
        place((x, 13, 6), STAIR, **stairs("south"))
        place((x, 13, 8), STAIR, **stairs("north"))
        place((x, 14, PORTAL_Z), COPPER_SLAB if x == 9 else SLAB, **slab("bottom"))


def generate_ember_gate(palette: dict[str, str] | None = None) -> Building:
    rune = (GATE_VANILLA | (palette or {}))["rune"]
    gate = Building("余烬门")
    lay_terrace(gate)
    build_piers(gate, rune)
    cap_arch(gate, rune)

    # The rounded-looking arch sits in front of the unobstructed rectangular frame.
    for x in range(PORTAL_X.start - 1, PORTAL_X.stop + 1):
        for y in range(PORTAL_Y.start - 1, PORTAL_Y.stop + 1):
            if x not in PORTAL_X or y not in PORTAL_Y:
                gate.place((x, y, PORTAL_Z), "minecraft:obsidian")
    return gate
