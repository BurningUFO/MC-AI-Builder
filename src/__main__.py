"""Generate and preview the included BMC4 buildings from PowerShell or a terminal."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from zipfile import BadZipFile

from src.assets.archives import Archives
from src.assets.catalog import build_index, choose_palette, save_project, write_materials
from src.assets.models import ModelLibrary
from src.generators.bridge import BRIDGE_MODDED, BRIDGE_VANILLA, generate_bridge
from src.generators.enchanting import LIBRARY_MODDED, LIBRARY_VANILLA, generate_library
from src.generators.flat_bridge import FLAT_BRIDGE_MODDED, FLAT_BRIDGE_VANILLA, generate_flat_bridge
from src.generators.medieval import generate_house
from src.assets.projects import valid_name
from src.core.building import Building, write_json
from src.renderers.views import preview_for


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BLENDER = ROOT / "tools" / "blender" / "blender.exe"
RENDER_SCRIPT = ROOT / "src" / "renderers" / "blender_preview.py"
VERIFY_SCRIPT = ROOT / "src" / "renderers" / "verify_preview.py"
DEFAULT_SAMPLE = "medieval_house"
GENERATORS = {
    "medieval_house": generate_house,
    "enchanting_library": generate_library,
    "warehouse_bridge": generate_bridge,
    "warehouse_flat_bridge": generate_flat_bridge,
}
PALETTES = {
    "enchanting_library": (LIBRARY_VANILLA, LIBRARY_MODDED),
    "warehouse_bridge": (BRIDGE_VANILLA, BRIDGE_MODDED),
    "warehouse_flat_bridge": (FLAT_BRIDGE_VANILLA, FLAT_BRIDGE_MODDED),
}


def project_paths(vanilla: bool, sample: str = DEFAULT_SAMPLE, workspace=ROOT) -> tuple[Path, Path]:
    if not valid_name(sample):
        raise ValueError("Project name must be a single directory name.")
    name = f"{sample}_vanilla" if vanilla else sample
    return workspace / "projects" / name, workspace / "previews" / name


def generate(args) -> None:
    project, _ = project_paths(args.vanilla, args.sample, args.workspace)
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


def refresh_project_index(data, building, instance, *, vanilla=False):
    digest = sha256(data.read_bytes()).hexdigest()
    namespaces = {block.state.block_id.split(":")[0] for block in building.blocks.values()} - {"minecraft"}
    with Archives(instance, vanilla_only=vanilla, extra_mods=tuple(sorted(namespaces))) as archives:
        archives.discover()
        catalog = build_index(ROOT, building, ModelLibrary(archives), {}, fallback_mods=True)
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


def verified_index(path, digest):
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("building_sha256") == digest
    except (OSError, ValueError, AttributeError):
        return False


def verify(args):
    project, folder = project_paths(args.vanilla, args.sample, args.workspace)
    data = project / "building.json"
    preview = preview_for(args.sample, Building.read(data), name=project.name)
    verify_project(args.blender.resolve(strict=True), data, folder, args.sample, preview)


def render(args):
    project, output = project_paths(args.vanilla, args.sample, args.workspace)
    scene = render_project(args.blender, project / "building.json", output, args.sample,
                           quality=args.quality, instance=args.instance, vanilla=args.vanilla)
    print(scene)


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
        cmd.add_argument("--sample", choices=tuple(GENERATORS) if name in {"generate", "build"} else None,
                         default=DEFAULT_SAMPLE, help="project directory name")
        cmd.add_argument("--vanilla", action="store_true", help="generate or select the separate vanilla sample")
        if name in {"generate", "build"}:
            cmd.add_argument("--instance", type=Path, required=True, help="BMC4 version directory with its client JAR")
        elif name == "render":
            cmd.add_argument("--instance", type=Path, help="refresh the material index from this Minecraft instance")
        if name != "generate":
            cmd.add_argument("--blender", type=Path, default=DEFAULT_BLENDER, help="path to blender.exe")
        if name in {"render", "build"}:
            cmd.add_argument("--quality", choices=("final", "draft"), default="final")
    return cli


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command in {"generate", "build"}:
            generate(args)
        if args.command in {"render", "build"}:
            render(args)
        if args.command == "verify":
            verify(args)
    except (OSError, ValueError, KeyError, BadZipFile, subprocess.CalledProcessError) as error:
        print(f"MC AI Builder: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

