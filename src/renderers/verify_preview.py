"""Reload the saved scene and decode all four PNGs with Blender."""

from array import array
from hashlib import sha256
from pathlib import Path
import sys

import bpy

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.building import Building, write_json
from src.renderers.views import PREVIEWS, VERIFICATION_FILE


def require(condition, message):
    if not condition:
        raise ValueError(message)


def png_report(path: Path) -> dict:
    raw = path.read_bytes()
    require(raw.startswith(b"\x89PNG\r\n\x1a\n"), f"Invalid PNG header: {path}")
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        require(width >= 720 and height >= 600, f"Unexpected PNG dimensions: {path}")
        values = array("f", [0.0]) * len(image.pixels)
        image.pixels.foreach_get(values)
        require(len(values) == width * height * 4, f"PNG has no decoded RGB pixels: {path}")
        color = values[:width * height * 4:388]
        span = max(color) - min(color)
        require(span > 0.10, f"PNG lacks visible tonal range: {path}")
        return {"width": width, "height": height, "bytes": len(raw),
                "sha256": sha256(raw).hexdigest(), "sampled_tonal_range": round(span, 4)}
    finally:
        bpy.data.images.remove(image)


def main():
    data_argument, folder_argument, sample = sys.argv[sys.argv.index("--") + 1:]
    data_path, folder = Path(data_argument), Path(folder_argument)
    preview = PREVIEWS[sample]
    building = Building.read(data_path)
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
    write_json(folder / VERIFICATION_FILE, report)
    print(f"Reopened {preview.scene_file}: {len(building.blocks)} blocks, {len(textures)} packed textures, {len(images)} decoded PNGs.")


if __name__ == "__main__":
    main()

