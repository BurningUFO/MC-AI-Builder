"""Render selected cameras and save an explorable blueprint in one Blender run."""

from collections import defaultdict
from pathlib import Path
import sys
from time import perf_counter

import bpy
from mathutils import Vector

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.assets.appearance import tint_rgb
from src.core.building import Building, write_json
from src.design import RENDER_RUN, digest, input_hashes, read_groups, read_json
from src.renderers.blender_preview import (
    add_block_mesh, add_camera, blender_space, choose_cycles_device, collection_for,
    configure_scene, light, mesh_sections, pixel_material, review_blocks, set_initial_view,
)
from src.renderers.verify_preview import verify_design
from src.renderers.views import View, preview_for, section_blocks, section_box


QUALITY = {"quick": (720, 600, 4, 0.12), "review": (1080, 900, 16, 0.065),
           "final": (1440, 1200, 64, 0.024)}


def material_key(block_id, face):
    return face["texture"], tint_rgb(block_id, face.get("tint"))


def two_sided_faces(faces):
    # Minecraft supplies front and back quads for some plants. Blender shades a single quad from either side.
    unique = {}
    for face in faces:
        key = (face["texture"], face.get("tint"), tuple(sorted(map(tuple, face["vertices"]))))
        previous = unique.get(key)
        if previous is None or face["cull"] and not previous["cull"]:
            unique[key] = face
    return list(unique.values())


def materials_for(baked, textures, root, presentation):
    keys = {material_key(asset["block_id"], face) for asset in baked.values() for face in asset["faces"]}
    materials = {key: pixel_material(key[0], textures[key[0]], root, presentation=presentation, tint=key[1])
                 for key in sorted(keys, key=lambda key: (key[0], key[1] or ()))}
    opaque = {key for key, asset in baked.items() if asset["full_cube"] and
              all(not materials[material_key(asset["block_id"], face)][1] for face in asset["faces"])}
    joined = {key for key, asset in baked.items() if "glass" in asset["block_id"] and key not in opaque}
    return materials, opaque, joined


def part_meshes(scene, selected, groups, baked, materials, opaque, joined, legacy):
    by_part = defaultdict(dict)
    if legacy and not groups:
        by_part.update(mesh_sections(selected, legacy.sections))
    else:
        for pos, block in selected.items():
            by_part[groups.get(pos, "Structure")][pos] = block
    tree = {"": collection_for(scene, "Building · " + scene.name)}
    for path, blocks in sorted(by_part.items()):
        parent = ""
        for name in path.split("/"):
            branch = parent + "/" + name
            if branch not in tree:
                collection = bpy.data.collections.new(name)
                collection["minecraft_part"] = branch[1:]
                tree[parent].children.link(collection)
                tree[branch] = collection
            parent = branch
        # Keep faces where independently hideable parts meet.
        obj = add_block_mesh(tree[parent], path.rpartition("/")[2], blocks, blocks, baked,
                             materials, opaque, joined_states=joined, material_key=material_key)
        obj["minecraft_part"] = path


LANTERNS = {"minecraft:lantern": (32, (1, 0.66, 0.37)),
            "minecraft:soul_lantern": (23, (0.39, 0.76, 1))}


def backdrop(selected, centre, y):
    x, z, height = blender_space((centre[0], y, centre[2]))
    radius = max(120, max(max(abs(a - x), abs(-c - z)) for a, _, c in selected) * 4)
    left, right, front, back = x - radius, x + radius, z - radius, z + radius
    underground = [(a, -c) for a, b, c in selected if b < y]
    if underground:
        xs, zs = zip(*underground)
        lx, rx, fz, bz = min(xs), max(xs) + 1, min(zs) - 1, max(zs)
        rectangles = ((left, front, lx, back), (rx, front, right, back),
                      (lx, front, rx, fz), (lx, bz, rx, back))
    else:
        rectangles = ((left, front, right, back),)
    vertices, faces = [], []
    for lo_x, lo_z, hi_x, hi_z in rectangles:
        start = len(vertices)
        vertices.extend(((lo_x, lo_z, height), (hi_x, lo_z, height),
                         (hi_x, hi_z, height), (lo_x, hi_z, height)))
        faces.append(tuple(range(start, start + 4)))
    mesh = bpy.data.meshes.new("Backdrop plane")
    mesh.from_pydata(vertices, [], faces)
    return bpy.data.objects.new("Backdrop plane", mesh)


