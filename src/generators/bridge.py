"""Seven-wide walkway over a three-arch masonry bridge."""

from src.core.building import Building
from src.generators.medieval import slab, stairs


WIDTH = 9
LENGTH = 25
DECK_Y = 3
RAILS = (0, WIDTH - 1)
ARCHES = ((2, 6), (9, 15), (18, 22))
CROWNS = tuple((start + end) // 2 for start, end in ARCHES)
POSTS = (0, 7, CROWNS[1], 17, LENGTH - 1)
RAIL_STATE = dict(north="low", south="low", east="none", west="none", up="false", waterlogged="false")

BRIDGE_VANILLA = {
    "masonry": "minecraft:stone_bricks",
    "vault_stairs": "minecraft:stone_brick_stairs",
    "vault_slab": "minecraft:stone_brick_slab",
    "deck_border": "minecraft:polished_andesite",
    "rail_wall": "minecraft:stone_brick_wall",
}
BRIDGE_MODDED = {
    "masonry": ("quark:shale_bricks", [{}]),
    "vault_stairs": ("quark:shale_bricks_stairs", [stairs(facing) | {"half": "top"} for facing in ("north", "south")]),
    "vault_slab": ("quark:shale_bricks_slab", [slab("top")]),
    "deck_border": ("quark:polished_shale", [{}]),
    "rail_wall": ("quark:calcite_bricks_wall", [RAIL_STATE]),
}


def build_vault(bridge, palette, start, end):
    crown = (start + end) // 2
    for z in range(start, end + 1):
        height = min(z - start, end - z)
        if height == DECK_Y:
            # The main arch closes at the underside of the deck itself.
            continue
        at_crown = z == crown
        facing = "north" if z < crown else "south"
        for x in range(WIDTH):
            if at_crown:
                material = "minecraft:smooth_quartz_slab" if x in RAILS else palette["vault_slab"]
                bridge.place((x, height, z), material, **slab("top"))
            else:
                material = "minecraft:smooth_quartz_stairs" if x in RAILS else palette["vault_stairs"]
                bridge.place((x, height, z), material, **(stairs(facing) | {"half": "top"}))
            for y in range(height + 1, DECK_Y):
                bridge.place((x, y, z), palette["masonry"])


def build_base(bridge, palette):
    openings = {z for start, end in ARCHES for z in range(start, end + 1)}
    for z in range(LENGTH):
        if z in openings:
            continue
        for x in range(WIDTH):
            for y in range(-3, DECK_Y):
                footing_edge = x in RAILS and y == -1
                material = "minecraft:polished_andesite" if footing_edge else palette["masonry"]
                bridge.place((x, y, z), material)
    for start, end in ARCHES:
        build_vault(bridge, palette, start, end)


def build_deck(bridge, palette):
    for z in range(LENGTH):
        for x in range(WIDTH):
            if x in RAILS:
                material = "minecraft:chiseled_quartz_block" if z in CROWNS else "minecraft:smooth_quartz"
            elif x in (1, 7):
                material = palette["deck_border"]
            elif x == 4 or (z in (0, 7, 17, LENGTH - 1) and x in (3, 5)):
                material = "minecraft:quartz_bricks"
            else:
                material = "minecraft:polished_diorite"
            bridge.place((x, DECK_Y, z), material)


def build_guardrails(bridge, palette, *, deck_y=DECK_Y):
    rail_y = deck_y + 1
    for x in RAILS:
        for z in range(LENGTH):
            if z in POSTS:
                bridge.place((x, rail_y, z), "minecraft:quartz_pillar", axis="y")
                if z == CROWNS[1]:
                    bridge.place((x, rail_y + 1, z), "minecraft:smooth_quartz_slab", **slab("bottom"))
                else:
                    bridge.place((x, rail_y + 1, z), "minecraft:soul_lantern",
                                 hanging="false", waterlogged="false")
            else:
                bridge.place((x, rail_y, z), palette["rail_wall"], **RAIL_STATE)


def generate_bridge(palette: dict[str, str] | None = None) -> Building:
    palette = BRIDGE_VANILLA | (palette or {})
    bridge = Building("Three-arch stone bridge")
    build_base(bridge, palette)
    build_deck(bridge, palette)
    build_guardrails(bridge, palette)
    return bridge

