"""Exercise the saved-owner, natural terrain and survival supply in a marked BMC4 copy."""

from argparse import ArgumentParser
import json
from pathlib import Path
import re
from time import sleep
from uuid import uuid4

from src.construction.client import BridgeClient
from src.core.building import Building, write_json
from tests.m1_smoke import rejected, require, wait_for, wait_placed, wait_ticks
from tests.m3_codex_live import await_approval, verify_edit, verify_partial
from tests.m3_smoke import assert_pending_without_actions, signal, verify_world
from tests.m4_supply_smoke import verify_receipts
from tests.m5_a_smoke import LaunchSession, LaunchedGame, PlayerChat
from tools.mcai_launcher import HOME, read_object
from tools.world_copy import WORLD, file_manifest, read_copy
from tools.save_nbt import saved_player


DESIGN = "@Builder 请在已批准的位置设计一面 8 格宽 4 格高的中世纪窗墙，使用深色橡木、石砖和透明玻璃。两扇窗各宽 2 格高 1 格，不加台阶。"
EDIT = "@Builder 先暂停施工，把窗户换成蓝色染色玻璃，其他部分保持不变。"
STOCK = {
    "minecraft:dark_oak_planks": 12, "minecraft:stone_bricks": 8, "minecraft:stone_brick_slab": 8,
    "minecraft:glass": 4, "minecraft:blue_stained_glass": 4,
    "minecraft:iron_pickaxe": 1, "minecraft:iron_axe": 1,
}


class CopySession(LaunchSession):
    def __init__(self, world, approve_versions=False):
        super().__init__("copy")
        self.world = world
        self.port = 18780
        self.approve_versions = approve_versions

    def child(self):
        state = self.phase("ready", 630)
        require(state["agent_pid"] and state["pack"].startswith("BMC4") and state["site"]["mode"] == "world_copy",
                "The Windows launcher opened a managed BMC4 copy and authenticated the Agent")
        return LaunchedGame(self, state["game_pid"])

    def saved(self):
        log = (self.world / "console.log").read_bytes()
        require(b"normal save flow" in log and b"All dimensions are saved" in log
                and (self.world / "saves" / WORLD / "level.dat").is_file(),
                "Minecraft saved and closed the managed copy normally")
        return True


def fixture(child, action, **body):
    run = child.session.world
    key = uuid4().hex
    signal(run, ".mcaibuilder-copy-test-request", json.dumps({"request": key, "action": action, **body}))
    file = run / ".mcaibuilder-copy-test-response"

    def observe():
        data = read_object(file)
        return data if data.get("request") == key else None

    result = wait_for(child, observe, f"copied-world test action {action}", seconds=70)
    require(result["success"], result.get("error", action))
    return result


def choose_site(terrain, player):
    rows = {(row["p"][0], row["p"][2]): row for row in terrain}
    choices = []
    for (x, z), row in rows.items():
        y = row["p"][1]
        footprint = [rows.get((x + offset, z)) for offset in range(8)]
        # Lower footing cells can use the already placed neighboring wall support.
        if not all(item and item["clear"] and item["anchor"] and y - 1 <= item["p"][1] <= y
                   for item in footprint):
            continue
        spawn = rows.get((x - 2, z + 5))
        chest = rows.get((x + 10, z + 4))
        stance = rows.get((x + 10, z + 6))
        if not all(item and item["stand"] and item["anchor"] for item in (spawn, chest, stance)):
            continue
        if not chest["clear"] or chest["p"][1] != stance["p"][1]:
            continue
        corners = {"min": [x - 4, y - 3, z - 3], "max": [x + 13, y + 5, z + 8]}
        if any(not (corners["min"][1] <= item["p"][1] < corners["max"][1]) for item in (spawn, chest, stance)):
            continue
        points = [rows.get((a, b)) for a in range(x - 5, x + 15) for b in range(z - 4, z + 10)]
        if any(item is None for item in points):
            continue
        heights = [item["p"][1] for item in points]
        spread = max(heights) - min(heights)
        if not 1 <= spread <= 14:
            continue
        # Prefer a long, walkable route with some real elevation change.
        distance = (x - player[0]) ** 2 + (z - player[2]) ** 2
        path = [rows[(a, z + 4)] for a in range(x - 2, x + 11)]
        walkable = sum(item["stand"] and corners["min"][1] <= item["p"][1] < corners["max"][1] for item in path)
        if walkable < 10 or any(abs(a["p"][1] - b["p"][1]) > 1 for a, b in zip(path, path[1:])):
            continue
        choices.append((-(walkable - int(spawn["p"][1] == chest["p"][1])), distance, {
            "origin": [x, y, z], "area": corners, "spawn": spawn["p"], "chest": chest["p"],
            "stance": stance["p"], "terrain_height_span": spread,
            "path_height_span": max(item["p"][1] for item in path) - min(item["p"][1] for item in path),
        }))
    require(choices, "No safe, naturally supported wall site with a walkable supply path was found")
    return min(choices, key=lambda item: item[:2])[2]


