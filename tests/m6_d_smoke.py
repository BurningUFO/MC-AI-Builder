"""Play and verify supervised builds in a marked synthetic BMC4 world copy."""

from argparse import ArgumentParser
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Thread
from time import sleep

from src.architect.codex_provider import CodexProvider
from src.architect.observations import rgb_png
from src.architect.providers import FixtureProvider, ModelTimeout
from src.core.building import write_json
from tests.m1_smoke import require, wait_for
from tests.m3_codex_live import await_approval
from tests.m3_smoke import assert_pending_without_actions, signal, verify_world
from tests.m4_supply_smoke import verify_receipts
from tests.m5_b_smoke import fixture as copy_fixture, unchanged
from tests.m6_a_smoke import fixture as camera_fixture, pixel_metrics
from tests.m6_c_smoke import CopyGame, HOME, ROOT, synthetic_copy
from tools.mcai_launcher import read_object
from tools.world_copy import file_manifest

INTENT = ("请观察已标记的天然地基，设计一面5格宽4格高的窗墙，两个1格宽2格高的透明玻璃窗。"
          "用深色橡木、石砖地基、石砖台阶收顶。保持地形，不加门廊。当前站位看得清就直接设计。")
MAIN = ("/architect supervise " + INTENT + "半程复查透明窗是否与明亮草地或天空混在一起。"
        "若实际已放置透明玻璃且难辨认，只建议改为蓝色玻璃，保持其他设计。")
NORMAL = "/architect supervise " + INTENT + "只复查进度，透明玻璃和其余批准设计保持原样。"
TRACE = HOME / "m6-d-trace.json"
PORT = 18788


@contextmanager
def agent(run, provider):
    runtime = run.runtime(provider)
    done = Event()
    thread = Thread(target=runtime.run, args=(done.is_set,), daemon=True)
    thread.start()
    try:
        wait_for(run.child, lambda: run.bridge.health()["architect_runtime_online"], "Architect connected", seconds=30)
        yield runtime
    finally:
        done.set()
        thread.join(timeout=75)
        require(not thread.is_alive(), "The scoped Runtime shut down cleanly")


def real_provider():
    return CodexProvider(timeout=50, max_turns=10, max_tokens=150_000, budget_log=HOME / "codex-turns.sqlite3")


def marks(runtime, kind):
    return set((runtime.store.root / kind).glob("*.json"))


def traces(runtime, kind, before):
    return [read_object(path) for path in sorted(marks(runtime, kind) - before, key=lambda p: p.stat().st_mtime_ns)]


def waiting_card(run, before):
    def ready():
        lines = run.chat.messages()[before:]
        return lines if any("[拒绝 " in line or "施工复查。" in line for line in lines) else None

    lines = wait_for(run.child, ready, "native visual checkpoint reply", seconds=125)
    if any("[拒绝 " in line for line in lines):
        return run.chat.read_card(lines)
    return run.chat.card(run.child, "/architect inspect 现场透明窗难辨认，请根据新的画面只改成蓝色玻璃，保留已建部分。",
                         timeout=90)


def accept(run, card, trace, label):
    report = card["report"]
    scope = run.bridge.health()["site_authorization"]
    require(scope["mode"] == "world_copy" and scope["authorization"] == report["authorization"]
            and report["counts"]["conflict"] == 0, "The saved test owner approves only this reviewed copy version")
    signal(run.world, ".mcaibuilder-architect-command", "architect approve " + card["code"])
    return await_approval(run.child, run.bridge, run.chat, card, trace, label)


def snapshot_progress(run, job_id, states, *, seconds=250):
    def check():
        status = run.bridge.progress()
        job = status.get("job", {})
        return status if job.get("job_id") == job_id and job["state"] in states else None

    return wait_for(run.child, check, "native construction state", seconds=seconds)


def assert_half(progress, report):
    job = progress["job"]
    require(job["state"] == "paused" and 0 < job["placed"] < job["target_count"], "An unfinished wall is frozen")
    desired = {tuple(row["world"]): row["desired"] for row in report["changes"]}
    actions = [row for row in job["actions"] if row["status"] == "placed"]
    require(len(actions) == job["placed"] and sum(a["actual"]["id"] == "minecraft:glass" for a in actions) >= 1,
            "Transparent panes were actually installed before the model evaluated the design issue")
    for row in actions:
        ev = row["interaction_evidence"]
        require(row["actual"] == desired[tuple(row["world"])]
                and ev["method"] == "ServerPlayerGameMode.useItemOn"
                and ev["right_click_events"] == ev["place_events"] == 1
                and ev["item_before"] - ev["item_after"] == 1 and ev["reach"] <= 4.5,
                "Native item and Forge evidence supports every partial placement")
    return actions


