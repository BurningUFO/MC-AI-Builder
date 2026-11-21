"""Exercise the Windows double-click launcher and private chat in a disposable client."""

from argparse import ArgumentParser
import ctypes
from ctypes import wintypes
from contextlib import closing
import json
import os
import socket
import subprocess
from time import monotonic, sleep

from src.architect.codex_budget import CodexBudget
from src.architect.codex_provider import DEFAULT_EFFORT, DEFAULT_MODEL
from src.construction.client import BridgeClient
from src.core.building import write_json
from tests.m1_smoke import require, wait_for, wait_placed, wait_terminal
from tests.m3_codex_live import DESIGN, REVISION, await_approval, verify_design, verify_edit, verify_partial
from tests.m3_smoke import Chat, assert_pending_without_actions, signal, verify_world
from tools.mcai_launcher import HOME, MAX_TOKENS, MAX_TURNS, ROOT, local_game, read_object
from tools.m1_client import BRIDGE_JAR

INTRO = "@bUiLdEr 你好，介绍一下你现在能做什么"
OFFLINE = "@Builder M5_PUBLIC_PRIVATE_OFFLINE 没有连接时这条话也应保持私密"
BEFORE_CHAT = "M5_PUBLIC_BEFORE 普通 Minecraft 聊天"
AFTER_CHAT = "M5_PUBLIC_AFTER 普通 Minecraft 聊天"


def await_value(condition, description, seconds=40):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        result = condition()
        if result:
            return result
        sleep(0.18)
    raise TimeoutError(f"Timed out waiting for {description}")


class LaunchedGame:
    def __init__(self, session, pid):
        self.session, self.pid = session, pid
        self.returncode = None

    def poll(self):
        state = self.session.state()
        if state.get("game_pid") == self.pid:
            return None
        self.returncode = state.get("game_exit_code")
        return self.returncode if self.returncode is not None else 1


class LaunchSession:
    def __init__(self, mode):
        self.mode = mode
        self.home = HOME / f"validation-{mode}"
        self.world = local_game(f"m5-validation-{mode}")
        self.port = 18778 if mode == "bmc4" else 18777
        self.home.mkdir(parents=True, exist_ok=True)
        self.ledger = self.home / "codex-turns.sqlite3"

    def launch(self):
        os.environ["MC_AI_BUILDER_CHECKS"] = self.mode
        os.startfile(str(ROOT / "Start MC AI Builder.cmd"), cwd=str(ROOT))

    def control(self, action):
        write_json(self.home / "control.json", {"action": action})

    def state(self):
        return read_object(self.home / "status.json")

    def phase(self, name, seconds=40):
        def observe():
            state = self.state()
            if state.get("phase") == "auth_wait" and "失败" in state.get("login", "") and name == "ready":
                raise RuntimeError("ChatGPT login verification failed; see the isolated launcher auth log.")
            return state if state.get("phase") == name else None
        return await_value(observe, name, seconds)

    def child(self):
        state = self.phase("ready", 630 if self.mode == "bmc4" else 170)
        require(state["agent_pid"], "The launcher owns a running Architect service")
        require(state["pack"].startswith("BMC4" if self.mode == "bmc4" else "Forge"), "Expected isolated pack")
        return LaunchedGame(self, state["game_pid"])

    def records(self):
        if not self.ledger.is_file():
            return []
        return CodexBudget(self.ledger, MAX_TURNS, MAX_TOKENS).records()

    def exit(self):
        self.control("exit_game")
        state = self.phase("idle", 180)
        require(state["game_pid"] is state["agent_pid"] is None and state["game_exit_code"] == state["agent_exit_code"] == 0,
                "Both processes exited successfully")
        return self.saved()

    def saved(self):
        log = (self.world / "console.log").read_text(encoding="utf-8", errors="replace")
        require("normal save flow" in log and "All dimensions are saved" in log
                and (self.world / "saves" / "m1-construction" / "level.dat").is_file(),
                "The launcher used the Minecraft save flow and the world persisted")
        return True


