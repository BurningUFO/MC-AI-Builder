"""Verify residential design and survival construction in a marked synthetic BMC4 copy."""

from argparse import ArgumentParser
from collections import Counter, deque
import json
from pathlib import Path
from uuid import uuid4

from src.architect.codex_provider import CodexProvider
from src.architect.design import DesignError
from src.architect.providers import FixtureProvider
from src.construction.review_preview import models_for_review
from src.core.building import write_json
from tests.m1_smoke import require, wait_for
from tests.m3_smoke import assert_pending_without_actions, signal, verify_world
from tests.m4_supply_smoke import verify_receipts
from tests.m5_b_smoke import fixture, fmt, scope, unchanged
from tests.m6_c_smoke import CopyGame, ROOT, synthetic_copy
from tests.m6_d_smoke import accept, agent, assert_half, marks, traces
from tools.mcai_launcher import read_object
from tools.world_copy import file_manifest

HOME = ROOT / ".cache" / "m7-a"
TRACE = HOME / "acceptance.json"
ALTERNATE = ("/architect survey 请立即根据当前观察设计一栋7格宽7格深的小木石屋。用云杉和石砖，透明玻璃，"
             "入口偏右，紧凑窗组，前檐屋顶，室内仅用长凳。选一个现场已核对的地基，现在直接出图。")
PRIMARY = ("/architect survey 请在这里设计一栋7格宽9格深的单层木石住宅。用云杉、石砖和透明玻璃，"
           "完整平屋顶用云杉，室内有长凳和桌台。根据第一人称画面里的地势和通行空间，在左、右侧之间选择入口，"
           "同时选择朝向和窗组，说明你看到的依据。当前画面和地基选项足够就直接出图。")
REVISION = "/architect ask 先暂停施工，把这栋住宅已经装好和还没装的透明窗都换成蓝色玻璃。布局、门洞、屋顶和其他材料保持。"
STOCK = [("minecraft:spruce_planks", 64)] * 3 + [("minecraft:stone_bricks", 64)] * 2 + [
    ("minecraft:glass", 32), ("minecraft:blue_stained_glass", 32), ("minecraft:oak_slab", 16),
    ("minecraft:iron_pickaxe", 1), ("minecraft:iron_axe", 1),
]


class LiveProvider(CodexProvider):
    def survey_with_metrics(self, payload, image, **flags):
        allowed = {ALTERNATE.split(" ", 2)[2]: (7, 7), PRIMARY.split(" ", 2)[2]: (7, 9)}
        if payload["request"] not in allowed:
            raise DesignError("本轮外部请求仅限已授权的住宅验收指令。")
        size = allowed[payload["request"]]
        if not any((fit["width"], fit["depth"]) == size for pad in payload["cottage_foundations"]
                   for fit in pad["sizes"]):
            raise DesignError("现场没有通过此占地的地基校验。")
        return super().survey_with_metrics(payload, image, **flags)

    def decide(self, context):
        if context["message"] == REVISION.split(" ", 2)[2]:
            permitted = {command.split(" ", 2)[2] for command in (ALTERNATE, PRIMARY, REVISION)}
            context = {**context, "recent": [row for row in context["recent"] if row["message"] in permitted]}
            return super().decide(context)
        return FixtureProvider().decide(context)


def provider():
    return LiveProvider(model="gpt-6.2-sol", effort="medium", timeout=60, max_turns=5,
                        max_tokens=80_000, budget_log=HOME / "codex-turns.sqlite3")


def loaded(run, site, at="player"):
    fixture(run.child, "field", position=site[at])

    def readable():
        try:
            return run.snapshot(site)
        except AssertionError as error:
            if "needs the loaded site" not in str(error):
                raise
            return None

    return wait_for(run.child, readable, "the fully loaded native construction site", seconds=65)


def stock(run, site):
    request = uuid4().hex
    file = run.world / ".mcaibuilder-stock-result"
    signal(run.world, ".mcaibuilder-stock-request", json.dumps({
        "request": request, "position": site["chest"], "stance": site["stance"], "place": True,
        "items": [{"id": item, "count": count} for item, count in STOCK],
    }))
    result = wait_for(run.child, lambda: row if (row := read_object(file)).get("request") == request else None,
                      "native chest stocked by the authorized test player", seconds=60)
    require(result["success"] and result["chest_placed"], result.get("error", "Fresh supply chest"))
    run.chat.send(run.child, "/architect supply " + fmt(site["chest"]), "供料箱已批准")
    return result


def prepare_plot(run, site):
    blocked = site.get("blocked", [])
    if not blocked:
        return []
    before = {tuple(row["pos"]): row for row in run.snapshot(site)}
    plants = {"minecraft:grass", "minecraft:tall_grass", "minecraft:fern", "minecraft:large_fern", "minecraft:bush"}
    require(all(before.get(tuple(pos), {}).get("id") in plants for pos in blocked),
            "Manual preparation is limited to known non-solid short plants")
    cleared = []
    for pos in blocked:
        fixture(run.child, "field", position=pos)
        cleared.append(fixture(run.child, "clear_grass", position=pos)["cleared"])
    return cleared


