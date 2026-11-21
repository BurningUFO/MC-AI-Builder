"""Verify live revisions, real demolition, and restart recovery in an isolated Forge world."""

from argparse import ArgumentParser
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from subprocess import TimeoutExpired
from tempfile import mkdtemp
from uuid import uuid4

from src.construction.client import BridgeClient
from src.construction.plan import demo_wall, make_plan
from src.construction.revisions import export_revision_demo, revised_wall
from src.core.building import Building, write_json
from tests.m1_smoke import rejected, require, wait_for, wait_placed, wait_terminal, wait_ticks
from tools.m1_client import TEST_ROOT, launch_client


def stop_cleanly(child, run, label, timeout=90):
    (run / ".mcaibuilder-stop").touch()
    child.wait(timeout=timeout)
    log = run / "console.log"
    saved_log = run / f"{label}.log"
    log.replace(saved_log)
    log_text = saved_log.read_text(encoding="utf-8", errors="replace")
    require(child.returncode == 0 and "All dimensions are saved" in log_text
            and "normal save flow" in log_text, "Minecraft must save, leave the world, and exit normally")
    return {"exit_code": child.returncode, "vanilla_save": True}


def live_client(child, run, seconds=150):
    bridge = BridgeClient(run / "config" / "mcaibuilder.json")
    wait_for(child, bridge.status, "Forge integrated server and bridge", seconds=seconds)
    return bridge


def start_revision(child, bridge, run, first, second):
    started = bridge.submit(first)
    worker_id = started["npc"]["npc_id"]
    base_id = started["job"]["job_id"]
    wait_placed(child, bridge, 17)
    review = bridge.propose(base_id, second)
    counts = review["counts"]
    require(all(counts[category] > 0 for category in ("keep", "add", "remove", "replace"))
            and counts["conflict"] == 0, f"Expected all incremental categories: {counts}")
    require(any(row["category"] == "add" and row["old"] == row["desired"]
                and row["actual"]["id"] == "minecraft:air" for row in review["changes"]),
            "An unbuilt unchanged V1 goal becomes an addition")
    require(review["old_version"]["model_sha256"] == first["model_sha256"]
            and review["new_version"]["model_sha256"] == second["model_sha256"],
            "The server verified both immutable model hashes")
    paused = bridge.progress()
    wait = wait_ticks(child, bridge, paused["game_tick"] + 20)
    require(paused["job"]["state"] == "paused" and 17 <= paused["job"]["placed"] < len(first["actions"])
            and wait["job"]["placed"] == paused["job"]["placed"], "V1 remains paused and unfinished through review")
    require(bridge.change()["review_sha256"] == review["review_sha256"], "Review hash is stable while the site is unchanged")
    rejected(lambda: bridge.control("resume", base_id), 409, "change_pending")
    write_json(run / "v1-paused.json", paused)
    write_json(run / "change.json", review)
    approved = bridge.approve(review["change_id"], review["review_sha256"])
    require(approved["npc"]["npc_id"] == worker_id and approved["job"]["previous_job_id"] == base_id
            and approved["job"]["building_version"] == 2, "Approval switched the same NPC to V2")
    require(bridge.approve(review["change_id"], review["review_sha256"])["job"]["job_id"] == second["plan_id"],
            "Retrying an approval does not create a new job")
    rejected(lambda: bridge.control("resume", base_id), 404, "job_not_found")
    rejected(lambda: bridge.submit(first), 409, "plan_already_used")
    write_json(run / "v2-approved.json", approved)
    return review, worker_id


