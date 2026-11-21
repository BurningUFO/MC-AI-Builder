"""A timber enchanting library with cherry accents and lavender borders."""

from src.core.building import Building
from src.generators.medieval import horizontal_connections, slab, stairs


SIZE = 16
POSTS = (0, 4, 11, 15)
ENTRANCES = (7, 8)
TABLE_XZ = (7, 7)
FACES = (("north", 0), ("south", 15), ("west", 0), ("east", 15))
DOOR_STATE = {"open": "false", "powered": "false"}
CHAIR_STATE = {"color": "light_blue", "waterlogged": "false"}

LIBRARY_VANILLA = {
    "floor": "minecraft:birch_planks",
    "floor_border": "minecraft:birch_planks",
    "wall": "minecraft:stripped_birch_log",
    "beam": "minecraft:birch_log",
    "door": "minecraft:birch_door",
    "roof_deck": "minecraft:deepslate_tiles",
    "roof_border": "minecraft:polished_andesite",
    "book_edge_slab": "minecraft:polished_andesite_slab",
    "book_edge_stairs": "minecraft:polished_andesite_stairs",
    "page_line_slab": "minecraft:quartz_slab",
    "page_line_stairs": "minecraft:quartz_stairs",
    "book_base": "minecraft:blue_terracotta",
    "shelves": "minecraft:bookshelf",
    "table": "minecraft:crafting_table",
    "chair": "minecraft:birch_stairs",
    "lavender": "minecraft:allium",
    "white_lavender": "minecraft:azure_bluet",
    "potted_sapling": "minecraft:potted_cherry_sapling",
}

LIBRARY_MODDED = {
    "floor": ("biomesoplenty:jacaranda_planks", [{}]),
    "floor_border": ("chipped:stacked_birch_planks", [{}]),
    "wall": ("biomesoplenty:stripped_jacaranda_log", [{"axis": "y"}]),
    "beam": ("biomesoplenty:magic_log", [{"axis": axis} for axis in ("x", "z")]),
    "door": ("biomesoplenty:jacaranda_door", [
        dict(DOOR_STATE, facing=facing, half=half, hinge=hinge)
        for facing, _ in FACES for half in ("lower", "upper") for hinge in ("left", "right")
    ]),
    "roof_deck": ("quark:blue_shingles", [{}]),
    "roof_border": ("quark:light_blue_shingles", [{}]),
    "book_edge_slab": ("quark:light_blue_shingles_slab", [slab("bottom"), slab("top")]),
    "book_edge_stairs": ("quark:light_blue_shingles_stairs", [stairs("west"), stairs("east")]),
    "page_line_slab": ("biomesoplenty:jacaranda_slab", [slab("bottom")]),
    "page_line_stairs": ("biomesoplenty:jacaranda_stairs", [stairs("west"), stairs("east")]),
    "book_base": ("biomesoplenty:magic_planks", [{}]),
    "shelves": ("quark:birch_bookshelf", [{}]),
    "table": ("twigs:birch_table", [{f"table_leg{i}": "true" for i in range(1, 5)}]),
    "chair": ("handcrafted:birch_chair", [dict(CHAIR_STATE, facing=direction) for direction in ("west", "east")]),
    "lavender": ("biomesoplenty:lavender", [{}]),
    "white_lavender": ("biomesoplenty:white_lavender", [{}]),
    "potted_sapling": ("biomesoplenty:potted_jacaranda_sapling", [{}]),
}


