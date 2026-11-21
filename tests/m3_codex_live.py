"""Drive real Architect chat in an isolated world and wait for player approvals."""

from argparse import ArgumentParser
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
from tempfile import mkdtemp

from src.architect.codex_provider import DEFAULT_EFFORT, DEFAULT_MODEL
from src.architect.runtime import ArchitectClient
from src.construction.client import BridgeClient
from src.core.building import Building, write_json
from tests.m1_smoke import rejected, require, wait_for, wait_placed, wait_terminal, wait_ticks
from tests.m2_smoke import stop_cleanly
from tests.m3_smoke import Chat, assert_pending_without_actions, signal, stop_runtime, verify_world
from tools.m1_client import BRIDGE_JAR, PLAYERS_JAR, ROOT, TEST_ROOT, launch_client


DESIGN = "请在这里（原点 6 -60 6，朝北）设计一面 8 格宽、3 格高的中世纪窗墙，用深色橡木和石砖，窗户用普通玻璃。保留默认入口台阶。"
EDIT = "把窗墙的深色橡木换成云杉木，其他不变。"
REVISION = "先暂停施工，把窗户换成蓝色染色玻璃，其他已经建好的部分保持不变。"


def runtime_process(run, source, ledger):
    args = [sys.executable, "-B", "-m", "src.architect",
            "--config", str(run / "config" / "mcaibuilder-architect.json"),
            "--instance", str(source), "--provider", "codex", "--timeout", "50",
            "--codex-model", DEFAULT_MODEL, "--codex-effort", DEFAULT_EFFORT,
            "--codex-max-turns", "10", "--codex-max-tokens", "150000",
            "--codex-budget-log", str(ledger)]
    with (run / "runtime.log").open("wb") as log:
        return subprocess.Popen(args, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)


def footprint(card):
    return {pos: block.state.block_id for pos, block in card["building"].blocks.items()}


def verify_design(card):
    model = card["building"]
    blocks = footprint(card)
    report = card["report"]
    require(model.bounds()[1][0] == 7 and model.bounds()[1][1] == 2
            and len(blocks) == 30 and report["counts"]["add"] == 30, "An 8 by 3 wall with its porch was generated")
    require({"minecraft:dark_oak_planks", "minecraft:stone_bricks", "minecraft:glass"} <= set(blocks.values()),
            "The model used the requested wood, masonry and clear glass")
    version = report["new_version"]
    require(version["origin"] == [6, -60, 6] and version["rotation"] == 0,
            "The approved site matches the coordinates and facing in chat")


def verify_edit(before, after, old_id, new_id):
    old, revised = footprint(before), footprint(after)
    require(set(old) == set(revised) and old_id in old.values()
            and all(revised[pos] == (new_id if state == old_id else state) for pos, state in old.items()),
            "The requested material alone changed")
    require(before["report"]["new_version"]["model_sha256"] != after["report"]["new_version"]["model_sha256"],
            "The edit generated a distinct immutable Building")


def await_approval(child, bridge, chat, card, trace, label):
    report = card["report"]
    version = report["new_version"]
    preview = chat.run / "architect-previews" / version["plan_id"] / report["review_sha256"][:16]
    trace["waiting_for_player"] = {
        "stage": label, "building_version": version["building_version"], "materials": report["counts"],
        "review_sha256": report["review_sha256"], "preview": str(preview), "approval_code": card["code"],
    }
    write_json(chat.run / "acceptance.json", trace)
    print(f"Waiting for the test player to approve {label} V{version['building_version']}.", flush=True)

    def approved():
        status = bridge.status()
        job = status.get("job") or {}
        if job.get("job_id") == version["plan_id"]:
            return status
        return None

    observed = wait_for(child, approved, f"the player's in-game approval of {label}", seconds=590)
    wait_for(child, lambda: any(f"已批准 V{version['building_version']}" in line for line in chat.messages()),
             f"the game's acknowledgment for {label}")
    log = (chat.run / "console.log").read_text(encoding="utf-8", errors="replace")
    line = next((line for line in log.splitlines() if "architect_decision " in line
                 and f"candidate={version['plan_id']} approved=true hash={report['review_sha256']}" in line), "")
    require(line, "Forge recorded the player decision against the concrete candidate and review")
    trace.setdefault("approvals", []).append({
        "stage": label, "building_version": version["building_version"], "review_sha256": report["review_sha256"],
        "job_id": version["plan_id"], "player_event": line.split("architect_decision ", 1)[1],
    })
    trace.pop("waiting_for_player")
    write_json(chat.run / "acceptance.json", trace)
    return observed