def pause_between_break_and_place(child, bridge, run, review, *, checkpoint_name="before-restart.json"):
    replacements = {tuple(row["world"]) for row in review["changes"] if row["category"] == "replace"}

    def observe():
        progress = bridge.progress()
        require(progress["job"]["state"] == "running", f"V2 ended before the checkpoint: {progress['job']['state']}")
        return progress if any(row["status"] == "removed" and tuple(row["world"]) in replacements
                               for row in progress["job"]["actions"]) else None

    wait_for(child, observe, "a replacement's demolition", seconds=70)
    bridge.control("pause", review["change_id"])
    checkpoint = bridge.progress()
    removed = {tuple(row["world"]) for row in checkpoint["job"]["actions"] if row["status"] == "removed"}
    placed = {tuple(row["world"]) for row in checkpoint["job"]["actions"] if row["status"] == "placed"}
    gaps = removed & replacements - placed
    require(gaps and checkpoint["job"]["state"] == "paused" and not checkpoint["job"]["in_flight"],
            "A confirmed demolition must be saved with its placement still pending")
    write_json(run / checkpoint_name, checkpoint)
    return checkpoint, gaps


def verify_finished(result, review, worker_id):
    job = result["job"]
    verification = job["verification"]
    require(result["npc"]["npc_id"] == worker_id, "The same UUID completed V2")
    require(job["state"] == "completed" and verification["passed"] and verification["matched"] == job["target_count"],
            f"V2 readback failed: {job.get('reason')}")
    require(not verification["target_mismatches"] and not verification["retired_mismatches"]
            and verification["non_target_changes"] == verification["outside_approved_changes"] == 0
            and verification["outside_approved_cells_checked"] > 0, "Target, removals and guard cells all match")
    targets = {tuple(row["world"]): row["desired"] for row in review["changes"] if row["desired"]["id"] != "minecraft:air"}
    readback = {tuple(row["world"]): row["actual"] for row in verification["target_states"]}
    require(readback == targets and all(row["actual"]["id"] == "minecraft:air" for row in verification["retired_states"]),
            "Every target and retired position has an explicit terminal state readback")
    require(len(result["npc"]["materials"]) == 0, "Exactly the preset placement materials were consumed")

    actual = [row for row in job["actions"] if row["status"] in ("placed", "removed")]
    retained = {tuple(row["world"]) for row in review["changes"] if row["category"] == "keep"}
    require(retained.isdisjoint({tuple(row["world"]) for row in actual}), "No retained position was modified")
    desired = {tuple(row["world"]): row["desired"] for row in review["changes"]}
    removals = [row for row in actual if row["status"] == "removed"]
    placements = [row for row in actual if row["status"] == "placed"]
    counts = review["counts"]
    require(len(removals) == counts["remove"] + counts["replace"]
            and len(placements) == counts["add"] + counts["replace"], "The exact reviewed operations were executed")
    require(len({(row["operation"], tuple(row["world"])) for row in actual}) == len(actual),
            "No destructive or material-consuming step was executed twice")
    for row in removals:
        proof = row["interaction_evidence"]
        require(proof["method"] == "ServerPlayerGameMode.destroyBlock" and proof["reach"] <= 4.5
                and proof["mining_ticks"] > 0 and proof["left_click_events"] == proof["break_events"] == 1
                and proof["tool_damage_after"] - proof["tool_damage_before"] == 1
                and row["actual"]["id"] == "minecraft:air", "Every removal has genuine mining and tool evidence")
    require({row["interaction_evidence"]["tool_id"] for row in removals}
            == {"minecraft:iron_axe", "minecraft:iron_pickaxe"}, "Wood and masonry used appropriate tools")
    require(any(drop["id"] == "minecraft:stone_bricks" for row in removals
                for drop in row["interaction_evidence"]["drops"]), "A mined stone block dropped a real item")
    for row in placements:
        proof = row["interaction_evidence"]
        require(proof["method"] == "ServerPlayerGameMode.useItemOn" and proof["reach"] <= 4.5
                and proof["right_click_events"] == proof["place_events"] == 1
                and proof["item_before"] - proof["item_after"] == 1
                and row["actual"] == desired[tuple(row["world"])], "Every item placement matches the reviewed state")


