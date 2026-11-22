"""Exercise the companion through actual chat and native Forge interactions."""

from argparse import ArgumentParser
from collections import Counter
import json
import os
import re
from pathlib import Path
import subprocess
import sys
from tempfile import mkdtemp
from uuid import uuid4

from src.construction.client import BridgeClient
from tests.m1_smoke import require, wait_for
from tests.m3_smoke import Chat, signal, stop_runtime
from tools.m1_client import ROOT, TEST_ROOT, launch_client
from tools.world_copy import create_copy


def fixture(child, run, action, position, **kwargs):
    request = str(uuid4())
    signal(run, ".mcaibuilder-stock-request", json.dumps({
        "request": request, "companion": action, "position": position, **kwargs,
    }))
    path = run / ".mcaibuilder-stock-result"

    def result():
        try:
            reply = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, PermissionError):
            return None
        return reply if reply.get("request") == request else None

    reply = wait_for(child, result, action)
    require(reply.get("success"), f"{action} failed: {reply}")
    return reply


def speak(child, chat, message, expected):
    before = len(chat.messages())
    signal(chat.run, ".mcaibuilder-chat-fixture", json.dumps({"action": "chat", "message": message}))
    return wait_for(child, lambda: next((line for line in chat.messages()[before:] if expected in line), None),
                    f"reply {expected}", seconds=55)


def items(counts):
    return [{"id": name, "count": count} for name, count in counts.items() if count]


def close_game(child, run):
    if child.poll() is None:
        (run / ".mcaibuilder-stop").touch()
        try:
            child.wait(timeout=70)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=10)


