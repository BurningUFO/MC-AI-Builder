"""Build and render the Blender scene using Blender's bundled Python."""

from hashlib import sha256
import json
from math import radians
from pathlib import Path
import sys

import bpy
from mathutils import Vector

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.assets.appearance import first_frame
from src.assets.models import AXES
from src.core.building import Building
from src.generators.grain_tower import GLAZING, TOWER_SAMPLES
from src.generators.medieval import UPPER_FLOOR
from src.renderers.views import legacy_section, preview_for


def blender_space(position):
    x, y, z = position
    return x, -z, y


def pixel_material(reference, info, root, *, presentation="legacy", tint=None):
    source = root / info["file"]
    if sha256(source.read_bytes()).hexdigest() != info["sha256"]:
        raise ValueError(f"Texture cache has changed: {source}")
    image = bpy.data.images.load(str(source), check_existing=True)
    image.name = "Minecraft | " + reference
    if not image.packed_file:
        image.pack()
    image.use_fake_user = True
    pixels = image.pixels[:]
    translucent = any(alpha < 0.999 for alpha in pixels[3::4])
    visible = [i for i in range(0, len(pixels), 4) if pixels[i + 3] >= 0.6]

    material = bpy.data.materials.new(reference if tint is None else f"{reference} | tint {tint}")
    if visible:
        material.diffuse_color = (*(sum(pixels[i + c] for i in visible) / len(visible) for c in range(3)), 1)
    tree = material.node_tree
    bsdf = tree.nodes.get("Principled BSDF")
    bsdf.inputs["Roughness"].default_value = 0.86
    bsdf.inputs["Specular IOR Level"].default_value = 0.24
    tex = tree.nodes.new("ShaderNodeTexImage")
    tex.label = "JAR texture · nearest pixel"
    tex.location = (-340, 220)
    tex.image = image
    tex.interpolation = "Closest"
    color = tex.outputs["Color"]
    if tint:
        multiply = tree.nodes.new("ShaderNodeMixRGB")
        multiply.blend_type = "MULTIPLY"
        multiply.inputs[0].default_value = 1
        multiply.inputs[2].default_value = tuple(
            ((channel / 255 + 0.055) / 1.055) ** 2.4 for channel in tint) + (1,)
        tree.links.new(color, multiply.inputs[1])
        color = multiply.outputs[0]
    tree.links.new(color, bsdf.inputs["Base Color"])
    if presentation != "legacy":
        bsdf.inputs["Roughness"].default_value = 0.91
        bsdf.inputs["Specular IOR Level"].default_value = 0.16
        emits = any(name in reference for name in ("lantern", "glowstone", "shroomlight", "froglight", "light_on"))
        if emits or presentation == "game":
            tree.links.new(color, bsdf.inputs["Emission Color"])
            bsdf.inputs["Emission Strength"].default_value = 0.65 if emits else 0.11
        width, height = image.size
        if reference == "minecraft:block/sea_lantern" and "animation" not in info:
            info = {**info, "animation": {}}
        left, top, right, bottom = first_frame(info, width, height)
        if (left, top, right, bottom) != (0, 0, width, height):
            map_texture(tree, tex, ((right - left) / width, (bottom - top) / height, 1),
                        (left / width, 1 - bottom / height, 0))
    elif reference == "minecraft:block/sea_lantern":
        # Sample the first square frame of the vertically stacked animation.
        frame_scale = image.size[0] / image.size[1]
        map_texture(tree, tex, (1, frame_scale, 1), (0, 1 - frame_scale, 0))
        tree.links.new(tex.outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = 0.55
    if translucent:
        alpha = tex.outputs["Alpha"]
        if presentation != "legacy" and "glass" not in reference and "ice" not in reference:
            cutout = tree.nodes.new("ShaderNodeMath")
            cutout.operation = "GREATER_THAN"
            cutout.inputs[1].default_value = 0.5
            tree.links.new(alpha, cutout.inputs[0])
            alpha = cutout.outputs[0]
        tree.links.new(alpha, bsdf.inputs["Alpha"])
    return material, translucent


def map_texture(tree, tex, scale, location):
    uv = tree.nodes.new("ShaderNodeTexCoord")
    frame = tree.nodes.new("ShaderNodeMapping")
    frame.label = "First displayed texture frame"
    frame.inputs["Scale"].default_value = scale
    frame.inputs["Location"].default_value = location
    tex.extension = "EXTEND"
    tree.links.new(uv.outputs["UV"], frame.inputs["Vector"])
    tree.links.new(frame.outputs["Vector"], tex.inputs["Vector"])


def add_block_mesh(collection, name, blocks, selected, baked, materials, opaque_cubes, *,
                   joined_states=(), material_key=None):
    vertices, polygons, uvs, textures = [], [], [], []
    indices = {}
    for (x, y, z), block in blocks.items():
        for face in baked[block.state.key]["faces"]:
            if face["cull"]:
                dx, dy, dz = AXES[face["cull"]]
                neighbor = selected.get((x + dx, y + dy, z + dz))
                if neighbor and (neighbor.state.key in opaque_cubes or
                                 block.state == neighbor.state and block.state.key in joined_states):
                    continue
            polygon = []
            for vx, vy, vz in face["vertices"]:
                point = blender_space((x + vx, y + vy, z + vz))
                if point not in indices:
                    indices[point] = len(vertices)
                    vertices.append(point)
                polygon.append(indices[point])
            polygons.append(polygon)
            uvs.append(face["uv"])
            textures.append(material_key(block.state.block_id, face) if material_key else face["texture"])

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], polygons)
    mesh.update()
    slots = {}
    for reference in dict.fromkeys(textures):
        slots[reference] = len(mesh.materials)
        mesh.materials.append(materials[reference][0])
    uv_layer = mesh.uv_layers.new(name="Minecraft UV")
    for face, reference, coords in zip(mesh.polygons, textures, uvs):
        face.material_index = slots[reference]
        for loop, uv in zip(face.loop_indices, coords):
            uv_layer.uv[loop].vector = uv
    obj = bpy.data.objects.new(name, mesh)
    obj["minecraft_blocks"] = len(blocks)
    collection.objects.link(obj)
    return obj


