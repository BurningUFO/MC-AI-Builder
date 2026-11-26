"""Design, generate and review Minecraft buildings in standalone Blender projects."""

import argparse
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from zipfile import BadZipFile

from src.assets.archives import Archives
from src.assets.catalog import build_index, choose_palette, save_project, write_materials
from src.assets.materials import add_parser as add_material_parser, find_materials
from src.assets.models import ModelLibrary
from src.circulation import trace_walk
from src.generators.bridge import BRIDGE_MODDED, BRIDGE_VANILLA, generate_bridge
from src.generators.ember_gate import GATE_MODDED, GATE_VANILLA, generate_ember_gate
from src.generators.enchanting import LIBRARY_MODDED, LIBRARY_VANILLA, generate_library
from src.generators.flat_bridge import FLAT_BRIDGE_MODDED, FLAT_BRIDGE_VANILLA, generate_flat_bridge
from src.generators.grain_tower import (
    CRYSTAL_PALETTE, DISPLAY_STATES, TOWER_MODDED, TOWER_SAMPLES, TOWER_VANILLA,
    generate_crystal_tower, generate_grain_tower,
)
from src.generators.medieval import generate_house
from src.assets.projects import valid_name
from src.core.building import Building, write_json
from src.design import (
    ARTIFACTS, RENDER_RUN, SOURCE, VIEWS, compare_blueprints, create_project, digest, image_selection,
    input_hashes, project_design, read_groups, read_json, restore, snapshot, stage_settings, write_groups,
)
from src.renderers.views import FULL_SCENE, PREVIEWS, VERIFICATION_FILE, Box, preview_for, view_catalog


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BLENDER = ROOT / "tools" / "blender" / "blender.exe"
RENDER_SCRIPT = ROOT / "src" / "renderers" / "blender_preview.py"
DESIGN_RENDER_SCRIPT = ROOT / "src" / "renderers" / "design_preview.py"
VERIFY_SCRIPT = ROOT / "src" / "renderers" / "verify_preview.py"
DEFAULT_SAMPLE = "medieval_house"
PRESETS = ("quick", "review", "final")
GENERATORS = {
    "medieval_house": generate_house,
    "enchanting_library": generate_library,
    "warehouse_bridge": generate_bridge,
    "warehouse_flat_bridge": generate_flat_bridge,
    "ember_gate": generate_ember_gate,
    "grain_tower": generate_grain_tower,
    "crystal_tower": generate_crystal_tower,
}
PALETTES = {
    "enchanting_library": (LIBRARY_VANILLA, LIBRARY_MODDED),
    "warehouse_bridge": (BRIDGE_VANILLA, BRIDGE_MODDED),
    "warehouse_flat_bridge": (FLAT_BRIDGE_VANILLA, FLAT_BRIDGE_MODDED),
    "ember_gate": (GATE_VANILLA, GATE_MODDED),
    "grain_tower": (TOWER_VANILLA, TOWER_MODDED),
    "crystal_tower": (CRYSTAL_PALETTE, {}),
}


def project_paths(vanilla: bool, sample: str = DEFAULT_SAMPLE, workspace=ROOT) -> tuple[Path, Path]:
    if not valid_name(sample):
        raise ValueError("Project name must be a single directory name.")
    name = f"{sample}_vanilla" if vanilla else sample
    return workspace / "projects" / name, workspace / "previews" / name


def add_planting_preview_assets(catalog, library):
    display = Building("Planted crops and hydrated soil")
    for x, state in enumerate(DISPLAY_STATES):
        display.place((x, 0, 0), state.block_id, **dict(state.properties))
    assets = build_index(ROOT, display, library, {})
    catalog["states"].update(assets["states"])
    catalog["textures"].update(assets["textures"])


