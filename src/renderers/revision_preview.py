"""Show the observed site and desired revision with the existing Blender block meshes."""

from itertools import combinations, product
import json
from pathlib import Path
import sys

import bpy

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.construction.review_preview import interior_cutaway, models_for_review, terrain_cutaway
from src.renderers.blender_preview import (
    add_block_mesh, add_camera, blender_space, choose_cycles_device, collection_for,
    configure_scene, pixel_material, set_initial_view, stage_scene,
)
from src.renderers.views import View


COLOURS = {
    "add": (0.05, 0.8, 0.24, 1),
    "remove": (0.9, 0.06, 0.04, 1),
    "replace": (1, 0.48, 0.035, 1),
    "conflict": (0.8, 0.09, 0.82, 1),
}


def outline_material(category):
    colour = COLOURS[category]
    material = bpy.data.materials.new(category.title())
    material.diffuse_color = colour
    tree = material.node_tree
    shader = tree.nodes.new("ShaderNodeEmission")
    shader.inputs["Color"].default_value = colour
    shader.inputs["Strength"].default_value = 1.1
    tree.links.new(shader.outputs[0], tree.nodes.get("Material Output").inputs["Surface"])
    return material


def outlines(scene, rows, categories):
    corners = list(product((-0.025, 1.025), repeat=3))
    edges = [(a, b) for a, b in combinations(corners, 2) if sum(x != y for x, y in zip(a, b)) == 1]
    for category in categories:
        collection = collection_for(scene, category.title())
        curve = bpy.data.curves.new(category.title() + " block extents", "CURVE")
        curve.dimensions = "3D"
        curve.bevel_depth = 0.018
        curve.bevel_resolution = 0
        curve.materials.append(outline_material(category))
        for row in rows:
            if row["category"] != category:
                continue
            for first, last in edges:
                spline = curve.splines.new("POLY")
                spline.points.add(1)
                for point, offset in zip(spline.points, (first, last), strict=True):
                    point.co = (*blender_space(tuple(a + b for a, b in zip(row["local"], offset))), 1)
        if len(curve.splines):
            collection.objects.link(bpy.data.objects.new(category.title(), curve))


def block_mesh(scene, name, model, index, root):
    if index:
        baked = index["states"]
        materials = {ref: pixel_material(ref, texture, root) for ref, texture in index["textures"].items()}
        opaque = {key for key, item in baked.items() if item["full_cube"]
                  and all(not materials[face["texture"]][1] for face in item["faces"])}
        missing = index.get("unrendered_observations", [])
        if missing:
            scene["observations_shown_as_outlines"] = ", ".join(missing)
        rendered = {pos: block for pos, block in model.blocks.items() if block.state.key in baked}
        if rendered:
            mesh = collection_for(scene, "Minecraft blocks")
            add_block_mesh(mesh, name, rendered, rendered, baked, materials, opaque)


def terrain_mesh(scene, columns, model, *, proposed=False, behind=None):
    materials = {
        "grass": (0.15, 0.23, 0.085, 1), "soil": (0.24, 0.16, 0.105, 1),
        "stone": (0.21, 0.22, 0.21, 1), "sand": (0.41, 0.35, 0.24, 1),
    }
    y_index = 4 if proposed else 2
    cells = {(x, z): (row[y_index], row[y_index + 1]) for row in columns
             for x, z in [row[:2]] if row[y_index] is not None and (behind is None or z >= behind)}
    if not cells:
        return
    bottom = min(y for y, _ in cells.values())
    vertices, faces, kinds = [], [], []

    def face(corners, kind):
        start = len(vertices)
        vertices.extend(blender_space(point) for point in corners)
        faces.append(tuple(range(start, start + 4)))
        kinds.append(kind)

    for (x, z), (y, kind) in cells.items():
        top = y + 1
        if (x, y, z) not in model.blocks:
            face(((x, top, z), (x, top, z + 1), (x + 1, top, z + 1), (x + 1, top, z)), kind)
        for dx, dz, p, q in ((-1, 0, (x, z), (x, z + 1)), (1, 0, (x + 1, z + 1), (x + 1, z)),
                              (0, -1, (x + 1, z), (x, z)), (0, 1, (x, z + 1), (x + 1, z + 1))):
            base = cells.get((x + dx, z + dz), (bottom - 1, None))[0] + 1
            if base < top:
                face(((p[0], top, p[1]), (q[0], top, q[1]), (q[0], base, q[1]), (p[0], base, p[1])),
                     "soil" if kind == "grass" else kind)
    mesh = bpy.data.meshes.new("Surveyed surface")
    mesh.from_pydata(vertices, [], faces)
    indices = {}
    for name, colour in materials.items():
        material = bpy.data.materials.new("Terrain " + name)
        material.diffuse_color = colour
        material.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value = colour
        mesh.materials.append(material)
        indices[name] = len(mesh.materials) - 1
    for polygon, kind in zip(mesh.polygons, kinds, strict=True):
        polygon.material_index = indices[kind]
    collection_for(scene, "Forge terrain surface").objects.link(bpy.data.objects.new("Loaded terrain columns", mesh))