def review_blocks(building, scene_name, full_scene):
    if scene_name == full_scene:
        return building.blocks
    return legacy_section(building, scene_name)


def mesh_sections(blocks, sections):
    groups = {name: {} for _, name in sections}
    for position, block in blocks.items():
        section = next((name for y, name in reversed(sections) if position[1] >= y), sections[0][1])
        groups[section][position] = block
    return {name: items for name, items in groups.items() if items}


def collection_for(scene, name):
    collection = bpy.data.collections.new(name)
    scene.collection.children.link(collection)
    return collection


def light(collection, name, kind, position, energy, color, target=None):
    data = bpy.data.lights.new(name, kind)
    data.energy = energy
    data.color = color
    obj = bpy.data.objects.new(name, data)
    collection.objects.link(obj)
    obj.location = position
    if target is not None:
        obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()
    return data


def interior_review_lights(scene, fixtures):
    collection = collection_for(scene, "Interior review lights")
    for name, origin, aim_height, power, diameter in fixtures:
        x, y, z = blender_space(origin)
        lamp = light(collection, name, "AREA", (x, y, z), power, (1, 0.93, 0.84), (x, y, aim_height))
        lamp.shape = "DISK"
        lamp.size = diameter


def add_water(stage, level):
    mesh = bpy.data.meshes.new("Water surface mesh")
    mesh.from_pydata([(-150, -150, 0), (150, -150, 0), (150, 150, 0), (-150, 150, 0)], [], [(0, 1, 2, 3)])
    water = bpy.data.objects.new("River surface", mesh)
    water.location.z = level
    water.hide_select = True
    stage.objects.link(water)

    material = bpy.data.materials.new("River water")
    material.diffuse_color = (0.08, 0.24, 0.36, 1)
    tree = material.node_tree
    shader = tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = material.diffuse_color
    shader.inputs["Roughness"].default_value = 0.22
    shader.inputs["IOR"].default_value = 1.333
    shader.inputs["Transmission Weight"].default_value = 0.34
    coordinates = tree.nodes.new("ShaderNodeTexCoord")
    noise = tree.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 0.8
    noise.inputs["Detail"].default_value = 2
    bump = tree.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.22
    bump.inputs["Distance"].default_value = 0.12
    tree.links.new(coordinates.outputs["Object"], noise.inputs["Vector"])
    tree.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    tree.links.new(bump.outputs["Normal"], shader.inputs["Normal"])
    mesh.materials.append(material)