def conflict_case(child, bridge, run):
    origin = (21, -60, 16)
    first = make_plan(demo_wall(), building_id="m2_conflict", version=1, origin=origin)
    second = make_plan(revised_wall(), building_id="m2_conflict", version=2, origin=origin)
    bridge.submit(first)
    bridge.control("pause", first["plan_id"])
    before = bridge.propose(first["plan_id"], second)
    require(before["counts"]["conflict"] == 0, "Conflict fixture starts clear")
    target = [28, -60, 16]
    proof = place_fixture_block(child, run, target)
    stale_review = rejected(lambda: bridge.approve(second["plan_id"], before["review_sha256"]), 409, "review_changed")
    after = bridge.change()
    collided = next(row for row in after["changes"] if row["world"] == target)
    require(collided["category"] == "conflict" and collided["reason"] == "player_modified"
            and collided["actual"] == proof["actual"], "The refreshed review identifies the player's block")
    fresh_review = rejected(lambda: bridge.approve(second["plan_id"], after["review_sha256"]), 409, "change_conflicts")
    bridge.reject(second["plan_id"])
    resume = rejected(lambda: bridge.control("resume", first["plan_id"]), 409, "world_conflict")
    blocked = bridge.progress()
    wait_ticks(child, bridge, blocked["game_tick"] + 30)
    still = bridge.progress()
    require(still["job"]["state"] == "paused" and still["job"]["placed"] == blocked["job"]["placed"]
            and any(row["world"] == target and row["actual"] == proof["actual"] for row in still["job"]["conflicts"]),
            "The conflicted job stays halted and the player's block is preserved")
    write_json(run / "conflict.json", {"player": proof, "before": before, "after": after, "blocked": still,
                                      "rejections": [stale_review, fresh_review, resume]})
    bridge.control("cancel", first["plan_id"])


