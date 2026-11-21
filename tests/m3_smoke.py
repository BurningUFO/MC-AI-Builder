"""Drive real Minecraft chat, approvals and construction with a deterministic HTTP model."""

from argparse import ArgumentParser
from collections import Counter
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import mkdtemp
from threading import Event, Thread
from time import sleep

from src.architect.providers import FixtureProvider, HttpProvider
from src.architect.runtime import ArchitectClient
from src.construction.client import BridgeClient
from src.core.building import Building, write_json
from tests.m1_smoke import rejected, require, wait_for, wait_placed, wait_terminal, wait_ticks
from tests.m2_smoke import place_fixture_block, stop_cleanly
from tools.m1_client import ROOT, TEST_ROOT, launch_client

UUID_PATTERN = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"


class ModelFixture(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        self.received = []
        self.slow_started = Event()
        self.slow_release = Event()
        super().__init__(("127.0.0.1", 0), ModelHandler)


class ModelHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        context = json.loads(data["messages"][1]["content"])
        self.server.received.append(context)
        text = context["message"]
        if text == "模拟模型超时":
            sleep(2.5)
        if text == "慢一点，帮我建窗墙":
            self.server.slow_started.set()
            self.server.slow_release.wait(0.8)
        if text == "模拟非法方块":
            output = {"action": "create_design", "kind": "window_wall", "changes": {"wood": "minecraft:tnt"}}
        elif text == "模拟越界尺寸":
            output = {"action": "create_design", "kind": "window_wall", "changes": {"width": 999}}
        elif text == "模拟模型绕过审批":
            output = {"action": "approve", "command": "setblock"}
        elif text == "模拟过期意图":
            output = {"action": "revise_design", "kind": "window_wall", "base_version": 1,
                      "changes": {"wood": "minecraft:oak_planks"}}
        else:
            output = FixtureProvider().decide(context)
        content = {"choices": [{"message": {"content": json.dumps(output)}}]}
        body = json.dumps(content).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def log_message(self, *args):
        pass


def signal(run, name, content):
    path = run / name
    stage = path.with_suffix(".fixture-tmp")
    stage.write_text(content, encoding="utf-8")
    stage.replace(path)


class Chat:
    def __init__(self, run):
        self.run = run
        self.file = run / "architect-chat.jsonl"
        self.trace = []

    def messages(self):
        if not self.file.is_file():
            return []
        try:
            return [json.loads(row)["message"] for row in self.file.read_text(encoding="utf-8").splitlines()]
        except json.JSONDecodeError:
            return []

    def send(self, child, command, expected, *, guest=False, timeout=70):
        already = len(self.messages())
        if guest:
            signal(self.run, ".mcaibuilder-guest-approve", command)
        else:
            signal(self.run, ".mcaibuilder-architect-command", f"architect {command}")
        lines = wait_for(child, lambda: any(expected in row for row in self.messages()[already:]),
                         f"Architect chat reply containing {expected!r}", seconds=timeout)
        require(lines, "The game displayed the response")
        received = self.messages()[already:]
        self.trace.append({
            "from": "unlisted fixture identity" if guest else "logged-in test player",
            "input": command, "responses": received,
        })
        return received

    def card(self, child, text, *, can_approve=True):
        replies = self.send(child, text, "[拒绝 ")
        button = replies[-1]
        matches = re.search(r"\[拒绝 (" + UUID_PATTERN + r")\]", button)
        preview = next((line.split("预览 ", 1)[1].split("（", 1)[0] for line in replies
                        if "预览 architect-previews/" in line), None)
        require(matches and preview and ("[批准 " in button) == can_approve, "Expected a preview and correctly gated buttons")
        path = self.run / preview
        require(path.is_file() and path.stat().st_size > 1000, "Opening the Blender project is possible")
        for name in ("proposed.png", "proposed-rear.png"):
            require((path.parent / name).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), "Both proposal angles were rendered")
        return {
            "code": matches[1], "report": json.loads((path.parent / "review.json").read_text(encoding="utf-8")),
            "building": Building.read(path.parent.parent / "building.json"),
        }