def fmt(position):
    return " ".join(map(str, position))


def scope(child, bridge, chat, site, proof, *, same_site=False):
    for label, position in (("pos1", site["area"]["min"]), ("pos2", site["area"]["max"]),
                            ("spawn", site["spawn"]), ("origin", site["origin"])):
        chat.send(child, f"/architect site {label} {fmt(position)}" + (" north" if label == "origin" else ""), "已标记")
    lines = chat.send(child, "/architect site review", "[批准施工区 ")
    digest = next(re.search(r"施工区审阅 ([a-f0-9]{16})", line)[1] for line in lines if "施工区审阅" in line)
    code = next(re.search(r"\[批准施工区 ([a-f0-9-]{36})\]", line)[1] for line in lines if "[批准施工区 " in line)
    proof["waiting_for_site"] = {"short_hash": digest, "code": code, "site": site}
    write_json(chat.run / "acceptance.json", proof)
    if same_site:
        chat.submit(child, "/architect site confirm " + code)
        proof["same_site_test_confirmation"] = True
    else:
        print(f"Waiting for player approval of copy site {digest}.", flush=True)
    state = wait_for(child, lambda: active
                     if (active := bridge.health()["site_authorization"]).get("approved") else None, "player approval of the loaded copy site", seconds=420)
    require(state["origin"] == site["origin"] and state["area"] == site["area"]
            and state["spawn"] == site["spawn"] and state["rotation"] == 0, "The actual scope equals the selected site")
    proof.pop("waiting_for_site")
    proof["site_approval"] = state


def give_stock(child, site):
    run = child.session.world
    token = uuid4().hex
    data = {"request": token, "position": site["chest"], "stance": site["stance"], "place": True,
            "items": [{"id": key, "count": count} for key, count in STOCK.items()]}
    signal(run, ".mcaibuilder-stock-request", json.dumps(data))
    file = run / ".mcaibuilder-stock-result"
    result = wait_for(child, lambda: (row if (row := read_object(file)).get("request") == token else None),
                      "player's native chest setup", seconds=60)
    require(result["success"] and result["chest_placed"], result.get("error", "Native player supplied a new chest"))
    return result


def progress(session):
    return BridgeClient(session.world / "config" / "mcaibuilder-copy.json", timeout=2)


def halo(area):
    return {"min": [axis - 1 for axis in area["min"]], "max": [axis + 1 for axis in area["max"]]}


def unchanged(before, after, permitted):
    original = {tuple(row["pos"]): row for row in before}
    latest = {tuple(row["pos"]): row for row in after}
    return [pos for pos in original.keys() | latest.keys()
            if pos not in permitted and original.get(pos) != latest.get(pos)]


def previously_approved_site(session, previous):
    saved = read_object(previous / "acceptance.json")
    prior = saved["site_approval"]
    site = saved["site_selection"]
    before = read_copy(previous)
    after = read_copy(session.world)
    require(previous != session.world and before["source_world"] == after["source_world"]
            and before["player_uuid"] == after["player_uuid"]
            and read_object(previous / "copy-integrity.json") == read_object(session.world / "copy-integrity.json")
            and prior["approved"] and prior["dimension"] == "minecraft:overworld"
            and prior["rotation"] == 0 and all(prior[key] == site[key] for key in ("area", "spawn", "origin")),
            "The fresh validation copy must repeat the already approved player's exact site and source")
    return site


