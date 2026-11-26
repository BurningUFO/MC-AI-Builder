"""Check a reopened Blender file, its blueprint provenance and decoded PNGs."""

from array import array
from hashlib import sha256
from math import isclose
from pathlib import Path
import sys
import json

import bpy

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.building import Building, write_json
from src.design import RENDER_RUN, VIEWS, input_hashes, read_json
from src.renderers.views import preview_for, VERIFICATION_FILE


def require(condition, message):
    if not condition:
        raise ValueError(message)


def png_report(path: Path, expected_size=None) -> dict:
    raw = path.read_bytes()
    require(raw.startswith(b"\x89PNG\r\n\x1a\n"), f"Invalid PNG header: {path}")
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        require((width, height) == tuple(expected_size) if expected_size else width >= 720 and height >= 600,
                f"Unexpected PNG dimensions: {path}")
        values = array("f", [0.0]) * len(image.pixels)
        image.pixels.foreach_get(values)
        require(len(values) == width * height * 4, f"PNG has no decoded RGB pixels: {path}")
        color = values[:width * height * 4:388]
        span = max(color) - min(color)
        require(span > 0.10, f"PNG lacks visible tonal range: {path}")
        return {"width": width, "height": height, "bytes": len(raw),
                "sha256": sha256(raw).hexdigest(), "sampled_tonal_range": round(span, 4),
                "pixel_sha256": sha256(memoryview(values).cast("B")).hexdigest()}
    finally:
        bpy.data.images.remove(image)


