"""Reuse the Minecraft asset catalogue to preview a live revision report."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from src.__main__ import ROOT, run_blender
from src.assets.archives import Archives
from src.assets.catalog import build_index
from src.assets.models import ModelLibrary
from src.core.building import Building, write_json


def models_for_review(report: dict) -> tuple[Building, Building]:
    current = Building("Observed construction")
    proposed = Building("Proposed revision")
    for row in report["changes"]:
        pos = tuple(row["local"])
        for model, state in ((current, row["actual"]), (proposed, row["desired"])):
            if state["id"] not in ("minecraft:air", "minecraft:cave_air", "minecraft:void_air"):
                model.place(pos, state["id"], **state["properties"])
    return current, proposed


def preview_review(report_path: Path, instance: Path, output: Path, blender: Path) -> Path:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    models = models_for_review(report)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with Archives(instance, vanilla_only=False) as archives:
        archives.discover()
        library = ModelLibrary(archives)
        indices = [build_index(ROOT, model, library, {}) if model.blocks else None for model in models]
    with TemporaryDirectory(prefix=".review-", dir=output) as name:
        stage = Path(name)
        manifest = stage / "review.json"
        write_json(manifest, {"review": report, "indices": indices}, compact=True)
        run_blender(blender.resolve(strict=True), ROOT / "src/renderers/revision_preview.py", manifest, ROOT, stage)
        for file in ("revision-review.blend", "current.png", "proposed.png"):
            (stage / file).replace(output / file)
    return output / "revision-review.blend"
