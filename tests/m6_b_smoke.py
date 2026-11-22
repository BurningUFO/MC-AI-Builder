"""Verify real Forge RGB/world binding and the bounded M6-B vision protocol."""

from argparse import ArgumentParser
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import struct
from uuid import uuid4

from src.architect.codex_provider import CodexProvider
from src.architect.runtime import ArchitectClient
from src.architect.scene import scene_context_message
from src.architect.vision import analyze, read_observation
from src.construction.client import BridgeClient
from src.core.building import write_json
from tests.m1_smoke import rejected, require, wait_for, wait_ticks
from tests.m2_smoke import place_fixture_block
from tests.m5_a_smoke import PlayerChat
from tests.m6_a_smoke import (
    PLAYER, ROOT, fixture, launch, pixel_metrics, reply, restore_camera, set_pose, stop, test_observations,
)
from tools.mcai_launcher import SETUP, find_setup, isolated_pack, local_game, read_object


HOME = ROOT / ".cache" / "m6-b"
LEDGER = HOME / "codex-turns.sqlite3"
TURN_LIMIT = 3
TOKEN_LIMIT = 84_000
BLUE, WOOD = "minecraft:blue_stained_glass", "minecraft:oak_planks"
CENTER = [0, 1, -3]


def cell(context, offset):
    return next(row for row in context["cells"] if row["offset"] == offset)


def neutral(observation):
    return replace(observation, context={key: value for key, value in observation.context.items() if key != "view"})


def conflict(observation):
    context = dict(observation.context)
    context["focus_sample"] = {"offset": CENTER, "id": WOOD}
    context["cells"] = [
        {**row, "id": WOOD, "properties": {}} if row["offset"] == CENTER else row for row in context["cells"]
    ]
    return replace(observation, context=context)


class LocalVisionFixture:
    """A deterministic pixel/ground-truth check for the interpreter's I/O contract."""

    def analyze_scene_with_metrics(self, context, image, *, observation_id, allow_upload=False):
        require(not allow_upload, "The simulated interpreter never shares an image")
        width, height = struct.unpack(">II", image[16:24])
        pixels = pixel_metrics(image, width, height)
        blue = pixels["cool_fraction"] > .5
        main_material = "glass" if blue else "wood"
        color = "blue" if blue else "tan"
        focus = context.get("focus_sample")
        mismatch = focus is not None and (BLUE if blue else WOOD) != focus["id"]
        stepped = context["terrain"]["connected_relief"] > 0
        report = {
            "scene_summary": "测试场景的颜色和地面数据可供本地比对。",
            "focal_surface": {"color": color, "material": main_material, "evidence": "本地像素中心颜色统计。"},
            "visible_objects": [{"label": "中央参考墙", "source": "rgb"}],
            "terrain_assessment": {"kind": "stepped" if stepped else "flat", "note": "依据方块样本的连通站位高差。"},
            "possible_obstacles": ["参考墙占用相对窗口空间。"],
            "suggested_observations": [],
            "uncertainties": ["图片中心和预期方块不同。"] if mismatch else [],
            "evidence": [{"source": "rgb", "detail": "中心像素颜色"}, {"source": "forge", "detail": "站位高差与精确方块"}],
            "block_claims": [{"offset": focus["offset"], "id": focus["id"], "properties": []}] if focus else [],
            "consistency": "conflict" if mismatch else "consistent",
        }
        return report, {
            "provider": "local_fixture", "observation_id": observation_id,
            "context_sha256": sha256(scene_context_message(context).encode("utf-8")).hexdigest(),
            "image_sha256": sha256(image).hexdigest(),
        }


