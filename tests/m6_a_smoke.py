"""Capture the same real Fake Player facing two distinct walls in an isolated Forge client."""

from argparse import ArgumentParser
import json
import math
from pathlib import Path
import struct
from time import sleep
from uuid import uuid4
import zlib

from src.architect.observations import observation_image
from src.architect.runtime import ArchitectClient
from src.construction.client import BridgeClient
from src.construction.plan import make_plan
from src.core.building import Building, write_json
from tests.m1_smoke import rejected, require, wait_for, wait_terminal, wait_ticks
from tests.m3_smoke import signal
from tests.m5_a_smoke import PlayerChat
from tools.m1_client import ROOT, launch_client
from tools.mcai_launcher import SETUP, find_setup, isolated_pack, local_game, read_object


HOME = ROOT / ".cache" / "m6-a"
POSE = (13.5, -60, 11.5)
PLAYER = (13.5, -60, 22.5)
PORTS = {"forge": 18782, "bmc4": 18783}


def fixture(child, game, action, **options):
    identity = uuid4().hex
    payload = {"request": identity, "action": action, **options}
    signal(game, ".mcaibuilder-camera-fixture.json", json.dumps(payload))

    def response():
        state = read_object(game / ".mcaibuilder-camera-proof.json")
        return state if state.get("request") == identity else None

    return wait_for(child, response, f"fixture response to {action}", seconds=80)


def start_worker():
    model = Building("Isolated camera worker")
    model.place((0, 0, 0), "minecraft:stone_bricks")
    return make_plan(model, building_id="camera-lab", version=1, origin=(4, -60, 13))


def restore_camera(child, game):
    state = fixture(child, game, "client")
    require(state["camera_is_player"] and state["rendered_camera_is_player"]
            and state["camera_type"] == "THIRD_PERSON_BACK", "Both active and rendered camera returned to the prior player view")
    return state


def reply(child, chat, scoped):
    prior = scoped.observation().get("observation_id")
    chat.send(child, "/architect observe", "已请求 NPC 观测", timeout=85)

    def ready():
        result = scoped.observation()
        return result if result.get("observation_id") != prior and result["capture_status"] != "pending" else None

    return wait_for(child, ready, "the result of a new camera request", seconds=22)


def paeth(a, b, c):
    estimate = a + b - c
    values = (a, b, c)
    return min(values, key=lambda value: abs(estimate - value))


