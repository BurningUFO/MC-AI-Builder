"""Exercise the Blender project library in a fresh, isolated BMC4 client and save."""

from collections import Counter
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory, mkdtemp
import tomllib
from zipfile import ZipFile

from src.assets.archives import MODS

from src.assets.projects import BUILT_INS
from src.construction.client import BridgeClient
from src.core.building import Building, write_json
from tests.builder_upgrade_smoke import completed, projection, world
from tests.companion_smoke import fixture, items, speak
from tests.m1_smoke import require, wait_for
from tests.m3_smoke import Chat, stop_runtime
from tools.m1_client import ROOT, TEST_ROOT, configure_world, launch_client


def stage_building_mods(instance, target):
    """Use the actual BMC4 building mods plus the Forge dependencies declared in their packages."""
    installed = {}
    for jar in (instance / "mods").glob("*.jar"):
        with ZipFile(jar) as archive:
            try:
                metadata = tomllib.loads(archive.read("META-INF/mods.toml").decode("utf-8-sig"))
            except (KeyError, tomllib.TOMLDecodeError):
                continue
        for mod in metadata.get("mods", []):
            id = mod["modId"]
            required = {dep["modId"] for dep in metadata.get("dependencies", {}).get(id, ())
                        if dep.get("mandatory")} - {"forge", "minecraft"}
            installed[id] = (jar, required)
    pending = set(MODS) | {"diagonalfences", "diagonalwalls", "amendments"}
    seen = set()
    selected = set()
    while pending:
        id = pending.pop()
        if id in seen:
            continue
        seen.add(id)
        if id not in installed and id in {"diagonalblocks", "puzzlesaccessapi"}:
            # These libraries are embedded inside their Forge parent JARs in BMC4.
            continue
        require(id in installed, f"The BMC4 source is missing required test mod {id}.")
        jar, required = installed[id]
        selected.add(jar)
        pending.update(required - seen)
    mods = target / "mods"
    mods.mkdir()
    (target / ".mcaibuilder-test").touch()
    for jar in sorted(selected):
        shutil.copyfile(jar, mods / jar.name)
    return len(selected)

def sample():
    building = Building("BMC4 状态样板")
    for x in range(6):
        for z in range(3):
            building.place((x, 0, z), "minecraft:stone_bricks")
    building.place((0, 1, 0), "chipped:stacked_spruce_planks")
    for height, half in ((1, "lower"), (2, "upper")):
        building.place((1, height, 0), "biomesoplenty:jacaranda_door", facing="north",
                       half=half, hinge="right", open="false", powered="false")
    building.place((2, 1, 0), "quark:red_shingles_stairs", facing="west", half="top",
                   shape="straight", waterlogged="false")
    building.place((3, 1, 0), "quark:red_shingles_slab", type="top", waterlogged="false")
    building.place((4, 1, 0), "biomesoplenty:stripped_jacaranda_log", axis="x")
    building.place((5, 1, 0), "quark:red_shingles")
    building.place((0, 1, 2), "handcrafted:spruce_chair", color="red", facing="east", waterlogged="false")
    building.place((1, 1, 2), "minecraft:barrel", facing="up", open="false")
    building.place((2, 1, 2), "biomesoplenty:potted_jacaranda_sapling")
    building.place((3, 1, 2), "minecraft:glass_pane", north="false", east="true", south="false", west="false",
                   waterlogged="false")
    building.place((4, 1, 2), "minecraft:glass_pane", north="false", east="false", south="false", west="true",
                   waterlogged="false")
    building.place((5, 1, 2), "minecraft:spruce_fence", north="false", east="false", south="false", west="false",
                   waterlogged="false")
    building.place((0, 2, 0), "minecraft:spruce_trapdoor", facing="south", half="bottom",
                   open="true", powered="false", waterlogged="false")
    return building