def run_main_case(run, *, reuse_v1=False):
    synthetic_copy(run.world)
    selected = read_object(HOME / "selected-sites.json")["sites"]
    site = selected[0]
    trace = {"passed": False, "site": site, "real_model": True}
    record = read_object(run.world / ".mcaibuilder-copy-player-proof.json")
    require(record["saved_identity_matches"] and record["inventory_matches"], "Native saved-player identity and inventory")
    run.select(site)
    trace["baseline"] = run.snapshot(site)
    with agent(run, real_provider()) as runtime:
        survey_before = marks(runtime, "survey")
        feedback_before = marks(runtime, "feedback")
        if reuse_v1:
            require(not run.bridge.status().get("job") and len(survey_before) == 1,
                    "The isolated copy still has its first signed visual design and no active job")
            lines = run.chat.messages()
            ticket = next(i for i in range(len(lines) - 1, -1, -1) if "[拒绝 " in lines[i])
            v1 = run.chat.read_card(lines[ticket - 2:ticket + 1])
            trace["survey"] = read_object(next(iter(survey_before)))
        else:
            v1 = run.chat.card(run.child, MAIN, timeout=105)
            trace["survey"] = traces(runtime, "survey", survey_before)[-1]
        trace["V1"] = v1["report"]
        require(v1["report"]["counts"]["add"] == 20, "The surveyed wall contains twenty exact block states")
        assert_pending_without_actions(run.child, run.bridge)
        require(run.snapshot(site) == trace["baseline"], "Pre-build visual planning remains read-only")
        frame_id = trace["survey"]["steps"][-1]["observation_id"]
        sensor = read_object(ROOT / ".cache" / "observations" / run.world.name / (frame_id + ".json"))
        require(sensor["observation_id"] == frame_id and sensor["camera_source"] == "npc_first_person",
                "The proposal is linked to the captured native NPC")
        trace["observer_npc"] = sensor["npc_uuid"]
        write_json(TRACE, trace)
        chat_before = len(run.chat.messages())
        started = accept(run, v1, trace, "M6-D V1")
        require(started["npc"]["npc_id"] == trace["observer_npc"], "The survey NPC is the actual construction worker")
        v2 = waiting_card(run, chat_before)
        current = assert_pending_without_actions(run.child, run.bridge)
        require(current["job"]["job_id"] == v1["report"]["new_version"]["plan_id"], "V1 remains active until V2 approval")
        V2 = v2["report"]
        trace["V2"] = V2
        trace["checkpoint"] = run.bridge.progress()
        write_json(TRACE, trace)
        placements = assert_half(trace["checkpoint"], v1["report"])
        require(V2["counts"]["replace"] >= 1 and V2["counts"]["keep"] > 0 and V2["counts"]["conflict"] == 0,
                "The proposal preserves completed work and replaces only affected windows")
        replaced = [row for row in V2["changes"] if row["category"] == "replace"]
        require(all(row["actual"]["id"] == "minecraft:glass" and row["desired"]["id"] == "minecraft:blue_stained_glass"
                    for row in replaced), "The model's proposal is the observed glass correction")
        trace["feedback"] = traces(runtime, "feedback", feedback_before)
        require(trace["feedback"][-1]["event"] in ("checkpoint", "requested")
                and trace["feedback"][-1]["steps"][-1]["decision"]["changes"] == {"glass": "minecraft:blue_stained_glass"},
                "The new building parameters came from a real paired visual checkpoint")
        trace["snapshot_at_review"] = run.snapshot(site)
        trace["receipts_v1"] = trace["checkpoint"]["job"]["supply"]["transfers"]
        require(sum(verify_receipts(trace["receipts_v1"]).values()) >= len(placements),
                "Every original placement has a material debit from the approved chest")
        trace["confirmation"] = v2["code"]
        trace["old_confirmation"] = v1["code"]
        trace["prepared"] = True
        write_json(TRACE, trace)
        print("M6-D live checkpoint proposed glass-only V2. Saving pending review for restart.", flush=True)