def capture_cases(child, game, bridge, client, directory, trace):
    paired = {}

    def record(direction, meta):
        observation = read_observation(client, directory, expected_id=meta["observation_id"])
        require(observation.metadata == meta, "The authenticated live pair and the captured metadata are identical")
        paired[direction] = observation

    camera_trace = {}
    npc, _ = test_observations(child, game, bridge, client, camera_trace, on_capture=record)
    blue, oak = (paired[name] for name in ("north_blue", "south_oak"))
    require(blue.context["terrain"]["connected_relief"] == oak.context["terrain"]["connected_relief"] == 0
            and blue.context["terrain"]["connected_footing"], "The connected Forge footing is flat")
    require(cell(blue.context, CENTER)["id"] == BLUE and cell(blue.context, [0, 1, 3])["id"] == WOOD
            and cell(blue.context, CENTER)["solid"], "Forge sees both actual materials and the physical wall")
    require(blue.context["view"]["yaw"] == 180 and oak.context["view"]["yaw"] == 0,
            "Opposing render cameras track their respective poses")
    trace["camera"] = camera_trace
    before = client.observe_world()
    native = place_fixture_block(child, game, (13, -60, 10))
    require(native["result"] in ("SUCCESS", "CONSUME"), "The fixture creates the step through native placement")
    trace["changed_scene_rejected"] = rejected(client.observe_world, 409, "world_observation_stale")
    require(client.observation()["observation_id"] == before["observation_id"],
            "An old image can still be viewed locally without serving a stale pair")
    chat = PlayerChat(game)
    chat.submit(child, "/tp @s " + " ".join(map(str, PLAYER)) + " 0 0")
    wait_ticks(child, bridge, bridge.status()["game_tick"] + 12)
    set_pose(child, game, npc, 180)
    meta = reply(child, chat, client)
    raised = read_observation(client, directory, expected_id=meta["observation_id"])
    require(raised.context["terrain"]["connected_relief"] == 1
            and [0, 1, -1] in raised.context["terrain"]["connected_footing"]
            and cell(raised.context, [0, 0, -1])["id"] == "minecraft:cobblestone",
            "The authoritative window sees the new one-block step and its reachable footing")
    restore_camera(child, game)
    return npc, blue, oak, raised


def manifest(observation):
    world = observation.metadata["world"]
    return {
        "observation_id": observation.id, "image_sha256": observation.metadata["image_sha256"],
        "size": [observation.metadata["width"], observation.metadata["height"]],
        "world_fingerprint": world["fingerprint"], "frame_tick": observation.metadata["game_tick"],
        "forge_tick": world["game_tick"], "frame_tick_delta": world["frame_tick_delta"],
        "sample_span_ticks": world["sample_span_ticks"], "sample_span_ms": world["sample_span_ms"],
        "yaw": observation.context["view"]["yaw"], "relief": observation.context["terrain"]["connected_relief"],
        "out_of_scope_cells": observation.context["unknown_outside_scope"],
        "unloaded_cells": observation.context["unknown_unloaded"],
        "capture_ms": observation.metadata["capture_ms"], "readback_ms": observation.metadata["readback_ms"],
        "frame_interval_ms": observation.metadata["frame_interval_ms"], "fps": observation.metadata["fps"],
    }


def analyze_cases(provider, blue, oak, raised, trace, *, share=False):
    first, second = neutral(blue), neutral(oak)
    require(scene_context_message(first.context) == scene_context_message(second.context)
            and blue.metadata["image_sha256"] != oak.metadata["image_sha256"],
            "Only the RGB content changes in the causal-control pair")
    third = conflict(raised)
    if share:
        require(all((scene.metadata["width"], scene.metadata["height"]) == (731, 432)
                    for scene in (blue, oak, raised))
                and len({sha256(scene.png).hexdigest() for scene in (blue, oak, raised)}) == 3,
                "The three distinct PNGs match the approved dimensions and test scenes")
    trace["observations"] = {name: manifest(row) for name, row in (
        ("blue", blue), ("oak", oak), ("raised", raised)
    )}
    trace["same_text_control"] = True
    trace["reports"] = {}
    for name, observation in (("blue", first), ("oak", second), ("conflict", third)):
        result = analyze(observation, provider, allow_upload=share)
        if name == "conflict":
            result["deliberately_conflicting_expected_cell"] = third.context["focus_sample"]
        trace["reports"][name] = result
    a, b, c = (trace["reports"][name]["scene"] for name in ("blue", "oak", "conflict"))
    require(a["focal_surface"]["color"] == "blue" and a["focal_surface"]["material"] == "glass"
            and b["focal_surface"]["color"] in ("tan", "brown") and b["focal_surface"]["material"] == "wood",
            "Identical orientation-neutral text produces different correct visual answers")
    require(a["terrain_assessment"]["kind"] == b["terrain_assessment"]["kind"] == "flat"
            and c["terrain_assessment"]["kind"] in ("stepped", "sloped"),
            "Flat and raised scenes are distinguished with the Forge footing data")
    require(c["focal_surface"]["color"] == "blue" and c["consistency"] == "conflict" and c["uncertainties"],
            "The conflicting text is reported as uncertain rather than replacing visual evidence")
    for report in (a, b, c):
        sources = {line["source"] for line in report["evidence"]}
        require("both" in sources or {"rgb", "forge"} <= sources, "The report distinguishes RGB and Forge evidence")


