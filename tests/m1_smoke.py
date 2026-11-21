"""Exercise the reobfuscated Bridge JAR in an isolated Minecraft client/server."""

from argparse import ArgumentParser
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from subprocess import TimeoutExpired
from tempfile import mkdtemp
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

from src.construction.client import BridgeClient, BridgeError
from src.construction.plan import demo_wall, make_plan
from src.core.building import Building, write_json
from tools.m1_client import ROOT, TEST_ROOT, launch_client


def require(condition, description):
    if not condition:
        raise AssertionError(description)


def wait_for(child, condition, description, seconds=40):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError(f"Forge client quit with {child.returncode} while waiting for {description}")
        try:
            data = condition()
            if data:
                return data
        except (URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(0.15)
    raise TimeoutError(f"Timed out waiting for {description}")


def rejected(action, status, code):
    try:
        action()
    except BridgeError as error:
        require((error.status, error.body["error"]) == (status, code), f"Unexpected bridge error: {error}")
        return {"status": error.status, "error": error.body["error"]}
    else:
        raise AssertionError(f"Expected {status} {code}")


def wait_placed(child, bridge, minimum):
    def observe():
        status = bridge.status()
        job = status["job"]
        if job["state"] not in ("running", "paused"):
            raise AssertionError(f"Job ended before control test: {job}")
        return status if job["placed"] >= minimum else None

    return wait_for(child, observe, f"{minimum} placed blocks")


def wait_ticks(child, bridge, tick):
    def observe():
        status = bridge.status()
        return status if status["game_tick"] >= tick else None

    return wait_for(child, observe, "Minecraft ticks to advance")


def wait_terminal(child, bridge):
    def observe():
        progress = bridge.progress()
        return progress if progress["job"]["state"] in ("completed", "failed", "cancelled") else None

    return wait_for(child, observe, "terminal job state", seconds=75)


def verify_interactions(plan, start, end):
    job = end["job"]
    require(job["state"] == "completed", f"Wall failed: {job.get('reason')}")
    require(start["npc"]["entity_type"] == "players:fake_player", "Visible worker has the Fake Players entity type")
    require(start["npc"]["npc_id"] == end["npc"]["npc_id"], "The same NPC performed the entire job")
    require(end["npc"]["walked_blocks"] > 4, "NPC must walk to the work area")
    require(sum(start["npc"]["materials"].values()) == len(plan["actions"]), "Preset one item per target")
    require(end["npc"]["materials"] == {}, "All preset items were consumed")
    actions = {tuple(action["local"]): action for action in job["actions"]}
    previous_tick = -1
    for action in job["actions"]:
        evidence = action["interaction_evidence"]
        require(action["status"] == "placed" and action["tick"] > previous_tick, "Each placement gets its own game tick")
        require(evidence["method"] == "ServerPlayerGameMode.useItemOn", "Normal server item-use path")
        require(evidence["right_click_events"] == evidence["place_events"] == 1, "Forge right click and place events")
        require(evidence["item_before"] - evidence["item_after"] == 1, "One consumed item per placement")
        require(evidence["reach"] <= 4.5, "Placement comes from within reach")
        require(action["before"]["id"] == "minecraft:air", "Each action started with an empty target")
        previous_tick = action["tick"]

    for target in plan["actions"]:
        action = actions[tuple(target["local"])]
        require(action["world"] == [a + b for a, b in zip(target["local"], plan["origin"], strict=True)], "World position follows plan")
        require(action["actual"] == action["expected"] == {"id": target["id"], "properties": target["properties"]},
                f"Block ID and properties match {target}")
    verify = job["verification"]
    require(verify["passed"] and verify["matched"] == len(plan["actions"]), "Readback matches the entire plan")
    require(verify["outside_approved_cells_checked"] > 0 and verify["outside_approved_changes"] == 0
            and verify["non_target_changes"] == 0, "Unplanned cells and the outside halo remain unchanged")


def smoke(child, run, port):
    config = run / "config" / "mcaibuilder.json"
    bridge = BridgeClient(config)
    print("Waiting for the integrated Forge server.", flush=True)
    wait_for(child, bridge.status, "the Bridge HTTP listener", seconds=150)
    try:
        build_opener(ProxyHandler({})).open(f"http://127.0.0.1:{port}/status", timeout=5).close()
    except HTTPError as error:
        with error:
            require(error.code == 401, "Unauthenticated status calls are rejected")
    else:
        raise AssertionError("Unauthenticated status call was accepted")
    plan = json.loads((ROOT / "projects" / "m1_wall" / "construction-plan.json").read_text(encoding="utf-8"))
    outside = deepcopy(plan)
    outside.update(plan_id=str(uuid4()), origin=[200, -60, 6])
    rejected(lambda: bridge.submit(outside), 400, "invalid_request")
    invalid_state = deepcopy(plan)
    invalid_state["plan_id"] = str(uuid4())
    next(action for action in invalid_state["actions"] if action["id"] == "minecraft:oak_stairs")["properties"]["facing"] = "up"
    rejected(lambda: bridge.submit(invalid_state), 400, "invalid_request")
    idle = bridge.status()
    require("job" not in idle and not idle["npc"]["present"], "Invalid jobs do not create a worker or change job state")

    write_json(run / "wall-plan.json", plan)
    start = bridge.submit(plan)
    write_json(run / "submitted.json", start)
    job_id = start["job"]["job_id"]
    require(bridge.submit(plan)["job"]["job_id"] == job_id, "Retried submission refers to the same job")
    wait_placed(child, bridge, 3)
    paused = bridge.control("pause", job_id)
    settled = wait_ticks(child, bridge, paused["game_tick"] + 30)
    require(settled["job"]["state"] == "paused" and settled["job"]["placed"] == paused["job"]["placed"],
            "Paused job placed nothing over at least 30 game ticks")
    write_json(run / "pause.json", {"before": paused, "after": settled})
    bridge.control("resume", job_id)
    end = wait_terminal(child, bridge)
    write_json(run / "completed.json", end)
    verify_interactions(plan, start, end)
    wait_ticks(child, bridge, end["game_tick"] + 10)
    (run / ".mcaibuilder-capture").touch()
    wait_for(child, lambda: (run / "screenshots" / "m1-wall-built.png").is_file(), "wall screenshot")
    print(f"Wall verified with {end['job']['placed']} real item uses. Testing cancellation and reach limits.", flush=True)

    occupied = Building("Protected target")
    occupied.place((0, 0, 0), "minecraft:glass")
    conflict = make_plan(occupied, building_id="occupied", version=1, origin=tuple(plan["origin"]))
    rejected(lambda: bridge.submit(conflict), 409, "occupied_target")

    alternate = make_plan(demo_wall(), building_id="cancel_wall", version=1, origin=(19, -60, 5), rotation=90)
    alternate_id = bridge.submit(alternate)["job"]["job_id"]
    wait_placed(child, bridge, 4)
    cancelled = bridge.control("cancel", alternate_id)
    settled_cancel = wait_ticks(child, bridge, cancelled["game_tick"] + 30)
    require(settled_cancel["job"]["state"] == "cancelled"
            and settled_cancel["job"]["placed"] == cancelled["job"]["placed"] < cancelled["job"]["total"],
            "Cancelled job placed nothing over at least 30 game ticks")
    rejected(lambda: bridge.control("resume", alternate_id), 409, "invalid_job_state")
    require(bridge.submit(alternate)["job"]["state"] == "cancelled", "Replaying a cancelled plan does not restart it")
    partial = bridge.progress()
    write_json(run / "cancelled.json", {"before": cancelled, "after": settled_cancel, "progress": partial})
    rotated_stairs = [action for action in partial["job"]["actions"]
                      if action["status"] == "placed" and action["item_id"] == "minecraft:oak_stairs"]
    require(rotated_stairs, "The rotated job placed a stair before cancellation")
    require(all(action["actual"]["properties"]["facing"] == "east"
                and action["world"] == [19 - action["local"][2], -60 + action["local"][1], 5 + action["local"][0]]
                for action in rotated_stairs), "A 90-degree rotation transforms both coordinates and stair facing")

    tower = Building("Unreachable tower")
    for y in range(11):
        tower.place((0, y, 0), "minecraft:stone_bricks")
    tall = make_plan(tower, building_id="reach_limit", version=1, origin=(22, -60, 9))
    bridge.submit(tall)
    unreachable = wait_terminal(child, bridge)
    write_json(run / "unreachable.json", unreachable)
    job = unreachable["job"]
    require(job["state"] == "failed" and job["reason"] in ("no_reachable_placement_stance", "navigation_timeout")
            and 0 < job["placed"] < job["total"],
            f"Expected a reach error above the tower base, got {job['state']} after {job['placed']} placements")
    rejected(lambda: bridge.submit(alternate), 409, "plan_already_used")
    return {
        "passed": True,
        "minecraft": "1.20.1",
        "forge": "47.4.20",
        "fake_players": "3.0.0",
        "construction": end["job"]["verification"],
        "npc_walked_blocks": end["npc"]["walked_blocks"],
        "pause_ticks": settled["game_tick"] - paused["game_tick"],
        "cancel_ticks": settled_cancel["game_tick"] - cancelled["game_tick"],
        "unreachable_reason": job["reason"],
        "checks": ["token", "approved_area", "valid_state", "spawn", "submit_retry", "pause", "resume",
                   "monotonic_action_ticks", "forge_item_events", "inventory", "state_readback",
                   "outside_halo", "occupied_target", "rotation", "cancel", "stale_plan", "unreachable"],
    }


def main() -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument("--source-instance", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args()
    runs = TEST_ROOT / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    run = Path(mkdtemp(prefix=datetime.now().strftime("%Y%m%d-%H%M%S-"), dir=runs))
    print(f"M1 smoke test files: {run}", flush=True)
    child = launch_client(args.java_home, args.source_instance, run, args.port, smoke=True)
    try:
        report = smoke(child, run, args.port)
    except Exception as error:
        write_json(run / "acceptance.json", {"passed": False, "error": str(error)})
        raise
    finally:
        if child.poll() is None:
            (run / ".mcaibuilder-stop").touch()
            try:
                child.wait(timeout=25)
            except TimeoutExpired:
                child.terminate()
                child.wait(timeout=10)
    require(child.returncode == 0, f"Client shutdown exited with {child.returncode}")
    write_json(run / "acceptance.json", report)
    print(f"M1 game verification passed: {run / 'acceptance.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