def start_runtime(run, source, provider, number):
    model_env = dict(os.environ)
    timeout = "45"
    if provider is not None:
        model_env.update(MC_ARCHITECT_BASE_URL=f"http://127.0.0.1:{provider.server_port}/v1",
                         MC_ARCHITECT_MODEL="deterministic-fixture",
                         MC_ARCHITECT_API_KEY="fixture-key-not-a-credential")
        timeout = "1.2"
    args = [sys.executable, "-B", "-m", "src.architect", "--config", str(run / "config" / "mcaibuilder-architect.json"),
            "--instance", str(source), "--timeout", timeout]
    with (run / f"runtime-{number}.log").open("wb") as log:
        return subprocess.Popen(args, cwd=ROOT, env=model_env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)


def stop_runtime(process):
    if process and process.poll() is None:
        process.terminate()
        process.wait(timeout=12)


def assert_pending_without_actions(child, bridge):
    stalled = bridge.status()
    wait_ticks(child, bridge, stalled["game_tick"] + 15)
    second = bridge.status()
    a = stalled.get("job")
    b = second.get("job")
    require((a is None and b is None) or (a["state"] == b["state"] == "paused" and a["processed"] == b["processed"]),
            "An unapproved candidate never advances construction")
    return second


def verify_world(completed, report):
    job = completed["job"]
    check = job["verification"]
    desired = {tuple(row["world"]): row["desired"] for row in report["changes"]
               if row["desired"]["id"] != "minecraft:air"}
    actual = {tuple(row["world"]): row["actual"] for row in check["target_states"]}
    require(job["state"] == "completed" and check["passed"] and actual == desired,
            f"The real final blocks and every state match the approved design: {job.get('reason')}")
    require(check["non_target_changes"] == check["outside_approved_changes"] == 0,
            "Targets and surrounding state were preserved")
    mutations = [row for row in job["actions"] if row["status"] in ("placed", "removed")]
    mutated = {tuple(row["world"]) for row in mutations}
    require(not mutated.intersection(tuple(row["world"]) for row in report["changes"] if row["category"] == "keep"),
            "No retained block was touched")
    require(len({(row["status"], tuple(row["world"])) for row in mutations}) == len(mutations),
            "Each mutation happened only once")
    for row in mutations:
        proof = row["interaction_evidence"]
        require(proof["reach"] <= 4.5, "Every action obeyed player reach")
        if row["status"] == "placed":
            require(proof["method"] == "ServerPlayerGameMode.useItemOn" and proof["place_events"] == 1
                    and proof["right_click_events"] == 1 and proof["item_before"] - proof["item_after"] == 1,
                    "Placement used a real item and Forge events")
        else:
            require(proof["method"] == "ServerPlayerGameMode.destroyBlock"
                    and proof["break_events"] == proof["left_click_events"] == 1
                    and proof["tool_damage_after"] - proof["tool_damage_before"] == 1,
                    "Removal used a tool with Forge interaction evidence")
    return dict(Counter(row["status"] for row in mutations))


def test_model_failures(child, chat, bridge, scoped, model):
    chat.send(child, "请帮我设计漂亮一点的东西", "你想建什么样的窗墙")
    chat.send(child, "模拟模型绕过审批", "模型输出未通过")
    chat.send(child, "模拟非法方块", "请选择橡木")
    chat.send(child, "模拟越界尺寸", "宽度需在 5–12 格")
    chat.send(child, "模拟模型超时", "响应超时")
    chat.send(child, r"这里环境，D:\Example\Private.csv，[示例] fixture-key-not-a-credential", "建议原点")
    sent = next(row for row in model.received if "这里环境" in row["message"])
    exposed = json.dumps(sent, ensure_ascii=False)
    require("Private.csv" not in exposed and "fixture-key-not-a-credential" not in exposed
            and "[已隐去]" in exposed and "player_id" not in sent and "request_id" not in sent,
            "Only the stripped build context reached the HTTP endpoint")

    signal(chat.run, ".mcaibuilder-architect-command", "architect 慢一点，帮我建窗墙")
    wait_for(child, model.slow_started.is_set, "the earlier model call start")
    old = next(row["request_id"] for row in scoped.inbox() if row["message"] == "慢一点，帮我建窗墙")
    chat.send(child, "施工进度如何？", "没有属于你的施工任务")
    model.slow_release.set()
    replay = rejected(lambda: scoped.answer(old, "重复消息"), 409, "stale_request")
    assert_pending_without_actions(child, bridge)
    return replay


