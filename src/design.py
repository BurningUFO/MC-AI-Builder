"""Project-local Python designs, blueprint comparisons and named snapshots."""

from collections import Counter
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import runpy
from shutil import copyfile
from tempfile import TemporaryDirectory

from src.assets.projects import valid_name
from src.core.building import Building, write_json
from src.renderers.views import Box, point


SOURCE = "design.py"
VIEWS = "views.json"
GROUPS = "design-groups.json"
ARTIFACTS = (SOURCE, VIEWS, "building.json", GROUPS, "material-index.json", "materials.csv")
RENDER_RUN = "render.json"


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def input_hashes(project):
    return {name: digest(project / name) for name in ARTIFACTS if (project / name).is_file()}


def create_project(project, title):
    project.mkdir(parents=True)
    (project / SOURCE).write_text(
        '"""Build the Minecraft blueprint for this project."""\n\n'
        'from src.core.building import Building\n\n\n'
        'def build():\n'
        f'    building = Building({title!r})\n'
        '    building.place((0, 0, 0), "minecraft:stone")  # Replace this origin marker with the design.\n'
        '    return building\n', encoding="utf-8",
    )


def project_design(project):
    source = project / SOURCE
    before = digest(source)
    build = runpy.run_path(str(source)).get("build")
    if not callable(build):
        raise ValueError(f"{source} must define build().")
    result = build()
    building, groups = result if isinstance(result, tuple) and len(result) == 2 else (result, {})
    if not isinstance(building, Building) or not isinstance(groups, dict):
        raise ValueError("build() must return Building or (Building, {position: collection_path}).")
    for pos, path in groups.items():
        if pos not in building.blocks:
            raise ValueError(f"Collection assignment at {pos} has no block.")
        if not isinstance(path, str) or any(not part.strip() or len(part.encode()) > 50
                                             for part in path.split("/")):
            raise ValueError(f"Invalid collection path at {pos}: {path!r}")
    if before != digest(source):
        raise ValueError("design.py changed while generating.")
    return building, groups, before


def write_groups(directory, groups, source_hash):
    write_json(directory / GROUPS, {
        "building_sha256": digest(directory / "building.json"),
        "design_sha256": source_hash,
        "groups": [[*pos, name] for pos, name in sorted(groups.items())],
    }, compact=True)


def read_groups(project, building, hashes):
    source_exists = (project / SOURCE).is_file()
    path = project / GROUPS
    if not path.is_file():
        if source_exists:
            raise ValueError("Run generate or build to generate this design first.")
        return {}
    data = read_json(path)
    if (data["building_sha256"] != hashes.get("building.json")
            or data["design_sha256"] != hashes.get(SOURCE)):
        raise ValueError("design.py, building.json or the group assignments changed. Regenerate the design.")
    groups = {(x, y, z): name for x, y, z, name in data["groups"]}
    if groups.keys() - building.blocks.keys():
        raise ValueError("Collection assignments refer to blocks missing from the blueprint.")
    return groups


def image_selection(images, requested):
    if not requested:
        return list(images)
    selected = list(dict.fromkeys(Path(name).stem for name in requested))
    missing = set(selected) - images.keys()
    if missing:
        raise ValueError(f"Unknown views: {', '.join(sorted(missing))}. Available: {', '.join(images)}")
    return selected


def stage_settings(options):
    if not isinstance(options, dict) or set(options) - {"ground", "ground_y", "ground_color", "sun", "interior_lights"}:
        raise ValueError("stage accepts ground, ground_y, ground_color, sun and interior_lights.")
    if "ground" in options and type(options["ground"]) is not bool:
        raise ValueError("stage ground must be true or false.")
    if "ground_y" in options and not finite_number(options["ground_y"]):
        raise ValueError("stage ground_y must be a Minecraft height.")
    if "ground_color" in options and not all(0 <= n <= 1 for n in point(options["ground_color"], "ground_color")):
        raise ValueError("stage ground_color uses three linear 0–1 color values.")
    if "sun" in options and not any(point(options["sun"], "sun direction")):
        raise ValueError("stage sun cannot be at the target.")
    lights = options.get("interior_lights", [])
    if not isinstance(lights, list):
        raise ValueError("interior_lights must list light fixtures.")
    for fixture in lights:
        if (not isinstance(fixture, dict) or set(fixture) != {"name", "position", "target", "power", "size"}
                or not isinstance(fixture["name"], str) or not fixture["name"]
                or any(not finite_number(fixture[key]) or fixture[key] <= 0 for key in ("power", "size"))):
            raise ValueError("An interior light needs a name, position, target, power and size.")
        if point(fixture["position"], "light position") == point(fixture["target"], "light target"):
            raise ValueError("An interior light needs a target different from its position.")
    return options