class PlayerChat(Chat):
    def send(self, child, message, expected, *, guest=False, timeout=95):
        if guest:
            return super().send(child, message, expected, guest=True, timeout=timeout)
        already = len(self.messages())
        self.submit(child, message)
        wait_for(child, lambda: any(expected in row for row in self.messages()[already:]),
                 f"private client chat reply “{expected}”", seconds=timeout)
        received = self.messages()[already:]
        self.trace.append({"input": message, "responses": received})
        return received

    def submit(self, child, message):
        signal(self.run, ".mcaibuilder-chat-fixture", json.dumps({"action": "chat", "message": message}))
        wait_for(child, lambda: not (self.run / ".mcaibuilder-chat-fixture").exists(), "client ChatScreen accepted text")

    def toggle(self, child, *, on):
        notice = "建筑师对话模式已开启" if on else "已退出建筑师对话模式"

        def notices():
            log = (self.run / "console.log").read_bytes()
            return sum(log.count(notice.encode(encoding)) for encoding in ("utf-8", "gb18030"))

        previous = notices()
        alt_b(child.pid)
        try:
            wait_for(child, lambda: notices() > previous, "the configured Alt+B mode notification", seconds=8)
        except TimeoutError:
            if child.session.mode != "bmc4":
                raise
            prompt = child.session.home / "waiting-for-key.json"
            write_json(prompt, {"enable": on, "pid": child.pid})
            try:
                wait_for(child, lambda: notices() > previous, "a player's Alt+B in the isolated window", seconds=155)
            finally:
                prompt.unlink(missing_ok=True)

    def ordinary(self):
        file = self.run / "public-chat.jsonl"
        if not file.is_file():
            return []
        try:
            return [json.loads(row)["message"] for row in file.read_text(encoding="utf-8").splitlines()]
        except (OSError, ValueError):
            return []


def alt_b(pid):
    """Post only the Alt+B scan codes to the owned Minecraft GLFW window."""
    win = ctypes.WinDLL("user32", use_last_error=True)
    window = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    win.IsWindowVisible.argtypes = (wintypes.HWND,)
    win.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    win.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))

    @callback_type
    def collect(handle, _):
        owner = wintypes.DWORD()
        name = ctypes.create_unicode_buffer(100)
        win.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if owner.value == pid and win.IsWindowVisible(handle) and win.GetClassNameW(handle, name, len(name)):
            if name.value == "GLFW30":
                window.append(handle)
        return True

    win.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    win.EnumWindows(collect, 0)
    require(len(window) == 1, "Expected the unique live GLFW window opened by the launcher")
    win.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    for message, key, flags in ((0x0104, 0x12, 0), (0x0104, 0x42, 0x20000000),
                                (0x0105, 0x42, 0xE0000000), (0x0105, 0x12, 0xC0000000)):
        scan_code = win.MapVirtualKeyW(key, 0)
        require(win.PostMessageW(window[0], message, key, 1 | (scan_code << 16) | flags),
                "Minecraft accepted the targeted Windows key message")
        sleep(0.28 if key == 0x42 and message == 0x0104 else 0.08)


def private(session, chat, messages):
    console = (session.world / "console.log").read_bytes()
    public = b"\n".join(line for line in console.splitlines() if b"<BuilderTester>" in line)
    for message in messages:
        require(all(message.encode(encoding) not in public for encoding in ("utf-8", "gb18030"))
                and all(message not in line for line in chat.ordinary()), "A targeted line was never public")