def exercise(run, java_home, source, port, bmc4, model, trace):
    def start_game():
        return launch_client(java_home, source, run, port, smoke=True, m3_smoke=True, bmc4=bmc4)

    runtime = None
    child = start_game()
    chat = Chat(run)
    startup = 600 if bmc4 else 150
    exit_timeout = 180 if bmc4 else 90
    try:
        runtime = start_runtime(run, source, model, 1)
        bridge = BridgeClient(run / "config" / "mcaibuilder.json")
        scoped = ArchitectClient(run / "config" / "mcaibuilder-architect.json")
        wait_for(child, lambda: scoped.inbox() is not None, "Architect loopback scope", seconds=startup)
        wait_for(child, bridge.status, "Forge world", seconds=startup)
        wait_ticks(child, bridge, bridge.status()["game_tick"] + 20)
        trace["scope_rejections"] = [
            rejected(lambda: scoped.submit({}), 401, "unauthorized"),
            rejected(lambda: scoped.approve("00000000-0000-0000-0000-000000000000", "0" * 64), 401, "unauthorized"),
            rejected(lambda: scoped.control("resume", "00000000-0000-0000-0000-000000000000"), 401, "unauthorized"),
            rejected(lambda: bridge._call("architect/inbox"), 401, "unauthorized"),
        ]
        if not bmc4 and model is not None:
            trace["superseded_request"] = test_model_failures(child, chat, bridge, scoped, model)
        first = chat.card(child, "请在 6 -60 6 朝北建一面 7 格宽的中世纪窗墙，用云杉和石砖。")
        require(first["building"].bounds()[1][0] == 6, "Natural language width became actual coordinates")
        assert_pending_without_actions(child, bridge)
        second = chat.card(child, "把这面窗墙改成 8 格宽，把木材换成深色橡木。")
        require(second["building"].bounds()[1][0] == 7
                and any(block.state.block_id == "minecraft:dark_oak_planks" for block in second["building"].blocks.values())
                and first["report"]["new_version"]["model_sha256"] != second["report"]["new_version"]["model_sha256"],
                "Second turn changes width, wood and immutable model hash")
        trace["drafts"] = [first["report"], second["report"]]
        chat.send(child, "approve " + first["code"], "stale_approval")
        chat.send(child, second["code"], "architect_forbidden", guest=True)
        assert_pending_without_actions(child, bridge)
        if not bmc4:
            if model is not None:
                chat.send(child, "模拟过期意图", "引用的建筑版本已过期")
            stop_runtime(runtime)
            runtime = start_runtime(run, source, model, 2)
        approval = chat.card(child, "这个版本可以，开始建造。")
        assert_pending_without_actions(child, bridge)
        chat.send(child, "approve " + approval["code"], "已批准 V2")
        initial = bridge.status()
        npc = initial["npc"]["npc_id"]
        chat.send(child, "approve " + approval["code"], "stale_approval")
        if not bmc4:
            wait_placed(child, bridge, 4)
            chat.send(child, "先停工，帮我暂停一下。", "施工已暂停")
            assert_pending_without_actions(child, bridge)
            chat.send(child, "现在继续施工。", "[继续当前施工]")
            assert_pending_without_actions(child, bridge)
            chat.send(child, "resume " + initial["job"]["job_id"], "已继续施工")
        wait_placed(child, bridge, 17)
        revised = chat.card(child, "暂停，把窗户换成蓝色玻璃，其他部分不要动。")
        draft = assert_pending_without_actions(child, bridge)
        v1 = bridge.progress()
        require(v1["job"]["placed"] >= 17 and v1["job"]["placed"] < v1["job"]["target_count"],
                "The revision arrived while the original build was incomplete")
        counts = revised["report"]["counts"]
        require(counts["replace"] >= 1 and counts["add"] >= 1 and counts["keep"] >= 1 and counts["conflict"] == 0,
                "The real world diff contains repair, remaining work and retained structure")
        trace["paused"] = v1
        trace["revision"] = revised["report"]
        trace["save_exits"] = []
        if not bmc4:
            trace["save_exits"].append(stop_cleanly(child, run, "waiting-approval", exit_timeout))
            child = start_game()
            wait_for(child, bridge.status, "saved Architect world", seconds=startup)
            wait_ticks(child, bridge, bridge.status()["game_tick"] + 28)
            reopened = bridge.progress()
            require(reopened["job"]["state"] == "paused" and reopened["job"]["processed"] == draft["job"]["processed"]
                    and reopened["recovery"]["npc_reused"] and reopened["recovery"]["inventory_matches_checkpoint"],
                    "The same pending job and inventory persisted through a normal game reopen")
            trace["reopened"] = reopened
        chat.send(child, "approve " + revised["code"], "已批准 V3")
        finished = wait_terminal(child, bridge)
        trace["finished"] = finished
        trace["mutations"] = verify_world(finished, revised["report"])
        require(finished["npc"]["npc_id"] == npc and trace["mutations"].get("removed", 0) > 0,
                "The same NPC demolished and completed the approved revision")
        chat.send(child, "这一版的施工进度如何？", "V3 completed")

        if not bmc4:
            extension = chat.card(child, "把这面墙改成 9 格宽，其他部分保留。")
            proof = place_fixture_block(child, run, [14, -60, 6])
            chat.send(child, "approve " + extension["code"], "review_changed")
            refreshed = chat.card(child, "请重新审阅现场变化。", can_approve=False)
            require(refreshed["report"]["counts"]["conflict"] == 1, "A fresh M2 review sees the player's block")
            chat.send(child, "approve " + refreshed["code"], "change_conflicts")
            chat.send(child, "reject " + refreshed["code"], "已拒绝")
            trace["conflict"] = {"fresh": refreshed["report"], "player": proof}
            chat.send(child, "请取消当前施工。", "[取消当前施工]")
            require(bridge.status()["job"]["state"] == "paused", "The model cannot cancel the job directly")
            chat.send(child, "cancel " + finished["job"]["job_id"], "已取消当前施工")
            require(bridge.status()["job"]["state"] == "cancelled", "The owner's explicit cancel is applied")
        wait_ticks(child, bridge, bridge.status()["game_tick"] + 8)
        (run / ".mcaibuilder-capture").touch()
        wait_for(child, lambda: (run / "screenshots" / "m1-wall-built.png").is_file(), "the finished V3 screenshot")
        trace["save_exits"].append(stop_cleanly(child, run, "finished", exit_timeout))
        trace["passed"] = True
    finally:
        trace["chat"] = chat.trace
        if model is not None:
            trace["http_contexts"] = model.received
        stop_runtime(runtime)
        if child.poll() is None:
            (run / ".mcaibuilder-stop").touch()
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=10)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument("--source-instance", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18768)
    parser.add_argument("--bmc4", action="store_true")
    parser.add_argument("--live-model", action="store_true", help="Use the configured real HTTP provider for the game flow.")
    args = parser.parse_args()
    if args.live_model:
        try:
            HttpProvider.from_environment(45)
        except ValueError:
            parser.error("M3Live requires MC_ARCHITECT_MODEL and a configured provider. See docs/m3-guide.md.")
    family = "m3-live-runs" if args.live_model else "m3-bmc4-runs" if args.bmc4 else "m3-runs"
    directory = TEST_ROOT / family
    directory.mkdir(parents=True, exist_ok=True)
    run = Path(mkdtemp(prefix=datetime.now().strftime("%Y%m%d-%H%M%S-"), dir=directory))
    print(f"M3 test directory: {run}", flush=True)
    mode = "configured HTTP model" if args.live_model else "deterministic HTTP fixture"
    trace = {"passed": False, "provider": mode, "real_llm": False}
    if args.bmc4:
        trace["bmc4_mod_jars"] = len(list((args.source_instance / "mods").glob("*.jar")))
    model = None if args.live_model else ModelFixture()
    if model is not None:
        thread = Thread(target=model.serve_forever, daemon=True)
        thread.start()
    try:
        exercise(run, args.java_home, args.source_instance, args.port, args.bmc4, model, trace)
        trace["real_llm"] = args.live_model
    except Exception as error:
        trace["error"] = str(error)
        raise
    finally:
        if model is not None:
            model.shutdown()
            model.server_close()
        write_json(run / "acceptance.json", trace)
    print(f"M3 game verification passed ({mode}): {run / 'acceptance.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