def recover(run, trace):
    expected = trace["checkpoint"]
    job = expected["job"]
    exact = wait_for(run.child, lambda: status.get("recovery")
                     if (status := run.bridge.progress()).get("recovery", {}).get("npc_reused") else None,
                     "the original NPC, journal, and warehouse after a normal restart", seconds=75)
    require(all(exact[key] for key in ("world_matches_checkpoint", "npc_reused", "inventory_matches_checkpoint",
                                       "warehouse_matches_checkpoint", "warehouse_transfer_confirmed", "interaction_confirmed")),
            "World, actor cargo, construction and transfer checkpoints agree after restart")
    trace["recovery"] = exact
    require(run.snapshot(trace["site"]) == trace["snapshot_at_review"], "Native reload preserved every monitored block")
    require(run.bridge.change()["review_sha256"] == trace["V2"]["review_sha256"], "The pending review survived native reload")
    if trace.get("chunk_unload_refused"):
        return
    # A dimension change goes through the real test client and releases the Overworld chunk tickets.
    run.chat.submit(run.child, "/gamemode spectator")
    run.chat.submit(run.child, "/execute in minecraft:the_nether run tp @s 0 110 0")
    wait_for(run.child, lambda: run.bridge.progress().get("recovery", {}).get("waiting") == "site_not_loaded",
             "the approved Overworld region to unload", seconds=65)
    before = len(run.chat.messages())
    signal(run.world, ".mcaibuilder-architect-command", "architect approve " + trace["confirmation"])
    wait_for(run.child, lambda: any("[architect_forbidden]" in line for line in run.chat.messages()[before:]),
             "refusing a signed approval from another dimension", seconds=10)
    unload = run.bridge.progress()
    require(unload["job"]["processed"] == job["processed"] and unload["change_pending"], "Unloaded scope stayed paused")
    trace["chunk_unload_refused"] = True
    copy_fixture(run.child, "field", position=trace["site"]["player"])
    wait_for(run.child, lambda: run.bridge.progress().get("recovery", {}).get("inventory_matches_checkpoint"),
             "the same cargo and loaded job after returning", seconds=55)
    require(run.bridge.change()["review_sha256"] == trace["V2"]["review_sha256"], "No new preflight hash after a read-only chunk visit")


def finish_main(run, trace, provider):
    with agent(run, provider) as runtime:
        recover(run, trace)
        before = marks(runtime, "feedback")
        copies = {"code": trace["confirmation"], "report": trace["V2"]}
        started = accept(run, copies, trace, "M6-D V2")
        job_id = started["job"]["job_id"]
        require(started["job"]["building_version"] == 2 and started["npc"]["npc_id"] == trace["observer_npc"],
                "The same survey and V1 NPC received the approved V2")
        command_before = len(run.chat.messages())
        signal(run.world, ".mcaibuilder-architect-command", "architect approve " + trace["old_confirmation"])
        wait_for(run.child, lambda: any("[stale_approval]" in line for line in run.chat.messages()[command_before:]),
                 "refusing the consumed approval", seconds=12)
        complete = snapshot_progress(run, job_id, {"completed", "failed", "paused"})
        require(complete["job"]["state"] == "completed", f"Incremental job outcome {complete['job'].get('reason')}")
        trace["verification"] = verify_world(complete, trace["V2"])
        trace["terminal"] = complete
        trace["receipts_v2"] = complete["job"]["supply"]["transfers"]
        v2_stock = verify_receipts(trace["receipts_v2"])
        require(v2_stock["minecraft:blue_stained_glass"] == 4, "The real approved chest supplied all four blue panes")
        dig = [row for row in complete["job"]["actions"] if row["status"] == "removed"]
        require(len(dig) == trace["V2"]["counts"]["replace"] and all(
            row["interaction_evidence"]["break_events"] == 1 for row in dig), "The real Forge break events match the authorized diff")
        allowed = {tuple(row["world"]) for row in trace["V2"]["changes"]}
        allowed |= {tuple(row["world"]) for row in trace["V1"].get("covered_grass", [])}
        require(not unchanged(trace["baseline"], run.snapshot(trace["site"]), allowed),
                "The approved wall and the already disclosed grass effects bound all world changes")
        wait_for(run.child, lambda: any("完工复查。Forge 逐格核验已通过" in line for line in run.chat.messages()[command_before:]),
                 "the real final visual assessment and precise Forge readback", seconds=90)
        trace["feedback"] += traces(runtime, "feedback", before)
        require(trace["feedback"][-1]["phase"] == "VERIFY", "The completed scene had final visual verification")
        trace["main_passed"] = True
        trace["final_local_monitor"] = isinstance(provider, LocalFeedback)
        if trace["final_local_monitor"]:
            trace["final_pixel_proof"] = provider.pixel_proof
        write_json(TRACE, trace)
        print("M6-D live improvement and final exact verification passed.", flush=True)


