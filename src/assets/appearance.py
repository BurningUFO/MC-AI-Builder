"""Static-render approximations shared by block previews and material swatches."""

import json

from src.assets.archives import location


def texture_metadata(archives, reference):
    namespace, name = location(reference)
    path = f"assets/{namespace}/textures/{name}.png"
    source = archives.source_for(namespace, path)
    try:
        data = source.read_optional_json(path + ".mcmeta") or {}
    except (ValueError, UnicodeError):
        return {}
    return {key: data[key] for key in ("animation", "ctm")
            if isinstance(data.get(key), dict)}


def first_frame(metadata, width, height):
    """Return the first displayed frame rectangle in PNG pixel coordinates."""
    animation = metadata.get("animation")
    if animation is None:
        return 0, 0, width, height
    w = animation.get("width", width if "height" in animation else min(width, height))
    h = animation.get("height", height if "width" in animation else min(width, height))
    frames = animation.get("frames") or [0]
    first = frames[0]
    index = first.get("index", 0) if isinstance(first, dict) else first
    if (any(type(n) is not int or n <= 0 for n in (w, h)) or width % w or height % h
            or type(index) is not int or not 0 <= index < (width // w) * (height // h)):
        return 0, 0, width, height
    x, y = index % (width // w) * w, index // (width // w) * h
    return x, y, x + w, y + h


def tint_rgb(block_id, tint_index):
    if tint_index is None or tint_index < 0:
        return None
    name = block_id.split(":", 1)[1]
    if any(token in name for token in ("spruce_leaves", "pine_leaves")):
        return 97, 153, 97
    if "birch_leaves" in name:
        return 128, 167, 85
    if "mangrove_leaves" in name:
        return 146, 193, 111
    if "leaves" in name or any(token in name for token in ("vine", "azalea_hedge", "leaf_carpet")):
        return 102, 157, 65
    if any(token in name for token in ("grass", "fern", "sugar_cane")):
        return 126, 170, 85
    return None


def appearance_notes(state, baked, texture_info):
    notes = []
    name = state.block_id.partition(":")[2]
    if any(word in name for word in ("chest", "shulker_box", "enchanting_table", "lectern", "_sign",
                                     "_banner", "_skull", "spawner", "campfire", "flower_pot", "chiseled_bookshelf")):
        notes.append("Static model; block-entity contents, moving parts or particles are omitted.")
    if any(count > 1 for count in baked.get("weighted_choices", ())):
        notes.append("Uses the first weighted model.")
    if any(face.get("tint") is not None for face in baked["faces"]):
        notes.append("Biome or runtime tint is approximated where available.")
    if any("animation" in info for info in texture_info):
        notes.append("Uses the first displayed texture frame.")
    if any("ctm" in info for info in texture_info):
        notes.append("Connected-texture effects are omitted.")
    if dict(state.properties).get("waterlogged") == "true":
        notes.append("Waterlogging is retained in the blueprint; fluid is omitted in the preview.")
    return notes
