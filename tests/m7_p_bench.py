"""Measure the same M7-A cottage in independently copied BMC4 terrain fixtures."""

from argparse import ArgumentParser
from hashlib import sha256
import json
import os
from pathlib import Path
from shutil import copy2
from time import monotonic, sleep
from uuid import uuid4

from src.architect.providers import FixtureProvider
from src.core.building import write_json
from tests.m1_smoke import require
from tests.m3_smoke import assert_pending_without_actions, verify_world
from tests.m4_supply_smoke import verify_receipts
from tests.m5_b_smoke import fixture, fmt, scope, unchanged
from tests.m6_c_smoke import CopyGame, HOME as M6, ROOT, synthetic_copy
from tests.m6_d_smoke import accept, agent
from tests.m7_a_smoke import HOME as M7A, freeze_mob_edits, loaded, prepare_plot, review_card, stock, walkable_cells
from tools.mcai_launcher import local_game, read_object
from tools.world_copy import PACK_DIRS, WORLD, copy_tree, create_copy, file_manifest, inventory

HOME = ROOT / ".cache" / "m7-p"
PORT = 18810
PROMPT = "/architect ask 请设计一栋7格宽9格深的小住宅，用云杉、石砖和蓝色玻璃。平屋顶、成对窗组、靠西门洞、长凳和桌台。"


class CottageFixture(FixtureProvider):
    def decide(self, context):
        if context["message"] == PROMPT.split(" ", 2)[2]:
            return {"action": "create_design", "kind": "cottage", "changes": {
                "width": 7, "depth": 9, "entry": "left", "windows": "paired",
                "roof_form": "flat", "interior": "bench_and_table", "glass": "minecraft:blue_stained_glass"}}
        return super().decide(context)


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def game_for(label, source):
    manifest = HOME / f"copy-{label}.json"
    cached = read_object(manifest)
    if cached:
        game = Path(cached["game"])
    else:
        game = create_copy(source, source / "saves" / "m1-construction", terrain_fixture=True)
        write_json(manifest, {"game": str(game), "label": label})
    synthetic_copy(game)
    return game


def freeze_preparation(game):
    """Copy a normally saved virgin fixture, leaving per-copy Bridge approvals behind."""
    synthetic_copy(game)
    source_world = game / "saves" / WORLD
    require(not any(read_object(file).get("current") for file in
                    (source_world / "data" / "mcaibuilder-sites").glob("*/mcaibuilder-journal.json")),
            "The fixture can only be frozen before construction ever started")
    target = local_game("m6-terrain-source-m7p-" + uuid4().hex[:8])
    target.mkdir()
    destination = target / "saves" / "m1-construction"
    native_lock = (source_world / "session.lock").read_bytes()
    before = file_manifest(source_world, world=True)
    dirs, files = inventory(source_world, world=True)
    def minecraft_file(path):
        return path.parts[:2] != ("data", "mcaibuilder-sites") and path.as_posix() != "data/mcaibuilder-site-authorization.json"

        for path in dirs:
            if minecraft_file(path):
                (destination / path).mkdir(parents=True, exist_ok=True)
        for path in files:
            if minecraft_file(path):
                (destination / path).parent.mkdir(parents=True, exist_ok=True)
                copy2(source_world / path, destination / path)
        require(file_manifest(source_world, world=True) == before, "Source save stayed byte-identical")
        # The source is a closed native save. Copy its lock file for the launcher's future read-only lock check.
        (destination / "session.lock").write_bytes(native_lock)
    copy_tree(game / "mods", target / "mods")
    for name in PACK_DIRS:
        path = game / name
        if path.is_dir():
            copy_tree(path, target / name, config=name == "config")
    copy2(game / "options.txt", target / "options.txt")
    (target / ".mcaibuilder-test").write_text("D盘合成基准场地\n", encoding="utf-8")
    write_json(HOME / "prepared-source.json", {"source": str(target)})
    return target


def reference_targets():
    return read_object(ROOT / "docs" / "m7-a-evidence" / "house-construction.json")["versions"]["revision"]["targets"]