def place_ground(hall, palette):
    for z in range(-3, 19):
        for x in range(-3, 19):
            edge_x, edge_z = x in (-3, 18), z in (-3, 18)
            approach = (edge_x and z in ENTRANCES) or (edge_z and x in ENTRANCES)
            if approach:
                facing = "east" if x == -3 else "west" if x == 18 else "south" if z == -3 else "north"
                hall.place((x, 0, z), "minecraft:polished_andesite_stairs", **stairs(facing))
            elif edge_x or edge_z:
                marker = (edge_x and z in (-3, -2, 2, 13, 17, 18)) or (edge_z and x in (-3, -2, 2, 13, 17, 18))
                hall.place((x, 0, z), "minecraft:calcite" if marker else "minecraft:polished_andesite")
            elif x in ENTRANCES or z in ENTRANCES:
                material = "minecraft:polished_deepslate" if 5 <= x <= 9 and 5 <= z <= 9 else "minecraft:polished_andesite"
                hall.place((x, 0, z), material)
            elif 0 <= x < SIZE and 0 <= z < SIZE:
                material = palette["floor_border"] if x in (1, 14) or z in (1, 14) else palette["floor"]
                hall.place((x, 0, z), material)
            else:
                hall.place((x, 0, z), "minecraft:moss_block")
    for x, z in ((-2, -2), (17, -2), (17, 17), (-2, 17)):
        hall.place((x, 1, z), "minecraft:polished_diorite")
        hall.place((x, 2, z), "minecraft:lantern", hanging="false", waterlogged="false")


def facade(hall, facing, edge, palette):
    horizontal = facing in ("north", "south")
    outside = -1 if facing in ("north", "west") else SIZE

    def at(t, y, fixed=edge):
        return (t, y, fixed) if horizontal else (fixed, y, t)

    axis = "x" if horizontal else "z"
    glass_connections = horizontal_connections(("east", "west") if horizontal else ("north", "south"))
    left_hinge = ENTRANCES[0] if facing in ("north", "east") else ENTRANCES[1]
    for t in range(SIZE):
        hall.place(at(t, 5), palette["beam"], axis=axis)
        if t in POSTS:
            for y in range(1, 6):
                hall.place(at(t, y), "minecraft:birch_log", axis="y")
            continue
        if t in ENTRANCES:
            hinge = "left" if t == left_hinge else "right"
            for y, half in ((1, "lower"), (2, "upper")):
                hall.place(at(t, y), palette["door"], facing=facing, half=half, hinge=hinge, **DOOR_STATE)
            hall.place(at(t, 3), palette["beam"], axis=axis)
            hall.place(at(t, 4), "minecraft:amethyst_block")
            hall.place(at(t, 4, outside), palette["book_edge_slab"], **slab("top"))
            continue

        hall.place(at(t, 1), "minecraft:polished_diorite")
        if t in (2, 3, 12, 13):
            tint = "light_blue" if t < 8 else "purple"
            for y in (2, 3, 4):
                hall.place(at(t, y), f"minecraft:{tint}_stained_glass_pane", **glass_connections)
            hall.place(at(t, 1, outside), "minecraft:cherry_slab", **slab("top"))
        else:
            for y in (2, 3, 4):
                if y == 3 and t in (5, 10):
                    hall.place(at(t, y), "minecraft:light_blue_terracotta")
                else:
                    hall.place(at(t, y), palette["wall"], axis="y")


def book_canopy(hall, palette):
    # The deck closes the room beneath the sculptural book roof.
    for z in range(-1, 17):
        for x in range(-1, 17):
            if x in (-1, 16) or z in (-1, 16):
                hall.place((x, 6, z), palette["book_edge_slab"], **slab("bottom"))
            else:
                perimeter = x in (0, SIZE - 1) or z in (0, SIZE - 1)
                tile = palette["roof_border"] if perimeter else palette["roof_deck"]
                hall.place((x, 6, z), tile)

    for x in range(1, 15):
        left_page = x < 8
        rise = (7 - x) if left_page else (x - 8)
        height = 8 + rise // 2
        facing = "west" if left_page else "east"
        line_rows = (4, 7, 10) if left_page else (5, 8, 11)
        shape = "stairs" if rise % 2 else "slab"
        properties = stairs(facing) if rise % 2 else slab("bottom")
        for z in range(2, 14):
            edge = x in (1, 14) or z in (2, 13)
            line = z in line_rows and 1 <= rise <= (3 if z % 2 else 4)
            if edge:
                material = palette[f"book_edge_{shape}"]
            elif x == 7:
                material = "minecraft:purpur_slab"
            elif line:
                material = palette[f"page_line_{shape}"]
            else:
                material = f"minecraft:smooth_quartz_{shape}"
            for y in range(7, height):
                fill = palette["book_base"] if y == 7 else "minecraft:smooth_quartz"
                hall.place((x, y, z), fill)
            hall.place((x, height, z), material, **properties)
    for z in (0, 1):
        hall.place((7, 7, z), "minecraft:cherry_planks")
        hall.place((7, 8, z), "minecraft:cherry_slab", **slab("bottom"))
    for x in (3, 12):
        for z in (1, 14):
            hall.place((x, 7, z), "minecraft:lantern", hanging="false", waterlogged="false")