def exercise(session, trace, previous=None):
    child = session.child()
    bridge = progress(session)
    chat = PlayerChat(session.world)
    trace["chat"] = chat.trace
    original = wait_for(child, lambda: read_object(session.world / ".mcaibuilder-copy-player-proof.json"),
                        "saved player identity proof")
    require(all(original[key] for key in ("saved_identity_matches", "dimension_matches", "position_matches",
                                          "inventory_matches", "game_mode_matches")), f"Original player loaded correctly: {original}")
    trace["saved_player"] = original
    require(not bridge.health()["site_authorization"]["approved"], "A fresh copy starts with no project permission")
    trace["http_write_denied"] = rejected(lambda: bridge.submit({}), 403, "copy_player_confirmation_required")
    if previous is None:
        spot = fixture(child, "field")["position"]
        sleep(7)
        terrain = fixture(child, "survey")
        site = choose_site(terrain["terrain"], spot)
    else:
        site = previously_approved_site(session, previous)
    trace["site_selection"] = site
    fixture(child, "field", position=site["stance"])
    scope(child, bridge, chat, site, trace, same_site=previous is not None)
    trace["stock"] = give_stock(child, site)
    chat.send(child, "/architect supply " + fmt(site["chest"]), "供料箱已批准")
    require(bridge.health()["site_authorization"]["warehouse_approved"], "Warehouse permission is visible in launcher health")
    baseline_world = fixture(child, "snapshot", area=halo(site["area"]))["blocks"]
    starting_records = len(session.records())
    trace["codex_turn_offset"] = starting_records

    first = chat.card(child, DESIGN)
    old_report = first["report"]
    require(old_report["counts"]["add"] == len(first["building"].blocks) == 32
            and old_report["new_version"]["origin"] == site["origin"] and old_report["counts"]["conflict"] == 0
            and old_report["authorization"] == trace["site_approval"]["authorization"],
            "The real design uses the authorized natural site, palette and size")
    assert_pending_without_actions(child, bridge)
    trace["draft"] = old_report
    started = await_version(session, child, bridge, chat, first, trace, "M5-B copy V1")
    worker = started["npc"]["npc_id"]
    finish_build(session, trace, child, bridge, chat, first, worker, baseline_world, starting_records, reopen=True)


def await_version(session, child, bridge, chat, card, trace, label):
    if session.approve_versions:
        report = card["report"]
        active = bridge.health()["site_authorization"]
        require(active["mode"] == "world_copy" and active["approved"]
                and active["authorization"] == report["authorization"] and report["counts"]["conflict"] == 0,
                "Test-only approval requires the current, conflict-free isolated review")
        require(Path(session.state()["game_dir"]) == session.world
                and session.home.name == "validation-copy", "Test client is bound to this marked copy")
        fixture(child, "field", position=trace["site_selection"]["stance"])
        signal(session.world, ".mcaibuilder-architect-command", "architect approve " + card["code"])
        trace.setdefault("test_player_confirmations", []).append(report["review_sha256"])
    return await_approval(child, bridge, chat, card, trace, label)


