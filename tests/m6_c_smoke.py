"""Exercise scoped NPC surveys in a copied, newly generated BMC4 terrain world."""

from argparse import ArgumentParser
from pathlib import Path
from threading import Event, Thread
from time import sleep
from uuid import UUID

from src.architect.codex_provider import CodexProvider
from src.architect.runtime import ArchitectClient, Runtime, SessionStore
from src.architect.survey import collect
from src.construction.client import BridgeClient
from src.core.building import write_json
from tests.m1_smoke import rejected, require, wait_for
from tests.m3_smoke import assert_pending_without_actions
from tests.m5_a_smoke import PlayerChat
from tests.m5_b_smoke import fixture, fmt, give_stock, halo, scope
from tests.m6_a_smoke import stop
from tools.m1_client import LOCAL_PLAYER_ID, TEST_ROOT, launch_client
from tools.mcai_launcher import SETUP, find_setup, read_object
from tools.world_copy import file_manifest, read_copy

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / ".cache" / "m6-cd"
DESIGN = ("/architect survey 先走几格到另一个安全站位看看地面，再按地形设计一面5至8格宽、4格高的小窗墙。"
          "平缓开阔可用浅色和较宽窗洞，坡地侧可用更窄墙体和较深木色。"
          "选实际支撑充足的地基和合适朝向。不要台阶，不清理地形。")


class CopyGame:
    def __init__(self, game, port):
        self.world = game
        self.settings = find_setup(read_object(SETUP))
        self.port = port
        self.child = None

    def __enter__(self):
        if self.child is not None:
            raise ValueError("Already opened the isolated client")
        settings = self.settings
        proof = self.world / ".mcaibuilder-copy-player-proof.json"
        previous = proof.stat().st_mtime_ns if proof.is_file() else 0
        self.child = launch_client(settings["java"], settings["instance"], self.world, self.port, bmc4=True,
                                   world_copy=True, copy_smoke=True, managed=True, camera_smoke=True, hide_console=True)
        self.child.session = self
        self.bridge = BridgeClient(self.world / "config" / "mcaibuilder-copy.json", timeout=4)
        self.client = ArchitectClient(self.world / "config" / "mcaibuilder-architect.json", timeout=4)
        self.chat = PlayerChat(self.world)
        try:
            wait_for(self.child, self.bridge.health, "the isolated BMC4 copy server", seconds=570)
            wait_for(self.child, lambda: read_object(proof) if proof.is_file() and proof.stat().st_mtime_ns > previous else None,
                     "the synthetic saved owner after this login", seconds=75)
        except Exception:
            self.__exit__()
            raise
        return self

    def __exit__(self, *_):
        stop(self.child, self.world)
        self.child = None

    def terrain(self):
        player = fixture(self.child, "field")["position"]
        # The teleport returns before the integrated server finishes the neighboring chunks.
        sleep(7)
        return fixture(self.child, "survey")["terrain"], player

    def select(self, site):
        active = self.bridge.health()["site_authorization"]
        if (active.get("approved") and active.get("warehouse_approved")
                and all(active[key] == site[key] for key in ("origin", "area", "spawn"))):
            fixture(self.child, "field", position=site["player"])
            return active
        fixture(self.child, "field", position=site["stance"])

        def loaded():
            try:
                self.snapshot(site)
            except AssertionError as error:
                if "needs the loaded site" not in str(error):
                    raise
                return False
            return True

        wait_for(self.child, loaded, "the natural test area's cold chunks before marking", seconds=65)
        proof = {}
        scope(self.child, self.bridge, self.chat, site, proof, same_site=True)
        give_stock(self.child, site)
        self.chat.send(self.child, "/architect supply " + fmt(site["chest"]), "供料箱已批准")
        fixture(self.child, "field", position=site["player"])
        return proof["site_approval"]

    def snapshot(self, site):
        return fixture(self.child, "snapshot", area=halo(site["area"]))["blocks"]

    def runtime(self, provider):
        settings = self.settings
        return Runtime(self.client, provider, SessionStore(self.world / "architect-previews"),
                       settings["instance"], settings["blender"], share_observations=True)