def generate(args) -> None:
    project, _ = project_paths(args.vanilla, args.sample, args.workspace)
    if (project / SOURCE).is_file():
        generate_design(args, project)
        return
    if (project.parent / args.sample / SOURCE).is_file():
        raise ValueError("--vanilla selects a separate built-in sample. A project design uses its specified IDs.")
    if args.sample not in GENERATORS:
        raise ValueError(f"No {SOURCE} in {project}. Use new --sample {args.sample!r} to start a design.")
    forest_library = args.sample == "enchanting_library"
    extra_mods = ("biomesoplenty",) if forest_library else ()
    with Archives(args.instance, vanilla_only=args.vanilla, extra_mods=extra_mods) as archives:
        archives.discover()
        library = ModelLibrary(archives)
        materials = PALETTES.get(args.sample)
        if materials is None:
            palette, choices = choose_palette(library)
        else:
            defaults, candidates = materials
            palette, choices = choose_palette(library, defaults=defaults, candidates=candidates)
        building = GENERATORS[args.sample](palette)
        catalog = build_index(ROOT, building, library, choices)
        if args.sample in TOWER_SAMPLES:
            add_planting_preview_assets(catalog, library)
        if forest_library:
            catalog["reference_video"] = {
                "url": "https://www.bilibili.com/video/BV1LKdGBAEpd",
                "title": "[minecraft我的世界]建筑教程：附魔台",
                "adaptation": "Timber entrances and a central enchanting area under an original open-book roof, with cherry accents and lavender borders.",
            }
        save_project(project, building, catalog)
    for role, choice in choices.items():
        if choice["status"] == "vanilla_fallback":
            print(f"{role}: {choice['selected']} ({choice['detail']})")
    mod_blocks = sum(entry["count"] for name, entry in catalog["blocks"].items() if not name.startswith("minecraft:"))
    print(f"{len(building.blocks)} blocks, {mod_blocks} from mods, {len(catalog['textures'])} textures.")
    print(project / "building.json")


def generate_design(args, project):
    building, groups, source_hash = project_design(project)
    namespaces = {state.block_id.split(":")[0] for state in building.state_counts()} - {"minecraft"}
    with Archives(args.instance, vanilla_only=args.vanilla, extra_mods=tuple(sorted(namespaces))) as archives:
        archives.discover()
        catalog = build_index(ROOT, building, ModelLibrary(archives), {}, standalone=True,
                              fallback_mods=args.approximate)
    with TemporaryDirectory(prefix=".generate-", dir=project) as temp:
        staged = Path(temp)
        save_project(staged, building, catalog)
        write_groups(staged, groups, source_hash)
        if digest(project / SOURCE) != source_hash:
            raise ValueError("design.py changed while indexing. Generate again.")
        for name in ARTIFACTS[2:]:
            (staged / name).replace(project / name)
    print(f"{len(building.blocks)} blocks, {len(catalog['blocks'])} block IDs, {len(catalog['textures'])} textures.")
    for key, record in catalog.get("render_fallbacks", {}).items():
        print(f"Visual substitution: {key} -> {record['rendered_as']} ({record['detail']})")
    print(project / "building.json")


def prepare_environment() -> dict[str, str]:
    cache = ROOT / ".cache"
    temp, optix, cuda = (cache / name for name in ("tmp", "optix", "cuda"))
    for folder in (temp, optix, cuda):
        folder.mkdir(parents=True, exist_ok=True)
    return dict(os.environ, TEMP=str(temp), TMP=str(temp), OPTIX_CACHE_PATH=str(optix),
                CUDA_CACHE_PATH=str(cuda), PYTHONDONTWRITEBYTECODE="1")


def run_blender(executable: Path, script: Path, *script_args, scene: Path | None = None,
                timeout: float | None = None) -> None:
    command = [str(executable), "--background", "--factory-startup", "--offline-mode", "--disable-autoexec"]
    if scene is not None:
        command.append(str(scene))
    command.extend(["--python-exit-code", "1", "--python", str(script), "--"])
    command.extend(str(value) for value in script_args)
    environment = prepare_environment()
    environment.pop("MC_ARCHITECT_API_KEY", None)
    subprocess.run(command, cwd=ROOT, env=environment, check=True, timeout=timeout)


