"""Verify a starter palette and cache the exact static resources it uses."""

from collections import Counter
import csv
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile

from src.assets.appearance import appearance_notes, texture_metadata
from src.assets.archives import Archives, AssetError, location
from src.assets.models import ModelLibrary, NON_VISUAL_PROPERTIES, check_loader, model_groups
from src.core.building import BlockState, Building, write_json
from src.generators.medieval import VANILLA, slab, stairs


# The blockstate and models from the live installation must match each probe.
STARTER = {
    "stone": ("twigs:polished_tuff_bricks", [{}]),
    "planks": ("chipped:stacked_spruce_planks", [{}]),
    "plaster": ("supplementaries:daub", [{}]),
    "brace": ("supplementaries:daub_cross_brace", [{}]),
    "frame": ("supplementaries:daub_frame", [{}]),
    "rafter": ("decorative_blocks:dark_oak_beam", [{"axis": "x"}]),
    "tiles": ("quark:red_shingles", [{}]),
    "roof_stairs": ("quark:red_shingles_stairs", [stairs("north"), stairs("south")]),
    "roof_slab": ("quark:red_shingles_slab", [slab("bottom")]),
    "shelves": ("quark:dark_oak_bookshelf", [{}]),
    "table": ("twigs:spruce_table", [{f"table_leg{i}": "true" for i in range(1, 5)}]),
    "chair": ("handcrafted:spruce_chair", [
        {"color": color, "facing": direction, "waterlogged": "false"}
        for color in ("none", "red") for direction in ("east", "west", "north", "south")
    ]),
}

EXCLUDED_EXAMPLES = {
    "chipped:brick_bordered_cobblestone": {},
    "supplementaries:timber_frame": {"has_block": "true"},
    "minecraft:red_bed": {},
}


def state_of(block_id: str, properties: dict) -> BlockState:
    return BlockState(block_id, tuple(properties.items()))


def choose_palette(library: ModelLibrary, *, defaults=VANILLA, candidates=STARTER) -> tuple[dict[str, str], dict]:
    palette = defaults.copy()
    decisions = {}
    for role, (block_id, probes) in candidates.items():
        entry = {"candidate": block_id, "selected": palette[role]}
        if library.archives.vanilla_only:
            entry["status"] = "vanilla_requested"
        else:
            try:
                info = library.archives.block_info(block_id)
                if not any(info["evidence"].values()):
                    raise AssetError(f"{block_id}: blockstate lacks a matching item, block loot table or block translation")
                for properties in probes:
                    library.bake(state_of(block_id, properties))
            except AssetError as error:
                entry.update(status="vanilla_fallback", detail=str(error))
            else:
                palette[role] = block_id
                entry.update(status="static_assets_verified", selected=block_id)
        decisions[role] = entry
    return palette, decisions


def cache_texture(project_root: Path, library: ModelLibrary, reference: str, *, cache=False, appearance=False) -> dict:
    namespace, name = location(reference)
    raw = library.texture(reference)
    digest = sha256(raw).hexdigest()
    relative = Path("assets", "textures", namespace, f"{name}-{digest[:12]}.png")
    if cache:
        relative = Path(".cache") / relative
    target = project_root / relative
    if not target.is_file() or sha256(target.read_bytes()).hexdigest() != digest:
        target.parent.mkdir(parents=True, exist_ok=True)
        scratch = None
        try:
            with NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                scratch = Path(stream.name)
                stream.write(raw)
            scratch.replace(target)
        finally:
            if scratch is not None:
                scratch.unlink(missing_ok=True)
    source_texture = f"assets/{namespace}/textures/{name}.png"
    source = library.archives.source_for(namespace, source_texture)
    info = {"file": relative.as_posix(), "sha256": digest,
            "source_jar": source.path.name,
            "source_texture": source_texture}
    if appearance:
        info.update(texture_metadata(library.archives, reference))
    return info


def excluded_examples(library: ModelLibrary) -> dict:
    examples = {}
    for block_id, properties in EXCLUDED_EXAMPLES.items():
        state = state_of(block_id, properties)
        try:
            library.bake(state)
        except AssetError as error:
            examples[state.key] = {"status": "outside_static_preview", "detail": str(error)}
    return examples



def vanilla_fallback(state, library):
    """Bake only a visual stand-in while retaining the source state as the catalogue key."""
    path = state.block_id.split(":", 1)[1]
    props = dict(state.properties)
    wood = next((wood for wood in ("dark_oak", "spruce", "birch", "mangrove", "jungle", "acacia", "cherry", "oak")
                 if wood in path), "oak")
    rocky = any(word in path for word in ("quark", "bricks", "stone", "tuff", "shale", "slate", "calcite", "shingles"))
    material = "brick" if "shingles" in path else "stone_brick" if rocky else wood
    matching = {}
    if path.endswith("_stairs") or path.endswith(("_chair", "_bench", "_couch")):
        block = material + "_stairs"
        matching = {"facing": props.get("facing", "north"), "half": props.get("half", "bottom"),
                    "shape": props.get("shape", "straight")}
    elif path.endswith("_slab"):
        block = material + "_slab"
        matching = {"type": props.get("type", "bottom")}
    elif path.endswith(("_log", "_wood", "_beam", "_stem", "_hyphae")):
        block = wood + "_log"
        matching = {"axis": props.get("axis", "y")}
    elif path.endswith(("_leaves", "_sapling", "_flower", "_lavender")):
        block = "oak_leaves" if path.endswith("_leaves") else "dandelion"
    elif "glass" in path:
        block = "glass"
    elif "bookshelf" in path:
        block = "bookshelf"
    else:
        block = "bricks" if "shingles" in path else "stone_bricks" if rocky else wood + "_planks"
    for candidate in (state_of("minecraft:" + block, matching), state_of("minecraft:oak_planks", {})):
        try:
            asset = library.bake(candidate)
            for reference in {face["texture"] for face in asset["faces"]}:
                library.texture(reference)
            return candidate.key, asset
        except (AssetError, ValueError, KeyError, TypeError):
            continue
    raise AssetError("The vanilla client is missing both stand-in materials.")


