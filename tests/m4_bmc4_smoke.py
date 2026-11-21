"""Verify the BMC4 material whitelist and real Codex survival revisions."""

from argparse import ArgumentParser
from datetime import datetime
from pathlib import Path
import subprocess
from tempfile import mkdtemp

from src.architect.design import CHIPPED_WOOD, QUARK_ROOF, QUARK_STAIR
from src.construction.client import BridgeClient
from src.core.building import write_json
from tests.m1_smoke import require, wait_for, wait_placed, wait_terminal, wait_ticks
from tests.m2_smoke import stop_cleanly
from tests.m3_codex_live import await_approval, runtime_process
from tests.m3_smoke import Chat, assert_pending_without_actions, stop_runtime, verify_world
from tests.m4_supply_smoke import CHEST, provision, start_runtime, verify_receipts, when_job
from tools.m1_client import BRIDGE_JAR, PLAYERS_JAR, TEST_ROOT, launch_client


MODDED = {CHIPPED_WOOD, QUARK_ROOF, QUARK_STAIR}
DESIGN = (
    "请在 6 -60 6 朝北建一面 8 格宽 3 格高的中世纪窗墙，"
    "用 Chipped 堆叠云杉板、石砖、透明玻璃，屋顶采用 Quark 红瓦，保留门前台阶。"
)
PLAIN = "把木板换成普通云杉，屋顶改成石砖台阶。"
RESTORE = "把木板恢复成 Chipped 堆叠云杉，屋顶换回 Quark 红瓦。"
REAL_DESIGN = (
    "请在 17 -60 5 朝北另建一面 8 格宽、3 格高的中世纪窗墙。"
    "用 Chipped 的堆叠云杉、石砖和透明玻璃，屋顶用石砖台阶，保留门前台阶。只用获准仓库的材料。"
)
REAL_REVISION = (
    "先暂停施工，把屋顶改用 Quark 的红色瓦片。保留木框、窗户、墙身和台阶。"
    "材料不够告诉我，不要自行替换。"
)
STOCK = {
    "minecraft:stone_bricks": 24,
    "minecraft:oak_stairs": 4,
    "minecraft:glass": 8,
    "minecraft:spruce_planks": 4,
    "minecraft:stone_brick_slab": 16,
    CHIPPED_WOOD: 12,
    QUARK_ROOF: 12,
    QUARK_STAIR: 4,
    "minecraft:iron_pickaxe": 1,
    "minecraft:iron_axe": 1,
    "minecraft:obsidian": 2,
}


def target_states(report):
    return {tuple(row["world"]): row["desired"] for row in report["changes"]}


def check_modded(report, catalog):
    states = list(target_states(report).values())
    require({entry["id"] for entry in states} >= MODDED, "The design really uses three registered modded blocks")
    stairs = [entry["properties"] for entry in states if entry["id"] == QUARK_STAIR]
    require({state["facing"] for state in stairs} == {"east", "west"}
            and all(state["shape"] == "straight" and state["half"] == "bottom" and state["waterlogged"] == "false"
                    for state in stairs)
            and any(state != catalog[QUARK_STAIR]["properties"] for state in stairs),
            "The Quark stairs have explicit directional, non-default states")


def fixture_version(child, bridge, chat, card):
    report = card["report"]
    version = report["new_version"]["building_version"]
    chat.send(child, "approve " + card["code"], f"已批准 V{version}")
    completed = wait_terminal(child, bridge)
    require(completed["job"]["job_id"] == report["new_version"]["plan_id"], "The approved fixture version is active")
    verify_world(completed, report)
    verify_receipts(completed["job"]["supply"]["transfers"])
    return completed


