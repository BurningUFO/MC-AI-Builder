"""A decorated single-layer deck with straight, submerged supports."""

from src.core.building import Building
from src.generators.bridge import BRIDGE_MODDED, BRIDGE_VANILLA, LENGTH, POSTS, WIDTH, build_guardrails


DECK_Y = 0
PIER_BOTTOM = -4
PIER_Z = (2, 7, 12, 17, 22)
PIER_X = (3, 4, 5)
EDGE_LIGHT_Z = (8, 16)
FLAT_BRIDGE_VANILLA = {key: BRIDGE_VANILLA[key] for key in ("masonry", "deck_border", "rail_wall")}
FLAT_BRIDGE_MODDED = {key: BRIDGE_MODDED[key] for key in FLAT_BRIDGE_VANILLA}


def generate_flat_bridge(palette: dict[str, str] | None = None) -> Building:
    palette = FLAT_BRIDGE_VANILLA | (palette or {})
    bridge = Building("Low flat bridge on submerged piers")
    for z in PIER_Z:
        for x in PIER_X:
            for y in range(PIER_BOTTOM, DECK_Y):
                bridge.place((x, y, z), palette["masonry"])

    center = WIDTH // 2
    for z in range(LENGTH):
        for x in range(WIDTH):
            if x in (0, WIDTH - 1):
                material = "minecraft:sea_lantern" if z in EDGE_LIGHT_Z else "minecraft:smooth_quartz"
            elif x == center or z in (0, LENGTH - 1) or (z in POSTS and abs(x - center) == 1):
                material = "minecraft:quartz_bricks"
            elif x in (1, WIDTH - 2):
                material = palette["deck_border"]
            else:
                material = "minecraft:polished_diorite"
            bridge.place((x, DECK_Y, z), material)
    build_guardrails(bridge, palette, deck_y=DECK_Y)
    return bridge