def check_live(session, trace, earlier=None):
    session.launch()
    child = session.child()
    bridge = BridgeClient(session.world / "config" / "mcaibuilder.json", timeout=1.5)
    chat = PlayerChat(session.world)
    trace.update(launcher=session.state(), chat=chat.trace)
    require(not bridge.status().get("job"), "Acceptance started in a fresh isolated world")
    ordinary_before = len(chat.ordinary())
    chat.submit(child, BEFORE_CHAT)
    wait_for(child, lambda: any(BEFORE_CHAT in line for line in chat.ordinary()[ordinary_before:]),
             "a new ordinary chat message before mode", seconds=65)
    sleep(1)
    baseline = len(session.records())
    if earlier:
        proof = session.records()[-1]
        require(proof.get("intent", {}).get("action") == "get_capabilities"
                and proof.get("status") == "completed" and earlier["chat"][-1]["input"] == INTRO
                and any("我是 MC Builder" in row for row in earlier["chat"][-1]["responses"]),
                "The interrupted run already received a real private intro")
        baseline -= 1
        chat.trace.extend(earlier["chat"])
        trace["reopened_after_intro"] = True
    else:
        chat.send(child, INTRO, "我是 MC Builder")
    chat.toggle(child, on=True)
    design = chat.card(child, DESIGN)
    verify_design(design)
    assert_pending_without_actions(child, bridge)
    private(session, chat, (INTRO, INTRO.split(" ", 1)[1], DESIGN))
    trace["draft"] = design["report"]
    start = await_approval(child, bridge, chat, design, trace, "M5-A BMC4 initial design")
    require(start["npc"]["entity_type"] == "players:fake_player", "The player released a genuine Fake Player")

    wait_placed(child, bridge, 17)
    revision = chat.card(child, REVISION)
    verify_edit(design, revision, "minecraft:glass", "minecraft:blue_stained_glass")
    assert_pending_without_actions(child, bridge)
    paused = bridge.progress()
    verify_partial(paused, design["report"])
    counts = revision["report"]["counts"]
    require(min(counts["replace"], counts["add"], counts["keep"]) > 0 and counts["conflict"] == 0,
            "The mid-build request generated real replacements, preserved blocks, and outstanding placements")
    trace.update(paused=paused, revision=revision["report"])
    await_approval(child, bridge, chat, revision, trace, "M5-A BMC4 blue glass revision")

    final = wait_terminal(child, bridge)
    trace["interaction_counts"] = verify_world(final, revision["report"])
    require(final["npc"]["npc_id"] == start["npc"]["npc_id"] and final["npc"]["walked_blocks"] > 4,
            "One Fake Player walked, built, and applied the revision")
    chat.send(child, "/architect approve " + revision["code"], "stale_approval")
    replay = bridge.progress()
    require(replay["job"]["actions"] == final["job"]["actions"] and all(replay["npc"][field] == final["npc"][field] for field in ("npc_id", "materials", "tools")),
            "The duplicate approval left the already completed job and inventory unchanged")
    chat.toggle(child, on=False)
    chat.submit(child, AFTER_CHAT)
    wait_for(child, lambda: any(AFTER_CHAT in line for line in chat.ordinary()), "ordinary chat after leaving mode")
    private(session, chat, (INTRO, INTRO.split(" ", 1)[1], DESIGN, REVISION))
    rows = session.records()[baseline:]
    require(len(rows) == 3 and [row["intent"]["action"] for row in rows] ==
            ["get_capabilities", "create_design", "revise_design"]
            and all(row["effective"]["model"] == DEFAULT_MODEL and row["effective"]["effort"] == DEFAULT_EFFORT
                    and row["status"] == "completed" for row in rows), "Exactly three successful real Codex turns")
    trace.update(ordinary=chat.ordinary(), model_turns=rows, final=final, save=session.exit())