class LocalFeedback:
    """Check real sensor frames locally and exercise safety faults with a deterministic backend."""

    def __init__(self, *, exact_final=False):
        self.mode = "normal"
        self.calls = 0
        self.exact_final = exact_final
        self.pixel_proof = None

    def survey_with_metrics(self, payload, image, **flags):
        choose = next(row for row in payload["foundation_options"] if row["span"] >= 5)
        return ({"action": "design", "pad": choose["id"], "offset": None, "reason": "本地回归检查的地基选择",
                 "changes": {"width": 5, "height": 4, "window_width": 1, "window_height": 2,
                             "style": "medieval", "wood": "minecraft:dark_oak_planks", "porch": False}},
                {})

    def feedback_with_metrics(self, payload, image, **flags):
        self.calls += 1
        if self.mode == "timeout":
            raise ModelTimeout("local visual fault")
        if self.mode == "expired":
            sleep(63)
        if self.mode == "probe" or self.exact_final and payload["event"] == "final":
            block = "minecraft:glass" if self.mode == "probe" else "minecraft:blue_stained_glass"
            target = payload.get("focus_relative_to_npc")
            require(any(row["offset"] == target and row["id"] == block for row in payload["scene"]["cells"]),
                    "The first-person lens is centered on a real glass window")
            png, width, height = rgb_png(image)
            self.pixel_proof = pixel_metrics(png, width, height)
            require(self.pixel_proof["center_distinct_colors"] > 10, "The window-focused frame has actual scene detail")
            if self.mode == "probe":
                return {"decision": "report", "changes": None, "reason": "本地窗格视角核对已通过。"}, {}
        decision = "continue" if payload["event"] == "checkpoint" or self.mode == "resume_proof" else "report"
        return {"decision": decision, "changes": None, "reason": "本地回归复查保持已批准设计。"}, {}


def protected_unchanged(run, site, baseline, job):
    current = run.bridge.progress()
    require(run.snapshot(site) == baseline and current["job"]["job_id"] == job["job_id"]
            and current["job"]["processed"] == job["processed"] and not current["change_pending"],
            "A visual failure did not alter the completed structure or create a candidate")


def normal_and_faults(run, trace):
    run.chat.send(run.child, "/architect site revoke", "已撤销施工区")
    site = read_object(HOME / "selected-sites.json")["sites"][1]
    run.select(site)
    engine = LocalFeedback()
    with agent(run, engine) as runtime:
        before = marks(runtime, "feedback")
        card = run.chat.card(run.child, NORMAL, timeout=100)
        offset = len(run.chat.messages())
        started = accept(run, card, trace, "M6-D normal V1")
        job = started["job"]
        wait_for(run.child, lambda: any("继续当前施工" in line for line in run.chat.messages()[offset:]),
                 "a no-change checkpoint offering the existing player resume action", seconds=110)
        resume_normal(run, trace, engine, runtime, card, started, before)


