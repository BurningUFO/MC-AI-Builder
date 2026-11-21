"""Test actual chest stock transfers in an isolated Minecraft survival world."""

from argparse import ArgumentParser
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
from tempfile import mkdtemp
from uuid import uuid4

from src.architect.runtime import ArchitectClient
from src.construction.client import BridgeClient
from src.construction.plan import demo_wall, make_plan
from src.core.building import write_json
from tests.m1_smoke import rejected, require, wait_for, wait_terminal, wait_ticks
from tests.m2_smoke import stop_cleanly
from tests.m3_smoke import Chat, assert_pending_without_actions, signal, stop_runtime, verify_world
from tools.m1_client import ROOT, TEST_ROOT, launch_client


CHEST = [22, -60, 15]
VANILLA = "请在 6 -60 6 朝北建一面 8 格宽 3 格高的中世纪窗墙，用云杉木、石砖和透明玻璃。只使用仓库的材料，缺料就告诉我。"
INITIAL_STOCK = {"minecraft:stone_bricks": 12, "minecraft:spruce_planks": 4,
                 "minecraft:stone_brick_slab": 8, "minecraft:oak_stairs": 2, "minecraft:obsidian": 3}


def provision(child, run, items, *, place=False):
    request = str(uuid4())
    rows = [{"id": kind, "count": value} if isinstance(value, int)
            else {"id": kind, "count": value[0], "damage": value[1]} for kind, value in items.items()]
    signal(run, ".mcaibuilder-stock-request", json.dumps({
        "request": request, "position": CHEST, "place": place, "items": rows,
    }))
    result = run / ".mcaibuilder-stock-result"

    def observe():
        if result.is_file():
            try:
                reply = json.loads(result.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return None
            if reply["request"] == request:
                return reply

    reply = wait_for(child, observe, "player menu transfer of the explicit fixture stock", seconds=90)
    require(reply["success"], f"Real player chest provisioning failed: {reply.get('error')}")
    return {key: value for key, value in reply.items() if key != "request"}


def start_runtime(run, source):
    args = [sys.executable, "-B", "-m", "src.architect", "--instance", str(source), "--provider", "fixture",
            "--config", str(run / "config" / "mcaibuilder-architect.json")]
    with (run / "runtime.log").open("wb") as log:
        return subprocess.Popen(args, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)


def when_job(child, bridge, predicate, description, *, seconds=95):
    def observe():
        state = bridge.progress()
        job = state.get("job")
        return state if job is not None and predicate(job) else None
    return wait_for(child, observe, description, seconds=seconds)


def verify_receipts(receipts):
    moved = Counter()
    for trip in receipts:
        require(trip["right_click_events"] == trip["container_open_events"] == 1 and trip["reach"] <= 4.5,
                "Each shipment used a reachable native chest interaction")
        require(0 < sum(trip["moved"].values()) <= 16, "A trip carries at most the bounded batch size")
        items = set().union(*(trip[key] for key in ("warehouse_before", "warehouse_after", "npc_before", "npc_after", "moved")))
        for kind in items:
            amount = trip["moved"].get(kind, 0)
            require(trip["warehouse_before"].get(kind, 0) - trip["warehouse_after"].get(kind, 0) == amount
                    and trip["npc_after"].get(kind, 0) - trip["npc_before"].get(kind, 0) == amount,
                    "The debit at the source equals the worker's receipt for every item")
        moved.update(trip["moved"])
    return moved


def base_case(options, run, trace):
    def launch():
        return launch_client(options.java_home, options.source_instance, run, options.port, smoke=True, m4_smoke=True)

    chat, child, runtime = Chat(run), None, None
    try:
        child = launch()
        runtime = start_runtime(run, options.source_instance)
        config = run / "config"
        bridge = BridgeClient(config / "mcaibuilder.json")
        scoped = ArchitectClient(config / "mcaibuilder-architect.json")
        wait_for(child, bridge.status, "isolated survival Bridge", seconds=150)
        wait_ticks(child, bridge, bridge.status()["game_tick"] + 24)
        require(bridge.status()["supply_mode"] == "survival", "The isolated instance uses survival supply")

        disallowed = make_plan(demo_wall(), building_id="guard_fixture", version=1, origin=(6, -60, 6))
        trace["unauthorized"] = [
            rejected(lambda: bridge.submit(disallowed), 403, "warehouse_player_approval"),
            rejected(lambda: scoped.submit(disallowed), 401, "unauthorized"),
        ]
        trace["stock"] = [provision(child, run, INITIAL_STOCK, place=True)]
        chat.send(child, "supply 33 -60 15", "warehouse_outside_site")
        chat.send(child, "supply " + " ".join(map(str, CHEST)), "供料箱已批准")
        card = chat.card(child, VANILLA)
        report = card["report"]
        require(report["counts"]["add"] == 30
                and report["supply"]["requirements"]["minecraft:glass"]["missing"] == 4,
                "Planning used the allowed chest and found exactly four missing glass blocks")
        trace["initial_review"] = report
        assert_pending_without_actions(child, bridge)
        require(not bridge.status()["npc"]["present"], "No worker or stock transfer before approval")
        chat.send(child, "approve " + card["code"], "已批准 V1")
        job_id = report["new_version"]["plan_id"]
        trace["unauthorized"].append(rejected(lambda: bridge.control("resume", job_id), 403,
                                               "warehouse_player_approval"))
        when_job(child, bridge, lambda j: j["supply"]["phase"] == "to_warehouse", "worker walking to supply")
        trace["stock"].append(provision(child, run, {"minecraft:obsidian": 1}))
        disturbed = when_job(child, bridge, lambda j: j.get("reason") == "warehouse_changed",
                             "human edit during navigation detected")
        require(not disturbed["job"]["supply"]["transfers"] and not disturbed["npc"]["materials"],
                "The changed chest was untouched by the worker")
        trace["warehouse_change"] = disturbed
        chat.send(child, "resume " + job_id, "已继续施工")
        packed = when_job(child, bridge, lambda j: j["supply"]["phase"] == "to_site" and j["placed"] == 0,
                          "verified shipment on its way to construction")
        trace["in_transport"] = packed
        trace["save_exits"] = [stop_cleanly(child, run, "transport")]
        child = launch()
        wait_for(child, bridge.status, "same world reopened with cargo", seconds=150)
        restored = wait_for(child, lambda: (status if (status := bridge.progress()).get("recovery", {}).get("npc_reused")
                                           else None), "durable in-transit worker")
        require(restored["recovery"]["warehouse_matches_checkpoint"]
                and restored["recovery"]["inventory_matches_checkpoint"]
                and restored["recovery"]["warehouse_transfer_confirmed"]
                and restored["npc"]["npc_id"] == packed["npc"]["npc_id"]
                and restored["job"]["supply"]["transfers"] == packed["job"]["supply"]["transfers"],
                "Reopening does not repeat a receipt or lose the worker's cargo")
        trace["transport_reopened"] = restored
        chat.send(child, "resume " + job_id, "已继续施工")
        missing = when_job(child, bridge, lambda j: j.get("reason") == "out_of_material", "glass shortage pauses construction")
        require(missing["job"]["placed"] > 0
                and missing["materials"]["requirements"]["minecraft:glass"]["missing"] == 4
                and "minecraft:glass" not in missing["npc"]["materials"]
                and any("缺料" in line and "minecraft:glass" in line for line in chat.messages()),
                "The worker constructed only what it actually received and reported the missing stock")
        trace["shortage"] = missing
        trace["stock"].append(provision(child, run, {"minecraft:glass": 4}))
        chat.send(child, "resume " + job_id, "已继续施工")
        finished = wait_terminal(child, bridge)
        trace["finished"] = finished
        require(verify_world(finished, report) == {"placed": 30}, "Thirty real item uses completed the approved building")
        evidence = finished["job"]["supply"]["transfers"]
        funds = Counter({item: count for item, count in INITIAL_STOCK.items() if item != "minecraft:obsidian"})
        funds["minecraft:glass"] = 4
        require(verify_receipts(evidence) == funds and len(evidence) > 1
                and finished["npc"]["materials"] == {}
                and finished["materials"]["warehouse_items"] == {"minecraft:obsidian": 4},
                "All used materials came through the approved chest and unrelated items stayed there")
        chat.send(child, "approve " + card["code"], "stale_approval")
        require(bridge.progress()["job"]["supply"]["transfers"] == evidence, "No repeated warehouse debit")
        draft = chat.card(child, "把窗户换成蓝色玻璃，其他部分保持不变。")
        trace["stock"].append(provision(child, run, {"minecraft:blue_stained_glass": 4,
                                                     "minecraft:iron_pickaxe": (1, 249)}))
        chat.send(child, "approve " + draft["code"], "review_changed")
        require(bridge.progress()["job"]["removed"] == 0, "Changing stock invalidates the reviewed approval")
        revision = chat.card(child, "请重新审阅这个方案。")
        changed = revision["report"]
        require(changed["review_sha256"] != draft["report"]["review_sha256"]
                and changed["counts"]["replace"] == 4
                and changed["supply"]["requirements"]["minecraft:iron_pickaxe"]["missing"] == 3,
                "The revision measured the actual remaining tool durability")
        trace["tool_review"] = changed
        chat.send(child, "approve " + revision["code"], "已批准 V2")
        next_job = changed["new_version"]["plan_id"]
        broken = when_job(child, bridge, lambda j: j.get("reason") == "out_of_tool", "real tool break blocks the next action")
        removals = [row for row in broken["job"]["actions"] if row["status"] == "removed"]
        require(len(removals) == 1 and removals[0]["interaction_evidence"]["tool_count_after"] == 0
                and broken["job"]["placed"] == 0 and broken["materials"]["npc_items"] == {},
                "The exhausted tool vanished by the native break and no replacement was generated")
        trace["broken_tool"] = broken
        chat.send(child, "还缺多少材料？", "缺料 iron_pickaxe ×3")
        trace["stock"].append(provision(child, run, {"minecraft:iron_pickaxe": 1}))
        chat.send(child, "resume " + next_job, "已继续施工")
        gap = when_job(child, bridge, lambda j: j["removed"] == 4 and j["placed"] == 0,
                       "demolition saved between removal and replacement")
        trace["replacement_gap"] = gap
        trace["save_exits"].append(stop_cleanly(child, run, "replacement-gap"))
        child = launch()
        wait_for(child, bridge.status, "same revision world reopened", seconds=150)
        loaded = wait_for(child, lambda: (status if (status := bridge.progress()).get("recovery", {}).get("npc_reused")
                                         else None), "removed blocks and resupplied tool checkpoint")
        require(loaded["recovery"]["world_matches_checkpoint"] and loaded["recovery"]["inventory_matches_checkpoint"]
                and loaded["recovery"]["warehouse_matches_checkpoint"] and loaded["job"]["removed"] == 4
                and loaded["job"]["supply"]["transfers"] == gap["job"]["supply"]["transfers"],
                "The block gap, remaining durability and material debits survived restart")
        trace["replacement_reopened"] = loaded
        chat.send(child, "resume " + next_job, "已继续施工")
        replaced = wait_terminal(child, bridge)
        trace["replaced"] = replaced
        require(verify_world(replaced, changed) == {"removed": 4, "placed": 4}
                and verify_receipts(replaced["job"]["supply"]["transfers"]) ==
                {"minecraft:blue_stained_glass": 4, "minecraft:iron_pickaxe": 2},
                "After restart only the missing placements consumed building items")
        trace["save_exits"].append(stop_cleanly(child, run, "finished"))
        trace["passed"] = True
    finally:
        trace["chat"] = chat.trace
        write_json(run / "acceptance.json", trace)
        stop_runtime(runtime)
        if child is not None and child.poll() is None:
            (run / ".mcaibuilder-stop").touch()
            try:
                child.wait(timeout=45)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=10)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument("--source-instance", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18773)
    args = parser.parse_args()
    runs = TEST_ROOT / "m4-supply-runs"
    runs.mkdir(parents=True, exist_ok=True)
    run = Path(mkdtemp(dir=runs, prefix=datetime.now().strftime("%Y%m%d-%H%M%S-")))
    (run / ".mcaibuilder-test").touch()
    print(f"M4 isolated survival world {run.name}", flush=True)
    trace = {"passed": False}
    try:
        base_case(args, run, trace)
    except Exception as error:
        trace["error"] = f"{type(error).__name__}: {error}"
        write_json(run / "acceptance.json", trace)
        raise
    print("M4 survival stock, scoped authorization and transport recovery passed.", flush=True)


if __name__ == "__main__":
    main()