def pixel_metrics(image, width, height):
    """Decode ordinary RGB PNG scanlines independently of Java and examine the center of the frame."""
    offset = 8
    chunks = []
    while offset < len(image):
        size = int.from_bytes(image[offset:offset + 4], "big")
        body = image[offset + 4:offset + 8 + size]
        crc = int.from_bytes(image[offset + 8 + size:offset + 12 + size], "big")
        require(zlib.crc32(body) == crc, "The PNG chunk checksum is valid")
        if body[:4] == b"IDAT":
            chunks.append(body[4:])
        offset += size + 12
    scanlines = zlib.decompress(b"".join(chunks))
    stride = width * 3
    require(len(scanlines) == height * (stride + 1), "The PNG pixel data is complete")
    above = bytearray(stride)
    colors = set()
    warm = cool = 0
    total = 0
    for y in range(height):
        index = y * (stride + 1)
        mode = scanlines[index]
        require(mode < 5, "Supported PNG filter")
        row = bytearray(scanlines[index + 1:index + 1 + stride])
        if mode:
            for x, val in enumerate(row):
                a, b, c = (row[x - 3] if x >= 3 else 0), above[x], (above[x - 3] if x >= 3 else 0)
                delta = (a if mode == 1 else b if mode == 2 else (a + b) // 2 if mode == 3 else paeth(a, b, c))
                row[x] = (val + delta) & 0xff
        above = row
        if int(height * .35) <= y < int(height * .65):
            for x in range(int(width * .35) * 3, int(width * .65) * 3, 3):
                r, g, b = row[x:x + 3]
                colors.add((r, g, b))
                warm += r > b + 20 and g > b
                cool += b > r + 20 and b > g
                total += 1
    return {"center_distinct_colors": len(colors), "warm_fraction": warm / total, "cool_fraction": cool / total}


def angle_difference(first, second):
    return abs((first - second + 180) % 360 - 180)


def set_pose(child, game, npc, yaw):
    expected = fixture(child, game, "pose", npc=npc, yaw=yaw, pitch=0)

    def tracked_pose():
        state = fixture(child, game, "client", entity_id=expected["entity_id"])
        angle = state.get("tracked_yaw")
        return state if angle is not None and angle_difference(angle, expected["yaw"]) < 1 else None

    wait_for(child, tracked_pose, "the real NPC head rotation to reach the client", seconds=18)
    return expected


def check_image(meta, directory, npc, expected, *, player):
    require(meta["capture_status"] == "captured", f"Capture failed: {meta.get('capture_error')}")
    require(meta["npc_uuid"] == npc and meta["camera_source"] == "npc_first_person"
            and meta["dimension"] == "minecraft:overworld" and meta["camera_restored"]
            and meta["rendered_frames_after_switch"] >= 2, "The renderer used the live NPC first-person camera")
    require(math.dist(meta["position"], expected["npc_position"]) < .11
            and math.dist(meta["eye_position"], expected["npc_eye"]) < .11
            and math.dist(meta["position"], player) > 6
            and angle_difference(meta["yaw"], expected["yaw"]) < 1
            and angle_difference(meta["yaw"], meta["server_request_yaw"]) < 2.01
            and abs(meta["pitch"] - expected["pitch"]) < 1,
            f"Capture pose matches Forge NPC rather than player: {meta['yaw']} vs {expected['yaw']}")
    require(20 < meta["fov"] < 150 and abs(meta["game_tick"] - expected["game_tick"]) < 150
            and meta["readback_ms"] >= 0 and meta["capture_ms"] < 5000,
            "FOV, timings and tick describe the rendered frame")
    image = observation_image(directory, meta)
    pixels = pixel_metrics(image, meta["width"], meta["height"])
    require(pixels["center_distinct_colors"] >= 12, f"Image center was blank: {pixels}")
    require(read_object(directory / (meta["observation_id"] + ".json")) == meta,
            "On-disk metadata and scoped HTTP response match")
    return pixels


def test_observations(child, game, bridge, scoped, trace):
    chat = PlayerChat(game)
    directory = ROOT / ".cache" / "observations" / game.name
    geometry = fixture(child, game, "panels")
    require(geometry["native_reference_placements"] == 42, "The real fixture player placed the two reference panels")
    started = bridge.submit(start_worker())
    end = wait_terminal(child, bridge)
    job = end["job"]
    npc = end["npc"]["npc_id"]
    require(npc == started["npc"]["npc_id"] and end["npc"]["entity_type"] == "players:fake_player"
            and job["state"] == "completed" and job["verification"]["passed"]
            and job["actions"][0]["interaction_evidence"]["place_events"] == 1,
            "The existing executor spawned and used the original Fake Player")
    chat.submit(child, f"/tp {npc} " + " ".join(map(str, POSE)))
    chat.submit(child, "/tp @s " + " ".join(map(str, PLAYER)) + " 0 0")
    wait_ticks(child, bridge, bridge.status()["game_tick"] + 15)
    camera = fixture(child, game, "third_person")
    require(camera["camera_is_player"] and math.dist(camera["position"], PLAYER) < .11,
            "Test player began in a separate third-person pose")
    before = fixture(child, game, "world", npc=npc)
    require(math.dist(before["npc_position"], POSE) < .11, "The real NPC stands between the two completed test panels")
    captures = []
    for direction, yaw in (("north_blue", 180), ("south_oak", 0)):
        expected = set_pose(child, game, npc, yaw)
        meta = reply(child, chat, scoped)
        metrics = check_image(meta, directory, npc, expected, player=PLAYER)
        restored = restore_camera(child, game)
        captures.append({"scene": direction, "observation": meta, "pixels": metrics, "restored": restored})
    blue, oak = captures
    require(blue["observation"]["image_sha256"] != oak["observation"]["image_sha256"]
            and blue["observation"]["render_frame"] < oak["observation"]["render_frame"]
            and blue["pixels"]["cool_fraction"] - oak["pixels"]["cool_fraction"] > .15
            and oak["pixels"]["warm_fraction"] - blue["pixels"]["warm_fraction"] > .25,
            f"Opposite views show the expected distinct materials: {[row['pixels'] for row in captures]}")
    after = fixture(child, game, "world", npc=npc)
    require(all(before[key] == after[key] for key in ("blocks_sha256", "inventory", "npc_position", "player_position")),
            "Image acquisition did not move either entity, mutate blocks or spend inventory")
    trace.update(captures=captures, capture_world_unchanged=True, plan_placements=job["placed"],
                 reference_placements=geometry["native_reference_placements"])
    write_json(game / "m6-a-acceptance.json", trace)
    return npc, before


def stop(child, game):
    if child.poll() is None:
        signal(game, ".mcaibuilder-stop", "")
        require(child.wait(timeout=150) == 0, "The isolated client exited normally")
        text = (game / "console.log").read_bytes()
        require(b"All dimensions are saved" in text and b"normal save flow" in text,
                "The integrated server completed Minecraft's native save flow")


def test_failures(child, game, bridge, scoped, npc, before, trace):
    chat = PlayerChat(game)
    directory = ROOT / ".cache" / "observations" / game.name
    original = sorted(path.name for path in directory.glob("*.png"))
    fixture(child, game, "drop_on_switch", npc=npc)
    result = reply(child, chat, scoped)
    camera = restore_camera(child, game)
    require(result.get("capture_error") == "npc_not_visible" and camera["tracked_entity_drops"] == 1,
            f"Losing the tracked NPC after switching restored the camera: {result}")
    sleep(.4)
    missing = reply(child, chat, scoped)
    require(missing.get("capture_error") == "npc_not_visible"
            and sorted(path.name for path in directory.glob("*.png")) == original,
            "An unavailable client-side NPC never yields an older image")
    chat.submit(child, "/gamemode spectator")
    chat.submit(child, "/execute in minecraft:the_nether run tp @s 0 105 0")
    wait_for(child, lambda: fixture(child, game, "client")["dimension"] == "minecraft:the_nether",
             "test player to join the Nether", seconds=65)
    chat.send(child, "/architect observe", "architect_forbidden")
    require(scoped.observation()["observation_id"] == missing["observation_id"],
            "An out-of-dimension request never starts a capture")
    chat.submit(child, "/execute in minecraft:overworld run tp @s " + " ".join(map(str, PLAYER)))
    wait_for(child, lambda: fixture(child, game, "client")["dimension"] == "minecraft:overworld",
             "test player to return to the overworld", seconds=65)
    chat.submit(child, "/gamemode creative")
    after = fixture(child, game, "world", npc=npc)
    require(all(before[key] == after[key] for key in ("blocks_sha256", "inventory", "npc_position")),
            "Failed requests did not mutate the server's NPC or scene")
    trace.update(failures={"lost_tracking_after_switch": result["capture_error"], "restored": camera,
                          "untracked_target": missing["capture_error"], "different_dimension_denied": True})


def launch(settings, game, port, pack):
    return launch_client(settings["java"], settings["instance"], game, port, architect=True,
                         bmc4=bool(pack), pack_source=pack, smoke=True, m5_smoke=True, camera_smoke=True,
                         managed=True, hide_console=True)


def exercise(profile):
    settings = find_setup(read_object(SETUP))
    pack = isolated_pack() if profile == "bmc4" else None
    if profile == "bmc4":
        require(pack is not None, "An existing isolated BMC4 pack is required")
    game = local_game(f"m6-a-{profile}-{uuid4().hex[:8]}")
    write_json(HOME / f"instance-{profile}.json", {"game": str(game)})
    port = PORTS[profile]
    child = launch(settings, game, port, pack)
    trace = {"profile": profile, "passed": False}
    try:
        config = game / "config"
        bridge = BridgeClient(config / "mcaibuilder.json", timeout=3)
        scoped = ArchitectClient(config / "mcaibuilder-architect.json", timeout=3)
        wait_for(child, bridge.health, "real integrated Minecraft server", seconds=630 if pack else 170)
        wait_ticks(child, bridge, bridge.status()["game_tick"] + 25)
        require(scoped.observation()["capture_status"] == "idle", "Observation starts with no results")
        trace["admin_is_not_architect"] = rejected(lambda: bridge._call("architect/observation"), 401, "unauthorized")
        npc, before = test_observations(child, game, bridge, scoped, trace)
        if profile == "forge":
            test_failures(child, game, bridge, scoped, npc, before, trace)
            stop(child, game)
            child = launch(settings, game, port, pack)
            wait_for(child, bridge.health, "isolated client restart", seconds=180)
            wait_ticks(child, bridge, bridge.status()["game_tick"] + 25)
            require(scoped.observation()["capture_status"] == "idle", "Restart clears the old in-flight result")
            chat = PlayerChat(game)
            chat.submit(child, "/tp @s " + " ".join(map(str, PLAYER)) + " 0 0")
            wait_ticks(child, bridge, bridge.status()["game_tick"] + 10)
            fixture(child, game, "third_person")
            expected = set_pose(child, game, npc, 180)
            meta = reply(child, chat, scoped)
            check_image(meta, ROOT / ".cache" / "observations" / game.name, npc, expected,
                        player=expected["player_position"])
            require(meta["observation_id"] not in {row["observation"]["observation_id"] for row in trace["captures"]},
                    "The reopened game provided a fresh image and identity")
            restore_camera(child, game)
            trace["reopened"] = True
        stop(child, game)
        trace["native_save"] = True
        trace["passed"] = True
        print(f"M6-A {profile}: two actual NPC RGB views, pose and restored camera verified.", flush=True)
    except Exception as error:
        trace["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        write_json(game / "m6-a-acceptance.json", trace)
        if child.poll() is None:
            stop(child, game)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=tuple(PORTS))
    exercise(parser.parse_args().profile)


if __name__ == "__main__":
    main()
