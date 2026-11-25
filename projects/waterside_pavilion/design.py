"""Build Chengyi Pavilion, a two-storey waterside belvedere in Minecraft blocks."""

from src.core.building import Building


STONE = "twigs:polished_tuff_bricks"
SILL = "minecraft:polished_andesite"
MEDALLION = "minecraft:chiseled_stone_bricks"
POST = "minecraft:stripped_mangrove_log"
WOOD = "minecraft:spruce_planks"
DARK = "minecraft:dark_oak_planks"
BEAM = "minecraft:stripped_dark_oak_log"
TILE = "quark:cyan_shingles"
RIDGE = "minecraft:polished_deepslate"
CELADON = "minecraft:dark_prismarine"
TRAPDOOR = "minecraft:mangrove_trapdoor"
MORTAR = "minecraft:smooth_sandstone"
UPPER_LINTEL = 12
DIRS = {"east": (1, 0), "west": (-1, 0), "south": (0, 1), "north": (0, -1)}
HALL_POSTS = {(x, z) for x in (-10, -6, -2, 2, 6, 10) for z in (-6, 6)}
HALL_POSTS |= {(x, z) for x in (-10, 10) for z in (-2, 2)}
GALLERY_POSTS = {(x, z) for x in (-6, -2, 2, 6) for z in (-4, 4)}
GALLERY_POSTS |= {(-6, z) for z in (-1, 1)}
LANDING = {(x, z) for x in (6, 7) for z in (2, 3, 4)}
STAIR_OPENING = {(x, z) for x in (6, 7) for z in range(-1, 3)}


class Pavilion:
    def __init__(self):
        self.building = Building("澄漪阁 · Chengyi Pavilion")
        self.parts = {}

    def set(self, label, x, y, z, block, **state):
        self.building.place((x, y, z), block, **state)
        self.parts[x, y, z] = label

    def log(self, label, x, y, z, block=BEAM, axis="y"):
        self.set(label, x, y, z, block, axis=axis)

    def stair(self, label, x, y, z, block, facing, half="bottom"):
        self.set(label, x, y, z, block, facing=facing, half=half, shape="straight", waterlogged="false")

    def slab(self, label, x, y, z, block, half="bottom"):
        self.set(label, x, y, z, block, type=half, waterlogged="false")

    def fence(self, label, points, y, anchors):
        connected = points | anchors
        for x, z in points:
            connections = {name: "true" if (x + dx, z + dz) in connected else "false"
                           for name, (dx, dz) in DIRS.items()}
            self.set(label, x, y, z, "minecraft:dark_oak_fence", **connections, waterlogged="false")

    def lantern(self, label, x, y, z, *, hanging):
        self.set(label, x, y, z, "minecraft:lantern", hanging=str(hanging).lower(), waterlogged="false")

    def screen(self, label, x, y, z, facing, block=TRAPDOOR):
        self.set(label, x, y, z, block, facing=facing, open="true", half="bottom",
                 powered="false", waterlogged="false")


def chamfered(rx, rz, cut):
    return {(x, z) for x in range(-rx, rx + 1) for z in range(-rz, rz + 1)
            if abs(x) + abs(z) <= rx + rz - cut}


def perimeter(points):
    return {(x, z) for x, z in points if any((x + dx, z + dz) not in points for dx, dz in DIRS.values())}


