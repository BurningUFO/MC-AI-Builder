"""Reuse the Minecraft asset catalogue to preview a live revision report."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from src.__main__ import ROOT, run_blender
from src.assets.archives import Archives
from src.assets.catalog import build_index
from src.assets.models import ModelLibrary
from src.architect.telemetry import timed
from src.core.building import Building, write_json


def models_for_review(report: dict) -> tuple[Building, Building]:
    current = Building("Observed construction")
    proposed = Building("Proposed revision")
    for row in report["changes"]:
        pos = tuple(row["local"])
        for model, state in ((current, row.get("local_actual", row["actual"])),
                             (proposed, row.get("local_desired", row["desired"]))):
            if state["id"] not in ("minecraft:air", "minecraft:cave_air", "minecraft:void_air"):
                model.place(pos, state["id"], **state["properties"])
    return current, proposed


def interior_cutaway(model: Building) -> Building | None:
    if not model.blocks:
        return None
    lower, upper = model.bounds()
    if min(upper[0] - lower[0], upper[2] - lower[2]) < 4 or upper[1] - lower[1] < 3:
        return None
    roof = upper[1]
    floor = roof - 3
    front = min((z for _, y, z in model.blocks if y == floor), default=None)
    if front is None:
        return None
    result = Building("Interior cutaway")
    result.blocks.update({pos: block for pos, block in model.blocks.items()
                          if pos[1] < roof and (pos[1] <= floor or pos[2] != front)})
    return result


def terrain_cutaway(model, report):
    landings = report["terrain"]["landings"]
    if len(landings) < 3:
        return None
    lower, upper = model.bounds()
    top = max(landing["local"][1] for landing in landings[1:]) + 2
    front = min(landing["local"][2] for landing in landings[1:])
    result = Building("Interior cutaway")
    result.blocks.update({pos: block for pos, block in model.blocks.items()
                          if pos[1] <= min(upper[1] - 1, top) and pos[2] >= max(lower[2] + 1, front - 2)})
    return result if result.blocks else None


def preview_review(report_path: Path, instance: Path, output: Path, blender: Path, *, multi_angle=False) -> Path:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    models = models_for_review(report)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with timed("asset_models"), Archives(instance, vanilla_only=False) as archives:
        archives.discover()
        library = ModelLibrary(archives)
        indices = [build_index(ROOT, model, library, {}, fallback_mods="terrain" in report,
                               outline_observations=(index == 0 and "preparation" in report))
                   if model.blocks else None for index, model in enumerate(models)]
    with TemporaryDirectory(prefix=".review-", dir=output) as name:
        stage = Path(name)
        manifest = stage / "review.json"
        write_json(manifest, {"review": report, "indices": indices, "multi_angle": multi_angle}, compact=True)
        with timed("blender_render"):
            run_blender(blender.resolve(strict=True), ROOT / "src/renderers/revision_preview.py", manifest, ROOT, stage,
                        timeout=(240 if "terrain" in report else 120) if multi_angle else None)
        files = ("revision-review.blend", "current.png", "proposed.png")
        if multi_angle:
            files += ("proposed-rear.png",)
            if ("terrain" in report and terrain_cutaway(models[1], report) is not None
                    or report["new_version"]["building_id"].startswith("cottage_") and interior_cutaway(models[1]) is not None):
                files += ("proposed-interior.png",)
        for file in files:
            (stage / file).replace(output / file)
    return output / "revision-review.blend"