def exercise(options, run, trace):
    def launch():
        return launch_client(options.java_home, options.source_instance, run, options.port, smoke=True,
                             m4_smoke=True, bmc4=True, pack_source=options.bmc4_pack)

    chat, child, runtime = Chat(run), None, None
    try:
        child = launch()
        runtime = start_runtime(run, options.source_instance)
        bridge = BridgeClient(run / "config" / "mcaibuilder.json")
        wait_for(child, bridge.status, "BMC4 survival world", seconds=600)
        wait_ticks(child, bridge, bridge.status()["game_tick"] + 30)
        catalog = bridge.progress()["modded_materials"]
        require(set(catalog) == MODDED, f"Expected the three curated block/placement-item pairs: {catalog}")
        require(all(catalog[block]["item_id"] in STOCK for block in MODDED), "Verified registry mappings supply the plans")
        trace["registry"] = catalog
        trace["stock"] = [provision(child, run, STOCK, place=True)]
        chat.send(child, "supply " + " ".join(map(str, CHEST)), "供料箱已批准")

        first = chat.card(child, DESIGN)
        check_modded(first["report"], catalog)
        assert_pending_without_actions(child, bridge)
        built = fixture_version(child, bridge, chat, first)
        trace["modded_initial"] = {"review": first["report"], "completed": built}

        second = chat.card(child, PLAIN)
        stripped = fixture_version(child, bridge, chat, second)
        removed = {row["before"]["id"] for row in stripped["job"]["actions"] if row["status"] == "removed"}
        require(removed == MODDED and stripped["job"]["removed"] == 12, "All three modded types were broken normally")
        trace["modded_removal"] = {"review": second["report"], "completed": stripped}

        third = chat.card(child, RESTORE)
        restored = fixture_version(child, bridge, chat, third)
        check_modded(third["report"], catalog)
        require(target_states(first["report"]) == target_states(third["report"])
                and restored["npc"]["npc_id"] == built["npc"]["npc_id"],
                "The same worker restored the exact modded states")
        trace["modded_restored"] = {"review": third["report"], "completed": restored}
        write_json(run / "acceptance.json", trace)

        stop_runtime(runtime)
        runtime = runtime_process(run, options.source_instance, options.ledger.resolve())
        real = chat.card(child, REAL_DESIGN)
        report = real["report"]
        require(report["new_version"]["origin"] == [17, -60, 5] and report["counts"]["add"] == 30
                and CHIPPED_WOOD in {row["desired"]["id"] for row in report["changes"]}
                and QUARK_ROOF not in {row["desired"]["id"] for row in report["changes"]},
                "Real model built a concrete new wall with the requested starter materials")
        trace["codex"] = {"initial_review": report}
        require(bridge.progress()["job"]["job_id"] == restored["job"]["job_id"],
                "The real draft has not started construction")
        active = await_approval(child, bridge, chat, real, trace, "m4-codex-start")
        worker = active["npc"]["npc_id"]

        wait_placed(child, bridge, 26)
        revised = chat.card(child, REAL_REVISION)
        paused = assert_pending_without_actions(child, bridge)
        diff = revised["report"]
        check_modded(diff, catalog)
        require(26 <= paused["job"]["placed"] < 30 and paused["npc"]["npc_id"] == worker,
                "The real revision paused before the original roof completed")
        original, desired = target_states(report), target_states(diff)
        changed = {pos for pos in desired if original[pos] != desired[pos]}
        require(len(changed) == 8 and all(original[pos]["id"] == "minecraft:stone_brick_slab" for pos in changed)
                and diff["counts"]["replace"] > 0 and diff["counts"]["add"] > 0
                and diff["supply"]["requirements"][QUARK_ROOF]["missing"] == 6
                and diff["supply"]["requirements"][QUARK_STAIR]["missing"] == 2,
                "The real revision changed only roof positions and its physical shortage was recomputed")
        trace["codex"].update(paused=bridge.progress(), revision=diff)
        await_approval(child, bridge, chat, revised, trace, "m4-codex-revision")

        missing = when_job(child, bridge, lambda j: j.get("reason") == "out_of_material",
                           "approved revision waits for unavailable Quark materials")
        require(missing["job"]["removed"] == diff["counts"]["replace"]
                and missing["job"]["placed"] == 0, "Only the approved removals occurred before supply arrived")
        trace["codex"]["shortage"] = missing
        chat.send(child, "还缺多少材料？请按实际仓库列出。", "缺料 quark:red_shingles ×6")
        trace["stock"].append(provision(child, run, {QUARK_ROOF: 6, QUARK_STAIR: 2}))
        chat.send(child, "resume " + diff["new_version"]["plan_id"], "已继续施工")
        completed = wait_terminal(child, bridge)
        verify_world(completed, diff)
        moved = verify_receipts(completed["job"]["supply"]["transfers"])
        require(moved[QUARK_ROOF] == 6 and moved[QUARK_STAIR] == 2 and completed["npc"]["npc_id"] == worker
                and completed["npc"]["materials"].get("minecraft:stone_brick_slab", 0) > 0
                and completed["materials"]["warehouse_items"] == {"minecraft:obsidian": 2},
                "New parts came from the box, obsolete stock stayed in the bag, and the worker was unchanged")
        trace["codex"]["finished"] = completed
        chat.send(child, "approve " + real["code"], "stale_approval")
        chat.send(child, "approve " + revised["code"], "stale_approval")
        require(bridge.progress()["job"]["supply"]["transfers"] == completed["job"]["supply"]["transfers"],
                "Stale approvals did not re-open the warehouse")
        (run / ".mcaibuilder-capture").touch()
        wait_for(child, lambda: (run / "screenshots" / "m1-wall-built.png").is_file(), "BMC4 survival build screenshot")
        trace["save_exits"] = [stop_cleanly(child, run, "finished", 180)]
        write_json(run / "acceptance.json", trace)
        print("BMC4 saved. Reopening the same isolated world.", flush=True)
        child = launch()
        wait_for(child, bridge.status, "saved BMC4 survival world", seconds=600)
        wait_ticks(child, bridge, bridge.status()["game_tick"] + 30)

        def read_checkpoint():
            state = bridge.progress()
            return state if state.get("recovery", {}).get("npc_reused") else None

        reopened = wait_for(child, read_checkpoint, "final world, approved warehouse, and cargo checkpoint")
        require(all(reopened["recovery"][key] for key in (
            "world_matches_checkpoint", "inventory_matches_checkpoint", "warehouse_matches_checkpoint",
            "warehouse_transfer_confirmed", "interaction_confirmed"))
            and reopened["npc"]["npc_id"] == worker
            and reopened["job"]["supply"]["transfers"] == completed["job"]["supply"]["transfers"],
            "The exact NPC, material balances and final states survived BMC4 restart")
        verify_world(reopened, diff)
        trace["reopened"] = reopened
        trace["save_exits"].append(stop_cleanly(child, run, "reopened", 180))
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
    parser.add_argument("--bmc4-pack", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18774)
    options = parser.parse_args()
    pack = options.bmc4_pack.resolve()
    if not pack.is_relative_to(TEST_ROOT.resolve()) or not (pack / ".mcaibuilder-test").is_file():
        parser.error("--bmc4-pack must be an existing, marked isolated test instance")
    count = sum(jar.name not in (BRIDGE_JAR.name, PLAYERS_JAR.name) for jar in (pack / "mods").glob("*.jar"))
    require(count == 387, f"Use the validated 387-mod BMC4 fixture, got {count}")
    runs = TEST_ROOT / "m4-bmc4-runs"
    runs.mkdir(parents=True, exist_ok=True)
    run = Path(mkdtemp(dir=runs, prefix=datetime.now().strftime("%Y%m%d-%H%M%S-")))
    (run / ".mcaibuilder-test").touch()
    print(f"M4 BMC4 survival world {run.name}", flush=True)
    trace = {"passed": False, "bmc4_mod_jars": count}
    try:
        exercise(options, run, trace)
    except Exception as error:
        trace["error"] = f"{type(error).__name__}: {error}"
        write_json(run / "acceptance.json", trace)
        raise
    print("M4 BMC4 modded restoration, real model and restart passed.", flush=True)


if __name__ == "__main__":
    main()