def place_fixture_block(child, run, target):
    proof_path = run / ".mcaibuilder-player-result.json"
    proof_path.unlink(missing_ok=True)
    temp = run / ".player-place.tmp"
    temp.write_text(" ".join(map(str, target)), encoding="utf-8")
    temp.replace(run / ".mcaibuilder-player-place")

    def player_proof():
        if proof_path.is_file():
            try:
                return json.loads(proof_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return None
        return None

    proof = wait_for(child, player_proof, "a real player placing the conflict block")
    require(proof["actor_type"] == "minecraft:player" and proof["actual"]["id"] == "minecraft:cobblestone",
            "The logged-in test player really changed the world")
    return proof


def blocked_replacement(child, bridge, run):
    origin = (25, -60, 6)
    old = Building("Replacement checkpoint V1")
    old.place((0, 0, 0), "minecraft:stone_bricks")
    new = Building("Replacement checkpoint V2")
    new.place((0, 0, 0), "minecraft:oak_planks")
    first = make_plan(old, building_id="m2_blocked_replacement", version=1, origin=origin)
    second = make_plan(new, building_id="m2_blocked_replacement", version=2, origin=origin)
    bridge.submit(first)
    require(wait_terminal(child, bridge)["job"]["state"] == "completed", "V1 prepared the replacement target")
    review = bridge.propose(first["plan_id"], second)
    require(review["counts"]["replace"] == 1 and review["counts"]["conflict"] == 0, "One safe replacement was reviewed")
    bridge.approve(review["change_id"], review["review_sha256"])
    checkpoint, gaps = pause_between_break_and_place(child, bridge, run, review,
                                                       checkpoint_name="blocked-replacement-checkpoint.json")
    require(gaps == {origin}, "The replacement is between demolition and placement")
    player = place_fixture_block(child, run, list(origin))
    resume = rejected(lambda: bridge.control("resume", second["plan_id"]), 409, "world_conflict")
    settled = wait_ticks(child, bridge, bridge.status()["game_tick"] + 20)
    blocked = bridge.progress()
    require(settled["job"]["state"] == "paused" and blocked["job"]["removed"] == 1
            and blocked["job"]["placed"] == 0 and blocked["job"]["processed"] == checkpoint["job"]["processed"]
            and blocked["npc"]["materials"] == checkpoint["npc"]["materials"]
            and blocked["npc"]["tools"] == checkpoint["npc"]["tools"]
            and blocked["job"]["conflicts"][0]["actual"] == player["actual"],
            "A blocked replacement keeps both the player's block and the exact demolition/material checkpoint")
    write_json(run / "blocked-replacement.json", {"change": review, "player": player,
                                                  "rejection": resume, "blocked": blocked})
    bridge.control("cancel", second["plan_id"])


def verify_m1_persistence(child, bridge, run, *, reopened=False):
    building_id = "m1_pack_wall_readback" if reopened else "m1_pack_wall"
    plan = make_plan(demo_wall(), building_id=building_id, version=1, origin=(6, -60, 17))
    bridge.submit(plan)
    result = wait_terminal(child, bridge)
    job = result["job"]
    write_json(run / ("m1-reopened.json" if reopened else "m1-built.json"), result)
    require(job["state"] == "completed" and job["verification"]["passed"]
            and job["verification"]["matched"] == 30, "M1 wall must match every state and coordinate")
    if reopened:
        require(job["placed"] == 0 and job["skipped"] == 30 and not result["npc"]["materials"],
                "The pre-exit M1 wall was saved exactly and needed no material or placement")
    else:
        require(job["placed"] == 30, "M1 regression built all 30 blocks in the pack")
    return job["verification"]


def scenario(run, java_home, source, port, *, bmc4=False):
    def launch():
        return launch_client(java_home, source, run, port, smoke=True, m2_smoke=True, bmc4=bmc4)

    child = launch()
    startup_timeout = 600 if bmc4 else 150
    exit_timeout = 180 if bmc4 else 90
    try:
        first, second = export_revision_demo(run / "models")
        bridge = live_client(child, run, startup_timeout)
        if bmc4:
            verify_m1_persistence(child, bridge, run)
            print("The isolated BMC4 copy completed the original M1 wall.", flush=True)
        review, worker_id = start_revision(child, bridge, run, first, second)
        print(f"Reviewed the partially built V1 and approved V2. Counts {review['counts']}", flush=True)
        checkpoint, gaps = pause_between_break_and_place(child, bridge, run, review)
        first_exit = stop_cleanly(child, run, "before-restart", exit_timeout)
        world = run / "saves" / "m1-construction"
        journal = json.loads((world / "data" / "mcaibuilder-journal.json").read_text(encoding="utf-8"))
        require((world / "level.dat").is_file() and journal["current"]["intent"] == -1,
                "Minecraft saved a world and the last confirmed construction checkpoint")
        require(journal["current"]["processed"] == checkpoint["job"]["processed"],
                "The durable cursor matches the pre-exit progress")
        print("Saved after demolition. Reopening the same world and NPC.", flush=True)
        if bmc4:
            print("If BCLib Guardian prompts, keep its backup selected and proceed in the isolated test window.",
                  flush=True)

        child = launch()
        bridge = live_client(child, run, startup_timeout)

        def ready():
            progress = bridge.progress()
            recovery = progress.get("recovery", {})
            return progress if recovery.get("npc_reused") else None

        reopened = wait_for(child, ready, "the saved Fake Player", seconds=25)
        require(reopened["job"]["state"] == "paused" and reopened["job"]["reopened"]
                and reopened["job"]["processed"] == checkpoint["job"]["processed"]
                and reopened["npc"]["npc_id"] == worker_id, "Reopened at the exact paused cursor and same UUID")
        require(all(reopened["recovery"][key] for key in ("world_matches_checkpoint",
                    "inventory_matches_checkpoint", "interaction_confirmed")), "World and inventory were checked on restart")
        require(reopened["npc"]["materials"] == checkpoint["npc"]["materials"]
                and reopened["npc"]["tools"] == checkpoint["npc"]["tools"], "Inventory and tool wear persisted without restocking")
        still_paused = wait_ticks(child, bridge, reopened["game_tick"] + 15)
        require(still_paused["job"]["processed"] == checkpoint["job"]["processed"], "Restart did not run actions before resume")
        write_json(run / "reopened.json", reopened)

        bridge.control("resume", second["plan_id"])
        result = wait_terminal(child, bridge)
        write_json(run / "v2-completed.json", result)
        verify_finished(result, review, worker_id)
        for gap in gaps:
            acts = [row["status"] for row in result["job"]["actions"] if tuple(row["world"]) == gap]
            require(acts == ["removed", "placed"], "A previously demolished replacement was only placed on resume")
        versions = bridge.versions()
        previous = next(version for version in versions["versions"] if version["plan_id"] == first["plan_id"])
        require(previous["archived_job"]["superseded_by"] == second["plan_id"]
                and previous["archived_job"]["placed"] < previous["archived_job"]["target_count"],
                "The archive preserves the superseded, unfinished V1")
        write_json(run / "versions.json", versions)
        stale = deepcopy(first)
        stale["plan_id"] = str(uuid4())
        replay = {
            "old_job": first["plan_id"], "active_job": second["plan_id"],
            "repeated_submit": rejected(lambda: bridge.submit(first), 409, "plan_already_used"),
            "new_id_for_old_version": rejected(lambda: bridge.submit(stale), 409, "stale_version"),
            "resume_superseded_job": rejected(lambda: bridge.control("resume", first["plan_id"]), 404, "job_not_found"),
        }
        write_json(run / "replay.json", replay)

        wait_ticks(child, bridge, result["game_tick"] + 10)
        (run / ".mcaibuilder-capture").touch()
        wait_for(child, lambda: (run / "screenshots" / "m1-wall-built.png").is_file(), "finished V2 screenshot")
        if bmc4:
            pack_checks = {"bmc4_mod_jars": len(list((source / "mods").glob("*.jar"))),
                           "m1_reopened": verify_m1_persistence(child, bridge, run, reopened=True)}
        else:
            conflict_case(child, bridge, run)
            blocked_replacement(child, bridge, run)
            pack_checks = {}
        second_exit = stop_cleanly(child, run, "after-restart", exit_timeout)

        return {
            **pack_checks,
            "passed": True, "minecraft": "1.20.1", "forge": "47.4.20", "fake_players": "3.0.0",
            "v1_placed_before_approval": previous["archived_job"]["placed"],
            "change_counts": review["counts"], "model_hashes": [first["model_sha256"], second["model_sha256"]],
            "same_npc": True, "restart_gaps": [list(pos) for pos in sorted(gaps)],
            "removed": result["job"]["removed"], "placed": result["job"]["placed"],
            "verification": result["job"]["verification"],
            "normal_exits": [first_exit, second_exit],
            "checks": ["partial_version_switch", "real_mining_and_item_use",
                       "restart_between_remove_and_place", "stale_plan_rejected", "boundary_and_final_readback",
                       *(["m1_saved_wall_reopened"] if bmc4 else ["player_conflict", "blocked_replacement"])],
        }
    finally:
        if child.poll() is None:
            (run / ".mcaibuilder-stop").touch()
            try:
                child.wait(timeout=30)
            except TimeoutExpired:
                child.terminate()
                child.wait(timeout=10)


def main():
    cli = ArgumentParser(description=__doc__)
    cli.add_argument("--java-home", type=Path, required=True)
    cli.add_argument("--source-instance", type=Path, required=True)
    cli.add_argument("--port", type=int, default=18766)
    cli.add_argument("--bmc4", action="store_true", help="Copy enabled BMC4 mod JARs into a fresh isolated run.")
    args = cli.parse_args()
    runs = TEST_ROOT / ("m2-bmc4-runs" if args.bmc4 else "m2-runs")
    runs.mkdir(parents=True, exist_ok=True)
    run = Path(mkdtemp(prefix=datetime.now().strftime("%Y%m%d-%H%M%S-"), dir=runs))
    print(f"M2 test directory: {run}", flush=True)
    try:
        result = scenario(run, args.java_home, args.source_instance, args.port, bmc4=args.bmc4)
    except Exception as error:
        write_json(run / "acceptance.json", {"passed": False, "error": str(error)})
        raise
    write_json(run / "acceptance.json", result)
    print(f"M2 live verification passed: {run / 'acceptance.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