def build_index(root: Path, building: Building, library: ModelLibrary, choices: dict, *,
                outline_observations=False, fallback_mods=False, standalone=False) -> dict:
    stats = building.state_counts()
    blocks = {}
    baked = {}
    references = set()
    unrendered = []
    fallbacks = {}
    for state, count in sorted(stats.items(), key=lambda entry: entry[0].key):
        info = blocks.get(state.block_id)
        if standalone:
            info = info or library.archives.block_info(state.block_id) | {"count": 0, "states": []}
            if any(value not in {"true", "false"} for key, value in state.properties
                   if key in NON_VISUAL_PROPERTIES):
                raise AssetError(f"{state.key}: expected true or false for the nonvisual state properties")
        try:
            if info is None:
                info = library.archives.block_info(state.block_id) | {"count": 0, "states": []}
            asset = library.bake(state)
            textures = {face["texture"] for face in asset["faces"]}
            if outline_observations or fallback_mods:
                for ref in sorted(textures - references):
                    library.texture(ref)
        except (AssetError, ValueError, KeyError, TypeError) as error:
            if fallback_mods and (standalone or not state.block_id.startswith("minecraft:")):
                if standalone:
                    spec = library.blockstate(state.block_id)
                    try:
                        check_loader(spec, state.block_id)
                    except AssetError:
                        pass  # Dynamic loaders expose their own state interpretation.
                    else:
                        if "variants" in spec or "multipart" in spec:
                            model_groups(spec, state)
                substitute, asset = vanilla_fallback(state, library)
                if info is None:
                    info = {"source_jar": None, "display_name": state.block_id, "count": 0, "states": []}
                info["preview_status"] = "vanilla_fallback"
                fallbacks[state.key] = {"status": "vanilla_fallback", "rendered_as": substitute, "detail": str(error)}
                textures = {face["texture"] for face in asset["faces"]}
            elif outline_observations:
                unrendered.append(state.key)
                continue
            else:
                raise
        blocks[state.block_id] = info
        info["count"] += count
        info["states"].append(state.key)
        references.update(textures)
        baked[state.key] = {"block_id": state.block_id, "properties": dict(state.properties),
                            "count": count, **asset}
    textures = {ref: cache_texture(root, library, ref, cache=fallback_mods or standalone, appearance=standalone)
                for ref in sorted(references)}
    approximations = {}
    if standalone:
        entity_states = {block.state.key for block in building.blocks.values() if block.block_entity is not None}
        for state in stats:
            asset = baked[state.key]
            notes = appearance_notes(state, asset, [textures[face["texture"]] for face in asset["faces"]])
            if state.key in entity_states:
                notes.append("The blueprint contains block-entity data; the preview shows static faces.")
            if notes:
                approximations[state.key] = notes
                blocks[state.block_id].setdefault("preview_status", "static_approximation")
    result = {
        "schema_version": 1,
        "verification_scope": "Installed JAR resources only. No live Forge registry, resource pack overrides, or runtime rendering.",
        "installation": library.archives.installation,
        "mods": library.archives.mod_summary(),
        "starter_palette": choices,
        "excluded_examples": excluded_examples(library),
        "blocks": blocks,
        "textures": textures,
        "states": baked,
    }
    if unrendered:
        result["unrendered_observations"] = unrendered
    if fallbacks:
        result["render_fallbacks"] = fallbacks
    if approximations:
        result["render_approximations"] = approximations
    return result


def write_materials(path: Path, catalog: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="", dir=path.parent, delete=False) as stream:
            scratch = Path(stream.name)
            writer = csv.writer(stream)
            writer.writerow(("block_id", "count", "display_name", "source_jar", "state_count", "preview"))
            for block_id, entry in sorted(catalog["blocks"].items()):
                writer.writerow((block_id, entry["count"], entry["display_name"], entry["source_jar"],
                                 len(entry["states"]), entry.get("preview_status", "static_assets_verified")))
        scratch.replace(path)
    finally:
        if scratch is not None:
            scratch.unlink(missing_ok=True)


def save_project(output: Path, building: Building, catalog: dict) -> None:
    structure = output / "building.json"
    building.write(structure)
    catalog["building_sha256"] = sha256(structure.read_bytes()).hexdigest()
    write_json(output / "material-index.json", catalog)
    write_materials(output / "materials.csv", catalog)