def check_lifecycle(session, trace):
    with closing(socket.socket()) as busy:
        busy.bind(("127.0.0.1", session.port))
        busy.listen()
        session.launch()
        refused = session.phase("error", 35)
        require(str(session.port) in refused["message"] and refused["game_pid"] is refused["agent_pid"] is None,
                "Port conflict was reported before launching either child")
        trace["port_conflict"] = True
    session.control("start")
    child = session.child()
    original = session.state()
    bridge_build = BRIDGE_JAR.stat().st_mtime_ns
    duplicate_log = session.home / "duplicate.log"
    previous = duplicate_log.stat().st_size if duplicate_log.is_file() else 0
    session.launch()
    await_value(lambda: duplicate_log.is_file() and duplicate_log.stat().st_size > previous, "duplicate startup refusal")
    require(session.state() == original, "A second Windows app launch did not replace the game or Agent")
    trace["duplicate"] = True

    bridge = BridgeClient(session.world / "config" / "mcaibuilder.json", timeout=1.5)
    chat = PlayerChat(session.world)
    chat.toggle(child, on=True)
    chat.toggle(child, on=False)
    chat.submit(child, BEFORE_CHAT)
    wait_for(child, lambda: any(BEFORE_CHAT in row for row in chat.ordinary()), "ordinary chat after native Alt+B exit")
    trace["native_mode_toggle"] = True

    session.control("stop_agent")
    state = session.phase("game_only")
    require(state["game_pid"] == child.pid and state["agent_pid"] is None and state["agent_exit_code"] == 0,
            "Stopping the Runtime preserved the game process")
    wait_for(child, lambda: not bridge.health()["architect_runtime_online"], "Runtime heartbeat expiry")
    turns = session.records()
    chat.send(child, OFFLINE, "没有进入模型队列")
    private(session, chat, (OFFLINE, "M5_PUBLIC_PRIVATE_OFFLINE"))
    require(session.records() == turns and not bridge.status().get("job"), "An offline line neither ran a model nor changed the world")
    trace["stopped_agent_and_private_offline"] = True
    session.control("start")
    ready = session.phase("ready")
    require(ready["game_pid"] == child.pid and ready["agent_pid"] != original["agent_pid"] and session.records() == turns,
            "Reconnection reused the running game and token budget")
    trace["agent_reconnected"] = True

    # Interrupt only this disposable client after checking the port, exact process and idle world.
    require(not bridge.status().get("job") and (session.world / ".mcaibuilder-test").is_file()
            and session.state()["game_pid"] == child.pid, "Only the idle acceptance client may be interrupted")
    inspected = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                                f"(Get-CimInstance Win32_Process -Filter 'ProcessId = {child.pid}').CommandLine"],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True, text=True, timeout=15)
    require("java.exe" in inspected.stdout.casefold() and str(session.world) in inspected.stdout
            and "launch.args" in inspected.stdout, "The interrupted process must own this isolated game's argument file")
    subprocess.run(["taskkill.exe", "/PID", str(child.pid), "/F"], check=True, stdout=subprocess.PIPE, timeout=15)
    await_value(lambda: (state := session.state()).get("phase") == "error" and state.get("agent_pid") is None,
                "the launcher to report abnormal Java exit and release the Runtime", 35)
    trace["abnormal_exit"] = True
    session.control("start")
    reopened = session.child()
    require(reopened.pid != child.pid and not bridge.status().get("job") and session.records() == turns,
            "The same isolated world reconnected after Java exited without spending more tokens")
    trace["reopened"] = True
    require(BRIDGE_JAR.stat().st_mtime_ns == bridge_build, "Current Bridge artifact was reused on relaunch")
    trace["existing_bridge_reused"] = True
    trace["save"] = session.exit()
    trace["chat"] = chat.trace


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("forge", "bmc4"))
    parser.add_argument("--resume-after-intro", action="store_true",
                        help="Reuse a real BMC4 intro after an interrupted key test in the same isolated world")
    options = parser.parse_args()
    session = LaunchSession(options.mode)
    earlier = None
    if options.resume_after_intro:
        earlier = read_object(session.world / "acceptance.json")
        require(options.mode == "bmc4" and earlier.get("passed") is False and earlier.get("chat")
                and not earlier.get("draft") and session.records(), "Expected an interrupted intro-only BMC4 run")
    else:
        require(not session.world.exists(), "Use a fresh M5 acceptance world; preserve previous evidence")
    trace = {"profile": options.mode, "entry": "Windows ShellExecute on the root .cmd", "passed": False}
    try:
        if options.mode == "bmc4":
            check_live(session, trace, earlier)
        else:
            check_lifecycle(session, trace)
        trace["passed"] = True
    finally:
        write_json(session.world / "acceptance.json" if session.world.is_dir() else session.home / "acceptance.json", trace)
        session.control("close")
    print("M5-A acceptance passed", options.mode, flush=True)


if __name__ == "__main__":
    main()