def stage(scene, selected, target, presentation, options, full, fixed_lighting=False):
    environment = collection_for(scene, "Environment and lights")
    game = presentation == "game"
    if options.get("ground", True):
        y = min(pos[1] for pos in selected) - 0.04
        if full or fixed_lighting:
            y = options.get("ground_y", y)
        ground = backdrop(selected, target, y)
        ground.hide_select = True
        environment.objects.link(ground)
        matte = bpy.data.materials.get("Backdrop matte")
        if matte is None:
            matte = bpy.data.materials.new("Backdrop matte")
            color = options.get("ground_color", (0.14, 0.15, 0.16) if game else (0.22, 0.24, 0.25))
            matte.diffuse_color = (*color, 1)
            shader = matte.node_tree.nodes.get("Principled BSDF")
            shader.inputs["Roughness"].default_value = 0.93
            shader.inputs["Base Color"].default_value = matte.diffuse_color
        ground.data.materials.append(matte)

    centre = Vector(blender_space(target))
    offset = Vector(blender_space(options.get("sun", (-4, 6, 5))))
    sun = light(environment, "Daylight", "SUN", centre + offset, 1.25 if game else 2.3,
                (1, 1, 1) if game else (1, 0.92, 0.79), centre)
    sun.angle = 0.11 if game else 0.17
    fixtures = () if fixed_lighting else selected.items()
    for (x, y, z), block in fixtures:
        energy = LANTERNS.get(block.state.block_id)
        if not energy:
            continue
        power, color = energy
        hanging = dict(block.state.properties).get("hanging") == "true"
        position = blender_space((x + 0.5, y + (0.2 if hanging else 0.65), z + 0.5))
        lamp = light(environment, f"Lantern {x}, {y}, {z}", "POINT", position, power, color)
        lamp.shadow_soft_size = 0.18

    for fixture in options.get("interior_lights", []):
        if not full or game:
            continue
        lamp = light(environment, fixture["name"], "AREA", blender_space(fixture["position"]),
                     fixture["power"], (1, 0.91, 0.80), blender_space(fixture["target"]))
        lamp.shape = "DISK"
        lamp.size = fixture["size"]


def configure(scene, preset, device, presentation):
    configure_scene(scene, preset, device)
    w, h, samples, threshold = QUALITY[preset]
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = w, h, 100
    scene.render.use_persistent_data = True
    scene.cycles.samples, scene.cycles.adaptive_threshold = samples, threshold
    scene.cycles.transparent_max_bounces = 12
    game = presentation == "game"
    if game:
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
    else:
        scene.view_settings.look = "AgX - Medium High Contrast"
    background = scene.world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value = (0.66, 0.69, 0.72, 1) if game else (0.49, 0.64, 0.81, 1)
    background.inputs["Strength"].default_value = 0.62 if game else 0.49


