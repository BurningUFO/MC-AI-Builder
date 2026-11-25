"""Search installed BMC4 building materials and draw texture swatches on demand."""

from collections import Counter, defaultdict
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from src.assets.appearance import appearance_notes, first_frame, texture_metadata, tint_rgb
from src.assets.archives import Archives, AssetError
from src.assets.models import ModelLibrary
from src.core.building import BlockState, write_json
from src.design import read_json


KINDS = {
    "wall": ("brick", "tile", "concrete", "plank", "daub", "terracotta", "plaster", "wall"),
    "stone": ("stone", "calcite", "tuff", "andesite", "diorite", "granite", "basalt", "shale", "marble", "slate"),
    "wood": ("plank", "wood", "log", "beam", "oak", "spruce", "birch", "jungle", "acacia", "cherry", "bamboo"),
    "roof": ("shingle", "roof", "copper", "thatch", "tiles"),
    "stairs": ("stairs",),
    "slab": ("slab",),
    "window": ("glass", "window", "lattice", "shutter"),
    "furniture": ("chair", "table", "bench", "shelf", "couch", "seat", "stool", "cabinet", "lectern", "desk", "drawer"),
    "light": ("lantern", "torch", "sconce", "lamp", "bulb", "froglight", "glowstone", "shroomlight", "candle"),
    "decor": ("carved", "trim", "pillar", "ornate", "planter", "vase", "statue", "pot", "tapestry", "mosaic", "banner"),
    "plant": ("leaves", "hedge", "fern", "moss", "flower", "azalea", "sapling"),
}
STYLES = {
    "modern": ("concrete", "polished", "glass", "plaster", "smooth", "quartz"),
    "rustic": ("shingle", "cobblestone", "mossy", "daub", "timber", "beam", "spruce", "thatch"),
    "classical": ("pillar", "marble", "calcite", "chiseled", "carved", "cornice", "sandstone"),
    "industrial": ("iron", "copper", "steel", "grate", "metal", "basalt", "industrial"),
    "fantasy": ("runic", "gilded", "amethyst", "purpur", "crystal", "gothic", "glowing"),
}
STATE_PREFERENCES = {"facing": "north", "axis": "y", "shape": "straight", "type": "bottom",
                     "color": "none", "part": "foot", "hinge": "left"}
ASSET_ERRORS = (AssetError, ValueError, TypeError, KeyError, AttributeError, OSError, BadZipFile)


def language_sources(archives):
    shared = Path(archives.installation["shared_assets"])
    index = shared / "indexes" / f"{archives.installation['shared_asset_index']}.json"
    vanilla = None
    if index.is_file():
        entry = read_json(index)["objects"].get("minecraft/lang/zh_cn.json")
        if entry:
            candidate = shared / "objects" / entry["hash"][:2] / entry["hash"]
            if candidate.is_file():
                vanilla = candidate
    packs = Path(archives.installation["instance_resourcepacks"])
    options = archives.instance / "options.txt"
    line = next((row.split(":", 1)[1] for row in options.read_text(encoding="utf-8-sig").splitlines()
                 if row.startswith("resourcePacks:")), "[]") if options.is_file() else "[]"
    local = []
    for name in json.loads(line):
        if name.startswith("file/"):
            name = name.removeprefix("file/")
            if name == Path(name).name and (packs / name).exists():
                local.append(packs / name)
    return vanilla, local


def language_overrides(vanilla, packs):
    labels = read_json(vanilla) if vanilla else {}
    for pack in packs:
        if pack.is_dir():
            for path in pack.glob("assets/*/lang/zh_cn.json"):
                labels.update(read_json(path))
        else:
            try:
                with ZipFile(pack) as jar:
                    for name in jar.namelist():
                        if name.startswith("assets/") and name.endswith("/lang/zh_cn.json"):
                            labels.update(json.loads(jar.read(name)))
            except (OSError, ValueError, BadZipFile):
                continue
    return labels


def cache_folder(root, archives, vanilla_language, packs):
    jars = [archives.sources["minecraft"].path, *sorted((archives.instance / "mods").glob("*.jar"))]
    local_languages = [file for path in packs for file in (path.glob("assets/*/lang/zh_cn.json") if path.is_dir() else (path,))]
    inputs = jars + ([vanilla_language] if vanilla_language else []) + local_languages
    stamp = [(str(path), stat.st_size, stat.st_mtime_ns) for path in inputs for stat in [path.stat()]]
    key = sha256(json.dumps([1, str(archives.instance), stamp]).encode()).hexdigest()
    return root / ".cache" / "material-library" / key[:20]


