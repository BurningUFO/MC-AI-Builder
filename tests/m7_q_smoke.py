"""Exercise delegated worksite clearance through a signed player in synthetic BMC4 copies."""

from argparse import ArgumentParser
from collections import Counter
import json
from math import dist
import os
from pathlib import Path
import re
from time import monotonic, sleep
from uuid import uuid4

from src.architect.codex_provider import CodexProvider
from src.architect.design import Cottage, DesignError
from src.architect.runtime import ArchitectClient, Runtime, SessionStore
from src.architect.survey import collect
from src.architect.providers import ModelBudgetExhausted, ModelTimeout
from src.construction.plan import make_plan
from src.core.building import write_json
from tests.m1_smoke import rejected, require, wait_for
from tests.m3_smoke import signal
from tests.m4_supply_smoke import verify_receipts
from tests.m5_b_smoke import fixture, fmt, unchanged
from tests.m6_c_smoke import CopyGame, ROOT, synthetic_copy, wait_request
from tests.m6_d_smoke import agent
from tests.m7_a_smoke import HOME as M7A, STOCK, checkpoint_after_reopen, freeze_mob_edits, loaded, walkable_cells
from tests.m7_p_bench import reference_targets
from tools.mcai_launcher import read_object
from tools.world_copy import create_copy, file_manifest


HOME = ROOT / ".cache" / "m7-q"
PORT = 18820
ROOF_HINT = "用当前路线视角评估是否先铺屋顶背侧，施工继续，保留所有已批准的建筑状态。"
SOURCE = ROOT / ".cache" / "m6-cd" / "terrain-source.json"
GRASS = ("@Builder 这块区域交给你，直接建一栋7格宽9格深的单层住宅，保持已标记地基和0度朝北。"
         "云杉木板、石砖、蓝色染色玻璃、平屋顶、入口偏左、成对窗、长凳和桌台。"
         "普通草你自己批量清理，有重要物品先问我。")


PROPERTY = ("@Builder 在初始标记原点直接建一栋正北朝向的7×9单层木石住宅，位置绝对不能移动。"
            "如果该位置和箱子、功能方块或玩家建筑相撞，先告诉我涉及哪些资产，问我是否换地点。")
TERRAIN = ("@Builder 这块区域交给你，直接建一栋7格宽9格深的单层住宅，朝北、云杉木板、石砖、蓝色染色玻璃、"
           "平屋顶也用云杉木板，成对窗、偏左门洞、长凳和桌台。可以挪动地基。保留西侧箱子、展示基座和标记的石砖。"
           "希望占地覆盖场中的两层木石挡板及附近隆起。要保留哪个位置、挡路的普通木石草木怎样整理，由你决定。"
           "说明选址和先做什么。")


def model_fixture(condition, reason):
    if not condition:
        raise DesignError("验收前提未满足。" + reason)


class WorksiteModel(CodexProvider):
    def __init__(self):
        super().__init__(model="gpt-6.2-sol", effort="medium", timeout=48, max_turns=6, max_tokens=100_000,
                         budget_log=HOME / "codex-turns.sqlite3")

    def survey_with_metrics(self, payload, image, **flags):
        message = payload["request"]
        model_fixture(payload["worksite"] and message in self.prompts(), "Only authorized scripted synthetic views can be sent")
        if sum("image_sha256" in row for row in self.budget.records()) >= 6:
            raise ModelBudgetExhausted("M7-Q image budget exhausted")
        payload = {**payload, "remaining_observations": 0, "movement_options": []}
        pads = payload["cottage_foundations"]
        marked = [fit for pad in pads if pad["offset_from_mark"] == [0, 0, 0]
                  for fit in pad["sizes"] if (fit["width"], fit["depth"]) == (7, 9)]
        if message == GRASS.split(" ", 1)[1]:
            model_fixture(any(fit["actionable"] and fit["clearance"].get("vegetation") for fit in marked),
                          "The original marked plot reaches the real model with live vegetation clearance")
        elif message == PROPERTY.split(" ", 1)[1]:
            findings = {row["reason"]: row["examples"] for row in payload["worksite_findings"]}
            model_fixture({"minecraft:chest", "supplementaries:pedestal"}.issubset(findings.get("protected_facility", []))
                    and "protected_area" in findings, "The real scan detects the stocked chest, mod facility and marked asset")
            model_fixture(marked and all(not fit["actionable"] for fit in marked),
                          "The protected mark reaches the model with a restricted clearance report")
        else:
            model_fixture(any((fit["width"], fit["depth"]) == (7, 9) and fit["actionable"]
                              and fit["clearance"].get("soil") and fit["clearance"].get("wood")
                              and fit["clearance"].get("masonry")
                              for pad in pads for fit in pad["sizes"]),
                          "At least one surveyed option keeps the facilities and can deal with ordinary wood and masonry")
        return super().survey_with_metrics(payload, image, **flags)

    @staticmethod
    def prompts():
        return {prompt.split(" ", 1)[1] for prompt in (GRASS, PROPERTY, TERRAIN)}

    def plan_with_metrics(self, payload, image=None, **flags):
        model_fixture(payload["player_suggestion"] == ROOF_HINT and image is not None,
                "Parallel planning sends only a scripted direction hint with the authorized route frame")
        if sum("image_sha256" in row for row in self.budget.records()) >= 6:
            raise ModelBudgetExhausted("M7-Q image budget exhausted")
        return super().plan_with_metrics({**payload, "can_observe_again": False}, image, **flags)

    def decide(self, context):
        raise DesignError("Outside the approved synthetic survey prompt set.")