def base(pavilion):
    deck = chamfered(14, 11, 2)
    landing = {(x, z) for x in range(-4, 5) for z in range(-14, -10)}
    jetty = {(x, z) for x in range(-11, -8) for z in range(-16, -10)}
    jetty |= {(x, z) for x in range(-12, -5) for z in range(-17, -14)}
    deck |= landing | jetty
    edge = perimeter(deck)
    skirt = "01 Water terrace/Underframe"
    for x, z in deck:
        board = SILL if abs(x) <= 1 and z >= 6 else DARK if (x, z) in edge else WOOD
        pavilion.set("01 Water terrace/Boardwalk", x, 0, z, board)
        if (x, z) in edge or (z in (-8, 0, 8) and abs(x) <= 12):
            pavilion.log(skirt, x, -1, z, axis="x" if abs(z) >= abs(x) else "z")

    pier_grid = HALL_POSTS | {(x, z) for x in (-13, 13) for z in (-8, 0, 8)}
    pier_grid |= {(-4, -13), (4, -13), (0, -13), (-11, -16), (-9, -14), (-7, -16),
                  (-12, 10), (12, 10), (0, 8), (0, 0), (0, -8)}
    for x, z in pier_grid:
        pavilion.set("01 Water terrace/Stone and timber piers", x, -3, z, STONE)
        pavilion.set("01 Water terrace/Stone and timber piers", x, -2, z, MEDALLION if z <= -13 else STONE)
        pavilion.log("01 Water terrace/Stone and timber piers", x, -1, z)
    for x in range(-3, 4):
        pavilion.set("01 Water terrace/Shore steps", x, -3, 15, STONE)
        pavilion.set("01 Water terrace/Shore steps", x, -2, 14, STONE)
        pavilion.set("01 Water terrace/Shore steps", x, -2, 13, STONE)
        pavilion.stair("01 Water terrace/Shore steps", x, -2, 15, "minecraft:polished_andesite_stairs", "north")
        pavilion.stair("01 Water terrace/Shore steps", x, -1, 14, "minecraft:polished_andesite_stairs", "north")
        pavilion.set("01 Water terrace/Shore steps", x, -1, 13, SILL)
        pavilion.set("01 Water terrace/Shore steps", x, -1, 12, STONE)
        pavilion.stair("01 Water terrace/Shore steps", x, 0, 12, "minecraft:polished_andesite_stairs", "north")

    dock_exit = {(x, -17) for x in range(-10, -7)}
    side_exits = {(x, 11) for x in range(-4, 5)}
    bollards = {(x, z) for x, z in edge
                if (abs(x) in (4, 14) and z % 4 == 0) or (z < -10 and x in (-12, -6, -4, 4))}
    rails = edge - side_exits - dock_exit - bollards
    for x, z in bollards:
        pavilion.set("01 Water terrace/Rail posts", x, 1, z, MEDALLION)
        pavilion.lantern("01 Water terrace/Landing lanterns", x, 2, z, hanging=False)
    pavilion.fence("01 Water terrace/Railings", rails, 1, bollards)
    for x, z in ((-12, -5), (12, -5), (-12, 3), (12, 3), (-3, -12), (3, -12)):
        pavilion.slab("01 Water terrace/Rest benches", x, 1, z, "minecraft:spruce_slab")
    for x, z in ((-11, 8), (11, 8), (-5, -11), (5, -11)):
        pavilion.set("01 Water terrace/Planters", x, 1, z, MORTAR)
        pavilion.set("01 Water terrace/Planters", x, 2, z, "minecraft:potted_bamboo")


def timber_frame(pavilion, points, span_x, span_z, foot, lintel, label):
    for x, z in points:
        pavilion.set(label + "/Plinths", x, foot, z, SILL)
        for y in range(foot + 1, lintel):
            pavilion.log(label + "/Columns", x, y, z, POST)
        pavilion.set(label + "/Bracket blocks", x, lintel, z, "minecraft:mangrove_planks")

    for z in (-span_z, span_z):
        for x in range(-span_x, span_x + 1):
            if (x, z) not in points:
                pavilion.log(label + "/Tie beams", x, lintel, z, axis="x")
    for x in (-span_x, span_x):
        for z in range(-span_z + 1, span_z):
            if (x, z) not in points:
                pavilion.log(label + "/Tie beams", x, lintel, z, axis="z")

    for x, z in points:
        arms = [(name, dx, dz) for name, (dx, dz) in DIRS.items()
                if (dx and x == dx * span_x) or (dz and z == dz * span_z)]
        for facing, dx, dz in arms:
            pavilion.stair(label + "/Layered brackets", x + dx, lintel - 1, z + dz,
                           "minecraft:mangrove_stairs", facing, "top")
            pavilion.log(label + "/Layered brackets", x + dx, lintel, z + dz, POST,
                         axis="x" if dx else "z")
            pavilion.slab(label + "/Layered brackets", x + 2 * dx, lintel, z + 2 * dz,
                          "minecraft:dark_oak_slab", "top")
            pavilion.set(label + "/Celadon beam ends", x + dx, lintel + 1, z + dz, CELADON)