def inventory(path, archives, vanilla_language, packs):
    if path.is_file():
        return read_json(path)["blocks"]
    translations = language_overrides(vanilla_language, packs)
    result = {}
    for block_id, source in archives.blockstate_sources():
        mod, name = block_id.split(":", 1)
        label = f"block.{mod}.{name.replace('/', '.')}"
        try:
            info = source.block_info(name)
            zh = translations.get(label) or source.chinese.get(label)
        except ASSET_ERRORS:
            continue
        if not any(info["evidence"].values()) and not zh:
            continue
        result[block_id] = {
            "id": block_id, "mod": mod, "jar": info["source_jar"],
            "en": info["display_name"], "zh": zh,
            "kinds": [key for key, tokens in KINDS.items() if any(token in name for token in tokens)],
            "styles": [key for key, tokens in STYLES.items() if any(token in name for token in tokens)],
        }
    write_json(path, {"blocks": result}, compact=True)
    return result


def add_condition_values(condition, domains):
    for name, value in condition.items():
        if name in {"AND", "OR"}:
            for term in value:
                add_condition_values(term, domains)
        else:
            domains[name].update(part for part in value.split("|") if not part.startswith("!"))
            if any(part.startswith("!") for part in value.split("|")):
                domains[name].update(part[1:] for part in value.split("|") if part.startswith("!"))


def sample_states(spec):
    domains = defaultdict(set)
    variants = [dict(part.split("=", 1) for part in key.split(",") if part)
                for key in spec.get("variants", {})]
    for variant in variants:
        add_condition_values(variant, domains)
    for part in spec.get("multipart", []):
        add_condition_values(part.get("when", {}), domains)
    for values in domains.values():
        if values & {"true", "false"}:
            values.update(("true", "false"))

    def rank(name, value):
        preferred = STATE_PREFERENCES.get(name)
        if name == "half":
            return value not in {"lower", "bottom"}, value
        return value != (preferred or "false"), value

    if variants:
        variants.sort(key=lambda items: tuple(rank(key, value) for key, value in sorted(items.items())))
    elif domains:
        variants = [{name: min(values, key=lambda value: rank(name, value)) for name, values in domains.items()}]
    return dict(sorted((key, sorted(values)) for key, values in domains.items())), variants or [{}]


def swatch_image(library, record):
    from PIL import Image, ImageChops

    ref = record["texture"]
    with Image.open(BytesIO(library.texture(ref))) as raw:
        image = raw.crop(first_frame(record["texture_metadata"], *raw.size)).convert("RGBA")
    tint = record["tint"]
    if tint:
        rgb = ImageChops.multiply(image.convert("RGB"), Image.new("RGB", image.size, tuple(tint)))
        rgb.putalpha(image.getchannel("A"))
        return rgb
    return image


def inspect(library, row):
    block_id = row["id"]
    result = {"properties": {}, "compatible_example": None, "status": "unsupported", "texture": None,
              "rgb": None, "notes": []}
    try:
        spec = library.blockstate(block_id)
        result["properties"], examples = sample_states(spec)
        for props in examples[:8]:
            state = BlockState(block_id, tuple(props.items()))
            try:
                baked = library.bake(state)
            except ASSET_ERRORS as error:
                result["notes"] = [str(error)]
                continue
            ordered = Counter((face["texture"], face["tint"]) for face in baked["faces"]
                              if face["cull"] not in {"up", "down"})
            ref, tint = (ordered.most_common(1)[0][0] if ordered else
                         (baked["faces"][0]["texture"], baked["faces"][0]["tint"]))
            textures = {face["texture"] for face in baked["faces"]}
            metadata = {key: texture_metadata(library.archives, key) for key in textures}
            notes = appearance_notes(state, baked, metadata.values())
            result.update(compatible_example=state.key, example_properties=props,
                          status="approximate" if notes else "static", texture=ref,
                          texture_metadata=metadata[ref], tint=tint_rgb(block_id, tint), notes=notes)
            image = swatch_image(library, result)
            pixels = list(image.getdata())
            opacity = sum(p[3] for p in pixels)
            if opacity:
                result["rgb"] = [round(sum(p[ch] * p[3] for p in pixels) / opacity) for ch in range(3)]
            break
    except ASSET_ERRORS as error:
        result.update(status="unsupported", rgb=None, texture=None, notes=[str(error)])
    return result