def prepare_site(run, site, receipt):
    require(not run.bridge.status().get("job"), "The performance case must use a virgin copy")
    loaded(run, site, "stance")
    active = run.bridge.health()["site_authorization"]
    if active.get("approved"):
        require(all(active[key] == site[key] for key in ("origin", "spawn", "area")),
                "The already reviewed copy uses the identical approved site")
    else:
        scope(run.child, run.bridge, run.chat, site, receipt, same_site=True)
    freeze_mob_edits(run)
    quiet_plot(run, site)
    live = {tuple(row["pos"]): row for row in run.snapshot(site)}
    footprint = {tuple(a + b for a, b in zip(site["origin"], row["building_local"], strict=True))
                 for row in reference_targets()}
    blocked = [list(pos) for pos in sorted(footprint & live.keys())]
    receipt["terrain_prepared"] = len(prepare_plot(run, {**site, "blocked": blocked}))
    fixture(run.child, "field", position=site["stance"])
    if not active.get("warehouse_approved"):
        if live.get(tuple(site["chest"]), {}).get("id") == "minecraft:chest":
            run.chat.send(run.child, "/architect supply " + fmt(site["chest"]), "供料箱已批准")
        else:
            stock(run, site)
    receipt["stock"] = run.bridge.progress()["materials"]["warehouse_items"]
    return loaded(run, site)


def quiet_plot(run, site):
    low, high = site["area"]["min"], site["area"]["max"]
    region = ",".join(f"{key}={value}" for key, value in zip(
        ("x", "y", "z", "dx", "dy", "dz"), [*low, *(b-a for a, b in zip(low, high, strict=True))], strict=True))
    # All comparison runs start with the same fixed plot free of wandering vanilla livestock.
    for species in ("sheep", "cow", "pig", "chicken", "rabbit"):
        run.chat.submit(run.child, f"/kill @e[type=minecraft:{species},{region}]")


def plan_matches_reference(report):
    pattern = {tuple(row["building_local"]): row["after"] for row in reference_targets()}
    observed = {tuple(row["local"]): row["desired"] for row in report["changes"]}
    require(len(observed) == 183 and pattern == observed, "Benchmark uses exactly the proven 183-state cottage")
    require(report["counts"] == {"keep": 0, "add": 183, "remove": 0, "replace": 0, "conflict": 0},
            "Each run starts with the identical empty structure footprint")


def monitor(run, job_id, start, filename, *, seconds=450, inspect=None):
    timeline = []
    deadline = monotonic() + seconds
    while monotonic() < deadline and run.child.poll() is None:
        sample = run.bridge.status()
        task = sample.get("job", {})
        require(task.get("job_id") == job_id, "The benchmark still executes the approved version")
        timeline.append({"wall_ms": round(1000 * (monotonic() - start), 2),
                         "tick": sample["game_tick"], "state": task["state"],
                         "processed": task["processed"], "placed": task["placed"],
                         "moving": sample["npc"]["moving"], "activity": sample.get("activity"),
                         "supply": task.get("supply", {}).get("phase"), "reason": task.get("reason"),
                         "planning": task.get("planning", {}).get("state")})
        if inspect is not None:
            inspect(task)
        if task["state"] != "running":
            terminal = run.bridge.progress()
            write_json(filename, {"timeline": timeline, "terminal": terminal}, compact=True)
            require(task["state"] == "completed", f"Uninterrupted performance run ended at {task['processed']} with {task.get('reason')}")
            return timeline, terminal
        sleep(.45)
    write_json(filename, {"timeline": timeline}, compact=True)
    raise TimeoutError("Benchmark did not reach a terminal state")


