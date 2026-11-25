"""Show the crop canopy, water and stairs in the greenhouse tower reviews."""

import bpy

from src.core.building import Block
from src.generators.grain_tower import (
    BED_COLUMNS, BED_ROWS, CROPS, CUTAWAY, FARMLAND, FLOORS, SEA_LANTERN, SOIL, STAIR_COLUMNS, STAIR_ROWS,
    WATER_COLUMNS, WATER_ROW, footprint,
)
from src.renderers.blender_preview import add_block_mesh, blender_space, collection_for, light


def planted_blocks(building, scene_name):
    selected = building.blocks
    if scene_name == CUTAWAY:
        middle = FLOORS[1]
        interior = footprint(1)
        selected = {
            (x, y, z): block
            for (x, y, z), block in selected.items()
            if y == middle or (
                middle < y <= FLOORS[-1] and x in STAIR_COLUMNS and z in STAIR_ROWS
            ) or (
                middle < y < middle + 4 and (x, z) in interior
            )
        }
    plots = dict(selected)
    for y in FLOORS:
        for columns in BED_COLUMNS:
            for x in columns:
                for z in BED_ROWS:
                    cell = (x, y, z)
                    block = selected.get(cell)
                    if block and block.state.block_id == SOIL:
                        plots[cell] = Block(FARMLAND)
    return plots


def add_water_surfaces(collection, basins):
    vertices, faces = [], []
    for x, y, z in basins:
        points = ((x, y + 0.88, z), (x, y + 0.88, z + 1),
                  (x + 1, y + 0.88, z + 1), (x + 1, y + 0.88, z))
        faces.append(tuple(range(len(vertices), len(vertices) + 4)))
        vertices.extend(blender_space(point) for point in points)
    mesh = bpy.data.meshes.new("Irrigation surfaces")
    mesh.from_pydata(vertices, [], faces)
    material = bpy.data.materials.get("Irrigation water")
    if material is None:
        material = bpy.data.materials.new("Irrigation water")
        material.diffuse_color = (0.063, 0.27, 0.37, 1)
        shader = material.node_tree.nodes["Principled BSDF"]
        shader.inputs["Base Color"].default_value = material.diffuse_color
        shader.inputs["Roughness"].default_value = 0.18
        shader.inputs["IOR"].default_value = 1.333
    mesh.materials.append(material)
    water = bpy.data.objects.new("Water sources · fill after build", mesh)
    collection.objects.link(water)


def stage_grain_tower(scene, ground, selected, baked, materials, opaque, *, crystal=False):
    if scene.name == CUTAWAY:
        ground.location.z = FLOORS[1] - 0.025
    matte = ground.data.materials[0]
    matte.diffuse_color = (0.115, 0.132, 0.185, 1) if crystal else (0.23, 0.25, 0.20, 1)
    matte.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = matte.diffuse_color
    scene.cycles.transparent_max_bounces = 24
    for reference, (material, _) in materials.items():
        if reference == "minecraft:block/glass" or reference.endswith("_stained_glass"):
            shader = material.node_tree.nodes["Principled BSDF"]
            shader.inputs["Roughness"].default_value = 0.25
    if crystal:
        for obj in scene.objects:
            if obj.type == "LIGHT" and obj.data.type == "SUN":
                obj.data.color = (0.92, 0.96, 1.0)

    if scene.name != CUTAWAY:
        for obj in scene.objects:
            if obj.type == "LIGHT" and obj.name.startswith("Lantern light "):
                obj.data.energy = 170
                obj.data.color = (1.0, 0.80, 0.55)
                obj.data.shadow_soft_size = 0.30
        fill = collection_for(scene, "Soft light through greenhouse windows")
        if crystal:
            for (x, y, z), block in selected.items():
                if block.state.block_id == SEA_LANTERN:
                    position = blender_space((x + 0.5, y - 0.10, z + 0.5))
                    lamp = light(fill, f"Sea lantern at {x}, {y}, {z}", "POINT", position, 120, (0.73, 0.89, 1))
                    lamp.shadow_soft_size = 0.38
        for floor in FLOORS:
            panel = light(fill, "Diffuse daylight", "AREA",
                          (8.5, -7.5, floor + 4.7), 320, (0.89, 0.97, 1.0), (8.5, -8, floor + 1))
            panel.shape = "RECTANGLE"
            panel.size = 9
            panel.size_y = 7

    crops = collection_for(scene, "Plants and water · after planting")
    plants = {
        (x, y + 1, z): Block(CROPS[FLOORS.index(y)][int(x not in BED_COLUMNS[0])])
        for (x, y, z), block in selected.items() if block.state == FARMLAND
    }
    add_block_mesh(crops, "Mature crop beds", plants, selected, baked, materials, opaque)
    basins = [(x, y, WATER_ROW) for y in FLOORS for x in WATER_COLUMNS
              if (x + 1, y, WATER_ROW) in selected and (x, y, WATER_ROW) not in selected]
    if basins:
        add_water_surfaces(crops, basins)