def main():
    manifest, root, output = map(Path, sys.argv[sys.argv.index("--") + 1:])
    data = json.loads(manifest.read_text(encoding="utf-8"))
    report = data["review"]
    models = models_for_review(report)
    points = [row["local"] for row in report["changes"]]
    limits = tuple(zip(*points))
    centre = tuple((min(axis) + max(axis) + 1) / 2 for axis in limits)
    scale = max(max(axis) - min(axis) + 1 for axis in limits) * 1.45
    cottage = report["new_version"]["building_id"].startswith("cottage_")
    terrain = report.get("terrain_scene")
    offset = (scale * 0.52, scale if cottage or terrain else -scale, scale * 0.66)
    floor = min(limits[1]) - 0.03
    if terrain:
        floor = min(floor, min(row[2] for row in terrain) - 0.03)
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    device = choose_cycles_device()
    scenes = []
    current_index, proposed_index = data["indices"]
    for slot, (name, image, model, index, categories) in enumerate((
        ("Current site", "current.png", models[0], current_index, ("remove", "replace", "conflict")),
        ("Proposed version", "proposed.png", models[1], proposed_index, ("add", "remove", "replace", "conflict")),
    )):
        scene = bpy.context.scene if slot == 0 else bpy.data.scenes.new(name)
        scene.name = name
        scene["plan_id"] = report["new_version"]["plan_id"]
        scene["model_sha256"] = report["new_version"]["model_sha256"]
        scene["minecraft_blocks"] = len(model.blocks)
        configure_scene(scene, "draft", device)
        block_mesh(scene, name, model, index, root)
        stage_scene(scene, model.blocks or models[1].blocks, centre, floor, (), None)
        if terrain:
            terrain_mesh(scene, terrain, model, proposed=slot == 1)
        if not terrain or slot == 0:
            outlines(scene, report["changes"], categories)
        view = View(image, name, "Review", centre, offset, scale)
        scene.camera = add_camera(collection_for(scene, "Review camera"), view)
        scene.render.filepath = "//" + image
        scenes.append((scene, image))

    rear = None
    if data.get("multi_angle"):
        proposal = scenes[1][0]
        rear_view = View("proposed-rear.png", "Rear review camera", "Review", centre,
                         (-offset[0], -offset[1], offset[2]), scale)
        rear = add_camera(collection_for(proposal, "Rear review camera"), rear_view)
        cutaway = terrain_cutaway(models[1], report) if terrain else interior_cutaway(models[1]) if cottage else None
        if cutaway is not None:
            interior = bpy.data.scenes.new("Interior cutaway")
            interior["plan_id"] = report["new_version"]["plan_id"]
            interior["minecraft_blocks"] = len(cutaway.blocks)
            interior["source_minecraft_blocks"] = len(models[1].blocks)
            configure_scene(interior, "draft", device)
            block_mesh(interior, "Interior cutaway", cutaway, proposed_index, root)
            stage_scene(interior, cutaway.blocks, centre, floor, (), None)
            if terrain:
                terrain_mesh(interior, terrain, cutaway, proposed=True, behind=cutaway.bounds()[0][2])
            image = "proposed-interior.png"
            interior_centre = (centre[0], (cutaway.bounds()[0][1] + cutaway.bounds()[1][1]) / 2
                               if terrain else max(limits[1]) - 1.75, centre[2])
            camera = View(image, "Interior cutaway camera", "Review", interior_centre,
                          (scale * 0.39, scale * 0.92, scale * 0.92), scale * 0.93)
            interior.camera = add_camera(collection_for(interior, "Interior camera"), camera)
            interior.render.filepath = "//" + image
            scenes.append((interior, image))
    scene = scenes[1][0] if rear is not None else scenes[0][0]
    bpy.context.window.scene = scene
    set_initial_view(scene, scene.camera, centre, scale)
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "revision-review.blend"), relative_remap=False)
    for scene, image in scenes:
        scene.render.filepath = str(output / image)
        bpy.ops.render.render(scene=scene.name, write_still=True)
    if rear is not None:
        proposal.camera = rear
        proposal.render.filepath = str(output / "proposed-rear.png")
        bpy.ops.render.render(scene=proposal.name, write_still=True)


if __name__ == "__main__":
    main()