class WorksiteCopy(CopyGame):
    parallel = False

    def runtime(self, provider):
        return Runtime(self.client, provider, SessionStore(self.world / "architect-previews"),
                       self.settings["instance"], self.settings["blender"],
                       share_observations=True, parallel_planning=self.parallel)


def game_for(label):
    HOME.mkdir(parents=True, exist_ok=True)
    marker = HOME / ("copy-B-natural.json" if label == "B" else "copy-A.json")
    cached = read_object(marker)
    if cached:
        game = Path(cached["game"])
    else:
        source = Path(read_object(SOURCE)["source"]).resolve(strict=True)
        game = create_copy(source, source / "saves" / "m1-construction", terrain_fixture=True)
        write_json(marker, {"game": str(game)})
    synthetic_copy(game)
    return game


def grant(run, site, mode, *, protected=(), minutes=480):
    for label, pos in (("pos1", site["area"]["min"]), ("pos2", site["area"]["max"]),
                       ("spawn", site["spawn"]), ("origin", site["origin"])):
        run.chat.send(run.child, f"/architect site {label} {fmt(pos)}" + (" " + site.get("direction", "north") if label == "origin" else ""),
                      "已标记")
    run.chat.send(run.child, f"/architect site autonomy {mode} {minutes}", "已标记自主权限")
    for first, last in protected:
        run.chat.send(run.child, f"/architect site protect {fmt(first)} {fmt(last)}", "处保护区")
    events = run.chat.send(run.child, "/architect site review", "[批准施工区 ")
    code = next(re.search(r"\[批准施工区 ([a-f0-9-]{36})\]", line)[1]
                for line in events if "[批准施工区 " in line)
    lines = run.chat.send(run.child, "/architect site confirm " + code, "施工区已批准")
    status = run.bridge.health()["site_authorization"]
    require(lines and status["authorization"] == code and status["worksite"]["active"]
            and status["worksite"]["mode"] == mode and status["area"] == site["area"],
            "The real test player signed exactly one bounded worksite permit")
    return status


def stock(run, site, *, place=True, items=None):
    if items is None:
        items = [*STOCK, ("minecraft:stone_brick_slab", 64), ("minecraft:iron_shovel", 1)]
    token = uuid4().hex
    signal(run.world, ".mcaibuilder-stock-request", json.dumps({
        "request": token, "position": site["chest"], "stance": site["stance"], "place": place,
        "items": [{"id": name, "count": count} for name, count in items],
    }))
    file = run.world / ".mcaibuilder-stock-result"
    result = wait_for(run.child, lambda: row if (row := read_object(file)).get("request") == token else None,
                      "real player chest interaction", seconds=60)
    require(result["success"] and (not place or result["chest_placed"]), "The actual approved chest contains test supplies")
    return result