def finite_number(value):
    return type(value) in (int, float) and isfinite(value)


def snapshot(project, preview, version, requested):
    run = read_json(preview / RENDER_RUN)
    current = input_hashes(project)
    if SOURCE not in current or run["inputs"] != current:
        raise ValueError("Generate and render the current design before saving a version.")
    images = image_selection(run["images"], requested)
    if not images:
        raise ValueError("Render a key view before saving a version.")
    versions = project / "versions"
    versions.mkdir(exist_ok=True)
    destination = named_version(project, version)
    if destination.exists():
        raise FileExistsError(destination)
    with TemporaryDirectory(dir=versions) as temp:
        staged = Path(temp) / "snapshot"
        staged.mkdir()
        for name in current:
            copyfile(project / name, staged / name)
        if input_hashes(staged) != current:
            raise ValueError("Project changed while saving the version.")
        for key in images:
            record = run["images"][key]
            name = record["file"]
            if Path(name).name != name or digest(preview / name) != record["sha256"]:
                raise ValueError(f"Preview changed while saving the version: {name}")
            copyfile(preview / name, staged / name)
        write_json(staged / RENDER_RUN, {**run, "blend": None,
                                        "images": {key: run["images"][key] for key in images}})
        staged.rename(destination)
    return destination


def named_version(project, version):
    if not valid_name(version):
        raise ValueError("Version must be a single directory name.")
    return project / "versions" / version


def restore(project, version, destination):
    saved = named_version(project, version)
    run = read_json(saved / RENDER_RUN)
    expected = run["inputs"].copy()
    available = input_hashes(saved)
    # Public snapshots omit the locally generated index.
    if "material-index.json" not in available:
        expected.pop("material-index.json", None)
    if expected != available:
        raise ValueError(f"Version files have changed: {saved}")
    if destination.exists():
        raise FileExistsError(destination)
    with TemporaryDirectory(dir=destination.parent) as temp:
        staged = Path(temp) / "project"
        staged.mkdir()
        for name in available:
            copyfile(saved / name, staged / name)
        staged.rename(destination)
    return destination


def block_value(block):
    if block is None:
        return None
    return {"state": block.state.key, "block_entity": block.block_entity}


def compare_blueprints(before_path, after_path, region=Box()):
    before = Building.read(before_path)
    after = Building.read(after_path)
    changes = []
    counts = Counter(added=0, removed=0, replaced=0)
    outside = 0
    for pos in sorted(before.blocks.keys() | after.blocks.keys()):
        previous, current = before.blocks.get(pos), after.blocks.get(pos)
        if previous == current:
            continue
        kind = "added" if previous is None else "removed" if current is None else "replaced"
        inside = region.contains(pos)
        outside += not inside
        counts[kind] += 1
        changes.append({"position": pos, "kind": kind, "inside": inside,
                        "before": block_value(previous), "after": block_value(current)})
    extents = list(zip(*(row["position"] for row in changes)))
    return {
        "before_sha256": digest(before_path), "after_sha256": digest(after_path),
        "before_blocks": len(before.blocks), "after_blocks": len(after.blocks),
        "bounds": {"min": list(map(min, extents)), "max": list(map(max, extents))} if extents else None,
        "expected_bounds": {"min": region.low, "max": region.high},
        "outside": outside, **counts, "changes": changes,
    }