def main():
    start = perf_counter()
    job_path, out, root = map(Path, sys.argv[sys.argv.index("--") + 1:])
    job = read_json(job_path)
    project = Path(job["project"])
    data = project / "building.json"
    building = Building.read(data)
    index = read_json(project / "material-index.json")
    inputs = job["inputs"]
    if inputs != input_hashes(project) or inputs["building.json"] != index["building_sha256"]:
        raise ValueError("The render inputs changed before Blender could read them.")
    groups = read_groups(project, building, inputs)
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    filepaths = bpy.context.preferences.filepaths
    filepaths.save_version = 0
    filepaths.file_preview_type = "NONE"

    baked = {key: {**asset, "faces": two_sided_faces(asset["faces"])} for key, asset in index["states"].items()}
    look = job["presentation"]
    materials, opaque, joined = materials_for(baked, index["textures"], root, look)
    device = "CPU" if job["cpu"] else choose_cycles_device()
    views = {name: View(**value) for name, value in job["cameras"].items()}
    full = job["full_scene"]
    legacy = preview_for(job["sample"], building) if job["legacy_views"] else None
    scenes, cameras, frames, collection_by_scene, expected = {}, {}, {}, {}, {}
    for key, view in views.items():
        if view.scene not in scenes:
            scene = bpy.context.scene if not scenes else bpy.data.scenes.new(view.scene)
            scene.name = view.scene
            scenes[view.scene] = scene
            scene["building_sha256"] = inputs["building.json"]
            scene["material_index_sha256"] = inputs["material-index.json"]
            scene["building_source"] = f"projects/{project.name}/building.json"
            scene["minecraft_axes"] = "Blender (X, Y, Z) = Minecraft (x, -z, y)"
            scene["presentation"] = look
            if index.get("render_fallbacks"):
                scene["visual_substitutions"] = ", ".join(index["render_fallbacks"])
            if view.scene == full:
                selected = building.blocks
            elif legacy and job["sample"] in ("grain_tower", "crystal_tower"):
                from src.renderers.grain_tower import planted_blocks

                selected = planted_blocks(building, view.scene)
            elif legacy:
                selected = review_blocks(building, view.scene, full)
            else:
                spec = job["sections"][view.scene]
                selected = section_blocks(building.blocks, (section_box(spec),), groups,
                                          include=spec.get("include", ()), exclude=spec.get("exclude", ()))
            if not selected:
                raise ValueError(f"Section {view.scene!r} contains no blocks.")
            expected[view.scene] = len(selected)
            configure(scene, job["preset"], device, look)
            part_meshes(scene, selected, groups, baked, materials, opaque, joined, legacy)
            stage(scene, selected, view.target, look, job["stage"], view.scene == full,
                  fixed_lighting=job.get("fixed_lighting", False))
            collection_by_scene[view.scene] = collection_for(scene, "Cameras")
        scene = scenes[view.scene]
        camera = add_camera(collection_by_scene[view.scene], view)
        cameras[key] = camera
        frame = 1 + 20 * len(scene.timeline_markers)
        scene.timeline_markers.new(view.camera, frame=frame).camera = camera
        scene.frame_end = max(scene.frame_end, frame)
        frames[key] = frame
        if scene.camera is None:
            scene.camera = camera
            scene.render.filepath = "//" + view.image

    blend = job["blend"]
    if blend:
        key = next(iter(views))
        view = views[key]
        set_initial_view(scenes[view.scene], cameras[key], view.target, view.scale)
        bpy.ops.wm.save_as_mainfile(filepath=str(out / blend), relative_remap=False, compress=True)
    setup_seconds = perf_counter() - start
    images = {}
    for key in job["render"]:
        view = views[key]
        scene = scenes[view.scene]
        scene.frame_set(frames[key])
        scene.camera = cameras[key]
        path = out / view.image
        scene.render.filepath = str(path)
        start_render = perf_counter()
        bpy.ops.render.render(scene=scene.name, write_still=True)
        images[key] = {"file": view.image, "scene": view.scene, "camera": cameras[key].name,
                       "sha256": digest(path), "seconds": round(perf_counter() - start_render, 3)}
    report = {"inputs": inputs, "preset": job["preset"], "presentation": look,
              "engine": "Cycles", "device": device, "resolution": QUALITY[job["preset"]][:2],
              "setup_seconds": round(setup_seconds, 3), "blend": blend, "images": images,
              "cameras": {key: {"scene": view.scene, "name": cameras[key].name, "frame": frames[key]}
                          for key, view in views.items()},
              "scene_block_counts": expected, "full_scene": full}
    if job["preset"] == "final":
        bpy.ops.wm.open_mainfile(filepath=str(out / blend), load_ui=False, use_scripts=False)
        verify_design(project, out, report)
    write_json(out / RENDER_RUN, report)


if __name__ == "__main__":
    main()
