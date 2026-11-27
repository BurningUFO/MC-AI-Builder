"""East satellite pavilion study. Local Y=0 is the lower orbit's block course."""
from src.core.building import Building


QUARTZ = "minecraft:smooth_quartz"
PILLAR = "minecraft:quartz_pillar"
SLAB = "minecraft:quartz_slab"
ICE = "minecraft:packed_ice"
MINT = "minecraft:prismarine"
LAMP = "minecraft:sea_lantern"
ROD = "minecraft:end_rod"
DIRECTIONS = ((1, 0, "east"), (-1, 0, "west"), (0, 1, "south"), (0, -1, "north"))
WALL = dict(north="none", east="none", south="none", west="none", up="true", waterlogged="false")
FENCE = dict(north="false", east="false", south="false", west="false", waterlogged="false")


def build(*, deck_radius=3, deck_y=8, inner_radius=2, tail_length=17,
          eave_lifts=(3, 2, 3, 1)):
    """Eave lifts are half-blocks above deck_y, at successive radii from the rim."""
    if not 1 < inner_radius < deck_radius or len(eave_lifts) < 3 or tail_length < 13:
        raise ValueError("The inner arms must fit under the deck and leave room for both drops and the tail.")
    b = Building("天空即为极限 · 东花亭")
    parts = {}

    def put(part, x, y, z, block=QUARTZ, **state):
        b.place((x, y, z), block, **state)
        parts[x, y, z] = part

    def slab(part, x, y, z, half="bottom"):
        put(part, x, y, z, SLAB, type=half, waterlogged="false")

    def drop(part, x, top_y, z, tip):
        put(part, x, top_y, z, "minecraft:prismarine_wall", **WALL)
        put(part, x, top_y - 1, z, "minecraft:warped_fence", **FENCE)
        if tip:
            put(part, x, top_y - 2, z, ROD, facing="down")

    # The ring hides part of the shaft in the cover. R7C5–8 exposes its light
    # bands and the single narrow stem below the four short outer piers.
    stem_bottom = 3 - tail_length
    for y in range(stem_bottom, deck_y):
        if y == stem_bottom + 4:
            put("Shaft/Stem", 0, y, 0, LAMP)
        else:
            put("Shaft/Stem", 0, y, 0, PILLAR, axis="y")
    put("Shaft/Finial", 0, stem_bottom - 1, 0, "minecraft:diorite_wall", **WALL)
    put("Shaft/Finial", 0, stem_bottom - 2, 0, "minecraft:warped_fence", **FENCE)
    put("Shaft/Finial", 0, stem_bottom - 3, 0, ROD, facing="down")

    for dx, dz, facing in DIRECTIONS:
        for y in range(-5, deck_y - 5):
            if y in (-4, 0):
                put("Shaft/Light bands", dx, y, dz, LAMP)
            else:
                put("Shaft/Piers", dx, y, dz, PILLAR, axis="y")
        slab("Shaft/Foot", dx, -6, dz, "top")
        put("Shaft/Neck lights", dx, deck_y - 5, dz, ROD, facing="up")
        # The lower corbels step out from the shaft to the four exposed lamps.
        for reach in range(1, inner_radius):
            y = deck_y - 3 + reach - inner_radius
            put("Bracket/Arms", reach*dx, y + 1, reach*dz)
            put("Bracket/Arms", reach*dx, y, reach*dz, "minecraft:quartz_stairs",
                facing=facing, half="top", shape="straight", waterlogged="false")
        put("Bracket/Lamp pins", inner_radius*dx, deck_y - 2, inner_radius*dz, LAMP)
        drop("Drops/Inner", inner_radius*dx, deck_y - 3, inner_radius*dz, tip=False)

    # R8C2 exposes four square wells between intersecting rails. R8C4 covers
    # them with a single stepped blue/white field; do not fill the wells below it.
    for x in range(-deck_radius, deck_radius + 1):
        for z in range(-deck_radius, deck_radius + 1):
            distance = abs(x) + abs(z)
            if distance > deck_radius:
                continue
            if abs(x) != 1 or abs(z) != 1:
                put("Bracket/Lattice", x, deck_y - 1, z)
            put("Deck/Blue and white course", x, deck_y, z,
                QUARTZ if distance < inner_radius else ICE)

    # An inset square pad remains above the lower diamond in R8C7.
    for x in (-1, 0, 1):
        for z in (-1, 0, 1):
            tile = ICE if x and z else MINT if x or z else QUARTZ
            put("Deck/Raised pad", x, deck_y + 1, z, tile)

    outer_radius = deck_radius + len(eave_lifts) - 2
    for dx, dz, facing in DIRECTIONS:
        for reach, lift in enumerate(eave_lifts, deck_radius):
            y, parity = divmod(2*deck_y + lift - 1, 2)
            x, z = reach*dx, reach*dz
            if reach == outer_radius:
                put("Eaves/Knee", x, y - 1, z, "minecraft:quartz_stairs",
                    facing=facing, half="top", shape="straight", waterlogged="false")
                drop("Drops/Outer", x, y - 2, z, tip=True)
            slab("Eaves/White blades", x, y, z, "top" if parity else "bottom")
        for side in (-1, 1):
            # Stagger the short flanking plates, leaving daylight between tips.
            slab("Eaves/Shoulders", deck_radius*dx + side*dz, deck_y,
                 deck_radius*dz + side*dx, "top")

    # White diagonal noses overlap the blue fascia in the finished side view.
    # The connected ice course behind them remains visible during construction.
    for x in (-inner_radius, inner_radius):
        for z in (-inner_radius, inner_radius):
            put("Eaves/White noses", x, deck_y, z)
            slab("Eaves/White noses", x, deck_y - 1, z, "top")

    lamp_y = deck_y + 3
    put("Lantern/Foot", 0, lamp_y - 1, 0)
    put("Lantern/Light", 0, lamp_y, 0, LAMP)
    slab("Lantern/Hat", 0, lamp_y + 1, 0)
    for dx, dz, _ in DIRECTIONS:
        slab("Lantern/Foot", dx, lamp_y - 1, dz)
        slab("Lantern/Hat", dx, lamp_y, dz, "top")
    return b, parts