def stage_scene(scene, blocks, target, stage_height, fixtures, water_level):
    stage = collection_for(scene, "Studio")
    mesh = bpy.data.meshes.new("Studio ground mesh")
    mesh.from_pydata([(-200, -200, 0), (200, -200, 0), (200, 200, 0), (-200, 200, 0)], [], [(0, 1, 2, 3)])
    ground = bpy.data.objects.new("Matte background", mesh)
    ground.location.z = stage_height
    stage.objects.link(ground)
    matte = bpy.data.materials.get("Studio matte")
    if matte is None:
        matte = bpy.data.materials.new("Studio matte")
        matte.diffuse_color = (0.165, 0.21, 0.196, 1)
        bsdf = matte.node_tree.nodes.get("Principled BSDF")
        bsdf.inputs["Base Color"].default_value = matte.diffuse_color
        bsdf.inputs["Roughness"].default_value = 0.94
    ground.data.materials.append(matte)
    ground.hide_select = True
    if water_level is not None:
        add_water(stage, water_level)

    point = Vector(blender_space(target))
    sun = light(stage, "Warm daylight", "SUN", point + Vector((-15, 14, 25)), 2.1, (1, 0.89, 0.74), point)
    sun.angle = radians(12)
    fill = light(stage, "Soft skylight", "AREA", point + Vector((13, -10, 15)), 3100, (0.74, 0.85, 1), point)
    fill.shape = "DISK"
    fill.size = 19
    if fixtures:
        facade_target = Vector((8, 0, 3))
        facade = light(stage, "Soft facade fill", "AREA", (12, 15, 7), 2250, (1, 0.94, 0.83), facade_target)
        facade.shape = "DISK"
        facade.size = 17
        interior_review_lights(scene, fixtures)
    lanterns = {
        "minecraft:lantern": (15, (1.0, 0.65, 0.29)),
        "minecraft:soul_lantern": (19, (0.26, 0.77, 1.0)),
    }
    for (x, y, z), block in blocks.items():
        emission = lanterns.get(block.state.block_id)
        if emission is None:
            continue
        power, color = emission
        hanging = dict(block.state.properties)["hanging"] == "true"
        p = blender_space((x + 0.5, y + (0.15 if hanging else 0.75), z + 0.5))
        lamp = light(stage, f"Lantern light {x}, {y}, {z}", "POINT", p, power, color)
        lamp.shadow_soft_size = 0.18
    return ground


def add_camera(collection, view):
    target = Vector(blender_space(view.target))
    data = bpy.data.cameras.new(view.camera)
    if view.lens_mm is None:
        data.type = "ORTHO"
        data.ortho_scale = view.scale
    else:
        data.type = "PERSP"
        data.lens = view.lens_mm
    data.clip_start = view.clip_start
    data.clip_end = max(500, 3 * Vector(view.offset).length + view.scale)
    camera = bpy.data.objects.new(view.camera, data)
    collection.objects.link(camera)
    camera.location = target + Vector(view.offset)
    if view.offset[0] == view.offset[1] == 0 and view.offset[2] > 0:
        camera.rotation_euler = (0, 0, 0)
    else:
        camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
    return camera


