"""Eight installed blocks in a projecting timber-and-stone bracket."""
from src.core.building import Building


def build():
    b = Building("Control")
    b.place((0, 0, 0), "minecraft:smooth_sandstone")
    b.place((0, 1, 0), "minecraft:chiseled_sandstone")
    b.place((0, 1, 1), "minecraft:smooth_sandstone_stairs",
            facing="north", half="top", shape="straight")
    for z in (0, 1):
        b.place((0, 2, z), "minecraft:oak_log", axis="z")
        b.place((0, 3, z), "minecraft:smooth_sandstone_slab", type="bottom")
    b.place((1, 2, 1), "minecraft:oak_trapdoor", facing="east", half="bottom", open="true")
    return b, {(1, 2, 1): "Panel"}