def verify_design(project, folder, run):
    inputs = run["inputs"]
    require(inputs == input_hashes(project), "Build or camera inputs changed since this render.")
    blend = run["blend"]
    require(blend and Path(bpy.data.filepath).resolve() == (folder / blend).resolve(),
            "Blender did not reopen the saved project.")
    data = project / "building.json"
    building = Building.read(data)
    require(run["scene_block_counts"].get(run["full_scene"]) == len(building.blocks),
            "Full scene does not cover the whole blueprint.")
    geometry = {}
    for name, count in run["scene_block_counts"].items():
        scene = bpy.data.scenes.get(name)
        require(scene is not None and scene.get("building_sha256") == inputs["building.json"]
                and scene.get("material_index_sha256") == inputs["material-index.json"], f"Wrong blueprint in {name}")
        collection = scene.collection.children.get("Building · " + name)
        require(collection is not None, f"Missing building mesh collection in {name}")
        objects = [obj for obj in collection.all_objects if obj.type == "MESH"]
        faces = sum(len(obj.data.polygons) for obj in objects)
        blocks = sum(obj.get("minecraft_blocks", 0) for obj in objects)
        require(faces > 0 and blocks == count, f"Unexpected mesh coverage in {name}")
        parts = {}
        for obj in objects:
            part = obj.get("minecraft_part")
            require(part and any(group.get("minecraft_part") == part for group in obj.users_collection),
                    f"Mesh {obj.name} is missing its independently hideable collection.")
            require(all(slot.material is not None for slot in obj.material_slots), f"Missing material on {obj.name}")
            parts[part] = obj["minecraft_blocks"]
        geometry[name] = {"mesh_objects": len(objects), "faces": faces, "source_blocks": blocks, "parts": parts}
    frames = {name: bpy.data.scenes[name].frame_current for name in geometry}
    configured = read_json(project / VIEWS).get("views", {}) if (project / VIEWS).is_file() else {}
    for key, record in run["cameras"].items():
        scene = bpy.data.scenes[record["scene"]]
        camera = scene.objects.get(record["name"])
        require(camera is not None and camera.type == "CAMERA"
                and any(marker.camera == camera and marker.frame == record["frame"]
                        for marker in scene.timeline_markers),
                f"Missing camera or camera marker: {key}")
        expected = configured.get(key, {}).get("shift", (0, 0))
        actual = camera.data.shift_x, camera.data.shift_y
        require(all(isclose(a, b, abs_tol=1e-6, rel_tol=1e-6) for a, b in zip(actual, expected, strict=True)),
                f"Saved camera framing differs from {VIEWS}: {key}")
        scene.frame_set(record["frame"])
        require(scene.camera == camera, f"Camera marker did not switch to {key}")
    for name, frame in frames.items():
        bpy.data.scenes[name].frame_set(frame)
    images = {}
    for record in run["images"].values():
        name = record["file"]
        report = png_report(folder / name, run["resolution"])
        require(report["sha256"] == record["sha256"], f"Rendered PNG changed: {name}")
        images[name] = report
    textures = [image for image in bpy.data.images if image.name.startswith("Minecraft | ")]
    require(textures and all(image.packed_file is not None for image in textures), "A Minecraft texture is not packed")
    index = read_json(project / "material-index.json")
    write_json(folder / VERIFICATION_FILE, {
        "blender_version": bpy.app.version_string,
        "building_sha256": inputs["building.json"],
        "geometry": geometry, "packed_minecraft_textures": len(textures), "images": images,
        "render_fallbacks": index.get("render_fallbacks", {}),
        "render_approximations": index.get("render_approximations", {}),
    })
    print(f"Reopened {blend}: {len(building.blocks)} blocks, {len(geometry)} scenes, "
          f"{len(run['cameras'])} cameras, {len(textures)} packed textures, {len(images)} decoded PNGs.")


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    if len(args) == 2:
        project, folder = map(Path, args)
        verify_design(project, folder, read_json(folder / RENDER_RUN))
        return
    data_argument, folder_argument, sample = args
    data_path, folder = Path(data_argument), Path(folder_argument)
    building = Building.read(data_path)
    preview = preview_for(sample, building, name=data_path.parent.name)
    digest = sha256(data_path.read_bytes()).hexdigest()
    require(Path(bpy.data.filepath) == folder / preview.scene_file, "Blender did not reopen the saved file")
    geometry, images = {}, {}
    for view in preview.views:
        scene = bpy.data.scenes.get(view.scene)
        require(scene is not None and scene.get("building_sha256") == digest, f"Wrong building in {view.scene}")
        require(scene.objects.get(view.camera) is not None, f"Missing camera: {view.camera}")
        if view.scene not in geometry:
            group = scene.collection.children.get("Building · " + view.scene)
            require(group is not None, f"Missing building mesh collection in {view.scene}")
            objects = [obj for obj in group.objects if obj.type == "MESH"]
            faces = sum(len(obj.data.polygons) for obj in objects)
            blocks = sum(obj["minecraft_blocks"] for obj in objects)
            require(faces > 0 and blocks > 0, f"Empty building geometry in {view.scene}")
            geometry[view.scene] = {"mesh_objects": len(objects), "faces": faces, "source_blocks": blocks}
        images[view.image] = png_report(folder / view.image)
    require(geometry[preview.full_scene]["source_blocks"] == len(building.blocks), "Full scene does not cover the whole structure")
    require(len({record["sha256"] for record in images.values()}) == len(preview.views), "At least two views are identical")
    textures = [img for img in bpy.data.images if img.name.startswith("Minecraft | ")]
    require(textures and all(img.packed_file is not None for img in textures), "A Minecraft texture is not packed")
    report = {"blender_version": bpy.app.version_string, "building_sha256": digest,
              "geometry": geometry, "packed_minecraft_textures": len(textures), "images": images}
    index = data_path.with_name("material-index.json")
    if index.is_file():
        fallbacks = json.loads(index.read_text(encoding="utf-8")).get("render_fallbacks")
        if fallbacks:
            report["render_fallbacks"] = fallbacks
    write_json(folder / VERIFICATION_FILE, report)
    print(f"Reopened {preview.scene_file}: {len(building.blocks)} blocks, {len(textures)} packed textures, {len(images)} decoded PNGs.")


if __name__ == "__main__":
    main()