def select_sites(terrain, center):
    rows = {(row["p"][0], row["p"][2]): row for row in terrain}

    def safe(row):
        return bool(row and row["stand"] and row["anchor"] and row["clear"])

    choices = []
    for (x, z), origin in rows.items():
        y = origin["p"][1]
        if not safe(origin):
            continue
        for forward in ((1, 0), (0, 1)):
            ux, uz = forward

            def column(a, b):
                return x + a * ux - b * uz, z + a * uz + b * ux

            def cell(a, b):
                return rows.get(column(a, b))

            width = 0
            for i in range(8):
                row = cell(i, 0)
                if not safe(row) or row["p"][1] != y:
                    break
                width += 1
            if width < 5:
                continue
            for sign in (1, -1):
                for offset in (2, 3, 4):
                    spawn = cell(2, sign * offset)
                    if not safe(spawn) or abs(spawn["p"][1] - y) > 3:
                        continue
                    sx, sy, sz = spawn["p"]
                    # Start with at least one plausible short, straight walk in the sampled area.
                    routes = [[rows.get((sx + a * step, sz + b * step)) for step in (1, 2, 3)]
                              for a, b in ((0, 1), (0, -1), (1, 0), (-1, 0))]
                    if not any(all(safe(row) and abs(row["p"][1] - sy) <= 1 for row in route)
                               for route in routes):
                        continue
                    window = [rows.get((a, b)) for a in range(sx - 4, sx + 5) for b in range(sz - 4, sz + 5)]
                    if not all(window):
                        continue
                    heights = [row["p"][1] for row in window if safe(row) and -1 <= row["p"][1] - sy <= 2]
                    relief = max(heights) - min(heights)
                    flat_count = heights.count(sy)
                    for along in (width + 2, width + 1, width - 1, -2):
                        for depth in (3, 5, 6):
                            chest = cell(along, sign * depth)
                            stance = cell(along, sign * (depth + 1))
                            player = cell(5, sign * offset)
                            if (not all(safe(row) and abs(row["p"][1] - y) <= 3 for row in (chest, stance, player))
                                    or chest["p"][1] != stance["p"][1]
                                    or any(player == step for route in routes for step in route[:2])):
                                continue
                            ends = [column(a, b) for a in (-4, width + 5)
                                    for b in (min(-4, sign * 8), max(4, sign * 8))]
                            ax, az = zip(*ends, strict=True)
                            area = {"min": [min(ax), y - 4, min(az)], "max": [max(ax), y + 6, max(az)]}
                            distance = (center[0] - x) ** 2 + (center[2] - z) ** 2
                            choices.append({"area": area, "origin": [x, y, z], "player": player["p"],
                                            "spawn": spawn["p"], "chest": chest["p"], "stance": stance["p"],
                                            "relief": relief, "flat_count": flat_count, "distance": distance,
                                            "width": width, "rotation": 0 if forward == (1, 0) else 90})
    require(choices, "Found naturally supported window-wall sites and safe supply paths")
    open_site = min(choices, key=lambda site: (-site["width"], site["relief"], -site["flat_count"], site["distance"]))
    other = [site for site in choices if sum((a - b)**2 for a, b in zip(
        site["origin"], open_site["origin"], strict=True)) >= 17**2 and site["relief"] > open_site["relief"]]
    if not other:
        open_site = min(choices, key=lambda site: (site["relief"], -site["width"], -site["flat_count"], site["distance"]))
        other = [row for row in choices if row["relief"] > open_site["relief"]
                 and sum((a - b)**2 for a, b in zip(row["origin"], open_site["origin"], strict=True)) >= 17**2]
    require(other, "A second natural site with different height relief is available")
    slope = min(other, key=lambda site: (-site["relief"], site["width"], site["flat_count"], site["distance"]))
    return open_site, slope

def wait_request(run, message):
    run.client.inbox()
    run.chat.send(run.child, message, "收到，正在处理。")
    pending = run.client.inbox()
    require(len(pending) == 1 and pending[0]["survey"], "The saved player's signed chat created one survey request")
    return pending[0]


def check_denials(run):
    request = wait_request(run, "/architect survey 只检查越界站位。")
    request_id = request["request_id"]
    rejected(lambda: run.bridge._call("architect/survey", {"request_id": request_id}), 401, "unauthorized")
    failure = collect(run.client, request_id, 1, "move_to_observation_point", [12, 0, 0])
    require(failure["capture_status"] == "failed" and failure["capture_error"] == "survey_unsafe_footing",
            "Out-of-distance movement cannot navigate or capture")
    replay = run.client.survey(request_id, step=1, action="move_to_observation_point", offset=[12, 0, 0])
    require(replay == failure, "Duplicate observations cannot move the NPC a second time")
    rejected(lambda: run.client.survey(request_id, step=1, action="observe_here"), 409, "survey_step_invalid")
    sky = collect(run.client, request_id, 2, "move_to_observation_point", [0, 4, 0])
    require(sky.get("capture_error") == "survey_unsafe_footing", "A view point without a floor cannot navigate")
    run.client.answer(request_id, "越界和无地面站位已拒绝。")
    rejected(lambda: run.client.survey(request_id, step=2, action="observe_here"), 409, "stale_request")
    return {"bridge_scope_rejected": True, "out_of_distance": failure["capture_error"],
            "unsafe_footing": sky["capture_error"],
            "duplicate_idempotent": True, "replaced_and_stale_rejected": True}