def prepare(run, site, mode, *, protected=(), minutes=480, items=None):
    loaded(run, site, "stance")
    freeze_mob_edits(run)
    run.chat.submit(run.child, "/gamerule randomTickSpeed 0")
    status = run.bridge.health()["site_authorization"]
    if status.get("approved"):
        require(not run.bridge.status().get("job") and status["area"] == site["area"]
                and status["worksite"]["active"] and status["worksite"]["mode"] == mode,
                "A preflight repeat may reuse only the same virgin, authorized worksite")
    else:
        status = grant(run, site, mode, protected=protected, minutes=minutes)
    if not status.get("warehouse_approved"):
        chest = tuple(site["chest"])
        if not any(tuple(row["pos"]) == chest and row["id"] == "minecraft:chest" for row in run.snapshot(site)):
            stock(run, site, items=items)
        run.chat.send(run.child, "/architect supply " + fmt(site["chest"]), "供料箱已批准")
    fixture(run.child, "field", position=site["player"])
    return status, run.snapshot(site)


def start(run, runtime, request, *, cottage=True):
    seen = len(run.chat.messages())
    run.chat.submit(run.child, request)

    def response():
        lines = run.chat.messages()[seen:]
        problems = [line for line in lines if any(message in line for message in
                    ("验收前提未满足", "模型服务响应超时", "模型输出未通过", "现场发现", "现场观察失败", "无法生成三维预览"))]
        if problems:
            raise AssertionError(problems[-1])
        return lines if any("按工作区许可启动" in line for line in lines) else None

    lines = wait_for(run.child, response, "the delegated native construction to start", seconds=160)
    require(not any("[批准 " in line for line in lines), "The test player only approved the bounded worksite")
    state = run.bridge.status()
    job = state["job"]
    require(job["autonomous"] and job["preparation"]["total"] > 0, "The native job contains the authorized preparation")
    folders = list(runtime.store.directory(job["job_id"]).glob("*/review.json"))
    image = "proposed-interior.png" if cottage else "proposed.png"
    reviewed = max(folders, key=lambda path: path.stat().st_mtime_ns, default=None)
    require(reviewed and (reviewed.parent / image).is_file(), "The approved model has Blender artifacts")
    traces = sorted((runtime.store.root / "survey").glob("*.json"), key=lambda file: file.stat().st_mtime_ns)
    return state, read_object(reviewed), read_object(traces[-1])


def terminal(run, job_id, *, seconds=420):
    result = []
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        state = run.bridge.status()
        job = state.get("job", {})
        require(job.get("job_id") == job_id, "Only the authorized job is under test")
        result.append({"tick": state["game_tick"], "phase": job["preparation"]["phase"],
                       "state": job["state"], "cleared": job["preparation"]["cleared"],
                       "placed": job["placed"], "activity": state["activity"]})
        if job["state"] != "running":
            return run.bridge.progress(), result
        sleep(.4)
    raise TimeoutError("Worksite runtime failed to reach a stable state")


def verify(run, site, before, report, state):
    job = state["job"]
    require(job["state"] == "completed" and job["verification"]["passed"],
            f"The actual world and prepared terrain passed exact verification ({job.get('reason')})")
    clear = [action for action in job["actions"] if action["operation"] == "clear"]
    place = [action for action in job["actions"] if action["status"] == "placed"]
    planned = {tuple(row["world"]) for row in report["preparation"]["actions"]}
    completed = {tuple(row["world"]) for row in clear}
    require(planned <= completed and len(place) == job["placed"],
            "All planned clearing and finished placement actions are recorded")
    for action in clear:
        evidence = action["interaction_evidence"]
        require(action["status"] == "removed" and action["actual"]["id"] == "minecraft:air"
                and evidence["left_click_events"] == evidence["break_events"] == 1
                and evidence["reach"] <= 4.5 and evidence["recovered_items"] == 0,
                "Each clearance is a real scoped removal and no uncollected drops were credited")
    verify_receipts(job["supply"]["transfers"], max_items=48)
    allowed = {tuple(row["world"]) for row in report["changes"]}
    allowed.update(tuple(row["world"]) for row in report.get("covered_grass", []))
    allowed.update(tuple(row["world"]) for row in clear)
    allowed.update(tuple(row["also_clears"]) for row in clear if "also_clears" in row)
    require(not unchanged(before, run.snapshot(site), allowed), "Every unaffected block and halo position was preserved")
    counts = Counter()
    for row in clear:
        counts[row["clearance_kind"]] += 1 + ("also_clears" in row)
    return dict(counts)


