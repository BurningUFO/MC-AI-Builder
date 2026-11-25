"""Three growing floors with timber or pastel glazing and masonry."""

from src.core.building import BlockState, Building
from src.generators.medieval import slab, stairs


FLOORS = (1, 7, 13)
BED_COLUMNS = (range(3, 7), range(10, 14))
BED_ROWS = range(3, 10)
WATER_COLUMNS = (4, 12)
WATER_ROW = 6
LAMP_ROWS = (4, 6, 8)
STAIR_COLUMNS = range(4, 10)
STAIR_ROWS = (12, 13)
CUTAWAY = "Growing floor"
TOWER_SAMPLES = frozenset(("grain_tower", "crystal_tower"))

BRICKS = "minecraft:stone_bricks"
MOSSY = "minecraft:mossy_stone_bricks"
PATH = "minecraft:polished_andesite"
SOIL = "minecraft:dirt"
FRAMING = "minecraft:stripped_spruce_log"
GLASS = "minecraft:glass"
BLUE_GLASS = "minecraft:light_blue_stained_glass"
PINK_GLASS = "minecraft:pink_stained_glass"
GLAZING = (GLASS, BLUE_GLASS, PINK_GLASS)
COPPER = "minecraft:waxed_weathered_cut_copper"
COPPER_SLAB = "minecraft:waxed_weathered_cut_copper_slab"
CREST = "minecraft:waxed_cut_copper_slab"
WOOD_SLAB = "minecraft:spruce_slab"
LANTERN = "minecraft:lantern"
SEA_LANTERN = "minecraft:sea_lantern"
ENTRY_STEP = "minecraft:stone_brick_stairs"
STAIR = "minecraft:spruce_stairs"
FENCE = "minecraft:spruce_fence"
DOOR = "minecraft:spruce_door"
BARREL = "minecraft:barrel"
PLANKS = "minecraft:spruce_planks"
TOWER_VANILLA = {"trim": PLANKS}
TOWER_MODDED = {"trim": ("chipped:stacked_spruce_planks", [{}])}
TOWER_MATERIALS = {
    "base": BRICKS,
    "base_accent": MOSSY,
    "entry_stairs": ENTRY_STEP,
    "boards": PLANKS,
    "path": PATH,
    "trim": PLANKS,
    "frame": FRAMING,
    "door": DOOR,
    "glazing_sill": GLASS,
    "glazing_head": GLASS,
    "eave": WOOD_SLAB,
    "roof_eave": COPPER_SLAB,
    "lamp": LANTERN,
    "stairs": STAIR,
    "fence": FENCE,
    "roof": COPPER,
    "roof_glazing": GLASS,
    "crest": CREST,
}
CRYSTAL_PALETTE = {
    "base": "minecraft:polished_diorite",
    "base_accent": "minecraft:calcite",
    "entry_stairs": "minecraft:smooth_quartz_stairs",
    "boards": "minecraft:birch_planks",
    "path": "minecraft:quartz_bricks",
    "trim": "minecraft:smooth_quartz",
    "frame": "minecraft:quartz_pillar",
    "door": "minecraft:birch_door",
    "glazing_sill": BLUE_GLASS,
    "glazing_head": PINK_GLASS,
    "eave": "minecraft:smooth_quartz_slab",
    "roof_eave": "minecraft:purpur_slab",
    "lamp": SEA_LANTERN,
    "stairs": "minecraft:birch_stairs",
    "fence": "minecraft:birch_fence",
    "roof": "minecraft:purpur_block",
    "roof_glazing": BLUE_GLASS,
    "roof_peak_glazing": PINK_GLASS,
    "crest": "minecraft:amethyst_block",
}

FARMLAND = BlockState("minecraft:farmland", (("moisture", "7"),))
WHEAT = BlockState("minecraft:wheat", (("age", "7"),))
CARROTS = BlockState("minecraft:carrots", (("age", "7"),))
POTATOES = BlockState("minecraft:potatoes", (("age", "7"),))
BEETROOTS = BlockState("minecraft:beetroots", (("age", "3"),))
CROPS = ((WHEAT, CARROTS), (POTATOES, BEETROOTS), (WHEAT, BEETROOTS))
DISPLAY_STATES = (FARMLAND, WHEAT, CARROTS, POTATOES, BEETROOTS)


def footprint(inset=0):
    left, right = 1 + inset, 15 - inset
    front, rear = 2 + inset, 14 - inset
    bevel = max(0, 2 - inset)
    cells = set()
    for z in range(front, rear + 1):
        margin = max(0, bevel - min(z - front, rear - z))
        cells.update((x, z) for x in range(left + margin, right + 1 - margin))
    return cells


def is_post(x, z):
    if z in (2, 14):
        return x in (3, 6, 10, 13)
    return x in (1, 15) and z in (4, 8, 12)


