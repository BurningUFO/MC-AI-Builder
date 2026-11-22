"""Exercise live recipe batches, physical workstations, lighting and localized ghost previews."""

from argparse import ArgumentParser
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import mkdtemp
from uuid import uuid4

from src.construction.client import BridgeClient
from tests.companion_smoke import close_game, fixture, items, speak
from tests.m1_smoke import require, wait_for
from tests.m3_smoke import Chat, signal, stop_runtime
from tools.m1_client import ROOT, TEST_ROOT, launch_client


def world(plan, local):
    x, y, z = local
    x, z = ((x, z), (-z, x), (-x, -z), (z, -x))[plan["rotation"] // 90]
    return [base + offset for base, offset in zip(plan["origin"], (x, y, z), strict=True)]


def projection(child, run, pos):
    identifier = str(uuid4())
    signal(run, ".mcaibuilder-chat-fixture", json.dumps({
        "action": "probe_preview", "request": identifier, "position": pos,
    }))

    def result():
        try:
            data = json.loads((run / ".mcaibuilder-preview-result").read_text(encoding="utf-8"))
            return data if data.get("request") == identifier else None
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            return None

    return wait_for(child, result, "client projection state", seconds=20)


def plan_at(run, version):
    return next(plan for path in (run / "architect-previews").glob("*/construction-plan.json")
                if (plan := json.loads(path.read_text(encoding="utf-8")))["building_version"] == version)


def completed(child, bridge, version, *, seconds=620):
    def result():
        status = bridge.status()
        job = status.get("job")
        if not job or job["building_version"] != version:
            return None
        require(job["state"] not in ("failed", "paused"),
                f"V{version} stopped at {job['processed']}/{job['total']}: {job.get('reason')}")
        return bridge.progress() if job["state"] == "completed" else None
    return wait_for(child, result, f"V{version} completed", seconds=seconds)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument("--source-instance", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18846)
    args = parser.parse_args()
    run = Path(mkdtemp(prefix="builder-workshop-ghost-", dir=TEST_ROOT))
    print(f"Upgrade fixture {run}", flush=True)
    child = launch_client(args.java_home, args.source_instance, run, args.port,
                          smoke=True, m4_smoke=True, m5_smoke=True, hide_console=True)
    runtime = None
    try:
        config = run / "config"
        bridge = BridgeClient(config / "mcaibuilder.json")
        wait_for(child, bridge.status, "Minecraft Forge", seconds=150)
        with (run / "runtime.log").open("wb") as log:
            runtime = subprocess.Popen([sys.executable, "-B", "-m", "src.architect",
                                        "--provider", "fixture", "--instance", str(args.source_instance),
                                        "--config", str(config / "mcaibuilder-architect.json"),
                                        "--blender", str(run / "missing-blender.exe")],
                                       cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        chat = Chat(run)
        fixture(child, run, "camera", [10, -60, 16])
        speak(child, chat, "@Builder 帮我盖一个5格宽、不出檐的人字顶云杉小木屋", "[批准]")
        plan = plan_at(run, 1)
        counts = Counter(row["item_id"] for row in plan["actions"])
        require(plan["lighting"] and counts["minecraft:spruce_stairs"] and counts["minecraft:spruce_log"],
                "The real plan contains a generated roof, frame, and lighting")
        sample = world(plan, [0, 1, 2])
        view = projection(child, run, sample)
        require(view["ghost_cells"] == len(plan["actions"]) and view["block"]["id"] == "minecraft:air"
                and not view["collides"], f"The client drew a virtual block without adding collision: {view}")
        (run / ".mcaibuilder-capture").touch()
        wait_for(child, lambda: (run / "screenshots/m1-wall-built.png").is_file(), "live preview capture")
        speak(child, chat, "/architect preview clear", "已清除")
        require(projection(child, run, sample)["ghost_cells"] == 0, "Manual clear removed the projection")
        speak(child, chat, "@Builder 预览", "[批准]")
        require(projection(child, run, sample)["ghost_cells"] > 0, "Re-review draws the projection again")

        missing = counts.pop("minecraft:stone_bricks")
        spruce = counts.pop("minecraft:spruce_log")
        raw_wood = sum(counts.pop("minecraft:spruce_" + part) for part in ("planks", "stairs", "slab"))
        lamps = counts.pop("minecraft:torch")
        chest, barrel = [18, -60, 16], [21, -60, 16]
        fixture(child, run, "double_chest", chest, items=items({
            "minecraft:spruce_log": spruce + raw_wood + 8, "minecraft:cobblestone": 15, "minecraft:coal": lamps + 1,
        }))
        fixture(child, run, "barrel", barrel, items=items({
            "minecraft:stone_bricks": missing - 1, **counts, "minecraft:glass": counts["minecraft:glass"] + 6,
        }))
        speak(child, chat, "材料齐了", "我会用")
        speak(child, chat, "建吧", "已批准 V1")
        require(projection(child, run, sample)["ghost_cells"] == 0, "Approval clears the ghost before placing real blocks")
        result = completed(child, bridge, 1)
        first = result["job"]
        lighting = first["verification"]["lighting"]
        require(first["verification"]["matched"] == len(plan["actions"]) and lighting["surfaces_checked"] > 0
                and not lighting["dark_cells"], f"All intended cells are present and surfaces have block light: {lighting}")
        crafted = {row["recipe"]: row["station"] for row in first["supply"]["recipes"]}
        expected = {"spruce_planks": "hand", "spruce_stairs": "table", "spruce_slab": "table",
                    "crafting_table": "hand", "stone": "furnace", "stone_bricks": "hand", "torch": "hand"}
        require(all(crafted.get("minecraft:" + name) == station for name, station in expected.items()),
                f"Raw, processed, and temporary station recipes: {crafted}")
        require(first["supply"]["workstations"] == 0 and first["temporary_blocks"] == 0,
                "The native workstations and any scaffolds were recovered")
        public_chat = run / "public-chat.jsonl"
        wait_for(child, lambda: public_chat.is_file() and "完工啦" in public_chat.read_text(encoding="utf-8"),
                 "public in-game completion message")
        print(f"V1. {len(plan['actions'])} blocks, {lighting['surfaces_checked']} lit surfaces, "
              f"{first['supply']['recipe_batches']} recipe batches, private collision-free projection.", flush=True)

        unrelated = world(plan, [2, 2, 0])
        fixture(child, run, "air", unrelated)
        fixture(child, run, "camera", world(plan, [0, 0, 4]), look=world(plan, [0, 1, 2]))
        speak(child, chat, "@Builder 在我看的这个位置加一扇双开落地窗", "[批准]")
        changed = plan_at(run, 2)
        before = {tuple(row["local"]): row for row in plan["actions"]}
        after = {tuple(row["local"]): row for row in changed["actions"]}
        diff = {point for point in before if before[point] != after[point]}
        require(changed["localized"] and diff
                and len({(x, z) for x, _, z in diff}) == 2
                and all(z == 2 and after[x, y, z]["id"] == "minecraft:glass" for x, y, z in diff),
                f"The localized revision changes only two columns in the gazed-at south wall: {diff}")
        speak(child, chat, "建吧", "已批准 V2")
        second = completed(child, bridge, 2, seconds=230)["job"]
        require(second["verification"]["matched"] == len(plan["actions"]) - 1
                and second["removed"] == second["placed"] == len(diff)
                and fixture(child, run, "read", unrelated)["block"]["id"] == "minecraft:air",
                "The worker changed precisely the local wall cells and left an unrelated player edit alone")
        print(f"V2. Replaced {len(diff)} window cells and preserved an unrelated player edit.", flush=True)
    finally:
        stop_runtime(runtime)
        close_game(child, run)


if __name__ == "__main__":
    main()