def choose_cycles_device():
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for backend in ("OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = backend
            devices = prefs.get_devices_for_type(backend)
        except (TypeError, RuntimeError):
            continue
        cards = [device.name for device in devices if device.type == backend]
        if cards:
            for device in devices:
                device.use = device.type == backend
            print(f"Cycles {backend}: {', '.join(cards)}", flush=True)
            return "GPU"
    print("Cycles: CPU", flush=True)
    return "CPU"


def set_cycles_device(scene, device):
    scene.cycles.device = device
    use_gpu = device == "GPU"
    scene.cycles.denoising_use_gpu = use_gpu
    scene.cycles.preview_denoising_use_gpu = use_gpu


def configure_scene(scene, quality, device):
    scene.render.engine = "CYCLES"
    set_cycles_device(scene, device)
    scene.cycles.samples = 32 if quality == "final" else 8
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = 0.035
    scene.cycles.use_denoising = True
    scene.cycles.preview_samples = 64
    scene.cycles.preview_adaptive_threshold = 0.06
    scene.cycles.use_preview_denoising = True
    scene.cycles.preview_denoiser = "OPENIMAGEDENOISE"
    scene.cycles.preview_denoising_input_passes = "RGB_ALBEDO_NORMAL"
    scene.cycles.preview_denoising_start_sample = 4
    scene.cycles.max_bounces = 6
    scene.render.resolution_x = 1440
    scene.render.resolution_y = 1200
    scene.render.resolution_percentage = 100 if quality == "final" else 50
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.world = bpy.data.worlds.get("Studio sky")
    if scene.world is None:
        scene.world = bpy.data.worlds.new("Studio sky")
        background = scene.world.node_tree.nodes.get("Background")
        background.inputs["Color"].default_value = (0.48, 0.57, 0.70, 1)
        background.inputs["Strength"].default_value = 0.52


def set_initial_view(scene, camera, target, scale):
    if bpy.context.window is not None:
        bpy.context.window.scene = scene
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                space = area.spaces.active
                space.region_3d.view_perspective = "ORTHO"
                space.region_3d.view_location = blender_space(target)
                space.region_3d.view_rotation = camera.rotation_euler.to_quaternion()
                space.region_3d.view_distance = scale
                space.shading.type = "MATERIAL"
                space.shading.use_scene_lights = False
                space.shading.use_scene_world = False
                space.shading.color_type = "MATERIAL"
                space.overlay.show_floor = False
                space.overlay.show_axis_x = False
                space.overlay.show_axis_y = False
                space.overlay.show_extras = False


def main():
    arguments = sys.argv[sys.argv.index("--") + 1:]
    data_path, index_path, root, out_path = map(Path, arguments[:4])
    sample, quality = arguments[4:]
    building = Building.read(data_path)
    preview = preview_for(sample, building, name=data_path.parent.name)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    source_hash = sha256(data_path.read_bytes()).hexdigest()
    if source_hash != index["building_sha256"]:
        raise ValueError("Building and index changed during scene generation")

    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    filepaths = bpy.context.preferences.filepaths
    filepaths.save_version = 0
    filepaths.file_preview_type = "NONE"
    baked = index["states"]
    materials = {ref: pixel_material(ref, info, root) for ref, info in index["textures"].items()}
    opaque = {key for key, item in baked.items() if item["full_cube"]
              and all(not materials[face["texture"]][1] for face in item["faces"])}

    device = choose_cycles_device()
    growing_tower = sample in TOWER_SAMPLES
    joined_states = GLAZING if growing_tower else ()
    if growing_tower:
        from src.renderers.grain_tower import planted_blocks, stage_grain_tower

    scenes, camera_groups, cameras = {}, {}, {}
    for view in preview.views:
        scene = scenes.get(view.scene)
        if scene is None:
            scene = bpy.context.scene if not scenes else bpy.data.scenes.new(view.scene)
            scene.name = view.scene
            scene["building_sha256"] = source_hash
            scene["building_source"] = (data_path.relative_to(root).as_posix() if data_path.is_relative_to(root)
                                        else f"projects/{data_path.parent.name}/building.json")
            scene["minecraft_axes"] = "Blender (X, Y, Z) = Minecraft (x, -z, y)"
            scenes[view.scene] = scene
            configure_scene(scene, quality, device)
            selected = (planted_blocks(building, view.scene) if growing_tower
                        else review_blocks(building, view.scene, preview.full_scene))
            geometry = collection_for(scene, "Building · " + view.scene)
            for name, blocks in mesh_sections(selected, preview.sections).items():
                add_block_mesh(geometry, name, blocks, selected, baked, materials, opaque, joined_states=joined_states)
            ground_z = UPPER_FLOOR - 0.22 if view.scene == "Upper floor cutaway" else min(0, building.bounds()[0][1]) - 0.025
            fixtures = preview.interior_lights if view.scene == preview.full_scene else ()
            if preview.water_level is not None:
                ground_z = min(preview.water_level - 3.25, building.bounds()[0][1] - 0.025)
            ground = stage_scene(scene, selected, view.target, ground_z, fixtures, preview.water_level)
            if sample == "ember_gate":
                from src.renderers.ember_gate import stage_ember_gate

                stage_ember_gate(scene, materials)
            elif growing_tower:
                stage_grain_tower(scene, ground, selected, baked, materials, opaque,
                                  crystal=sample == "crystal_tower")
            camera_groups[view.scene] = collection_for(scene, "View cameras")
        camera = add_camera(camera_groups[view.scene], view)
        cameras[view.image] = camera
        if scene.camera is None:
            scene.camera = camera
            scene.render.filepath = "//" + view.image

    initial = preview.views[0]
    set_initial_view(scenes[initial.scene], cameras[initial.image], initial.target, initial.scale)
    bpy.ops.wm.save_as_mainfile(filepath=str(out_path / preview.scene_file), relative_remap=False)
    for view in preview.views:
        scene = scenes[view.scene]
        scene.camera = cameras[view.image]
        if preview.water_level is not None:
            scene.objects["River surface"].hide_render = view.hide_water
        scene.render.filepath = str(out_path / view.image)
        bpy.ops.render.render(scene=scene.name, write_still=True)


if __name__ == "__main__":
    main()