def verify_source(game):
    record = synthetic_copy(game)
    manifest = read_object(game / "copy-integrity.json")
    require(file_manifest(Path(record["source_world"]), world=True) == manifest["world"]
            and file_manifest(Path(record["source_instance"]) / "mods") == manifest["mods"],
            "The tested copy's source save and mod bytes remained untouched")


def grass_case(run, site, history, *, retry_preview=False):
    permit, before = prepare(run, site, "terrain")
    original = {tuple(row["pos"]): row["id"] for row in before}
    targets = {tuple(a + b for a, b in zip(site["origin"], row["building_local"], strict=True))
               for row in reference_targets()}
    occupied = {pos: original[pos] for pos in targets & original.keys()}
    require(occupied, "The unchanged natural footprint presents live obstacles")
    with agent(run, WorksiteModel()) as runtime:
        started, report, decision = start(run, runtime, "/architect work retry" if retry_preview else GRASS)
        history.update(site=site, permit=permit, initial=before, review=report, decision=decision, started=started)
        write_json(HOME / "A.json", history)
        state, timeline = terminal(run, started["job"]["job_id"])
        history.update(terminal=state, timeline=timeline)
        write_json(HOME / "A.json", history)
        result = verify(run, site, before, report, state)
        reference = {tuple(row["building_local"]): row["after"] for row in reference_targets()}
        planned = {tuple(row["local"]): row["desired"] for row in report["changes"]
                   if row["desired"]["id"] != "minecraft:air"}
        actual = {tuple(pos - origin for pos, origin in zip(row["world"], site["origin"], strict=True)): row["actual"]
                  for row in state["job"]["verification"]["target_states"]}
        require(actual == planned and actual.keys() == reference.keys() and state["job"]["placed"] == len(reference)
                and started["npc"]["npc_id"] == state["npc"]["npc_id"],
                "The same Fake Player built the reference geometry with all material states chosen in the approved plan")
        actual_removals = {tuple(row["world"]): row["before"]["id"] for row in state["job"]["actions"]
                           if row["operation"] == "clear" and row["status"] == "removed"}
        actual_removals.update({tuple(row["also_clears"]): row["before"]["id"] for row in state["job"]["actions"]
                                if row["operation"] == "clear" and "also_clears" in row and row["status"] == "removed"})
        require(all(actual_removals.get(pos) == block for pos, block in occupied.items()),
                "The untouched footprint was cleared through native Fake Player actions at each occupied cell")
        history.update(clearance=result, original_obstacles=dict(Counter(occupied.values())),
                       removal_species=dict(Counter(actual_removals.values())), interior=walkable_cells(report, state),
                       changed_reference_materials=sum(actual[pos] != reference[pos] for pos in actual), passed=True)
        print(f"Worksite A passed with {result} and 183 verified placements.", flush=True)


def seed_block(run, site, pos, item, *, grow=False):
    active = {tuple(row["pos"]): row for row in run.snapshot(site)}.get(tuple(pos))
    if active:
        if active["id"] == item or grow and active["id"] == "minecraft:oak_log":
            return {key: active[key] for key in ("id", "properties")}
        require(False, "The isolated setup target is already occupied by " + active["id"])
    terrain = fixture(run.child, "survey")["terrain"]
    target = [value + .5 for value in pos]
    choices = sorted((row["p"] for row in terrain if row["stand"] and row["p"] != pos
                      and all(a <= b <= c for a, b, c in zip(site["area"]["min"], row["p"], site["area"]["max"], strict=True))
                      and dist([row["p"][0] + .5, row["p"][1] + 1.6, row["p"][2] + .5], target) <= 4),
                     key=lambda p: dist([p[0] + .5, p[1] + 1.6, p[2] + .5], target))
    require(choices, "The isolated test owner can reach the fixture cell")
    for feet in choices[:4]:
        fixture(run.child, "field", position=feet)
        try:
            return fixture(run.child, "worksite_seed", position=pos, id=item, grow=grow)["placed"]
        except AssertionError:
            if any(tuple(row["pos"]) == tuple(pos) for row in run.snapshot(site)):
                raise
    raise AssertionError(f"The isolated owner did not find a placement stance for {item} at {pos}")


