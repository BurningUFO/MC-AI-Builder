"""Verify a starter palette and cache the exact static resources it uses."""

from collections import Counter
import csv
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile

from src.assets.archives import Archives, AssetError, location
from src.assets.models import ModelLibrary
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


def cache_texture(project_root: Path, library: ModelLibrary, reference: str) -> dict:
    namespace, name = location(reference)
    raw = library.texture(reference)
    digest = sha256(raw).hexdigest()
    relative = Path("assets", "textures", namespace, f"{name}-{digest[:12]}.png")
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
    return {"file": relative.as_posix(), "sha256": digest,
            "source_jar": source.path.name,
            "source_texture": source_texture}


def excluded_examples(library: ModelLibrary) -> dict:
    examples = {}
    for block_id, properties in EXCLUDED_EXAMPLES.items():
        state = state_of(block_id, properties)
        try:
            library.bake(state)
        except AssetError as error:
            examples[state.key] = {"status": "outside_static_preview", "detail": str(error)}
    return examples


def build_index(root: Path, building: Building, library: ModelLibrary, choices: dict, *, outline_observations=False) -> dict:
    stats = building.state_counts()
    blocks = {}
    baked = {}
    references = set()
    unrendered = []
    for state, count in sorted(stats.items(), key=lambda entry: entry[0].key):
        info = blocks.get(state.block_id)
        try:
            if info is None:
                info = library.archives.block_info(state.block_id) | {"count": 0, "states": []}
            asset = library.bake(state)
            textures = {face["texture"] for face in asset["faces"]}
            if outline_observations:
                for ref in sorted(textures - references):
                    library.texture(ref)
        except AssetError:
            if not outline_observations:
                raise
            unrendered.append(state.key)
            continue
        blocks[state.block_id] = info
        info["count"] += count
        info["states"].append(state.key)
        references.update(textures)
        baked[state.key] = {"block_id": state.block_id, "properties": dict(state.properties),
                            "count": count, **asset}
    result = {
        "schema_version": 1,
        "verification_scope": "Installed JAR resources only. No live Forge registry, resource pack overrides, or runtime rendering.",
        "installation": library.archives.installation,
        "mods": library.archives.mod_summary(),
        "starter_palette": choices,
        "excluded_examples": excluded_examples(library),
        "blocks": blocks,
        "textures": {ref: cache_texture(root, library, ref) for ref in sorted(references)},
        "states": baked,
    }
    if unrendered:
        result["unrendered_observations"] = unrendered
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
                                 len(entry["states"]), "static_assets_verified"))
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