def verify_project(executable, data, folder, sample, preview, *, timeout=None):
    run_blender(executable, VERIFY_SCRIPT, data, folder, sample, scene=folder / preview.scene_file, timeout=timeout)


def refresh_project_index(data, building, instance, *, vanilla=False, standalone=False, approximate=False):
    digest = sha256(data.read_bytes()).hexdigest()
    namespaces = {block.state.block_id.split(":")[0] for block in building.blocks.values()} - {"minecraft"}
    with Archives(instance, vanilla_only=vanilla, extra_mods=tuple(sorted(namespaces))) as archives:
        archives.discover()
        library = ModelLibrary(archives)
        catalog = build_index(ROOT, building, library, {}, fallback_mods=approximate or not standalone,
                              standalone=standalone)
        if not (data.parent / SOURCE).is_file() and data.parent.name.removesuffix("_vanilla") in TOWER_SAMPLES:
            add_planting_preview_assets(catalog, library)
    if Building.read(data) != building or sha256(data.read_bytes()).hexdigest() != digest:
        raise ValueError("Building changed while indexing.")
    catalog["building_sha256"] = digest
    write_json(data.with_name("material-index.json"), catalog)
    write_materials(data.with_name("materials.csv"), catalog)


def render_project(blender, data, output, sample, *, quality="final", instance=None, vanilla=False, timeout=None):
    data = data.resolve(strict=True)
    output = output.resolve()
    building = Building.read(data)
    preview = preview_for(sample, building, name=data.parent.name)
    index = data.with_name("material-index.json")
    digest = sha256(data.read_bytes()).hexdigest()
    if instance is not None and not verified_index(index, digest):
        refresh_project_index(data, building, instance, vanilla=vanilla)
    if not verified_index(index, digest):
        raise ValueError("Building and material index differ. Supply the Minecraft instance to refresh the index.")
    executable = blender.resolve(strict=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".render-", dir=output.parent) as temp:
        staged = Path(temp)
        run_blender(executable, RENDER_SCRIPT, data, index, ROOT, staged, sample, quality, timeout=timeout)
        verify_project(executable, data, staged, sample, preview, timeout=timeout)
        output.mkdir(parents=True, exist_ok=True)
        for name in preview.files:
            (staged / name).replace(output / name)
    return output / preview.scene_file


def verified_index(path, digest, *, textures_root=None):
    try:
        index = read_json(path)
        return index.get("building_sha256") == digest and (
            textures_root is None or all((textures_root / info["file"]).is_file()
                                         for info in index["textures"].values()))
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        return False


def verify(args):
    project, folder = project_paths(args.vanilla, args.sample, args.workspace)
    new_folder = folder / (args.preset or "final")
    if args.preset or (project / SOURCE).is_file() or (new_folder / RENDER_RUN).is_file():
        report = read_json(new_folder / RENDER_RUN)
        if not report["blend"]:
            raise ValueError("This preview has no saved .blend. Render with --save-scene or use review/final.")
        folder = new_folder.resolve()
        run_blender(args.blender.resolve(strict=True), VERIFY_SCRIPT, project.resolve(), folder,
                    scene=folder / report["blend"])
        return
    data = project / "building.json"
    preview = preview_for(args.sample, Building.read(data), name=project.name)
    verify_project(args.blender.resolve(strict=True), data, folder, args.sample, preview)


def render(args):
    project, output = project_paths(args.vanilla, args.sample, args.workspace)
    if ((project / SOURCE).is_file() or (project / VIEWS).is_file()
            or args.preset or args.view or args.look or args.save_scene or args.cpu or args.approximate):
        render_design(args, project.resolve(), output.resolve())
        return
    scene = render_project(args.blender, project / "building.json", output, args.sample,
                           quality=args.quality or "final", instance=args.instance, vanilla=args.vanilla)
    print(scene)