def mixed_fixtures(run, site):
    history = []
    loaded(run, site, "stance")
    positions = [
        ([102, 72, 297], "minecraft:oak_sapling", True),
        ([97, 72, 297], "minecraft:dirt", False),
        ([98, 72, 298], "minecraft:grass_block", False),
        ([99, 72, 300], "minecraft:stone", False),
    ]
    occupied = {tuple(row["pos"]): row["id"] for row in run.snapshot(site)}

    def plant_or_place(pos, item, *, grow=False):
        state = seed_block(run, site, list(pos), item, grow=grow)
        occupied.update({tuple(row["pos"]): row["id"] for row in run.snapshot(site)})
        history.append({"pos": list(pos), "actual": state, "native_bonemeal": grow})

    for (x, y, z), item, grow in positions:
        variation = [(x, y, z + offset) for offset in (0, -1, 1, -2, 2)]
        cell = next((pos for pos in variation if pos not in occupied), None)
        require(cell is not None, f"No free fixture cell for {item}")
        plant_or_place(cell, item, grow=grow)

    # Build two real columns at the west edge, choosing empty spots around the living vegetation.
    west = site["origin"][0] - 3
    grade = site["origin"][1]
    z0 = site["origin"][2]
    vacant = [(x, grade, z) for x in (west, west - 1) for z in range(z0 - 4, z0 + 5)
              if (x, grade, z) not in occupied and (x, grade + 1, z) not in occupied]
    require(len(vacant) >= 5 and vacant[0][0] == west, "The preserved fixtures can occupy the original west edge")
    log, masonry, pedestal, chest, marked = vacant[:5]
    plant_or_place(log, "minecraft:oak_log")
    plant_or_place((log[0], log[1] + 1, log[2]), "minecraft:oak_log")
    plant_or_place(masonry, "minecraft:stone_bricks")
    plant_or_place((masonry[0], masonry[1] + 1, masonry[2]), "minecraft:oak_planks")
    plant_or_place(pedestal, "supplementaries:pedestal")
    plant_or_place(marked, "minecraft:stone_bricks")
    fixture(run.child, "field", position=[west - 1, grade, z0])
    choices = [row["p"] for row in fixture(run.child, "survey")["terrain"]
               if row["stand"] and 0 < dist(row["p"], chest) <= 2.4]
    require(choices, "A safe stance reaches the protected chest")
    property_chest = {"chest": list(chest), "stance": min(choices, key=lambda pos: dist(pos, chest))}
    fixture(run.child, "field", position=site["player"])
    return history, property_chest


def property_case(run, runtime, site, original):
    lines = run.chat.send(run.child, PROPERTY, "现场发现", timeout=120)
    require(any("minecraft:chest" in line and "supplementaries:pedestal" in line for line in lines),
            "The private question summarizes both actual facilities")
    require(not run.bridge.status().get("job") and run.snapshot(site) == original,
            "The unresolved property decision left the entire site untouched")
    traces = sorted((runtime.store.root / "survey").glob("*.json"), key=lambda path: path.stat().st_mtime_ns)
    result = read_object(traces[-1])
    require(result["decisions"][-1]["action"] in {"request_player_decision", "unsuitable"},
            "The real model requested an owner decision about the protected mark")
    return result


class TimeoutFixture:
    def survey_with_metrics(self, payload, image, **flags):
        raise ModelTimeout("M7-Q offline timeout fixture")


def verify_denials(run, site, before):
    prior = game_for("A")
    mismatched = ArchitectClient(prior / "config" / "mcaibuilder-architect.json", timeout=4)
    cross_copy = rejected(mismatched.inbox, 401, "unauthorized")
    request = wait_request(run, "/architect work 请核对测试中的越界构建请求。")
    request_id = request["request_id"]
    frame = collect(run.client, request_id, 1, "observe_here")
    require(frame["capture_status"] == "captured", "The out-of-area validation uses a fresh actual owner request")
    x, y, z = site["origin"]
    unsafe = make_plan(Cottage().building(), building_id="cottage_out_of_scope", version=1,
                       origin=(x + 40, y, z), rotation=0)
    out_of_scope = rejected(lambda: run.client.stage(request_id, "new", unsafe), 409, "placement_scope")
    run.client.answer(request_id, "测试已拒绝越界方案。")
    stale = rejected(lambda: run.client.stage(request_id, "new", unsafe), 409, "stale_request")
    with agent(run, TimeoutFixture()):
        run.chat.send(run.child, "/architect work 校验模型超时后仍保持现场原样。", "模型服务响应超时", timeout=65)
    require(not run.bridge.status().get("job") and run.snapshot(site) == before,
            "Out of scope, stale, and timed-out plans caused no world changes")
    return {"other_copy": cross_copy, "out_of_scope": out_of_scope, "stale": stale, "model_timeout": True}