def start(run, workspace, options, blender, label):
    stop = run / f".stop-{label}"
    with (workspace / f"{label}.log").open("wb") as log:
        process = subprocess.Popen([sys.executable, "-B", "-m", "src.architect",
                                    "--provider", "fixture", "--instance", str(options.source_instance),
                                    "--config", str(run / "config/mcaibuilder-architect.json"),
                                    "--project-root", str(workspace), "--blender", str(blender), "--stop-file", str(stop)],
                                   cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    return process, stop


def stop_agent(agent):
    if agent is not None:
        process, stop = agent
        if process.poll() is None:
            stop.touch()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                stop_runtime(process)
        stop.unlink(missing_ok=True)


def plans(run, source=None):
    return [data for path in (run / "architect-previews").glob("*/construction-plan.json")
            if (data := json.loads(path.read_text(encoding="utf-8"))).get("source_project") == source]


def look_for_project(child, run, chat, workspace, phrase, name, material):
    speak(child, chat, phrase, "[批准]")
    plan, = plans(run, name)
    expected = Building.read(workspace / "projects" / name / "building.json")
    require(any(f"共 {len(expected.blocks)} 个方块" in line and f"previews/{name}/" in line for line in chat.messages()),
            f"The chat loaded the precise {name} project and described its preview location")
    pos = world(plan, next(row["local"] for row in plan["actions"] if row["id"] == material))
    overlay = projection(child, run, pos)
    require(overlay["projection"]["id"] == material and not overlay["collides"]
            and overlay["ghost_cells"] == len(expected.blocks),
            f"The private overlay displayed the source modded geometry without collision: {overlay}")
    return plan


def exercise(options):
    workspace = Path(mkdtemp(prefix="bmc4-project-library-", dir=TEST_ROOT))
    print(f"BMC4 project sandbox {workspace}", flush=True)
    for name in BUILT_INS:
        directory = workspace / "projects" / name
        directory.mkdir(parents=True)
        shutil.copyfile(ROOT / "projects" / name / "building.json", directory / "building.json")
    model = sample()
    model.write(workspace / "projects" / "bmc4_state_fixture" / "building.json")
    with (TemporaryDirectory(prefix="client-", dir=workspace) as temp,
          TemporaryDirectory(prefix="pack-", dir=workspace) as pack_temp):
        run = Path(temp)
        pack = options.bmc4_pack
        if pack is None:
            pack = Path(pack_temp)
            count = stage_building_mods(options.source_instance, pack)
            print(f"Staged {count} BMC4 building mod and dependency JARs read-only.", flush=True)
        configure_world(run, options.port, architect=True, survival=True)
        config = run / "config/mcaibuilder.json"
        authorization = json.loads(config.read_text(encoding="utf-8"))
        authorization["area"] = {"min": [0, -64, 0], "max": [63, -32, 63]}
        authorization["npc_spawn"] = [4, -60, 34]
        write_json(config, authorization)
        child = launch_client(options.java_home, options.source_instance, run, options.port, smoke=True,
                              m4_smoke=True, m5_smoke=True, bmc4=True, pack_source=pack,
                              project_root=workspace, copy_pack_defaults=options.bmc4_pack is not None, hide_console=True)
        agent = None
        bridge = BridgeClient(config)
        try:
            wait_for(child, bridge.status, "isolated BMC4 Forge server", seconds=600)
            chat = Chat(run)
            agent = start(run, workspace, options, workspace / "absent-blender.exe", "without-blender")
            fixture(child, run, "camera", [28, -60, 36])
            speak(child, chat, "@Builder 你有哪些现成的建筑？", "木石平桥")
            look_for_project(child, run, chat, workspace, "@Builder 帮我建一下 medieval_house",
                             "medieval_house", "chipped:stacked_spruce_planks")
            speak(child, chat, "@Builder 取消", "已放下")
            look_for_project(child, run, chat, workspace, "@Builder 复现附魔图书馆",
                             "enchanting_library", "biomesoplenty:magic_log")
            require(any("twigs:birch_table ×2" in line for line in chat.messages()),
                    "The entire long material list reached the reviewing player's chat")
            speak(child, chat, "取消", "已放下")
            require(not bridge.status().get("job"), "Both historic projects remained at review before approval")
            print("Loaded and projected 1478 medieval and 1819 library cells using the real mod registry.", flush=True)

            stop_agent(agent)
            agent = start(run, workspace, options, ROOT / "tools/blender/blender.exe", "with-blender")
            speak(child, chat, "@Builder 设计一个带斜顶的深色橡木凉亭", "[批准]")
            pavilion, = plans(run)
            archive, = (workspace / "projects").glob("custom_pavilion_*")
            require((archive / "building.json").is_file(), "The pavilion was archived before Blender delivered links")
            first = projection(child, run, world(pavilion, pavilion["actions"][0]["local"]))
            require(first["ghost_cells"] > 0, "The in-game preview was available during asynchronous rendering")
            link_path = run / "project-links.jsonl"
            previews = workspace / "previews" / archive.name
            wait_for(child, lambda: link_path.is_file() and archive.name in link_path.read_text(encoding="utf-8"),
                     "clickable archive links", seconds=150)
            links = json.loads(link_path.read_text(encoding="utf-8").splitlines()[-1])
            require({Path(file).name for file in links["files"]} == {"01-front.png", archive.name + ".blend"}
                    and all(Path(file).parent == previews for file in links["files"]), f"Native client OPEN_FILE links: {links}")
            require(len(list(previews.glob("*.png"))) == 4 and (previews / "verification.json").is_file(),
                    "The standard pipeline produced four verified camera renders")
            speak(child, chat, "取消", "已放下")
            stop_agent(agent)
            agent = start(run, workspace, options, workspace / "absent-blender.exe", "replica")

            plan = look_for_project(child, run, chat, workspace, "@Builder 把 bmc4_state_fixture 朝东盖在这",
                                    "bmc4_state_fixture", "quark:red_shingles_stairs")
            require(plan["rotation"] == 90, "The project honored the requested direction")
            stock = Counter(block.state.block_id for block in model.blocks.values())
            stock["biomesoplenty:jacaranda_door"] = 1
            del stock["biomesoplenty:potted_jacaranda_sapling"]
            stock.update({"minecraft:flower_pot": 1, "biomesoplenty:jacaranda_sapling": 1})
            chest = [35, -60, 38]
            stocked = fixture(child, run, "mod_chest", chest, block_id="quark:spruce_chest", items=items(stock))
            require(stocked["stock"] == stock, "The real Quark chest contains the exact placement items")
            speak(child, chat, "材料齐了", "附近库存够用")
            speak(child, chat, "建吧", "已批准 V1")
            require(projection(child, run, world(plan, [0, 1, 0]))["ghost_cells"] == 0,
                    "Approval removed the private projection")
            state = completed(child, bridge, 1, seconds=290)
            job = state["job"]
            require(job["source_project"] == "bmc4_state_fixture" and job["verification"]["matched"] == len(model.blocks),
                    f"Every world state equals its rotated source state: {job.get('verification')}")
            moved = Counter()
            for transfer in job["supply"]["transfers"]:
                require(transfer["warehouse_position"] == chest and transfer["reach"] <= 4.5,
                        "The FakePlayer navigated to the physical Quark chest")
                moved.update(transfer["items"])
            require(moved == stock and not fixture(child, run, "read", chest).get("stock"),
                    f"Exact real material consumption, including the two-part door and pot: {moved}")
            write_json(workspace / "replica-verification.json", job["verification"])
            print(f"Archived a real .blend and four views. Built all {len(model.blocks)} source states using Quark storage.", flush=True)
        finally:
            stop_agent(agent)
            try:
                if child.poll() is None:
                    (run / ".mcaibuilder-stop").touch()
                    child.wait(timeout=150)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=30)
                for source, name in ((run / "logs/latest.log", "game.log"), (run / "architect-chat.jsonl", "chat.jsonl")):
                    if source.is_file():
                        shutil.copyfile(source, workspace / name)
    print("Closed the isolated client and cleaned its temporary world and Agent sessions.", flush=True)