def design_render_inputs(args, project, *, refresh=True):
    data = project / "building.json"
    building = Building.read(data)
    hashes = input_hashes(project)
    read_groups(project, building, hashes)
    building_digest = hashes["building.json"]
    index = project / "material-index.json"
    compatible = verified_index(index, building_digest, textures_root=ROOT)
    if refresh and args.instance is not None and not compatible:
        refresh_project_index(data, building, args.instance, vanilla=args.vanilla,
                              standalone=(project / SOURCE).is_file(), approximate=args.approximate)
        compatible = verified_index(index, building_digest, textures_root=ROOT)
    if not compatible:
        raise ValueError("The material index or local textures do not match. Generate the design or supply --instance.")

    return building, input_hashes(project)


def view_settings(path):
    settings = read_json(path) if path.exists() else {}
    if not isinstance(settings, dict) or settings.keys() - {"views", "sections", "stage"}:
        raise ValueError("views.json accepts views, sections and stage.")
    return settings


def run_design_render(blender, job, staged):
    write_json(staged / "job.json", job)
    run_blender(blender.resolve(strict=True), DESIGN_RENDER_SCRIPT, staged / "job.json", staged, ROOT)
    if job["inputs"] != input_hashes(Path(job["project"])):
        raise ValueError("The project changed while rendering. Run the preview again.")
    (staged / "job.json").unlink()


def render_design(args, project, output):
    building, inputs = design_render_inputs(args, project)
    settings = view_settings(project / VIEWS)
    legacy = args.sample in PREVIEWS and not settings and SOURCE not in inputs
    if legacy:
        preview = preview_for(args.sample, building)
        views = {Path(view.image).stem: view for view in preview.views}
        scene_file, full, sections = preview.scene_file, preview.full_scene, {}
    else:
        views, sections = view_catalog(settings, building)
        scene_file, full = project.name + ".blend", FULL_SCENE
    preset = args.preset or {"draft": "quick", "final": "final"}.get(args.quality, "review")
    selected = image_selection(views, args.view)
    if not args.view and preset != "final":
        selected = selected[:1 if preset == "quick" else 4]
    save_scene = args.save_scene or preset != "quick"
    job = {"project": str(project), "sample": args.sample, "inputs": inputs, "preset": preset,
           "presentation": args.look or "showcase", "cpu": args.cpu, "legacy_views": legacy, "full_scene": full,
           "stage": stage_settings(settings.get("stage", {})), "sections": sections, "render": selected,
           "blend": scene_file if save_scene else None,
           "cameras": {key: view._asdict() for key, view in views.items() if save_scene or key in selected}}
    output.mkdir(parents=True, exist_ok=True)
    folder = output / preset
    with TemporaryDirectory(prefix=".render-", dir=output) as temp:
        staged = Path(temp)
        run_design_render(args.blender, job, staged)
        publish_preview(folder, staged)
    run = read_json(folder / RENDER_RUN)
    for record in run["images"].values():
        print(folder / record["file"])
    if run["blend"]:
        print(folder / run["blend"])


def preview_files(run):
    names = [row["file"] for row in run["images"].values()]
    if run["blend"]:
        names.append(run["blend"])
    if run["preset"] == "final":
        names.append(VERIFICATION_FILE)
    return set(names)


def publish_preview(folder, staged):
    folder.mkdir(exist_ok=True)
    path = folder / RENDER_RUN
    previous = preview_files(read_json(path)) if path.is_file() else set()
    if (folder / VERIFICATION_FILE).is_file():
        previous.add(VERIFICATION_FILE)
    files = preview_files(read_json(staged / RENDER_RUN))
    for name in sorted(files):
        (staged / name).replace(folder / name)
    (staged / RENDER_RUN).replace(path)
    for name in previous - files:
        if name == Path(name).name and Path(name).suffix in {".png", ".blend", ".json"}:
            (folder / name).unlink(missing_ok=True)