def run_case(run, label, site, provider, prompt=PROMPT, *, on_start=None, inspect=None):
    setup_start = monotonic()
    receipt = {"label": label, "phase": "preparing", "passed": False}
    path = HOME / f"{label}.json"
    start_snapshot = prepare_site(run, site, receipt)
    receipt["site_block_digest"] = digest(start_snapshot)
    receipt["setup_ms"] = 1000 * (monotonic() - setup_start)
    with agent(run, provider) as runtime:
        start_review = monotonic()
        card, details = review_card(run, runtime, prompt)
        receipt["design_to_review_ms"] = 1000 * (monotonic() - start_review)
        plan_matches_reference(card["report"])
        assert_pending_without_actions(run.child, run.bridge)
        require(start_snapshot == run.snapshot(site), "Review and observer left the initial site unchanged")
        receipt["review_wait_ms"] = 1000 * (monotonic() - start_review) - receipt["design_to_review_ms"]
        started = accept(run, card, receipt, f"M7-P {label}")
        signal_time = monotonic()
        receipt["approval_to_readback_ms"] = 1000 * (signal_time - start_review) - (
            receipt["design_to_review_ms"] + receipt["review_wait_ms"])
        job_id = started["job"]["job_id"]
        if on_start is not None:
            on_start(run)
        trace, terminal = monitor(run, job_id, signal_time, HOME / f"{label}-samples.json", inspect=inspect)
        receipt["approval_through_completion_ms"] = 1000 * (monotonic() - start_review)
        require(started["npc"]["npc_id"] == terminal["npc"]["npc_id"], "The same Fake Player built the entire house")
        report = card["report"]
        receipt["action_counts"] = verify_world(terminal, report)
        receipt["walkable_cells"] = walkable_cells(report, terminal)
        receipt["materials"] = dict(verify_receipts(terminal["job"]["supply"]["transfers"], max_items=16 if label == "A" else 48))
        receipt["trips"] = len(terminal["job"]["supply"]["transfers"])
        allowed = {tuple(row["world"]) for row in report["changes"]}
        allowed.update(tuple(row["world"]) for row in report.get("covered_grass", []))
        require(not unchanged(start_snapshot, run.snapshot(site), allowed),
                "No unapproved structures or terrain changed during the performance run")
        receipt["performance"] = terminal["job"].get("performance")
        require(receipt["performance"] and receipt["performance"]["complete"], "The measured job segment finished")
        receipt["model_sha256"] = terminal["job"]["model_sha256"]
        receipt["review_sha256"] = report["review_sha256"]
        receipt["passed"] = True
        receipt["phase"] = "world_verified"
        # The detailed local trace contains action timestamps and can be audited before redaction.
        receipt["actions"] = terminal["job"]["actions"]
        receipt["work_ticks"] = trace[-1]["tick"] - trace[0]["tick"]
        if "planning" in terminal["job"]:
            receipt["planning"] = terminal["job"]["planning"]
        if "survey" in details:
            receipt["survey"] = details["survey"]
        write_json(path, receipt)
        print({key: receipt[key] for key in ("label", "passed", "site_block_digest", "trips", "action_counts")},
              flush=True)
        print(f"Measured construction seconds {receipt['performance']['execution_wall_ms'] / 1000:.2f}", flush=True)
        return receipt


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("label", choices=("A", "B", "B2", "freeze"))
    parser.add_argument("--site", type=Path, default=M7A / "selected-site.json")
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    if args.label == "freeze":
        freeze_preparation(Path(read_object(HOME / "copy-A.json")["game"]))
        print("Preserved the normally saved synthetic terrain and stocked chest as the shared fixture.", flush=True)
        return
    prepared = read_object(HOME / "prepared-source.json")
    source = (args.source or Path(prepared.get("source") or read_object(M6 / "terrain-source.json")["source"])).resolve(strict=True)
    site = read_object(args.site)
    os.environ["MC_AI_BUILDER_PROFILE"] = "1"
    os.environ["MC_AI_BUILDER_LEGACY_EXECUTOR"] = "1" if args.label == "A" else "0"
    game = game_for(args.label, source)
    if (HOME / f"{args.label}.json").is_file():
        raise ValueError("Preserve the completed run and choose a fresh benchmark label")
    print(f"Opening the marked BMC4 copy for case {args.label}.", flush=True)
    with CopyGame(game, PORT) as run:
        receipt = run_case(run, args.label, site, CottageFixture())
    record = synthetic_copy(game)
    manifest = read_object(game / "copy-integrity.json")
    require(file_manifest(Path(record["source_world"]), world=True) == manifest["world"]
            and file_manifest(Path(record["source_instance"]) / "mods") == manifest["mods"],
            "Source fixture and mod bytes remained untouched")
    receipt["native_save"] = receipt["source_unchanged"] = True
    write_json(HOME / f"{args.label}.json", receipt)


if __name__ == "__main__":
    main()