def verify_partial(progress, report):
    job = progress["job"]
    require(job["state"] == "paused" and 17 <= job["placed"] < job["target_count"],
            "The chat paused an unfinished job")
    targets = {tuple(row["world"]): row["desired"] for row in report["changes"]}
    placements = [action for action in job["actions"] if action["status"] == "placed"]
    require(len(placements) == job["placed"], "Gathered every action already placed")
    for row in placements:
        proof = row["interaction_evidence"]
        require(row["actual"] == row["expected"] == targets[tuple(row["world"])]
                and proof["method"] == "ServerPlayerGameMode.useItemOn"
                and proof["right_click_events"] == proof["place_events"] == 1
                and proof["item_before"] - proof["item_after"] == 1 and proof["reach"] <= 4.5,
                "The original version was placed by item interaction at the approved target")


def exercise(run, options, trace):
    bmc4 = options.bmc4_pack is not None
    startup = 600 if bmc4 else 150
    exit_timeout = 180 if bmc4 else 90

    def start_game():
        return launch_client(options.java_home, options.source_instance, run, options.port, smoke=True,
                             m3_smoke=True, bmc4=bmc4, pack_source=options.bmc4_pack)

    runtime = None
    child = None
    chat = Chat(run)
    try:
        child = start_game()
        runtime = runtime_process(run, options.source_instance, options.ledger.resolve())
        bridge = BridgeClient(run / "config" / "mcaibuilder.json")
        scoped = ArchitectClient(run / "config" / "mcaibuilder-architect.json")
        wait_for(child, bridge.status, "Forge integrated server", seconds=startup)
        wait_ticks(child, bridge, bridge.status()["game_tick"] + 28)
        require(runtime.poll() is None, "Codex Architect Runtime is running")
        if options.resume_pending:
            pending = trace["waiting_for_player"]
            report = trace["drafts"][-1]
            version = report["new_version"]
            require(not bridge.status().get("job") and pending["review_sha256"] == report["review_sha256"],
                    "The original initial approval is still pending in the saved world")
            selected = {"report": report, "code": pending["approval_code"], "building": Building.read(
                run / "architect-previews" / version["plan_id"] / "building.json")}
            trace["pending_preview_reopened"] = True
        else:
            trace["scope_rejections"] = [
                rejected(lambda: scoped.submit({}), 401, "unauthorized"),
                rejected(lambda: scoped.approve("00000000-0000-0000-0000-000000000000", "0" * 64), 401, "unauthorized"),
                rejected(lambda: scoped.control("resume", "00000000-0000-0000-0000-000000000000"), 401, "unauthorized"),
            ]
            first = chat.card(child, DESIGN)
            verify_design(first)
            assert_pending_without_actions(child, bridge)
            trace["drafts"] = [first["report"]]
            selected = first
            if not bmc4:
                selected = chat.card(child, EDIT)
                verify_edit(first, selected, "minecraft:dark_oak_planks", "minecraft:spruce_planks")
                trace["drafts"].append(selected["report"])
                chat.send(child, "approve " + first["code"], "stale_approval")
                chat.send(child, selected["code"], "architect_forbidden", guest=True)
        assert_pending_without_actions(child, bridge)
        start = await_approval(child, bridge, chat, selected, trace, "initial construction")
        npc = start["npc"]["npc_id"]
        require(start["npc"]["entity_type"] == "players:fake_player", "The approved job has a Fake Players worker")
        chat.send(child, "approve " + selected["code"], "stale_approval")

        wait_placed(child, bridge, 17)
        revision = chat.card(child, REVISION)
        verify_edit(selected, revision, "minecraft:glass", "minecraft:blue_stained_glass")
        assert_pending_without_actions(child, bridge)
        paused = bridge.progress()
        verify_partial(paused, selected["report"])
        counts = revision["report"]["counts"]
        require(counts["replace"] > 0 and counts["add"] > 0 and counts["keep"] > 0 and counts["conflict"] == 0,
                "The world-based report has outstanding builds, real replacements and preserved locations")
        trace.update(paused=paused, revision=revision["report"])
        await_approval(child, bridge, chat, revision, trace, "mid-build revision")
        finished = wait_terminal(child, bridge)
        trace["finished"] = finished
        trace["mutations"] = verify_world(finished, revision["report"])
        require(finished["npc"]["npc_id"] == npc and trace["mutations"].get("removed", 0) == counts["replace"]
                and finished["npc"]["walked_blocks"] > 4,
                "The same NPC navigated, removed, and completed the construction")
        chat.send(child, "approve " + revision["code"], "stale_approval")
        chat.send(child, "approve " + selected["code"], "stale_approval")
        replay = bridge.progress()
        require(replay["job"]["actions"] == finished["job"]["actions"]
                and replay["npc"]["materials"] == finished["npc"]["materials"]
                and replay["npc"]["tools"] == finished["npc"]["tools"],
                "Neither old nor duplicate approval incurred more actions, materials or tool wear")
        trace["replay_preserved_terminal_state"] = True

        signal(run, ".mcaibuilder-capture", "")
        wait_for(child, lambda: (run / "screenshots" / "m1-wall-built.png").is_file(), "finished wall screenshot")
        trace["save_exits"] = [stop_cleanly(child, run, "finished", exit_timeout)]
        write_json(run / "acceptance.json", trace)
        if bmc4:
            print("BMC4 finished and saved. Reopening the same isolated world.", flush=True)
            child = start_game()
            wait_for(child, bridge.status, "BMC4 saved world to reopen", seconds=startup)
            wait_ticks(child, bridge, bridge.status()["game_tick"] + 30)
            def recovered_progress():
                progress = bridge.progress()
                return progress if progress.get("recovery", {}).get("npc_reused") else None

            reopened = wait_for(child, recovered_progress, "saved NPC and world checkpoint to reload")
            recovered = reopened["recovery"]
            require(recovered["world_matches_checkpoint"] and recovered["inventory_matches_checkpoint"]
                    and recovered["interaction_confirmed"] and reopened["npc"]["npc_id"] == npc,
                    "The live reopened world matched its saved blocks, worker and inventory")
            require(reopened["job"]["actions"] == finished["job"]["actions"], "No old action replayed on reopen")
            verify_world(reopened, revision["report"])
            trace["reopened"] = reopened
            trace["save_exits"].append(stop_cleanly(child, run, "reopened", exit_timeout))
        trace["passed"] = True
    finally:
        trace["chat"] = trace.get("chat", []) + chat.trace
        write_json(run / "acceptance.json", trace)
        stop_runtime(runtime)
        if child and child.poll() is None:
            (run / ".mcaibuilder-stop").touch()
            try:
                child.wait(timeout=45)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=12)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument("--source-instance", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18771)
    parser.add_argument("--resume-pending", type=Path, help="Reopen a saved run awaiting its initial approval")
    parser.add_argument("--bmc4-pack", type=Path, help="Existing isolated directory supplying the BMC4 mod JARs")
    options = parser.parse_args()
    pack = options.bmc4_pack
    if pack and (not pack.resolve().is_relative_to(TEST_ROOT.resolve()) or not (pack / ".mcaibuilder-test").is_file()):
        parser.error("--bmc4-pack must refer to an existing isolated MC-AI-Builder test client")
    runs = TEST_ROOT / "m3-codex-runs"
    runs.mkdir(parents=True, exist_ok=True)
    if options.resume_pending:
        run = options.resume_pending.resolve()
        if not run.is_relative_to(runs.resolve()) or not (run / ".mcaibuilder-test").is_file():
            parser.error("--resume-pending must refer to a previously saved Codex game run")
        trace = json.loads((run / "acceptance.json").read_text(encoding="utf-8"))
        if trace.get("waiting_for_player", {}).get("stage") != "initial construction":
            parser.error("The saved run must be waiting for its initial approval")
        trace.pop("error", None)
    else:
        run = Path(mkdtemp(dir=runs, prefix=datetime.now().strftime("%Y%m%d-%H%M%S-")))
        (run / ".mcaibuilder-test").touch()
        trace = {"provider": "codex", "model": DEFAULT_MODEL, "effort": DEFAULT_EFFORT, "passed": False}
        if pack:
            trace["bmc4_mod_jars"] = sum(jar.name not in (BRIDGE_JAR.name, PLAYERS_JAR.name)
                                        for jar in (pack / "mods").glob("*.jar"))
    print(f"Isolated Codex game run {run.name}", flush=True)
    try:
        exercise(run, options, trace)
    except Exception as error:
        trace["error"] = f"{type(error).__name__}: {error}"
        write_json(run / "acceptance.json", trace)
        raise
    print("The real Codex game scenario passed.", flush=True)


if __name__ == "__main__":
    main()