def open_project(args, project, previews):
    presets = (args.preset,) if args.preset else reversed(PRESETS)
    for preset in presets:
        folder = previews / preset
        if (folder / RENDER_RUN).is_file():
            name = read_json(folder / RENDER_RUN)["blend"]
            if name and (folder / name).is_file():
                scene = folder / name
                break
    else:
        if args.preset or not (project / "building.json").is_file():
            raise ValueError("No saved .blend for this project. Render in review or final mode first.")
        preview = preview_for(args.sample, Building.read(project / "building.json"), name=project.name)
        scene = previews / preview.scene_file
    executable = args.blender.resolve(strict=True)
    scene = scene.resolve(strict=True)
    subprocess.Popen([str(executable), "--offline-mode", "--disable-autoexec", str(scene)],
                     cwd=ROOT, env=prepare_environment())
    print(scene)


def compare(args):
    a, b = (path / "building.json" if path.is_dir() else path for path in (args.before, args.after))
    region = Box(tuple(args.bounds[:3]), tuple(args.bounds[3:])) if args.bounds else Box()
    if args.bounds and any(low > high for low, high in zip(*region)):
        raise ValueError("Each minimum comparison coordinate must be at most its maximum.")
    result = compare_blueprints(a, b, region)
    if args.out:
        write_json(args.out, result)
    print(f"Added {result['added']}, removed {result['removed']}, replaced {result['replaced']}; "
          f"{result['outside']} changes outside the requested bounds.")
    print(f"Changed bounds: {result['bounds']}")
    return int(result["outside"] > 0)