def finish_build(session, trace, child, bridge, chat, first, worker, baseline_world, starting_records, *, reopen=False):
    old_report = first["report"]
    site = trace["site_selection"]
    wait_placed(child, bridge, 17)
    wait_for(child, lambda: sum(row["status"] == "placed" and row["actual"]["id"] == "minecraft:glass"
                               for row in bridge.progress()["job"]["actions"]) >= 2,
             "the original glass to be physically placed", seconds=25)
    second = chat.card(child, EDIT)
    verify_edit(first, second, "minecraft:glass", "minecraft:blue_stained_glass")
    paused = assert_pending_without_actions(child, bridge)
    before_revision = bridge.progress()
    verify_partial(before_revision, old_report)
    require(paused["job"]["state"] == "paused" and second["report"]["counts"]["replace"] > 0,
            "The real runtime staged an incremental mid-job revision")
    trace.update(paused=before_revision, revision=second["report"])
    if reopen:
        trace["paused_native_save"] = session.exit()
        write_json(chat.run / "acceptance.json", trace)
        session.control("start")
        child = session.child()
        fixture(child, "field", position=site["stance"])

        def worker_restored():
            state = bridge.progress()
            return state if state.get("recovery", {}).get("npc_reused") else None

        state = wait_for(child, worker_restored, "the original NPC and saved cargo after the paused copy reload", seconds=110)
        facts = state["recovery"]
        require(state["npc"]["npc_id"] == worker and state["job"]["state"] == "paused"
                and state["job"]["actions"] == before_revision["job"]["actions"]
                and facts["world_matches_checkpoint"] and facts["interaction_confirmed"]
                and facts["inventory_matches_checkpoint"] and facts["warehouse_matches_checkpoint"],
                "The native restart recovered the same paused job, world and inventories")
        trace["paused_copy_reopened"] = state
        check_chunk_reload(session, trace, child, bridge, chat, state, site)
        write_json(chat.run / "acceptance.json", trace)
    finish_change(session, trace, child, bridge, chat, second, worker, baseline_world, starting_records)


def finish_change(session, trace, child, bridge, chat, second, worker, baseline_world, starting_records):
    await_version(session, child, bridge, chat, second, trace, "M5-B copy V2")
    wait_final(session, trace, child, bridge, chat, second["report"], worker, baseline_world, starting_records)


def wait_final(session, trace, child, bridge, chat, report, worker, baseline_world, starting_records):
    site = trace["site_selection"]
    before_revision = trace["paused"]

    def completed():
        state = bridge.progress()
        job = state["job"]
        require(job["state"] != "paused", f"The approved V2 paused: {job.get('reason')}")
        return state if job["state"] in ("completed", "failed", "cancelled") else None

    final = wait_for(child, completed, "the approved V2 terminal state", seconds=180)
    trace["mutations"] = verify_world(final, report)
    require(final["npc"]["npc_id"] == worker and final["job"]["verification"]["matched"] == 32,
            "The original survival worker completed all 32 states")
    v1_moves = before_revision["job"]["supply"]["transfers"]
    v2_moves = final["job"]["supply"]["transfers"]
    trace["transfer_v1"] = verify_receipts(v1_moves)
    trace["transfer_v2"] = verify_receipts(v2_moves)
    require(v1_moves and v2_moves, "Both versions used paid chest cargo")
    permitted = {tuple(row["world"]) for row in report["changes"]}
    covered = {tuple(row["world"]) for row in report.get("covered_grass", [])}
    actual_covered = {tuple(row["world"]) for row in final["job"]["verification"].get("expected_covered_grass_changes", [])}
    require(actual_covered <= covered, "Only disclosed, verified ground-cover changes are allowed")
    permitted.update(actual_covered)
    after_world = fixture(child, "snapshot", area=halo(site["area"]))["blocks"]
    different = unchanged(baseline_world, after_world, permitted)
    require(not different, f"Non-target cells changed during the test: {len(different)}")
    trace["preserved_non_targets"] = True
    trace["final"] = final

    if not trace.get("chunks_unloaded"):
        check_chunk_reload(session, trace, child, bridge, chat, final, site)
    trace["save"] = session.exit()
    write_json(chat.run / "acceptance.json", trace)

    if not trace.get("paused_copy_reopened"):
        session.control("start")
        child = session.child()

        def restored():
            state = bridge.progress()
            return state if state.get("recovery", {}).get("npc_reused") else None

        reopened = wait_for(child, restored, "worker and saved chest to be attached after the real copy reload", seconds=110)
        facts = reopened["recovery"]
        require(facts["world_matches_checkpoint"] and facts["inventory_matches_checkpoint"]
                and facts["warehouse_matches_checkpoint"] and reopened["npc"]["npc_id"] == worker
                and reopened["job"]["actions"] == final["job"]["actions"], "The world, cargo and receipts were restored")
        verify_world(reopened, report)
        trace["reopened"] = reopened
        trace["saved_again"] = session.exit()
    turns = session.records()[starting_records:]
    actions = [row["intent"]["action"] for row in turns]
    require(actions[:2] == ["create_design", "revise_design"] and len(actions) in (2, 3)
            and (len(actions) == 2 or actions[-1] in ("review_changes", "preview_design", "request_approval"))
            and all(row["effective"]["model"] == "gpt-6.2-sol" and row["effective"]["effort"] == "medium"
                    for row in turns), "Real Codex decisions drove building, revision and any necessary re-review")
    trace["model_turns"] = turns


