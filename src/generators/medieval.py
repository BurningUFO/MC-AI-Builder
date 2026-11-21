"""A two-storey half-timbered house with the front facing Minecraft north."""

from src.core.building import Building

WIDTH, DEPTH = 16, 12
UPPER_FLOOR, EAVES = 5, 9

VANILLA = {
    "stone": "minecraft:stone_bricks",
    "planks": "minecraft:spruce_planks",
    "plaster": "minecraft:white_terracotta",
    "brace": "minecraft:stripped_dark_oak_wood",
    "frame": "minecraft:stripped_dark_oak_wood",
    "rafter": "minecraft:dark_oak_log",
    "tiles": "minecraft:bricks",
    "roof_stairs": "minecraft:brick_stairs",
    "roof_slab": "minecraft:brick_slab",
    "shelves": "minecraft:bookshelf",
    "table": "minecraft:crafting_table",
    "chair": "minecraft:spruce_stairs",
}

DIRECTIONS = {"north": (0, -1), "east": (1, 0), "south": (0, 1), "west": (-1, 0)}
TIMBER = "minecraft:stripped_dark_oak_log"
STAIR_X = (12, 13)
STAIR_OPEN_Z = range(4, 7)


def stairs(facing: str) -> dict[str, str]:
    return {"facing": facing, "half": "bottom", "shape": "straight", "waterlogged": "false"}


def slab(kind: str = "top") -> dict[str, str]:
    return {"type": kind, "waterlogged": "false"}


def horizontal_connections(directions) -> dict[str, str]:
    return {direction: str(direction in directions).lower() for direction in DIRECTIONS} | {"waterlogged": "false"}


def foundation_and_walls(house: Building, palette: dict[str, str]) -> None:
    for z in range(DEPTH):
        for x in range(WIDTH):
            perimeter = x in (0, WIDTH - 1) or z in (0, DEPTH - 1)
            wall_axis = "x" if z in (0, DEPTH - 1) else "z"
            post = x in (0, 5, 10, 15) if wall_axis == "x" else z in (4, 7)
            floor = palette["planks"] if z in (1, 10) else "minecraft:spruce_planks"
            house.place((x, 0, z), palette["stone"] if perimeter else floor)
            if not (x in STAIR_X and z in STAIR_OPEN_Z):
                if perimeter:
                    house.place((x, UPPER_FLOOR, z), TIMBER, axis=wall_axis)
                else:
                    house.place((x, UPPER_FLOOR, z), floor)
            if not perimeter:
                continue

            base = "minecraft:mossy_stone_bricks" if (x + 3 * z) % 11 == 0 else palette["stone"]
            house.place((x, 1, z), base)
            for y in (2, 3, 6, 7):
                if post:
                    house.place((x, y, z), TIMBER, axis="y")
                else:
                    material = palette["plaster"]
                    if y == 7 and (x in (1, 6, 9, 14) if wall_axis == "x" else z in (1, 10)):
                        material = palette["brace"]
                    props = {"axis": "y"} if material == "minecraft:stripped_dark_oak_wood" else {}
                    house.place((x, y, z), material, **props)
            for y in (4, 8):
                house.place((x, y, z), TIMBER, axis=wall_axis)


def doors_and_windows(house: Building) -> None:
    for x, hinge in ((7, "left"), (8, "right")):
        for y, half in ((1, "lower"), (2, "upper")):
            house.place((x, y, 0), "minecraft:spruce_door",
                        facing="north", half=half, hinge=hinge, open="false", powered="false")
        house.place((x, 3, 0), TIMBER, axis="x")
    for x in range(6, 10):
        house.place((x, 0, -1), "minecraft:stone_bricks")
        house.place((x, 0, -2), "minecraft:stone_brick_stairs", **stairs("south"))
        house.place((x, 3, -1), "minecraft:spruce_stairs", **stairs("south"))
    for x in (6, 9):
        house.place((x, 2, -1), "minecraft:lantern", hanging="true", waterlogged="false")

    for z, outside, facing in ((0, -1, "north"), (11, 12, "south")):
        for level in (2, 6):
            window_starts = (2, 12) if level == 2 else (2, 7, 12)
            for start in window_starts:
                for x in (start, start + 1):
                    for y in (level, level + 1):
                        house.place((x, y, z), "minecraft:glass_pane", **horizontal_connections(("west", "east")))
                    house.place((x, level - 1, outside), "minecraft:spruce_slab", **slab())
                if level == 2:
                    for x in (start - 1, start + 2):
                        for y in (level, level + 1):
                            house.place((x, y, outside), "minecraft:spruce_trapdoor",
                                        facing=facing, half="bottom", open="true", powered="false", waterlogged="false")

    for x, outside in ((0, -1), (15, 16)):
        for start in (2, 8):
            for z in (start, start + 1):
                for level in (2, 6):
                    for y in (level, level + 1):
                        house.place((x, y, z), "minecraft:glass_pane", **horizontal_connections(("north", "south")))
                    house.place((outside, level - 1, z), "minecraft:spruce_slab", **slab())