def resume_normal(run, trace, engine, runtime, card, started, before):
    site = read_object(HOME / "selected-sites.json")["sites"][1]
    job = started["job"]
    partial = snapshot_progress(run, job["job_id"], {"paused"}, seconds=35)
    require(partial["job"]["reason"] == "visual_checkpoint" and not partial["change_pending"]
            and 0 < partial["job"]["processed"] < partial["job"]["total"],
            "A compliant site does not produce a new draft at its visual milestone")
    assert_pending_without_actions(run.child, run.bridge)
    baseline = run.snapshot(site)
    engine.mode = "timeout"
    run.chat.send(run.child, "/architect inspect 检查施工中的墙体。", "模型服务响应超时。", timeout=25)
    protected_unchanged(run, site, baseline, partial["job"])
    trace["model_timeout_no_world_change"] = True
    engine.mode = "expired"
    run.chat.send(run.child, "/architect inspect 再检查墙体。", "现场或 NPC 已变化", timeout=86)
    protected_unchanged(run, site, baseline, partial["job"])
    trace["expired_pair_no_world_change"] = True
    engine.mode = "normal"
    signal(run.world, ".mcaibuilder-architect-command", "architect resume " + job["job_id"])
    complete = snapshot_progress(run, job["job_id"], {"completed", "failed"}, seconds=135)
    verify_world(complete, card["report"])
    wait_for(run.child, lambda: any(row["phase"] == "VERIFY" for row in traces(runtime, "feedback", before)),
             "the normal wall's final observation", seconds=30)
    trace["normal"] = {"backend": "deterministic", "version": complete["job"]["building_version"],
                       "steps": len(complete["job"]["verification"]["target_states"]),
                       "event_trace": traces(runtime, "feedback", before), "no_revision": not complete["change_pending"]}
    baseline = run.snapshot(site)
    calls = engine.calls
    camera_fixture(run.child, run.world, "drop_on_switch", npc=started["npc"]["npc_id"])
    run.chat.send(run.child, "/architect inspect 相机可用性检查。", "本次视觉复查失败", timeout=35)
    protected_unchanged(run, site, baseline, complete["job"])
    require(engine.calls == calls and camera_fixture(run.child, run.world, "client")["camera_is_player"],
            "Missing client-side tracking sends no request to the model and restores the owner camera")
    trace["camera_loss_no_model_or_world_change"] = True
    write_json(TRACE, trace)