def check_chunk_reload(session, trace, child, bridge, chat, checkpoint, site):
    _, original = saved_player(Path(read_copy(session.world)["source_world"]) / "level.dat")
    require(original["Dimension"] == "minecraft:the_end", "The test returns to the saved original dimension to unload the site")
    chat.submit(child, "/gamemode spectator")
    chat.submit(child, "/execute in minecraft:the_end run tp @s " + fmt(original["Pos"]))
    wait_for(child, lambda: not bridge.health()["site_authorization"]["chunks_loaded"],
             "world-copy site's natural chunk unload after leaving the dimension", seconds=75)
    no_mutations = bridge.progress()
    require(no_mutations["job"]["actions"] == checkpoint["job"]["actions"], "Chunk unload ran no additional action")
    trace["chunks_unloaded"] = True
    fixture(child, "field", position=site["stance"])
    wait_for(child, lambda: bridge.health()["site_authorization"]["chunks_loaded"], "site reload", seconds=90)
    if checkpoint["job"]["state"] == "paused":
        wait_for(child, lambda: bridge.progress().get("recovery", {}).get("npc_reused"),
                 "the original worker to reload after leaving the site", seconds=100)
    require(bridge.progress()["job"]["actions"] == checkpoint["job"]["actions"], "Returning did not replay the job")

def resume_v1(session, trace):
    child = session.child()
    bridge = progress(session)
    chat = PlayerChat(session.world)
    trace["chat"] = [*trace.get("chat", [])]
    chat.trace = trace["chat"]
    report = trace["draft"]
    plan_id = report["new_version"]["plan_id"]
    state = bridge.progress()
    require(state["job"]["job_id"] == plan_id and state["job"]["state"] in ("running", "paused"),
            "Resume must use the already approved V1")
    baseline = checkpoint_baseline(session, report)
    first = {"report": report, "building": Building.read(session.world / "architect-previews" / plan_id / "building.json")}
    starting_records = trace.get("codex_turn_offset", len(session.records()) - 1)
    finish_build(session, trace, child, bridge, chat, first, state["npc"]["npc_id"], baseline, starting_records)


def resume_v2(session, trace):
    child = session.child()
    bridge = progress(session)
    chat = PlayerChat(session.world)
    trace["chat"] = [*trace.get("chat", [])]
    chat.trace = trace["chat"]
    paused = trace["paused"]
    report = trace["revision"]
    saved = read_object(session.home / "paused-copy-checkpoint.json")
    require(saved["native_save"] and saved["stored_entity_matches"], "Same NPC was stored during native save")
    safe = trace["site_selection"]["stance"]
    fixture(child, "field", position=safe)

    def restored():
        state = bridge.progress()
        return state if state.get("recovery", {}).get("npc_reused") else None

    state = wait_for(child, restored, "original worker and its cargo after mid-job restart", seconds=110)
    facts = state["recovery"]
    require(facts["world_matches_checkpoint"] and facts["inventory_matches_checkpoint"]
            and facts["warehouse_matches_checkpoint"] and state["job"]["state"] == "paused"
            and state["job"]["actions"] == saved["actions"] == paused["job"]["actions"]
            and state["npc"]["npc_id"] == paused["npc"]["npc_id"],
            "Paused world, receipts, NPC and warehouse were recovered without replay")
    trace["paused_copy_reopened"] = state
    starting_records = trace.get("codex_turn_offset", len(session.records()) - 2)
    second = chat.card(child, "@Builder 请重新审阅已暂停的蓝色玻璃修改方案。沿用当前版本，不新增改动。")
    require(second["report"]["new_version"] == report["new_version"], "Re-review preserved the same immutable V2")
    trace["revision"] = second["report"]
    baseline = checkpoint_baseline(session, report)
    finish_change(session, trace, child, bridge, chat, second, state["npc"]["npc_id"], baseline, starting_records)