def roof(house: Building, palette: dict[str, str]) -> None:
    for step in range(7):
        for z, facing in ((step - 1, "south"), (DEPTH - step, "north")):
            y = EAVES + step
            for x in range(-1, WIDTH + 1):
                verge = x in (-1, WIDTH)
                if step > 0:
                    if verge:
                        house.place((x, y - 1, z), palette["rafter"], axis="x")
                    else:
                        house.place((x, y - 1, z), palette["tiles"])
                if step == 6:
                    house.place((x, y, z), "minecraft:spruce_slab" if verge else palette["roof_slab"], **slab("bottom"))
                else:
                    house.place((x, y, z), "minecraft:spruce_stairs" if verge else palette["roof_stairs"], **stairs(facing))
    for x in (0, WIDTH - 1):
        for step in range(5):
            for z in range(step, DEPTH - step):
                y = EAVES + step
                if (x, y, z) in house.blocks:
                    continue
                if z in (5, 6) and y in (10, 11):
                    house.place((x, y, z), "minecraft:glass_pane", **horizontal_connections(("north", "south")))
                elif z in (4, 7):
                    house.place((x, y, z), TIMBER, axis="y")
                else:
                    gable = palette["frame"] if y == 9 and z in (3, 8) else palette["plaster"]
                    props = {"axis": "y"} if gable == "minecraft:stripped_dark_oak_wood" else {}
                    house.place((x, y, z), gable, **props)


def interior_stairs(house: Building) -> None:
    for x in STAIR_X:
        for rise in range(5):
            z = 2 + rise
            for y in range(1, rise + 1):
                house.place((x, y, z), "minecraft:spruce_planks")
            house.place((x, 1 + rise, z), "minecraft:spruce_stairs", **stairs("south"))
    rail = {(11, z) for z in range(3, 7)} | {(12, 3), (13, 3)} | {(14, z) for z in range(3, 7)}
    for x, z in sorted(rail):
        connects = [d for d, (dx, dz) in DIRECTIONS.items() if (x + dx, z + dz) in rail]
        if x == 14 and z in STAIR_OPEN_Z:
            connects.append("east")
        house.place((x, 6, z), "minecraft:spruce_fence", **horizontal_connections(connects))


def interiors(house: Building, palette: dict[str, str]) -> None:
    # The fireplace is backed by the back wall, with the flue above its hearth.
    for y in range(1, 17):
        for x in (3, 4):
            house.place((x, y, 10), "minecraft:bricks")
    for x in (3, 4):
        house.place((x, 17, 10), "minecraft:stone_brick_slab", **slab("bottom"))
    house.place((3, 1, 9), "minecraft:furnace", facing="north", lit="false")
    house.place((4, 1, 9), "minecraft:brick_stairs", **stairs("north"))
    for x in (2, 3, 4, 5):
        house.place((x, 2, 9), "minecraft:brick_slab", **slab())
    for x in (3, 4):
        house.place((x, 1, 8), "minecraft:stone_brick_slab", **slab("bottom"))

    for x, z in ((5, 5), (8, 8)):
        house.place((x, 1, z), palette["table"], **(
            {f"table_leg{i}": "true" for i in range(1, 5)}
            if palette["table"].startswith("twigs:") else {}
        ))
        house.place((x, 2, z), "minecraft:lantern", hanging="false", waterlogged="false")
    for x, y, z, facing, color in ((4, 1, 5, "east", "none"), (6, 1, 5, "west", "none"),
                                   (8, 1, 7, "south", "none"), (9, 1, 8, "west", "none"),
                                   (10, 6, 8, "west", "red"), (5, 6, 8, "north", "red")):
        properties = {"facing": facing, "color": color, "waterlogged": "false"}
        if palette["chair"].startswith("minecraft:"):
            properties = stairs(facing)
        house.place((x, y, z), palette["chair"], **properties)

    for x, z in ((2, 8), (9, 10)):
        house.place((x, 1, z), "minecraft:barrel", facing="up", open="false")
    house.place((9, 4, 4), "minecraft:lantern", hanging="true", waterlogged="false")
    house.place((2, 1, 5), "minecraft:crafting_table")
    for x in range(7, 10):
        for z in range(1, 5):
            color = "green" if x == 8 and z in (2, 3) else "brown"
            house.place((x, 1, z), f"minecraft:{color}_carpet")

    for x in range(5, 10):
        for y in (6, 7):
            house.place((x, y, 10), palette["shelves"])
    for x, z in ((2, 8), (10, 10)):
        house.place((x, 6, z), "minecraft:barrel", facing="up", open="false")
        house.place((x, 7, z), "minecraft:lantern", hanging="false", waterlogged="false")
    house.place((9, 6, 8), "minecraft:lectern", facing="east", has_book="false", powered="false")
    for x in range(5, 10):
        for z in range(3, 8):
            border = x in (5, 9) or z in (3, 7)
            accent = x == 7 and z == 5 or (x, z) in {(6, 4), (8, 4), (6, 6), (8, 6)}
            color = "brown" if border else "orange" if accent else "red"
            house.place((x, 6, z), f"minecraft:{color}_carpet")


def generate_house(palette: dict[str, str] | None = None) -> Building:
    palette = VANILLA | (palette or {})
    house = Building("Half-timbered medieval residence")
    foundation_and_walls(house, palette)
    doors_and_windows(house)
    roof(house, palette)
    interior_stairs(house)
    interiors(house, palette)
    return house