def review_card(run, runtime, prompt):
    before = marks(runtime, "survey")
    card = run.chat.card(run.child, prompt, timeout=200)
    report = card["report"]
    version = report["new_version"]
    path = runtime.store.directory(version["plan_id"]) / report["review_sha256"][:16]
    _, proposed = models_for_review(report)
    require(card["building"].blocks == proposed.blocks and version["building_id"].startswith("cottage_"),
            "Blender and the reviewed proposal receive the exact generated Building")
    require((path / "proposed-interior.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n"),
            "The house has a native Blender interior cutaway")
    details = {"folder": str(path), "report": report, "code": card["code"], "prompt": prompt}
    if prompt.startswith("/architect survey "):
        survey = traces(runtime, "survey", before)[-1]
        frame = survey["steps"][-1]["observation_id"]
        sensor = read_object(ROOT / ".cache" / "observations" / run.world.name / (frame + ".json"))
        require(sensor["observation_id"] == frame and sensor["camera_source"] == "npc_first_person",
                "The real survey consumed the worker's first-person frame")
        details.update(survey=survey, observer_npc=sensor["npc_uuid"])
    return card, details


def snapshot_progress(run, job_id, condition, description, *, seconds=280):
    def check():
        status = run.bridge.progress()
        job = status.get("job", {})
        if job.get("job_id") != job_id:
            return None
        if job["state"] in ("failed", "paused") and not condition(job):
            write_json(HOME / "stalled.json", status)
            raise AssertionError(f"{description}: {job.get('reason', job['state'])}")
        return status if condition(job) else None

    return wait_for(run.child, check, description, seconds=seconds)


def prepare(run, site):
    record = synthetic_copy(run.world)
    proof = read_object(run.world / ".mcaibuilder-copy-player-proof.json")
    require(proof["saved_identity_matches"] and proof["inventory_matches"], "The isolated saved owner logged in")
    require(not run.bridge.status().get("job"), "The house test starts with no existing construction")
    trace = {"passed": False, "site": site, "source_copy": record["enabled_jars"]}
    write_json(TRACE, trace)
    loaded(run, site, "stance")
    active = run.bridge.health()["site_authorization"]
    if active.get("approved") and active["rotation"] == 0 and all(
            active[key] == site[key] for key in ("origin", "area", "spawn")):
        trace["site_approval"] = active
    else:
        scope(run.child, run.bridge, run.chat, site, trace, same_site=True)
    trace["test_player_preparation"] = prepare_plot(run, site)
    fixture(run.child, "field", position=site["stance"])
    trace["stock"] = stock(run, site)
    freeze_mob_edits(run)
    trace["baseline"] = loaded(run, site)
    chosen = provider()
    with agent(run, chosen) as runtime:
        alternate, trace["alternate"] = review_card(run, runtime, ALTERNATE)
        write_json(TRACE, trace)
        assert_pending_without_actions(run.child, run.bridge)
        require(run.snapshot(site) == trace["baseline"], "The alternate proposal left every monitored block unchanged")
        stage_house(run, runtime, trace, alternate["building"].blocks)


def freeze_mob_edits(run):
    with (run.world / "console.log").open("rb") as console:
        console.seek(0, 2)
        run.chat.submit(run.child, "/gamerule mobGriefing false")
        wait_for(run.child, lambda: b"mobGriefing is now set to: false" in console.read(),
                 "the native copy-only rule for isolating mob edits", seconds=20)