def checkpoint_baseline(session, report):
    checkpoint = read_object(session.world / "saves" / WORLD / "data" / "mcaibuilder-sites"
                             / report["authorization"] / "mcaibuilder-journal.json")
    return [{"pos": row["pos"], **row["state"]} for row in checkpoint["current"]["snapshot"]["baseline"]
            if row["state"]["id"] not in ("minecraft:air", "minecraft:cave_air", "minecraft:void_air")]


def resume_approved_v2(session, trace):
    child = session.child()
    bridge = progress(session)
    chat = PlayerChat(session.world)
    chat.trace = trace["chat"]
    report = trace["revision"]
    fixture(child, "field", position=trace["site_selection"]["stance"])
    version = report["new_version"]
    accepted = trace["approvals"][-1]
    require(accepted["job_id"] == version["plan_id"] and accepted["review_sha256"] == report["review_sha256"]
            and version["building_version"] == 2, "Resume must use the already approved V2 and review")
    state = wait_for(child, lambda: row if (row := bridge.progress()).get("recovery", {}).get("npc_reused") else None,
                     "the saved worker following the navigation stop", seconds=110)
    job = state["job"]
    recovery = state["recovery"]
    require(job["job_id"] == accepted["job_id"] and job["state"] in ("paused", "failed")
            and recovery["world_matches_checkpoint"] and recovery["interaction_confirmed"]
            and recovery["inventory_matches_checkpoint"] and recovery["warehouse_matches_checkpoint"]
            and state["npc"]["npc_id"] == trace["paused"]["npc"]["npc_id"],
            "The original V2 and transfer checkpoint remain intact")
    trace.setdefault("approved_copy_reopened", state)
    trace["latest_copy_reopened"] = state
    baseline = checkpoint_baseline(session, report)
    chat.send(child, "/architect resume " + job["job_id"], "已继续施工")
    wait_final(session, trace, child, bridge, chat, report, state["npc"]["npc_id"], baseline,
               trace.get("codex_turn_offset", 0))


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--world", type=Path)
    parser.add_argument("--same-site-as", type=Path, help="Retest the exact previously approved site in a fresh copy")
    resume = parser.add_mutually_exclusive_group()
    resume.add_argument("--resume-v1", action="store_true")
    resume.add_argument("--resume-v2", action="store_true")
    resume.add_argument("--resume-approved-v2", action="store_true")
    parser.add_argument("--approve-test-versions", action="store_true",
                        help="Relay validated versions through the saved test player in this isolated fixture")
    args = parser.parse_args()
    if args.same_site_as and (not args.approve_test_versions or args.resume_v1 or args.resume_v2 or args.resume_approved_v2):
        parser.error("--same-site-as requires a fresh isolated run with --approve-test-versions")
    if args.world is None:
        args.world = Path(read_object(HOME / "m5b-acceptance-copy.json")["game"])
    marker = read_copy(args.world)
    session = CopySession(args.world, approve_versions=args.approve_test_versions)
    recovering = args.resume_v1 or args.resume_v2 or args.resume_approved_v2
    trace = read_object(args.world / "acceptance.json") if recovering else {"passed": False}
    trace.pop("error", None)
    try:
        if args.resume_approved_v2:
            resume_approved_v2(session, trace)
        elif args.resume_v2:
            resume_v2(session, trace)
        elif args.resume_v1:
            resume_v1(session, trace)
        else:
            exercise(session, trace, args.same_site_as)
        before = read_object(args.world / "copy-integrity.json")
        require(file_manifest(Path(marker["source_world"]), world=True) == before["world"]
                and file_manifest(Path(marker["source_instance"]) / "mods") == before["mods"],
                "Original save and original mod directory remained byte-for-byte identical")
        trace["source_unchanged"] = True
        trace["passed"] = True
        print("M5-B real world core acceptance passed.", flush=True)
    except Exception as error:
        trace["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        write_json(session.world / "acceptance.json", trace)
        if trace["passed"]:
            session.control("close")


if __name__ == "__main__":
    main()