def verify_copy_area(options, source):
    game = create_copy(source, source / "saves" / "m1-construction", terrain_fixture=True)
    child = launch_client(options.java_home, options.source_instance, game, options.port,
                          smoke=True, world_copy=True, copy_smoke=True, hide_console=True)
    try:
        bridge = BridgeClient(game / "config/mcaibuilder-copy.json")
        wait_for(child, bridge.status, "isolated save copy", seconds=150)
        chat = Chat(game)
        line = speak(child, chat, "@Builder 以我为中心 30x30", "[批准施工区")
        permit = re.search(r"批准施工区 ([0-9a-f-]{36})", line)[1]
        chat.send(child, "site confirm " + permit, "施工区已批准")
        site = bridge.status()["site_authorization"]
        size = [hi - lo + 1 for lo, hi in zip(site["area"]["min"], site["area"]["max"], strict=True)]
        require(size == [30, 32, 30], f"The large confirmed site has unexpected bounds: {size}")
        print(f"Verified {'×'.join(map(str, size))} site in the copied save.", flush=True)
    finally:
        close_game(child, game)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument("--source-instance", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18842)
    options = parser.parse_args()
    run = Path(mkdtemp(prefix="m6-terrain-source-companion-", dir=TEST_ROOT))
    print(f"Companion fixture {run}", flush=True)
    child = launch_client(options.java_home, options.source_instance, run, options.port,
                          smoke=True, m4_smoke=True, m5_smoke=True, hide_console=True)
    runtime = None
    try:
        config = run / "config"
        bridge = BridgeClient(config / "mcaibuilder.json")
        wait_for(child, bridge.status, "integrated Forge", seconds=150)
        with (run / "runtime.log").open("wb") as log:
            runtime = subprocess.Popen([sys.executable, "-B", "-m", "src.architect",
                                        "--provider", "fixture", "--instance", str(options.source_instance),
                                        "--config", str(config / "mcaibuilder-architect.json"),
                                        "--blender", str(run / "missing-blender.exe")],
                                       cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        chat = Chat(run)
        fixture(child, run, "camera", [10, -60, 16])
        speak(child, chat, "@Builder 你好", "我在")
        speak(child, chat, "@Builder 帮我在眼前这块地盖一个带烟囱的小木屋，用深色橡木风格", "[批准]")
        plan_file = next((run / "architect-previews").glob("*/construction-plan.json"))
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        require(plan["building_id"].startswith("voxel_"), "The cabin used the generic voxel plan")
        require(not list((run / "architect-previews").rglob("proposed.png")), "No Blender output was available")
        require(not bridge.status().get("job"), "The player has not approved the proposal yet")

        def world_position(local):
            x, y, z = local
            x, z = ((x, z), (-z, x), (-x, -z), (z, -x))[plan["rotation"] // 90]
            return [origin + offset for origin, offset in zip(plan["origin"], (x, y, z), strict=True)]

        floor = [world_position(row["local"]) for row in plan["actions"]
                 if row["local"][1] == 0]
        protected, grass, tree, blocked = map(list, (floor[1], floor[2], floor[3], floor[0]))
        fixture(child, run, "chest", protected)
        speak(child, chat, "建吧", "保护资产")
        require(not bridge.status().get("job"), "The macro fence protected a chest added after the review")
        fixture(child, run, "air", protected)
        fixture(child, run, "grass", grass)
        fixture(child, run, "tree", tree)

        stock = Counter(row["item_id"] for row in plan["actions"])
        expected_stock = stock.copy()
        glass = stock.pop("minecraft:glass")
        wood = stock.pop("minecraft:dark_oak_planks")
        chest, second, barrel = [18, -60, 16], [19, -60, 16], [21, -60, 16]
        bricks = stock.pop("minecraft:stone_bricks")
        left = fixture(child, run, "double_chest", chest, items=items({"minecraft:stone_bricks": bricks // 2}))
        require(left["block"]["properties"]["type"] == "left", "The first storage is a joined chest")
        fixture(child, run, "stock", second, items=items({"minecraft:stone_bricks": bricks - bricks // 2}))
        fixture(child, run, "barrel", barrel, items=items({"minecraft:dark_oak_planks": wood, **stock}))
        speak(child, chat, "材料放箱子里了", f"glass ×{glass}")
        speak(child, chat, "批准", "已批准 V1")
        waiting = wait_for(child, lambda: (q if (q := bridge.progress())["job"].get("reason") == "awaiting_materials" else None),
                           "material negotiation")
        report = waiting["materials"]
        require(report["containers"] == 3 and report["requirements"]["minecraft:glass"]["missing"] == glass,
                f"Expected three physical storage inventories and the exact glass shortage: {report}")

        cow = fixture(child, run, "cow", blocked)["cow"]
        fixture(child, run, "stock", barrel, items=items({"minecraft:glass": glass}))
        speak(child, chat, "材料齐了", "附近库存够用")
        furthest = 0

        def progress():
            nonlocal furthest
            status = bridge.status()
            job = status["job"]
            furthest = max(furthest, job["temporary_blocks"])
            require(job["state"] != "failed", f"Worker stopped: {job.get('reason')}")
            if job["state"] == "paused":
                raise AssertionError(f"Worker paused: {job.get('reason')}")
            return status

        wait_for(child, lambda: (p if (p := progress())["job"]["placed"] >= 8 else None), "build while a cow shares the site", seconds=110)
        require(fixture(child, run, "release", [28, -60, 21], cow=cow)["cow_alive"], "The cow was unharmed")
        wait_for(child, lambda: (p if (p := progress())["job"]["state"] == "completed" else None),
                          "finished cabin and cleaned scaffolds", seconds=420)
        result = bridge.progress()
        job = result["job"]
        require(job["verification"]["matched"] == len(plan["actions"]) and job["temporary_blocks"] == 0,
                "Every planned block was present and temporary blocks were cleaned")
        require(furthest > 0 and result["npc"]["scaffold_dirt_collected"] >= furthest,
                "The high roof used temporary scaffolds made from soil mined on site")
        require(result["npc"]["materials"].get("minecraft:oak_log") == 2,
                "The two cleared logs were collected into the NPC inventory")
        debited = Counter()
        for trip in job["supply"]["transfers"]:
            require(trip["reach"] <= 4.5, "Every warehouse trip came from interaction range")
            debited.update(trip["items"])
        require(debited == expected_stock, f"Materials came from nearby physical containers: {debited}")
        (run / "companion-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Verified {job['verification']['matched']} planned blocks across {len(job['supply']['transfers'])} trips and {furthest} temporary blocks.", flush=True)
    finally:
        stop_runtime(runtime)
        close_game(child, run)
    verify_copy_area(options, run)


if __name__ == "__main__":
    main()