def prepare_mixed(run, site, history):
    terrain, property_chest = mixed_fixtures(run, site)
    asset = terrain[-1]["pos"]
    permit, _ = prepare(run, site, "trees", protected=[(asset, asset)], items=STOCK)
    protected_stock = stock(run, property_chest, items=[("minecraft:spruce_planks", 3)])
    fixture(run.child, "field", position=site["player"])
    before = run.snapshot(site)
    history.update(site=site, permit=permit, initial=before, native_fixture=terrain,
                   property_chest=property_chest, protected_stock=protected_stock)
    history["denials"] = verify_denials(run, site, before)
    write_json(HOME / "B.json", history)
    stock(run, site, place=False, items=[("minecraft:shears", 1)])
    fixture(run.child, "field", position=site["player"])
    with agent(run, WorksiteModel()) as runtime:
        history["property"] = property_case(run, runtime, site, before)
    plan_mixed(run, site, history)


def plan_mixed(run, site, history):
    require(not run.bridge.status().get("job"), "The reviewed worksite remains unstarted")
    # The authorized test player leaves an ordinary two-block obstruction in the east of the building area.
    before = {tuple(row["pos"]): row["id"] for row in loaded(run, site, "stance")}
    positions = [[x, 72, z] for x in (100, 101, 99) for z in (299, 300, 298, 297)]
    base = next((pos for pos in positions if before.get(tuple(pos)) == "minecraft:oak_planks"
                 and before.get((pos[0], pos[1] + 1, pos[2])) == "minecraft:stone_bricks"), None)
    if base is None:
        base = next((pos for pos in positions if tuple(pos) not in before
                     and (pos[0], pos[1] + 1, pos[2]) not in before
                     and before.get((pos[0], pos[1] - 1, pos[2])) in {"minecraft:dirt", "minecraft:grass_block"}), None)
        require(base, "A native fixture stance can keep ambient vegetation intact")
    above = [base[0], base[1] + 1, base[2]]
    seed_block(run, site, base, "minecraft:oak_planks")
    seed_block(run, site, above, "minecraft:stone_bricks")
    fixture(run.child, "field", position=site["player"])
    history.update(initial=run.snapshot(site), ordinary_barricade=[base, above])
    write_json(HOME / "B.json", history)
    with agent(run, WorksiteModel()) as runtime:
        # Wait for the integrated BMC4 client to finish recipes and render at the stable spawn.
        sleep(8)
        try:
            started, report, decision = start(run, runtime, "/architect work " + TERRAIN.split(" ", 1)[1])
        except AssertionError as error:
            if "tick_skew" not in str(error):
                raise
            sleep(5)
            started, report, decision = start(run, runtime, "/architect work " + TERRAIN.split(" ", 1)[1])
        history.update(review=report, decision=decision, started=started)
        write_json(HOME / "B.json", history)
        state, timeline = terminal(run, started["job"]["job_id"])
        history.update(paused=state, before_restart=run.snapshot(site), timeline=timeline)
        write_json(HOME / "B.json", history)
        require(state["job"]["state"] == "paused" and state["job"]["reason"] == "out_of_tool"
                and state["job"]["preparation"]["cleared"] > 0, "Clearance safely waits for a real shovel")
        print("The worksite genuinely cleared part of the site, then paused for the absent shovel.", flush=True)