def stage_house(run, runtime, trace, alternate_blocks):
    site = trace["site"]
    run.chat.send(run.child, "/architect reject " + trace["alternate"]["code"], "已拒绝这个候选")
    initial, trace["initial"] = review_card(run, runtime, PRIMARY)
    write_json(TRACE, trace)
    require(initial["report"]["counts"]["conflict"] == 0 and 150 <= len(initial["building"].blocks) <= 300,
            "A full house fits the natural plot and the size budget")
    decision = trace["initial"]["survey"]["decisions"][-1]
    require(decision["changes"]["entry"] in ("left", "right") and decision["reason"],
            "The visual model selected a side entrance from checked options")
    alternate_cells, initial_cells = set(alternate_blocks), set(initial["building"].blocks)
    require(len(alternate_cells ^ initial_cells) >= 24, "The two natural language layouts differ in block occupancy")
    assert_pending_without_actions(run.child, run.bridge)
    require(run.snapshot(site) == trace["baseline"], "Real observation and rendering left the site read-only")
    started = accept(run, initial, trace, "M7-A house")
    job_id = started["job"]["job_id"]
    trace["npc"] = started["npc"]["npc_id"]
    require(trace["npc"] == trace["initial"]["observer_npc"], "The observing NPC performs the construction")
    write_json(TRACE, trace)
    snapshot_progress(run, job_id, lambda job: sum(
        row["status"] == "placed" and row["actual"]["id"] == "minecraft:glass" for row in job["actions"]) >= 2,
                      "actual transparent glass installed before revision")
    changed, trace["revision"] = review_card(run, runtime, REVISION)
    write_json(TRACE, trace)
    counts = changed["report"]["counts"]
    require(counts["replace"] >= 2 and counts["keep"] > 0 and counts["add"] > 0
            and counts["conflict"] == counts["remove"] == 0, "An unfinished house has a true glass-only delta")
    require(set(changed["building"].blocks) == initial_cells, "Revision preserves house geometry")
    actual_changes = {pos: (initial["building"].blocks[pos].state.block_id, row.state.block_id)
                      for pos, row in changed["building"].blocks.items() if row != initial["building"].blocks[pos]}
    require(actual_changes and set(actual_changes.values()) == {("minecraft:glass", "minecraft:blue_stained_glass")},
            "The model changes exactly the requested window material")
    trace["checkpoint"] = run.bridge.progress()
    trace["original_placements"] = assert_half(trace["checkpoint"], initial["report"])
    trace["receipts_v1"] = trace["checkpoint"]["job"]["supply"]["transfers"]
    assert_pending_without_actions(run.child, run.bridge)
    trace["snapshot_at_review"] = run.snapshot(site)
    trace["usage"] = runtime.provider.budget.records()
    trace["prepared"] = True
    write_json(TRACE, trace)
    print("House paused during construction with a reviewed LLM revision. Saving normally.", flush=True)


def checkpoint_after_reopen(run):
    checks = ("world_matches_checkpoint", "npc_reused", "inventory_matches_checkpoint",
              "warehouse_matches_checkpoint", "warehouse_transfer_confirmed", "interaction_confirmed")

    def read_checkpoint():
        state = run.bridge.progress()
        recovery = state.get("recovery", {})
        return recovery if all(recovery.get(key) for key in checks) else None

    return wait_for(run.child, read_checkpoint, "same NPC, job, warehouse and inventory checkpoints", seconds=85)


def recover(run, trace):
    require(loaded(run, trace["site"]) == trace["snapshot_at_review"], "Normal save restored the exact partial world")
    trace["recovery"] = checkpoint_after_reopen(run)
    report = trace["revision"]["report"]
    live = run.bridge.change()
    if not trace.get("previously_blocked_review"):
        require(live["review_sha256"] == report["review_sha256"], "Pending review survived the native restart")
    else:
        require(live["new_version"] == report["new_version"], "The pending reviewed Building survived the native restart")
    write_json(TRACE, trace)
    return live


def walkable_cells(report, terminal):
    """Check the two-block interior from exact post-construction readback."""
    local = {tuple(row["world"]): tuple(row["local"]) for row in report["changes"]}
    actual = {local[tuple(row["world"])]: row["actual"]["id"]
              for row in terminal["job"]["verification"]["target_states"]}
    footprint = {(x, z) for x, y, z in actual if y == 0}
    front = min(z for _, z in footprint)
    inside = {(x, z) for x, z in footprint if (x, 1, z) not in actual and (x, 2, z) not in actual}
    doors = {(x, z) for x, z in inside if z == front}
    require(len(doors) == 1, "The exact built facade has one two-block doorway")
    queue = deque(doors)
    reached = set(doors)
    while queue:
        x, z = queue.popleft()
        for point in ((x+1, z), (x-1, z), (x, z+1), (x, z-1)):
            if point in inside - reached:
                reached.add(point)
                queue.append(point)
    require(reached == inside and len(reached) >= 20 and all((x, 3, z) in actual for x, z in reached),
            "An accessible two-block interior and a roof cover the whole connected room")
    return len(reached)