def compare_views(args):
    try:
        from PIL import Image, ImageDraw, ImageOps
    except ImportError as error:
        raise ValueError("compare-views needs Pillow, also used by the materials command.") from error

    output = args.out.resolve()
    if output.exists():
        raise FileExistsError(output)
    before, after = (path.resolve(strict=True) for path in (args.before, args.after))
    left, left_inputs = design_render_inputs(args, before, refresh=False)
    right, right_inputs = design_render_inputs(args, after, refresh=False)
    settings = view_settings(args.views_file.resolve(strict=True) if args.views_file else before / VIEWS)
    views, sections = view_catalog(settings, left)
    selected = image_selection(views, args.view)
    references = {}
    for key, filename in args.reference or ():
        if key not in selected or key in references:
            raise ValueError(f"Reference camera {key!r} must be selected and appear only once.")
        data = Path(filename).read_bytes()
        with Image.open(BytesIO(data)) as original:
            frame = ImageOps.exif_transpose(original).convert("RGBA")
            references[key] = (frame, {"file": filename, "sha256": sha256(data).hexdigest(),
                                       "size": list(original.size)})
    stage = dict(stage_settings(settings.get("stage", {})))
    stage.setdefault("ground_y", min(left.bounds()[0][1], right.bounds()[0][1]) - 0.04)
    shared = {
        "preset": args.preset, "presentation": args.look, "cpu": args.cpu,
        "fixed_lighting": True, "legacy_views": False, "full_scene": FULL_SCENE,
        "stage": stage, "sections": sections, "render": selected, "blend": None,
        "cameras": {key: views[key]._asdict() for key in selected},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".compare-", dir=output.parent) as temp:
        staged = Path(temp) / "comparison"
        for side, project, inputs in (("before", before, left_inputs), ("after", after, right_inputs)):
            folder = staged / side
            folder.mkdir(parents=True)
            run_design_render(args.blender, dict(shared, project=str(project), inputs=inputs), folder)
        delta = compare_blueprints(before / "building.json", after / "building.json")
        delta.pop("changes")
        if input_hashes(before) != left_inputs or input_hashes(after) != right_inputs:
            raise ValueError("A project changed during the comparison. Run compare-views again.")
        reports = {side: read_json(staged / side / RENDER_RUN) for side in ("before", "after")}
        if reports["before"]["device"] != reports["after"]["device"]:
            raise ValueError("The two renders used different devices. Re-run with --cpu.")
        pairs = {}
        for key in selected:
            paths = {side: f"{side}/{run['images'][key]['file']}" for side, run in reports.items()}
            reference = references.get(key)
            with Image.open(staged / paths["before"]) as a, Image.open(staged / paths["after"]) as b:
                width, height = a.size
                offset = width + 4 if reference else 0
                strip = Image.new("RGB", (offset + width * 2 + 4, height + 28), "#24272b")
                strip.paste(a, (offset, 28))
                strip.paste(b, (offset + width + 4, 28))
                labels = ImageDraw.Draw(strip)
                labels.text((offset + 12, 8), f"BEFORE  /  {key}", fill="white")
                labels.text((offset + width + 16, 8), f"AFTER  /  {key}", fill="white")
                if reference:
                    frame, record = reference
                    fitted = ImageOps.contain(frame, (width, height), Image.Resampling.LANCZOS)
                    strip.paste(fitted, ((width - fitted.width) // 2, 28 + (height - fitted.height) // 2), fitted)
                    labels.text((12, 8), f"REFERENCE  /  {key}", fill="white")
                    paths["reference"] = record
                strip.save(staged / f"{key}.png")
            pairs[key] = {**paths, "pair": f"{key}.png"}
        write_json(staged / "comparison.json", {
            "before": str(before), "after": str(after), "render_settings": shared,
            "device": reports["before"]["device"], "views": pairs, "blueprint_diff": delta,
        })
        staged.rename(output)
    print(output / "comparison.json")


def check_walk(args):
    stops = [tuple(p) for p in (args.start, *args.via, args.end)]
    result = trace_walk(args.project, stops)
    if args.out:
        write_json(args.out, result)
    failed = False
    for segment in result["segments"]:
        start, end, path = segment["from"], segment["to"], segment["path"]
        if path:
            print(f"{start} -> {end}: {len(path) - 1} steps")
            continue
        failed = True
        print(f"{start} -> {end}: no clear walking connection")
        for row in segment["invalid_stops"]:
            print(f"  {row['stop']}: occupied headroom at {row['blockers']}" if row["blockers"]
                  else f"  {row['stop']}: no supported floor")
    return int(failed)


def project_command(args):
    project, previews = project_paths(getattr(args, "vanilla", False), args.sample, args.workspace)
    if args.command == "new":
        create_project(project, args.title or args.sample.replace("_", " "))
        print(project / SOURCE)
    elif args.command == "snapshot":
        print(snapshot(project, previews / args.preset, args.version, args.view))
    elif args.command == "restore":
        destination, _ = project_paths(False, args.destination, args.workspace)
        print(restore(project, args.version, destination))
    else:
        open_project(args, project, previews)


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("generate", "generate a building and index its source assets"),
        ("render", "render and verify an already generated building"),
        ("build", "generate, render and verify a building"),
        ("verify", "reopen the saved Blender scene and validate its PNGs"),
    ):
        cmd = commands.add_parser(name, help=help_text)
        cmd.add_argument("--workspace", type=Path, default=ROOT, help="root containing projects/ and previews/")
        cmd.add_argument("--sample", default=DEFAULT_SAMPLE, help="project directory name")
        cmd.add_argument("--vanilla", action="store_true", help="generate or select the separate vanilla sample")
        if name != "verify":
            cmd.add_argument("--instance", type=Path, default=os.environ.get("MCBUILDER_INSTANCE"),
                             help="BMC4 version directory or MCBUILDER_INSTANCE")
            cmd.add_argument("--approximate", action="store_true",
                             help="allow explicitly reported stand-ins for installed blocks with unsupported models")
        if name != "generate":
            cmd.add_argument("--blender", type=Path, default=DEFAULT_BLENDER, help="path to blender.exe")
            cmd.add_argument("--preset", choices=PRESETS)
        if name in {"render", "build"}:
            cmd.add_argument("--quality", choices=("final", "draft"), help="legacy preview quality")
            cmd.add_argument("--view", action="append", help="camera ID, repeat to render several")
            cmd.add_argument("--look", choices=("game", "showcase"))
            cmd.add_argument("--save-scene", action="store_true", help="save a .blend even in quick mode")
            cmd.add_argument("--cpu", action="store_true", help="use Cycles on CPU without probing GPU devices")
    for name, help_text in (
        ("new", "create a project-local design.py"),
        ("open", "launch a saved project in Blender"),
        ("snapshot", "keep the current source, blueprint, materials and selected images"),
        ("restore", "copy a saved version to a new project directory"),
    ):
        cmd = commands.add_parser(name, help=help_text)
        cmd.add_argument("--workspace", type=Path, default=ROOT)
        cmd.add_argument("--sample", required=True, help="project directory name")
        if name == "new":
            cmd.add_argument("--title")
        elif name == "open":
            cmd.add_argument("--preset", choices=PRESETS)
            cmd.add_argument("--blender", type=Path, default=DEFAULT_BLENDER)
            cmd.add_argument("--vanilla", action="store_true")
        else:
            cmd.add_argument("--version", required=True)
            if name == "restore":
                cmd.add_argument("--as", dest="destination", required=True)
            else:
                cmd.add_argument("--preset", choices=PRESETS, default="review")
                cmd.add_argument("--view", action="append")
    change = commands.add_parser("diff", help="compare blueprint coordinates, states and block entities")
    change.add_argument("before", type=Path)
    change.add_argument("after", type=Path)
    change.add_argument("--bounds", nargs=6, type=int, metavar="N", help="inclusive min x y z, then max x y z")
    change.add_argument("--out", type=Path, help="write the full coordinate diff as JSON")
    pairs = commands.add_parser("compare-views", help="render two blueprints with shared cameras and stage")
    pairs.add_argument("before", type=Path, help="generated project or snapshot directory")
    pairs.add_argument("after", type=Path, help="generated project or snapshot directory")
    pairs.add_argument("--out", type=Path, required=True, help="new folder for the renders and comparison")
    pairs.add_argument("--view", action="append", required=True, help="shared camera ID, repeat to render several")
    pairs.add_argument("--reference", nargs=2, action="append", metavar=("VIEW", "IMAGE"),
                       help="add a local reference image to a selected view (repeat for other views)")
    pairs.add_argument("--views-file", type=Path, help="shared views.json, defaulting to the first project")
    pairs.add_argument("--preset", choices=("quick", "review"), default="quick")
    pairs.add_argument("--look", choices=("game", "showcase"), default="showcase")
    pairs.add_argument("--cpu", action="store_true")
    pairs.add_argument("--blender", type=Path, default=DEFAULT_BLENDER)
    walk = commands.add_parser("walk", help="check a route along floor, slab and straight stair blocks")
    walk.add_argument("project", type=Path, help="generated project or snapshot directory")
    walk.add_argument("--from", dest="start", nargs=3, type=int, required=True, metavar="N")
    walk.add_argument("--via", nargs=3, type=int, action="append", default=[], metavar="N")
    walk.add_argument("--to", dest="end", nargs=3, type=int, required=True, metavar="N")
    walk.add_argument("--out", type=Path, help="write the result and route coordinates as JSON")
    add_material_parser(commands, os.environ.get("MCBUILDER_INSTANCE"))
    return cli


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command in {"generate", "build"}:
            if args.instance is None:
                raise ValueError("Supply --instance or set MCBUILDER_INSTANCE.")
            generate(args)
        if args.command in {"render", "build"}:
            render(args)
        if args.command == "verify":
            verify(args)
        if args.command in {"new", "open", "snapshot", "restore"}:
            project_command(args)
        if args.command == "diff":
            return compare(args)
        if args.command == "compare-views":
            compare_views(args)
        if args.command == "walk":
            return check_walk(args)
        if args.command == "materials":
            find_materials(args, ROOT)
    except (OSError, ValueError, KeyError, BadZipFile, subprocess.CalledProcessError) as error:
        print(f"MC AI Builder: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