def reopen_mixed(run, history):
    site, paused = history["site"], history["paused"]
    require(loaded(run, site) == history["before_restart"], "Native reload retained the partial clearance and every other cell")
    history["recovery"] = checkpoint_after_reopen(run)
    state = run.bridge.progress()
    require(state["npc"]["npc_id"] == paused["npc"]["npc_id"]
            and state["job"]["actions"] == paused["job"]["actions"]
            and state["job"]["preparation"] == paused["job"]["preparation"],
            "The same worker and exact partially cleared queue are restored")
    run.chat.submit(run.child, "/gamemode spectator")
    run.chat.submit(run.child, "/execute in minecraft:the_nether run tp @s 0 110 0")
    wait_for(run.child, lambda: run.bridge.progress().get("recovery", {}).get("waiting") == "site_not_loaded",
             "Overworld chunks to unload while clearing is paused", seconds=75)
    run.chat.send(run.child, "/architect resume " + paused["job"]["job_id"], "[architect_forbidden]")
    require(run.bridge.progress()["job"]["actions"] == paused["job"]["actions"],
            "Neither unload nor a command from another dimension executed any clearance")
    fixture(run.child, "field", position=site["player"])
    checkpoint_after_reopen(run)
    history["chunk_unload_recovered"] = True
    stock(run, site, place=False, items=[("minecraft:iron_shovel", 1)])
    fixture(run.child, "field", position=site["player"])
    run.chat.send(run.child, "/architect resume " + paused["job"]["job_id"], "已继续施工")
    job_id = paused["job"]["job_id"]
    wait_for(run.child, lambda: run.bridge.status()["job"]["preparation"]["phase"] == "build",
             "native grading to finish before following the construction route", seconds=110)
    run.chat.send(run.child, "/architect plan " + ROOF_HINT, "施工顺序建议已记录")
    run.parallel = True
    try:
        with agent(run, WorksiteModel()):
            final, timeline = terminal(run, job_id)
    finally:
        run.parallel = False
    history.update(terminal=final, resumed_timeline=timeline)
    write_json(HOME / "B.json", history)
    history["clearance"] = verify(run, site, history["initial"], history["review"], final)
    readback = stock(run, history["property_chest"], place=False, items=[])
    require(readback["before"] == readback["after"] == history["protected_stock"]["after"],
            "The original protected chest kept every item")
    history["protected_inventory_matches"] = True
    job = final["job"]
    require(job["verification"]["matched"] == job["target_count"] and job["placed"] >= 183,
            "Every final cottage block matches the eastward design")
    clearance = [action for action in job["actions"] if action["operation"] == "clear"]
    removed = {tuple(action["world"]): action["before"]["id"] for action in clearance if action["status"] == "removed"}
    require({removed.get(tuple(pos)) for pos in history["ordinary_barricade"]}
            == {"minecraft:oak_planks", "minecraft:stone_bricks"},
            "Player-placed wood and stone were handled in the same native queue without another approval")
    for action in clearance:
        if action["clearance_kind"] in {"soil", "stone", "logs", "leaves", "wood", "masonry"}:
            evidence = action["interaction_evidence"]
            tool = {"soil": "iron_shovel", "stone": "iron_pickaxe", "logs": "iron_axe",
                    "leaves": "shears", "wood": "iron_axe", "masonry": "iron_pickaxe"}[action["clearance_kind"]]
            require(evidence["tool_id"] == "minecraft:" + tool
                    and evidence["tool_damage_after"] - evidence["tool_damage_before"] == 1,
                    "The real Fake Player used the correct tools and persisted durability")
    history["native_reopen"] = True
    planning = job.get("planning", {})
    require(planning.get("observation_id") and planning.get("state") in {"applied", "kept"}
            and planning.get("eligible_actions_during_thinking", 0) > 0,
            "The real planner remained asynchronous after clearance and a native reopen")
    print(f"Mixed terrain passed with {history['clearance']} and {final['job']['placed']} verified placements.", flush=True)
    write_json(HOME / "B.json", history)
    history["permission_controls"] = verify_permission_end(run)
    history["passed"] = True


class PermissionFixture:
    def survey_with_metrics(self, payload, image, **flags):
        pad = next(row for row in payload["foundation_options"]
                   if row["rotation"] == 90 and row["offset_from_mark"] == [0, 0, 0])
        return {"action": "design", "kind": "window_wall", "pad": pad["id"], "offset": None,
                "changes": {"width": 5, "height": 4}, "reason": "在已批准的小地块整理可用支撑。"}, {}