def lower_hall(pavilion):
    timber_frame(pavilion, HALL_POSTS, 10, 6, 1, 5, "02 Lower hall")
    for z in (-6, 6):
        facing = "north" if z < 0 else "south"
        for x in range(-9, 10):
            if (x, z) in HALL_POSTS or abs(x) < 2:
                continue
            pavilion.set("02 Lower hall/Screen sills", x, 1, z, MORTAR)
            for y in (2, 3):
                material = "minecraft:bamboo_trapdoor" if x % 4 == 0 else TRAPDOOR
                pavilion.screen("02 Lower hall/Lattice panels", x, y, z, facing, material)
            pavilion.log("02 Lower hall/Screen headers", x, 4, z, POST, "x")

    for x in (-10, 10):
        facing = "west" if x < 0 else "east"
        for z in range(-5, 6):
            if (x, z) in HALL_POSTS or abs(z) < 2:
                continue
            pavilion.set("02 Lower hall/Screen sills", x, 1, z, MORTAR)
            for y in (2, 3):
                material = "minecraft:bamboo_trapdoor" if z % 4 == 0 else TRAPDOOR
                pavilion.screen("02 Lower hall/Lattice panels", x, y, z, facing, material)
            pavilion.log("02 Lower hall/Screen headers", x, 4, z, POST, "z")

    for x in range(-9, 10):
        for z in range(-5, 6):
            if abs(x) <= 1:
                pavilion.set("02 Lower hall/Floor pattern", x, 0, z, SILL)
            elif abs(x) in (2, 9) or abs(z) == 5:
                pavilion.set("02 Lower hall/Floor pattern", x, 0, z, DARK)

    for x in (-7, -6, -5):
        for z in (0, 1):
            pavilion.set("02 Lower hall/Tea tables", x, 1, z, "twigs:spruce_table",
                         **{f"table_leg{i}": "true" for i in range(1, 5)})
    for x in (-7, -5):
        pavilion.lantern("02 Lower hall/Table lanterns", x, 2, 0, hanging=False)
        for z, facing in ((-2, "south"), (3, "north")):
            pavilion.set("02 Lower hall/Tea benches", x, 1, z, "handcrafted:spruce_bench",
                         facing=facing, color="none", shape="single", waterlogged="false")
    for x in (-8, -7, -6, 3, 4):
        pavilion.set("02 Lower hall/Bookkeeping shelves", x, 1, -5, "quark:dark_oak_bookshelf")
        pavilion.set("02 Lower hall/Bookkeeping shelves", x, 2, -5, "quark:dark_oak_bookshelf")
    for x in (4, 5, 6):
        pavilion.set("02 Lower hall/Boat gear", x, 1, 5, "minecraft:barrel", facing="north", open="false")
    for x in (-6, -3, 3, 6):
        for z in (-7, 7):
            pavilion.log("02 Lower hall/Eave lantern beams", x, 5, z, POST, "z")
            pavilion.lantern("02 Lower hall/Eave lanterns", x, 4, z, hanging=True)


def upper_gallery(pavilion, gallery):
    edge = perimeter(gallery)
    for x, z in gallery - STAIR_OPENING:
        timber = DARK if (x, z) in edge else "quark:vertical_spruce_planks"
        pavilion.set("03 Upper gallery/Floor", x, 7, z, timber)
    timber_frame(pavilion, GALLERY_POSTS, 6, 4, 8, UPPER_LINTEL, "03 Upper gallery")
    for x in (-6, -2, 2, 6):
        for z in range(-3, 4):
            pavilion.log("03 Upper gallery/Transverse roof beams", x, UPPER_LINTEL, z, BEAM, "z")
    rail = edge - LANDING - GALLERY_POSTS
    rail |= {(5, z) for z in range(-1, 2)}
    pavilion.fence("03 Upper gallery/Balustrade", rail, 8, GALLERY_POSTS)

    for x in (-6, 6):
        for z in (-3, 3):
            if (x, z) in LANDING:
                continue
            for y in (8, 9):
                pavilion.screen("03 Upper gallery/Wind screens", x, y, z, "east" if x > 0 else "west")
    for x in (-4, 0, 4):
        for z, facing in ((-2, "north"), (2, "south")):
            pavilion.set("03 Upper gallery/Seats", x, 8, z, "handcrafted:spruce_bench",
                         facing=facing, color="none", shape="single", waterlogged="false")
    for x, z in ((-4, -3), (4, -3), (-4, 3), (4, 3)):
        pavilion.set("03 Upper gallery/Planters", x, 8, z, "twigs:spruce_table",
                     **{f"table_leg{i}": "true" for i in range(1, 5)})
        pavilion.set("03 Upper gallery/Planters", x, 9, z, "minecraft:potted_bamboo")
    for x in (-4, 0, 4):
        for z in (-4, 4):
            pavilion.lantern("03 Upper gallery/Lighting", x, UPPER_LINTEL - 1, z, hanging=True)
    for x in (6, 7):
        for z in range(-4, 3):
            y = z + 5
            if y > 1:
                pavilion.log("04 East stair/Stringers", x, y - 1, z, BEAM, "z")
            pavilion.stair("04 East stair/Treads", x, y, z, "minecraft:spruce_stairs", "south")