def build_floors(tower, palette):
    shell = footprint()
    wall = shell - footprint(1)
    eaves = {
        (x + dx, z + dz)
        for x, z in wall
        for dx, dz in ((-1, 0), (1, 0), (0, -1), (0, 1))
    } - shell
    beds = {(x, z) for columns in BED_COLUMNS for x in columns for z in BED_ROWS}
    basins = {(x, WATER_ROW) for x in WATER_COLUMNS}
    stairwell = {(x, z) for x in STAIR_COLUMNS for z in STAIR_ROWS}
    place = tower.place
    base, trim, frame = palette["base"], palette["trim"], palette["frame"]
    entry_stairs = palette["entry_stairs"]
    lamp = palette["lamp"]
    lamp_state = {"hanging": "true", "waterlogged": "false"} if lamp == LANTERN else {}

    for x, z in wall:
        place((x, 0, z), palette["base_accent"] if (5 * x + 11 * z) % 9 < 3 else base)
    for x in range(6, 11):
        place((x, 0, 0), entry_stairs, **stairs("south"))
        place((x, 0, 1), base)
        place((x, 1, 1), entry_stairs, **stairs("south"))
        place((x, 4, 1), palette["roof_eave"], **slab("top"))

    for floor in FLOORS:
        openings = basins if floor == FLOORS[0] else basins | stairwell
        for x, z in shell - openings:
            material = palette["boards"]
            if (x, z) in wall:
                material = base if floor == FLOORS[0] else trim
            if (x, z) in beds:
                material = SOIL
            elif (x, z) not in wall and x in (7, 8, 9):
                material = palette["path"]
            place((x, floor, z), material)
        if floor == FLOORS[0]:
            for x, z in basins:
                place((x, floor - 1, z), base)

        for x, z in wall:
            for y in range(floor + 1, floor + 5):
                if floor == FLOORS[0] and z == 2 and x == 8 and y in (2, 3):
                    place((x, y, z), palette["door"], facing="north", half="lower" if y == 2 else "upper",
                          hinge="left", open="false", powered="false")
                elif is_post(x, z) or (floor == FLOORS[0] and z == 2 and x in (7, 8, 9) and y <= 4):
                    place((x, y, z), frame, axis="y")
                elif y == floor + 1:
                    place((x, y, z), palette["glazing_sill"])
                elif y == floor + 3:
                    place((x, y, z), palette["glazing_head"])
                else:
                    place((x, y, z), GLASS)
            place((x, floor + 5, z), trim)
        for x, z in eaves:
            material = palette["roof_eave"] if floor == FLOORS[-1] else palette["eave"]
            place((x, floor + 5, z), material, **slab("top"))

        if floor != FLOORS[-1]:
            for x, z in beds:
                if x not in WATER_COLUMNS:
                    place((x, floor + 5, z), palette["eave"], **slab("top"))
        beam_rows = range(3, 14) if floor == FLOORS[-1] else BED_ROWS
        for x in WATER_COLUMNS:
            for z in beam_rows:
                place((x, floor + 5, z), frame, axis="z")
                if z in LAMP_ROWS:
                    place((x, floor + 4, z), lamp, **lamp_state)
        for x in (12, 13):
            place((x, floor + 1, 13), BARREL, facing="north", open="false")


def build_stairs(tower, palette):
    for floor in FLOORS[:-1]:
        for rise, x in enumerate(STAIR_COLUMNS, start=1):
            for z in STAIR_ROWS:
                if rise > 1 or floor != FLOORS[0]:
                    tower.place((x, floor + rise - 1, z), palette["boards"])
                tower.place((x, floor + rise, z), palette["stairs"], **stairs("east"))
    for floor in FLOORS[1:]:
        for x in range(STAIR_COLUMNS.start, STAIR_COLUMNS.stop - 1):
            tower.place((x, floor + 1, 11), palette["fence"], north="false", south="false", waterlogged="false",
                        west=str(x > STAIR_COLUMNS.start).lower(), east=str(x < STAIR_COLUMNS.stop - 2).lower())


def build_roof(tower, palette):
    for tier in range(5):
        outline = footprint(tier)
        shell = outline - footprint(tier + 2) if tier < 4 else outline
        corner = (7 - tier) + (6 - tier) - max(0, 2 - tier)
        glazing = palette["roof_glazing"]
        if tier == 4:
            glazing = palette.get("roof_peak_glazing", glazing)
        for x, z in shell:
            rib = x == 8 or z == 8 or abs(x - 8) + abs(z - 8) >= corner - 1
            tower.place((x, 19 + tier, z), palette["roof"] if rib else glazing)
    for x in range(7, 10):
        block = palette["crest"] if x == 8 else palette["roof_eave"]
        tower.place((x, 24, 8), block, **(slab("bottom") if block.endswith("_slab") else {}))


def build_tower(name, palette):
    tower = Building(name)
    build_floors(tower, palette)
    build_stairs(tower, palette)
    build_roof(tower, palette)
    return tower


def generate_grain_tower(palette: dict[str, str] | None = None) -> Building:
    return build_tower("青穗塔", TOWER_MATERIALS | (palette or {}))


def generate_crystal_tower(palette: dict[str, str] | None = None) -> Building:
    return build_tower("霞晶塔", TOWER_MATERIALS | CRYSTAL_PALETTE | (palette or {}))