def finish(run, trace):
    with agent(run, FixtureProvider()) as runtime:
        report = recover(run, trace)
        if trace.get("previously_blocked_review"):
            _, trace["revision"] = review_card(run, runtime, "/architect ask 请重新审阅当前候选。")
            require(trace["revision"]["report"]["review_sha256"] == report["review_sha256"],
                    "The post-recovery player ticket is bound to the current review")
            write_json(TRACE, trace)
        revision = trace["revision"]
        started = accept(run, {"code": revision["code"], "report": report}, trace, "M7-A glass revision")
        require(started["npc"]["npc_id"] == trace["npc"], "The same Fake Player continues V2")
        before = len(run.chat.messages())
        signal(run.world, ".mcaibuilder-architect-command", "architect approve " + trace["initial"]["code"])
        wait_for(run.child, lambda: any("[stale_approval]" in line for line in run.chat.messages()[before:]),
                 "retired V1 confirmation refused", seconds=14)
        terminal = snapshot_progress(run, report["new_version"]["plan_id"],
                                     lambda job: job["state"] == "completed", "native house construction complete",
                                     seconds=540)
        trace["terminal"] = terminal
        write_json(TRACE, trace)
        trace["actions_v2"] = verify_world(terminal, report)
        trace["walkable_cells"] = walkable_cells(report, terminal)
        receipts = verify_receipts(trace["receipts_v1"])
        receipts.update(verify_receipts(terminal["job"]["supply"]["transfers"]))
        placed = Counter(row["actual"]["id"] for row in trace["original_placements"]
                         + [row for row in terminal["job"]["actions"] if row["status"] == "placed"])
        require(all(receipts[key] >= count for key, count in placed.items()), "Actual chest receipts cover all block placements")
        trace["materials_transferred"] = dict(receipts)
        trace["materials_placed"] = dict(placed)
        permitted = {tuple(row["world"]) for row in report["changes"]}
        permitted.update(tuple(row["world"]) for version in ("initial", "revision")
                         for row in trace[version]["report"].get("covered_grass", []))
        final_snapshot = run.snapshot(trace["site"])
        require(not unchanged(trace["baseline"], final_snapshot, permitted),
                "Every monitored non-target block remains unchanged except reviewed grass cover")
        trace["final_snapshot"] = final_snapshot
        source = synthetic_copy(run.world)
        manifests = read_object(run.world / "copy-integrity.json")
        require(file_manifest(Path(source["source_world"]), world=True) == manifests["world"]
                and file_manifest(Path(source["source_instance"]) / "mods") == manifests["mods"],
                "Synthetic original world and source mod bytes remained unchanged")
        trace["source_unchanged"] = True
        trace["built"] = True
        write_json(TRACE, trace)
        print("M7-A native exact states, survival receipts and incremental construction verified. Saving the final house.", flush=True)


def verify_material_balance(trace, actual):
    stock = trace["stock"]["after"]
    chest, cargo = (actual["materials"][key] for key in ("warehouse_items", "npc_items"))
    expected = trace["terminal"]["materials"]
    require(chest == expected["warehouse_items"] and cargo == expected["npc_items"],
            "The reopened native container and cargo agree with the terminal counts")
    placed = trace["materials_placed"]
    result = {item: {"stocked": count, "chest": chest.get(item, 0),
                     "npc": cargo.get(item, 0), "placed": placed.get(item, 0)} for item, count in stock.items()}
    require(all(row["stocked"] == row["chest"] + row["npc"] + row["placed"] for row in result.values()),
            "Stock, final chest and cargo, and real item use balance for every supplied item")
    return result


def reopen_final(run, trace):
    require(trace.get("built") and loaded(run, trace["site"]) == trace["final_snapshot"],
            "Normal reload preserves the completed house and its monitored surroundings")
    trace["completed_recovery"] = checkpoint_after_reopen(run)
    final = run.bridge.progress()
    saved = trace["terminal"]
    require(final["npc"]["npc_id"] == trace["npc"] and not final["change_pending"],
            "Native reopen retained the same actor and finished version")
    require(all(final["job"][key] == saved["job"][key] for key in
                ("state", "processed", "job_id", "building_version", "actions", "verification")),
            "The completed journal and exact state verification survived the second native reopen")
    trace["material_balance"] = verify_material_balance(trace, final)
    x, y, z = trace["revision"]["report"]["new_version"]["origin"]
    room = [x, y + 1, z]
    trace["player_can_stand_inside"] = fixture(run.child, "field", position=room)["position"] == room
    require(trace["player_can_stand_inside"], "The native player fits inside the completed two-block room")
    fixture(run.child, "field", position=trace["site"]["player"])
    trace["passed"] = True
    write_json(TRACE, trace)
    print("The finished house, original NPC, inventory and action history survived native reopen.", flush=True)


def main():
    cli = ArgumentParser(description=__doc__)
    cli.add_argument("phase", choices=("prepare", "finish", "reopen"))
    arguments = cli.parse_args()
    setup = read_object(HOME / "instance.json")
    game = Path(setup["game"])
    synthetic_copy(game)
    with CopyGame(game, setup["port"]) as run:
        if arguments.phase == "prepare":
            prepare(run, read_object(HOME / "selected-site.json"))
        elif arguments.phase == "finish":
            trace = read_object(TRACE)
            require(trace.get("prepared"), "The paused review must already be saved")
            finish(run, trace)
        else:
            reopen_final(run, read_object(TRACE))
    require("All dimensions are saved" in (game / "console.log").read_text(encoding="utf-8", errors="replace"),
            "Minecraft exited through its normal full save")


if __name__ == "__main__":
    main()