def synthetic_copy(game):
    record = read_copy(game)
    original = Path(record["source_instance"])
    require(original.parent == TEST_ROOT.resolve() and original.name.startswith("m6-terrain-source-")
            and record["player_name"] == "BuilderTester" and UUID(record["player_uuid"]) == UUID(LOCAL_PLAYER_ID),
            "The external-image test accepts only a generated synthetic copy")
    return record


def run_c(game):
    provider = CodexProvider(timeout=50, max_turns=10, max_tokens=150_000, budget_log=HOME / "codex-turns.sqlite3")
    trace = {"passed": False, "sites": []}
    paths = synthetic_copy(game)
    source, source_instance = Path(paths["source_world"]), Path(paths["source_instance"])
    baseline = read_object(game / "copy-integrity.json")
    try:
        with CopyGame(game, 18787) as run:
            prepared = read_object(HOME / "selected-sites.json")
            active = run.bridge.health()["site_authorization"]
            if (active.get("approved") and prepared.get("sites")
                    and all(active[key] == prepared["sites"][0][key] for key in ("origin", "area", "spawn"))):
                sites = prepared["sites"]
            else:
                sites = select_sites(*run.terrain())
                write_json(HOME / "selected-sites.json", {"sites": sites})
            for index, site in enumerate(sites):
                result = {"site": site}
                trace["sites"].append(result)
                result["scope"] = run.select(site)
                before = run.snapshot(site)
                if index == 0:
                    result["rejections"] = check_denials(run)
                runtime = run.runtime(provider)
                done = Event()
                worker = Thread(target=runtime.run, args=(done.is_set,), daemon=True)
                worker.start()
                previous = set((runtime.store.root / "survey").glob("*.json"))
                try:
                    wait_for(run.child, lambda: run.bridge.health()["architect_runtime_online"],
                             "vision-enabled Architect Runtime", seconds=20)
                    card = run.chat.card(run.child, DESIGN, timeout=155)
                finally:
                    done.set()
                    worker.join(timeout=60)
                added = set((runtime.store.root / "survey").glob("*.json")) - previous
                require(len(added) == 1, "The runtime recorded exactly one survey")
                observation = read_object(added.pop())
                result["survey"] = observation
                motions = [step for step in observation["steps"] if step["action"] == "move_to_observation_point"
                           and step["capture_status"] == "captured" and step["from"] != step["to"]]
                require(motions and any(a["relative_scene"]["view"]["yaw"] != b["relative_scene"]["view"]["yaw"]
                                        for a, b in zip(observation["steps"], observation["steps"][1:])
                                        if "relative_scene" in a and "relative_scene" in b),
                        "The Fake Player changed both native standing position and actual view")
                report = card["report"]
                proposal = runtime.store.design(report["new_version"]["plan_id"])
                result.update(parameters=proposal["parameters"], report=report)
                require(report["counts"]["conflict"] == 0 and report["counts"]["add"] >= 20,
                        "The Forge review accepted all real positions and foundation blocks")
                assert_pending_without_actions(run.child, run.bridge)
                require(run.snapshot(site) == before, "Waiting at the review did not change a single monitored block")
                result["scope_and_world_before_approval"] = True
                run.chat.send(run.child, "/architect reject " + card["code"], "已拒绝这个候选")
                run.chat.send(run.child, "/architect site revoke", "已撤销施工区")
                write_json(HOME / "m6-c-trace.json", trace)
                print(f"Verified scene {index + 1} with real model and no world writes.", flush=True)
        require(trace["sites"][0]["parameters"] != trace["sites"][1]["parameters"],
                "The same natural request produced a distinct terrain-adapted design")
        require(file_manifest(source, world=True) == baseline["world"]
                and file_manifest(source_instance / "mods") == baseline["mods"],
                "The source world and original fixture mod tree are byte-for-byte unchanged")
        trace.update(passed=True, native_save=True, source_unchanged=True)
    except Exception as error:
        trace["error"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        trace["budget"] = provider.budget.records()
        write_json(HOME / "m6-c-trace.json", trace)


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("copy", type=Path)
    parser.add_argument("--share-test-images", action="store_true", required=True,
                        help="Use only the approved synthetic BMC4 test scene images and shared ledger")
    run_c(parser.parse_args().copy)