def continue_after_restart(game, *, refresh_review=False):
    trace = read_object(TRACE)
    require(trace.get("prepared"), "A reviewed V2 must be pending from the live test")
    record = synthetic_copy(game)
    baseline = read_object(game / "copy-integrity.json")
    try:
        with CopyGame(game, PORT) as run:
            if refresh_review:
                copy_fixture(run.child, "field", position=trace["site"]["player"])
                wait_for(run.child, lambda: run.bridge.progress().get("recovery", {}).get("inventory_matches_checkpoint"),
                         "current journal and cargo before reissuing a consumed test ticket", seconds=105)
                with agent(run, FixtureProvider()):
                    refreshed = run.chat.card(run.child, "/architect ask 请重新审阅当前待批准修改。", timeout=50)
                    require(refreshed["report"]["review_sha256"] == trace["V2"]["review_sha256"],
                            "The renewed signed approval is for exactly the original model-derived V2")
                    trace["confirmation"] = refreshed["code"]
                    write_json(TRACE, trace)
            provider = real_provider()
            verifier = LocalFeedback(exact_final=True) if len(provider.budget.records()) >= 10 else provider
            finish_main(run, trace, verifier)
            normal_and_faults(run, trace)
        require(file_manifest(Path(record["source_world"]), world=True) == baseline["world"]
                and file_manifest(Path(record["source_instance"]) / "mods") == baseline["mods"],
                "The synthetic source and its mod files remain byte-for-byte identical")
        trace.update(passed=True, source_unchanged=True, native_save_reopen=True)
    except Exception as error:
        trace["error"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        trace["budget"] = real_provider().budget.records()
        write_json(TRACE, trace)


def continue_from_checkpoint(game):
    trace = read_object(TRACE)
    record = synthetic_copy(game)
    initial_hashes = read_object(game / "copy-integrity.json")
    require("V1" in trace and not trace.get("prepared"), "The resumed run is a paused visual V1")
    trace["approvals"] = read_object(game / "acceptance.json")["approvals"]
    try:
        with CopyGame(game, PORT) as run:
            copy_fixture(run.child, "field", position=trace["site"]["player"])
            def loaded_checkpoint():
                status = run.bridge.progress()
                job = status.get("job", {})
                if (job.get("job_id") == trace["V1"]["new_version"]["plan_id"] and job.get("state") == "paused"
                        and status.get("recovery", {}).get("npc_reused")):
                    return status
                return None

            trace["checkpoint"] = wait_for(run.child, loaded_checkpoint, "the active original NPC after entity tracking loads",
                                           seconds=105)
            require(trace["checkpoint"]["recovery"]["inventory_matches_checkpoint"]
                    and trace["checkpoint"]["job"]["checkpoint_npc"] == trace["observer_npc"],
                    "The original authorized V1, frozen pose and NPC recovered")
            assert_half(trace["checkpoint"], trace["V1"])
            probe = LocalFeedback()
            probe.mode = "probe"
            with agent(run, probe) as runtime:
                before = marks(runtime, "feedback")
                run.chat.send(run.child, "/architect inspect 请在本地核对窗格视角。", "本地窗格视角核对已通过。", timeout=65)
                observation = wait_for(run.child, lambda: traces(runtime, "feedback", before),
                                       "the completed local camera proof")
                trace["local_reframe"] = observation[-1]
                trace["checkpoint_pixel_proof"] = probe.pixel_proof
            write_json(TRACE, trace)
            with agent(run, real_provider()) as runtime:
                v2 = run.chat.card(run.child, "/architect inspect 我现在选择蓝色玻璃。请先看实际透明窗格及其背景，只改窗的玻璃，保留其他设计。",
                                   timeout=100)
                status = assert_pending_without_actions(run.child, run.bridge)
                require(status["job"]["building_version"] == 1 and status["job"]["processed"] == trace["checkpoint"]["job"]["processed"],
                        "The live redesign caused no action until signed V2 approval")
                trace["V2"] = v2["report"]
                painted = [row for row in trace["V2"]["changes"] if row["category"] in ("replace", "add")
                           and row["desired"]["id"] == "minecraft:blue_stained_glass"]
                require(len(painted) == 4 and trace["V2"]["counts"]["replace"] == 2
                        and trace["V2"]["counts"]["keep"] == 8 and trace["V2"]["counts"]["conflict"] == 0,
                        "The visual correction revises only this window material and keeps eight existing blocks")
                trace["feedback"] = traces(runtime, "feedback", set())
                require(trace["feedback"][-1]["steps"][-1]["decision"]["changes"] == {"glass": "minecraft:blue_stained_glass"},
                        "The last real turn proposes the scoped glass-only change")
                trace["snapshot_at_review"] = run.snapshot(trace["site"])
                trace["receipts_v1"] = trace["checkpoint"]["job"]["supply"]["transfers"]
                require(sum(verify_receipts(trace["receipts_v1"]).values()) >= 10, "The original parts were supplied with real materials")
                trace["old_confirmation"] = next(line for line in run.chat.messages() if "[拒绝 " in line).split("[拒绝 ", 1)[1].split("]", 1)[0]
                trace["confirmation"] = v2["code"]
                trace["prepared"] = True
            write_json(TRACE, trace)
            print("M6-D re-aimed the original NPC, passed local camera checks, and obtained a real glass-only revision.", flush=True)
            finish_main(run, trace, LocalFeedback(exact_final=True))
            normal_and_faults(run, trace)
        require(file_manifest(Path(record["source_world"]), world=True) == initial_hashes["world"]
                and file_manifest(Path(record["source_instance"]) / "mods") == initial_hashes["mods"],
                "The original synthetic template world and mod files are byte-for-byte unchanged")
        trace.update(passed=True, native_save_reopen=True, source_unchanged=True)
        trace.pop("error", None)
    except Exception as error:
        trace["error"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        trace["budget"] = real_provider().budget.records()
        write_json(TRACE, trace)


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("copy", type=Path)
    parser.add_argument("--share-test-images", action="store_true", required=True)
    parser.add_argument("--resume-review", action="store_true")
    parser.add_argument("--resume-checkpoint", action="store_true")
    parser.add_argument("--refresh-review", action="store_true")
    parser.add_argument("--reuse-v1", action="store_true", help="Continue the live first review after a code-only harness failure")
    options = parser.parse_args()
    synthetic_copy(options.copy)
    if options.resume_checkpoint:
        continue_from_checkpoint(options.copy)
        raise SystemExit(0)
    if not options.resume_review:
        with CopyGame(options.copy, PORT) as run:
            run_main_case(run, reuse_v1=options.reuse_v1)
    continue_after_restart(options.copy, refresh_review=options.refresh_review)