def roof_domain(rx, rz):
    for x in range(-rx - 1, rx + 2):
        for z in range(-rz - 1, rz + 2):
            ax, az = abs(x), abs(z)
            if (ax <= rx and az <= rz) or (ax >= rx - 1 and az >= rz - 1 and ax + az <= rx + rz + 1):
                yield x, z


def corner_kick(x, z, rx, rz):
    # The last five courses flare out and lift toward each diagonal corner.
    u = max(0, min(5, abs(x) - rx + 5)) / 5
    v = max(0, min(4, abs(z) - rz + 4)) / 4
    return 4.2 * u * u * v * v


def lower_roof(gallery):
    heights = {}
    for x, z in roof_domain(13, 9):
        if (x, z) in gallery:
            continue
        inset = max(0, min(13 - abs(x), 9 - abs(z)))
        heights[x, z] = 13 + round(0.52 * inset + 0.12 * inset ** 2 + corner_kick(x, z, 13, 9))
    return heights


def upper_roof():
    heights = {}
    for x, z in roof_domain(11, 8):
        ax, az = abs(x), abs(z)
        slope = 13 * (max(0, 8 - az) / 8) ** 1.65
        if ax >= 8:
            # The lower hips close at the ends below the upright gable.
            slope = min(slope, 5 * max(0, 11 - ax) / 3)
        heights[x, z] = 2 * UPPER_LINTEL + 3 + round(slope + corner_kick(x, z, 11, 8))
    return heights


def tile_roof(pavilion, heights, label, rx, rz, hip_run):
    for (x, z), top in heights.items():
        edge = {side: heights[x + dx, z + dz] for side, (dx, dz) in DIRS.items()
                if (x + dx, z + dz) in heights}
        y = (top - 1) // 2
        base_y = min((value - 1) // 2 for value in (*edge.values(), top))
        # Adjacent courses share a layer, including the raised corner extensions.
        for under in range(base_y, y):
            pavilion.set(label + "/Backing courses", x, under, z, TILE)
        ax, az = abs(x), abs(z)
        hip = ax == rx - round(hip_run * max(0, rz - az) / rz)
        rim = ax >= rx or az >= rz
        material = RIDGE if hip or rim else TILE
        group = label + "/Hip ridges and fascia" if hip or rim else label + "/Tile courses"
        facing = max(edge, key=edge.get, default="north")
        if top % 2:
            pavilion.slab(group, x, y, z, material + "_slab")
        elif edge.get(facing, 0) > top:
            pavilion.stair(group, x, y, z, material + "_stairs", facing)
        else:
            pavilion.set(group, x, y, z, material)


def ridge_and_gables(pavilion, heights):
    ridge_y = heights[0, 0] // 2
    for side, facing in ((-1, "west"), (1, "east")):
        for z in range(-6, 7):
            x = 7 * side
            lower = heights[8 * side, z] // 2
            upper = (heights[x, z] - 1) // 2
            for y in range(lower, upper):
                at_border = y in (lower, upper - 1) or z == 0
                pavilion.set("06 Upper roof/Gable infill", x, y, z, DARK if at_border else MORTAR)
            if upper - lower >= 3 and abs(z) == 1:
                pavilion.screen("06 Upper roof/Gable lattice", x, lower + 1, z, facing)
        pavilion.stair("06 Upper roof/Curled ridge ends", 7 * side, ridge_y, 0,
                       "minecraft:polished_deepslate_stairs", facing)
        pavilion.slab("06 Upper roof/Curled ridge ends", 7 * side, ridge_y + 1, 0,
                      "minecraft:dark_prismarine_slab")
        pavilion.slab("06 Upper roof/Curled ridge ends", 6 * side, ridge_y + 1, 0,
                      "minecraft:polished_deepslate_slab")
        pavilion.set("06 Upper roof/Ridge", 6 * side, ridge_y, 0, RIDGE)
    for x in range(-5, 6):
        pavilion.slab("06 Upper roof/Ridge", x, ridge_y, 0, "minecraft:polished_deepslate_slab")
    pavilion.set("06 Upper roof/Ridge", 0, ridge_y, 0, CELADON)
    pavilion.slab("06 Upper roof/Ridge", 0, ridge_y + 1, 0, "minecraft:dark_prismarine_slab")


def build():
    pavilion = Pavilion()
    gallery = chamfered(8, 5, 1)
    base(pavilion)
    lower_hall(pavilion)
    upper_gallery(pavilion, gallery)
    tile_roof(pavilion, lower_roof(gallery), "05 Lower roof", 13, 9, 8)
    heights = upper_roof()
    ridge_and_gables(pavilion, heights)
    tile_roof(pavilion, heights, "06 Upper roof", 11, 8, 5)
    return pavilion.building, pavilion.parts