def contact_sheet(path, rows, library):
    from PIL import Image, ImageDraw, ImageFont

    columns = min(4, len(rows))
    width, height = 302, 154
    sheet = Image.new("RGB", (columns * width + 22, ((len(rows) - 1) // columns + 1) * height + 65), "#e5e7e3")
    draw = ImageDraw.Draw(sheet)
    font_path = Path(os.environ.get("WINDIR", "")) / "Fonts" / "msyh.ttc"
    font = ImageFont.truetype(str(font_path), 16) if font_path.is_file() else ImageFont.load_default(size=16)
    small = ImageFont.truetype(str(font_path), 12) if font_path.is_file() else ImageFont.load_default(size=12)
    draw.text((15, 12), "BMC4   /   JAR texture samples", font=font, fill="#242a27")
    draw.text((15, 35), "Static sample, first frame and representative face where applicable", font=small, fill="#505b54")
    for i, row in enumerate(rows):
        left, top = (i % columns) * width + 10, (i // columns) * height + 58
        draw.rounded_rectangle((left, top, left + width - 8, top + height - 8), 7, fill="#f8f9f6")
        x, y = left + 9, top + 9
        draw.rectangle((x, y, x + 80, y + 80), fill="#cfd4d0")
        if row["texture"]:
            image = swatch_image(library, row)
            image.thumbnail((80, 80), Image.Resampling.NEAREST)
            ratio = 80 / max(image.size)
            image = image.resize(tuple(max(1, round(n * ratio)) for n in image.size), Image.Resampling.NEAREST)
            sheet.paste(image, (x, y), image)
        title = (row["zh"] if font_path.is_file() else None) or row["en"] or row["id"].split(":", 1)[1]
        draw.text((x + 92, y + 3), title[:19], font=font, fill="#242a27")
        draw.text((x + 92, y + 29), row["mod"], font=small, fill="#505b54")
        draw.text((x + 92, y + 52), row["status"], font=small, fill="#383e39" if row["status"] == "static" else "#9a4e2b")
        draw.text((x, y + 88), row["id"][:45], font=small, fill="#242a27")
        if row["rgb"]:
            draw.text((x, y + 109), "#%02x%02x%02x" % tuple(row["rgb"]), font=small, fill="#505b54")
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, format="PNG")


def find_materials(args, root):
    try:
        from PIL import ImageColor
    except ImportError as error:
        raise ValueError("Material searches need Pillow. Install it with python -m pip install Pillow.") from error
    if args.instance is None:
        raise ValueError("Supply --instance or set MCBUILDER_INSTANCE.")
    if args.limit < 1:
        raise ValueError("--limit must be positive.")
    color = ImageColor.getcolor(args.color, "RGB") if args.color else None
    with Archives(args.instance, extra_mods=tuple(args.mod or ())) as archives:
        archives.discover()
        vanilla, packs = language_sources(archives)
        folder = cache_folder(root, archives, vanilla, packs)
        blocks = inventory(folder / "inventory.json", archives, vanilla, packs)
        candidates = search(blocks, args)
        if not candidates:
            raise ValueError("No installed block IDs match those filters.")
        path = folder / "appearance.json"
        cache = read_json(path) if path.is_file() else {}
        before = len(cache)
        library = ModelLibrary(archives)
        results = []
        for row in candidates if color else candidates[:args.limit]:
            block_id = row["id"]
            if block_id not in cache:
                cache[block_id] = inspect(library, row)
            results.append(row | cache[block_id])
        if before != len(cache):
            write_json(path, cache, compact=True)
        if color:
            results.sort(key=lambda row: (row["rgb"] is None,
                                          sum((a - b) ** 2 for a, b in zip(color, row["rgb"] or (0, 0, 0)))))
        results = results[:args.limit]
        if args.sheet:
            contact_sheet(args.sheet, results, library)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for row in results:
            names = " / ".join(value for value in (row["zh"], row["en"]) if value)
            print(f"{row['id']}  {names}  [{row['status']}]")
            if row["compatible_example"]:
                print(f"  {row['compatible_example']}   {row['texture']}   {row['rgb']}")
            for note in row["notes"]:
                print(f"  {note}")
        if args.sheet:
            print(args.sheet.resolve())


def search(blocks, args):
    if args.id:
        missing = set(args.id) - blocks.keys()
        if missing:
            raise ValueError(f"No unambiguous installed blockstate with block evidence: {', '.join(sorted(missing))}")
        return [blocks[key] for key in dict.fromkeys(args.id)]
    terms = [term.casefold().replace("_", " ") for term in args.terms]
    results = []
    for row in blocks.values():
        if (args.mod and row["mod"] not in args.mod or args.kind and args.kind not in row["kinds"]
                or args.style and args.style not in row["styles"]):
            continue
        text = " ".join(value for value in (row["id"], row["zh"], row["en"]) if value).casefold().replace("_", " ")
        if all(term in text for term in terms):
            results.append(row)
    return sorted(results, key=lambda row: (not bool(row["en"] or row["zh"]), row["id"]))


def add_parser(commands, instance):
    parser = commands.add_parser("materials", help="find installed materials and make a texture contact sheet")
    parser.add_argument("terms", nargs="*", help="ID, Chinese or English search terms")
    parser.add_argument("--instance", type=Path, default=instance)
    parser.add_argument("--id", action="append", help="inspect an exact installed block ID, repeatable")
    parser.add_argument("--mod", action="append", help="filter by installed namespace, repeatable")
    parser.add_argument("--kind", choices=tuple(KINDS))
    parser.add_argument("--style", choices=tuple(STYLES), help="filter by suggested architectural style")
    parser.add_argument("--color", help="rank by named color or #RRGGBB texture color")
    parser.add_argument("--limit", type=int, default=16)
    parser.add_argument("--sheet", type=Path, help="save the selected texture swatches as PNG")
    parser.add_argument("--json", action="store_true", help="include names, properties and compatibility as JSON")