def library_room(hall, palette):
    tx, tz = TABLE_XZ
    hall.place((tx, 1, tz), "minecraft:enchanting_table")
    for dz in range(-2, 3):
        for dx in range(-2, 3):
            if max(abs(dx), abs(dz)) != 2 or dx == 0 or dz == 0:
                continue
            hall.place((tx + dx, 1, tz + dz), "minecraft:bookshelf")
    for dx, dz in ((-2, 2), (2, 2), (1, 2)):
        hall.place((tx + dx, 2, tz + dz), "minecraft:bookshelf")

    for x in (3, 12):
        for z in (3, 12):
            hall.place((x, 5, z), "minecraft:lantern", hanging="true", waterlogged="false")
    for x in (5, 6, 8, 9):
        for z in (2, 3, 11, 12, 13):
            color = "light_gray" if x in (5, 9) or z in (2, 13) else "purple"
            hall.place((x, 1, z), f"minecraft:{color}_carpet")
    for x in (2, 13):
        for z in (11, 12, 13):
            for y in (1, 2):
                hall.place((x, y, z), palette["shelves"])
    for (x, z), facing in (((3, 3), "west"), ((12, 3), "east")):
        table = palette["table"]
        legs = {f"table_leg{i}": "true" for i in range(1, 5)} if table.startswith("twigs:") else {}
        hall.place((x, 1, z), table, **legs)
        hall.place((x, 2, z), "minecraft:lantern", hanging="false", waterlogged="false")
        chair = palette["chair"]
        properties = dict(CHAIR_STATE, facing=facing) if chair.startswith("handcrafted:") else stairs(facing)
        hall.place((x + (1 if x < 8 else -1), 1, z), chair, **properties)
    hall.place((10, 1, 11), "minecraft:lectern", facing="north", has_book="false", powered="false")
    for x, z in ((1, 8), (14, 7)):
        hall.place((x, 1, z), "minecraft:barrel", facing="up", open="false")
        hall.place((x, 2, z), palette["potted_sapling"])


def plant_borders(hall, palette):
    # Plant in short drifts along the moss strips between the stone approaches.
    flower_beds = {
        "lavender": (
            (2, -2), (3, -2), (4, -1), (10, -2), (12, -2), (13, -2),
            (2, 17), (4, 16), (5, 17), (10, 17), (11, 16), (13, 17),
            (-2, 2), (-2, 4), (-1, 5), (-2, 10), (-2, 12), (-1, 14),
            (17, 2), (17, 5), (16, 6), (16, 10), (17, 12), (17, 14),
        ),
        "white_lavender": (
            (4, -2), (11, -2), (4, 17), (12, 17),
            (-2, 3), (-2, 11), (17, 4), (17, 13),
        ),
    }
    for material, positions in flower_beds.items():
        for x, z in positions:
            hall.place((x, 1, z), palette[material])

    petals = (
        (0, -2), (1, -1), (5, -2), (6, -1), (9, -1), (15, -2),
        (0, 17), (1, 16), (6, 17), (9, 16), (14, 16), (15, 17),
        (-1, 1), (-2, 5), (-1, 6), (-2, 9), (-1, 11), (-2, 15),
        (16, 1), (17, 3), (17, 6), (17, 9), (16, 11), (17, 16),
    )
    for index, (x, z) in enumerate(petals):
        facing, _ = FACES[index % len(FACES)]
        hall.place((x, 1, z), "minecraft:pink_petals", facing=facing, flower_amount=str(2 + index % 3))


def generate_library(palette: dict[str, str] | None = None) -> Building:
    palette = LIBRARY_VANILLA | (palette or {})
    library = Building("Blue grove enchanting library")
    place_ground(library, palette)
    for facing, edge in FACES:
        facade(library, facing, edge, palette)
    book_canopy(library, palette)
    library_room(library, palette)
    plant_borders(library, palette)
    return library