def verify_permission_end(run):
    site = {"area": {"min": [89, 69, 291], "max": [94, 78, 305]},
            "origin": [91, 72, 299], "spawn": [91, 72, 297], "chest": [89, 72, 294],
            "stance": [90, 72, 294], "player": [90, 72, 296], "direction": "east"}
    loaded(run, site, "stance")
    before = {tuple(row["pos"]): row["id"] for row in run.snapshot(site)}
    mark = next(([91, 72, z] for z in (299, 298, 300, 297, 301)
                 if (91, 72, z) not in before and before.get((91, 71, z)) in {"minecraft:grass_block", "minecraft:dirt"}), None)
    require(mark, "The permission test has an unchanged ground cell for its native dirt fixture")
    site["origin"] = mark
    seed_block(run, site, mark, "minecraft:dirt")
    permit = grant(run, site, "terrain")
    stock(run, site, items=STOCK)
    run.chat.send(run.child, "/architect supply " + fmt(site["chest"]), "供料箱已批准")
    capacity = fixture(run.child, "worksite_capacity", position=site["chest"])["capacity"]
    fixture(run.child, "field", position=site["player"])
    original = run.snapshot(site)
    with agent(run, PermissionFixture()) as runtime:
        started, _, _ = start(run, runtime, "/architect work 在这个标记建一面小窗墙，先整理普通障碍。", cottage=False)
        job = terminal(run, started["job"]["job_id"])[0]["job"]
        require(job["state"] == "paused" and job["reason"] == "out_of_tool"
                and job["preparation"]["cleared"] < job["preparation"]["total"],
                "A pending natural obstacle awaits a real tool when permission is revoked")
        run.chat.send(run.child, "/architect site autonomy revoke", "已撤销自主建设")
        run.chat.send(run.child, "/architect resume " + job["job_id"], "[worksite_permission_inactive]")
        run.chat.send(run.child, "/architect work 再核对这个旧清障方案。", "[worksite_permission_inactive]")
        restored = run.bridge.progress()
        require(restored["job"]["actions"] == job["actions"] and restored["job"]["placed"] == 0,
                "Revocation prevents any pending clear or build action from resuming")
    snapshot = run.snapshot(site)
    short = grant(run, site, "terrain", minutes=1)
    require(short["authorization"] != permit["authorization"], "The new signed worksite is a different authorization version")
    run.chat.send(run.child, "/architect resume " + job["job_id"], "[architect_job_scope]")
    wait_for(run.child, lambda: run.bridge.health()["site_authorization"]["worksite"]["state"] == "expired",
             "the actual one-minute authorization to expire", seconds=80)
    run.chat.send(run.child, "/architect work 再启动旧现场的准备。", "[worksite_permission_inactive]")
    require(not run.bridge.status().get("job") and run.snapshot(site) == snapshot,
            "An expired or replaced permit cannot convert any pending preparation into a mutation")
    return {"revoked_pending_clearance": True, "stale_scope_replay": True, "elapsed_expiry": True,
            "untouched_before_missing_tool": original == snapshot, "full_cargo_guard": capacity}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=("A", "A-retry", "B", "B-continue", "resume"))
    args = parser.parse_args()
    os.environ["MC_AI_BUILDER_PROFILE"] = "1"
    os.environ["MC_AI_BUILDER_LEGACY_EXECUTOR"] = "0"
    label = "A" if args.case.startswith("A") else "B"
    game = game_for(label)
    history = read_object(HOME / "B.json") if args.case in {"resume", "B-continue"} else {"passed": False}
    site = read_object(M7A / "selected-site.json")
    if label == "B":
        site["area"]["max"][1] = 81
    print(f"Opening the marked BMC4 worksite copy for case {args.case}.", flush=True)
    try:
        with WorksiteCopy(game, PORT) as run:
            if label == "A":
                grass_case(run, site, history, retry_preview=args.case == "A-retry")
            elif args.case == "B":
                prepare_mixed(run, site, history)
            elif args.case == "B-continue":
                plan_mixed(run, history["site"], history)
            else:
                reopen_mixed(run, history)
        verify_source(game)
        history["native_save"] = True
    finally:
        write_json(HOME / f"{label}.json", history)


if __name__ == "__main__":
    main()