def test_chunk_loss(child, game, client, npc, trace):
    fixture(child, game, "drop_chunk_on_switch", npc=npc)
    result = reply(child, PlayerChat(game), client)
    restored = restore_camera(child, game)
    require(result["capture_status"] == "captured" and result["client_unloaded_cells"] > 0
            and result.get("world_error") == "world_changed" and restored["observed_chunk_drops"] == 1,
            "The actual unloaded client chunk is marked unknown, and a mismatched pair is withheld")
    trace["unloaded_chunk"] = {
        "client_unknown_cells": result["client_unloaded_cells"], "reason": result["world_error"],
        "paired_read_rejected": rejected(client.observe_world, 409, "world_observation_required"),
    }


def exercise(profile, share=False):
    if profile != "forge" and share:
        raise ValueError("Approval covers only the synthetic Forge scene for this acceptance.")
    settings = find_setup(read_object(SETUP))
    pack = isolated_pack() if profile == "bmc4" else None
    if profile == "bmc4":
        require(pack is not None, "The isolated BMC4 pack is required")
    game = local_game(f"m6-b-{profile}-{uuid4().hex[:8]}")
    write_json(HOME / f"instance-{profile}{'-codex' if share else ''}.json", {"game": str(game)})
    output = game / "m6-b-acceptance.json"
    directory = ROOT / ".cache" / "observations" / game.name
    child = launch(settings, game, 18784 if profile == "forge" else 18785, pack)
    trace = {"profile": profile, "model_mode": "codex" if share else "local_fixture", "passed": False}
    provider = (CodexProvider(timeout=75, max_turns=TURN_LIMIT, budget_log=LEDGER, max_tokens=TOKEN_LIMIT)
                if share else LocalVisionFixture())
    try:
        bridge = BridgeClient(game / "config" / "mcaibuilder.json", timeout=3)
        scoped = ArchitectClient(game / "config" / "mcaibuilder-architect.json", timeout=3)
        wait_for(child, bridge.health, "real integrated Minecraft server", seconds=630 if pack else 170)
        wait_ticks(child, bridge, bridge.status()["game_tick"] + 25)
        trace["idle_rejected"] = rejected(scoped.observe_world, 409, "world_observation_required")
        trace["scope_rejected"] = rejected(lambda: bridge._call("architect/observe_world"), 401, "unauthorized")
        npc, blue, oak, raised = capture_cases(child, game, bridge, scoped, directory, trace)
        analyze_cases(provider, blue, oak, raised, trace, share=share)
        if not share:
            test_chunk_loss(child, game, scoped, npc, trace)
        stop(child, game)
        trace["native_save"] = True
        trace["passed"] = True
        print(f"M6-B {profile} {trace['model_mode']}: paired observations and comparison verified.", flush=True)
    except Exception as error:
        trace.update(error=type(error).__name__ + ": " + str(error))
        raise
    finally:
        if share:
            trace["ledger"] = provider.budget.records()
        write_json(output, trace)
        if child.poll() is None:
            stop(child, game)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=("forge", "bmc4"))
    parser.add_argument("--share-images", action="store_true",
                        help="Run the externally approved three-turn Codex image acceptance")
    options = parser.parse_args()
    exercise(options.profile, options.share_images)


if __name__ == "__main__":
    main()
