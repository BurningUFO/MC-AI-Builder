"""Windows launcher for the isolated Minecraft Architect player profiles."""

import ctypes
from hashlib import sha256
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import socket
import sqlite3
import subprocess
import sys
from threading import Thread
from time import monotonic, sleep
from zipfile import BadZipFile, ZipFile

from src.architect.codex_budget import CodexBudget
from src.architect.codex_provider import DEFAULT_EFFORT, DEFAULT_MODEL
from src.architect.diagnostics import diagnose
from src.construction.client import BridgeClient, BridgeError
from src.core.building import write_json
from tools.m1_client import BRIDGE_JAR, PLAYERS_HASH, PLAYERS_JAR, ROOT, TEST_ROOT, launch_client
from tools.world_copy import MARKER, copy_root, create_copy, existing_copies, read_copy, world_choices


HOME = ROOT / ".cache" / "m5-launcher"
PYTHON = Path(sys.executable).with_name("python.exe")
MAX_TURNS, MAX_TOKENS = 10, 150_000
SETUP = HOME / "settings.json"
DEFAULT_BLENDER = ROOT / "tools" / "blender" / "blender.exe"
INPUT_LABELS = {"java": "Java 17 JDK 文件夹", "instance": "Minecraft Forge 版本文件夹", "blender": "blender.exe"}
ACTIONS = {"start", "stop_agent", "exit_game", "login", "close"}


def read_object(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def single_launcher():
    """Hold this handle for the lifetime of the launcher window."""
    key = sha256(str(ROOT).casefold().encode()).hexdigest()[:24]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateMutexW
    create.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    create.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = create(None, False, "Local\\MC-AI-Builder-" + key)
    already_running = ctypes.get_last_error() == 183
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if already_running:
        kernel.CloseHandle(handle)
        return None
    return handle


def find_setup(settings):
    profile_home = Path(os.environ.get("USERPROFILE", Path.home()))
    roaming = Path(os.environ.get("APPDATA", profile_home / "AppData" / "Roaming"))
    runtimes = roaming / ".minecraft" / "runtime"
    java_candidates = [
        settings.get("java"), os.environ.get("JAVA_HOME"), ROOT / "tools" / "java-17", ROOT / "tools" / "jdk-17",
        runtimes / "java-runtime-gamma-snapshot", runtimes / "java-runtime-gamma",
        *sorted(runtimes.glob("*/windows-x64/*")), *sorted((ROOT / "tools").glob("jdk-17*")),
    ]
    java = None
    for candidate in filter(None, java_candidates):
        javac = Path(candidate) / "bin" / "javac.exe"
        if javac.is_file() and javac.with_name("java.exe").is_file():
            try:
                result = subprocess.run([str(javac), "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        timeout=8, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode == 0 and re.search(rb"\bjavac 17(?:[.\s]|$)", result.stdout):
                java = Path(candidate)
                break
    desktop = profile_home / "Desktop"
    versions = [desktop / "PCL2-214-Test" / ".minecraft" / "versions",
                roaming / ".minecraft" / "versions", ROOT / "tools" / "minecraft" / "versions",
                *sorted(desktop.glob("*/.minecraft/versions"))]
    locations = [Path(settings["instance"])] if settings.get("instance") else []
    locations.extend(path / "BMC4" for path in versions)
    locations.extend(item for path in versions for item in sorted(path.glob("*")))
    instance = next((path for path in locations if forge_profile(path)), None)
    blender = next((Path(path) for path in (settings.get("blender"), DEFAULT_BLENDER) if path and Path(path).is_file()),
                   None)
    required = {"java": java, "instance": instance, "blender": blender}
    missing = [INPUT_LABELS[key] for key, value in required.items() if value is None]
    if missing:
        raise ValueError("打开「设置」选择：" + "、".join(missing))
    return required


def forge_profile(path):
    description = read_object(path / (path.name + ".json"))
    arguments = description.get("arguments")
    return (description.get("mainClass") == "cpw.mods.bootstraplauncher.BootstrapLauncher"
            and isinstance(arguments, dict) and isinstance(arguments.get("game"), list)
            and all(value in arguments["game"] for value in ("1.20.1", "47.4.20", "forgeclient"))
            and (path / (path.name + ".jar")).is_file())


def isolated_pack():
    for family in ("m4-bmc4-runs", "m3-bmc4-runs"):
        for run in sorted((TEST_ROOT / family).glob("*"), reverse=True):
            acceptance = read_object(run / "acceptance.json")
            if (acceptance.get("passed") is True and acceptance.get("bmc4_mod_jars") == 387
                    and run.resolve().is_relative_to(TEST_ROOT.resolve())
                    and (run / ".mcaibuilder-test").is_file()
                    and len(list((run / "mods").glob("*.jar"))) == 389):
                return run
    return None


def local_game(name):
    root = TEST_ROOT.resolve()
    game = (root / name).resolve()
    if game.parent != root or game.name != name:
        raise ValueError("练习世界必须位于项目 mc-test 的指定独立目录。")
    return game


def source_revision():
    forge = ROOT / "forge"
    files = [*sorted((forge / "src" / "main").rglob("*")),
             *sorted(forge.glob("*.gradle")), forge / "gradle.properties",
             forge / "gradle" / "wrapper" / "gradle-wrapper.properties"]
    digest = sha256()
    latest = 0
    for path in files:
        if path.is_file():
            digest.update(path.relative_to(forge).as_posix().encode() + b"\0" + path.read_bytes())
            latest = max(latest, path.stat().st_mtime_ns)
    return digest.hexdigest(), latest


def bridge_revision():
    try:
        with ZipFile(BRIDGE_JAR) as mod:
            if not {"dev/mcaibuilder/bridge/ForgeBridge.class",
                    "dev/mcaibuilder/bridge/ArchitectChat.class"} <= set(mod.namelist()):
                return None
        return sha256(BRIDGE_JAR.read_bytes()).hexdigest()
    except (OSError, BadZipFile):
        return None


def require_free_port(port):
    try:
        with socket.socket() as listener:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", port))
    except OSError as error:
        raise ValueError(f"本地端口 {port} 已被占用。关闭占用程序后，点击「启动 / 重连」。") from error


def describe_execution(execution):
    labels = {
        "survey": "尚无施工任务", "running": "施工中", "working": "处理方块",
        "scheduled_wait": "施工批次间等待", "navigating": "正在寻路",
        "to_warehouse": "前往仓库取料", "warehouse_return": "运料返回工地",
        "mining": "正在拆除方块", "jumping": "移动到放置位置",
        "placement_obstructed": "等待实体让出作业位置", "completed": "完工",
        "cancelled": "施工已取消", "failed": "施工已停止",
    }
    if not execution:
        return ""
    activity = execution.get("activity", "idle")
    reason = execution.get("reason")
    if execution.get("state") == "paused":
        status = ("等待材料 · 安全暂停" if reason in {"missing_material", "out_of_material", "out_of_tool"}
                  else "现场冲突 · 安全暂停" if reason == "world_conflict" else "施工已安全暂停")
    else:
        status = labels.get(activity, labels.get(execution.get("state"), "待命"))
    version = execution.get("building_version", 0)
    if version:
        status = f"V{version} · {execution['processed']}/{execution['total']} · {status}"
    if preparation := execution.get("preparation"):
        cleared = f"{preparation['cleared']}/{preparation['total']}"
        if preparation.get("phase") == "clear":
            tool = {"minecraft:air": "徒手", "minecraft:iron_axe": "铁斧",
                    "minecraft:iron_shovel": "铁铲", "minecraft:iron_pickaxe": "铁镐",
                    "minecraft:shears": "剪刀"}.get(preparation.get("tool"), "")
            point = preparation.get("next")
            position = f" · {', '.join(map(str, point))}" if point else ""
            status += f" · 清障 {cleared} · {tool}{position}"
        elif preparation["total"]:
            status += f" · 已清理 {cleared}"
    if execution.get("awaiting_approval"):
        status += " · 等待玩家审批"
    if execution.get("observation") == "pending":
        status += " · 正在视觉观察"
    plan = execution.get("planning", {})
    if plan.get("state") == "observing" and execution.get("observation") != "pending":
        status += " · 准备下一阶段观察"
    elif plan.get("state") == "thinking":
        status += " · LLM 正在思考，NPC 按当前顺序施工"
    elif plan.get("state") in {"applied", "kept"}:
        group = {"roof_front": "前侧屋顶", "roof_rear": "后侧屋顶"}.get(plan.get("group"), "原顺序")
        status += f" · 最近规划 {group} · {plan.get('reason', '')}"
    elif plan.get("state") == "discarded":
        status += " · 本次策略已失效"
    if reason and execution.get("state") in {"paused", "failed"}:
        status += " · " + diagnose(reason).recovery
    return status


class Launcher:
    def __init__(self, checks):
        self.checks = checks
        self.home = HOME / f"validation-{checks}" if checks else HOME
        self.home.mkdir(parents=True, exist_ok=True)
        self.window_updates = Queue()
        self.commands = Queue()
        self.settings = read_object(SETUP)
        self.port = {"forge": 18777, "bmc4": 18778, "copy": 18780}.get(checks, 18776)
        self.game = self.agent = self.auth = self.bridge = None
        self.game_dir = None
        self.environ = None
        self.live_site = None
        self.execution = None
        self.world_label = ""
        self.login_ok = self.want_agent = self.exiting_game = self.closing = False
        self.game_exit_code = self.agent_exit_code = None
        self.state = {}
        self.next_poll = 0
        self.budget = self.home / "codex-turns.sqlite3"
        self.stop_agent = self.home / "stop-agent"
        self.login_status = ""
        self.last_error = ""

    def publish(self, phase, message):
        if self.game_dir:
            game = str(self.game_dir)
        else:
            game = ""
        state = {"phase": phase, "message": message, "game_pid": self.game.pid if self.game else None,
                 "agent_pid": self.agent.pid if self.agent else None, "game_dir": game,
                 "game_exit_code": self.game_exit_code, "agent_exit_code": self.agent_exit_code,
                 "login": self.login_status, "pack": self.state.get("pack", ""),
                 "world_label": self.world_label, "site": self.live_site, "execution": self.execution}
        if self.budget.is_file():
            try:
                usage = CodexBudget(self.budget, MAX_TURNS, MAX_TOKENS).records()
            except sqlite3.Error:
                state["budget_error"] = True
            else:
                state.update(turns=len(usage), tokens=sum(row["charged_tokens"] for row in usage))
        if state != self.state:
            log_transition = any(state[key] != self.state.get(key) for key in ("phase", "message"))
            self.state = state
            self.window_updates.put(state)
            if self.checks:
                write_json(self.home / "status.json", state)
            if log_transition:
                with (self.home / "launcher.log").open("a", encoding="utf-8") as log:
                    print(f"{phase}: {message}", file=log)

    def prepare_bridge(self):
        if not PLAYERS_JAR.is_file() or sha256(PLAYERS_JAR.read_bytes()).hexdigest() != PLAYERS_HASH:
            raise ValueError("Fake Players 3.0.0 缺失或校验失败，请参阅使用指南。")
        stamp = HOME / "bridge-build.json"
        sources, latest = source_revision()
        jar = bridge_revision()
        signature = {"source_sha256": sources, "jar_sha256": jar}
        old = read_object(stamp)
        if jar and old == signature:
            return
        built_elsewhere = jar and jar != old.get("jar_sha256") and BRIDGE_JAR.stat().st_mtime_ns >= latest
        if not built_elsewhere:
            self.publish("building", "正在使用本地 Forge 缓存构建 Bridge。")
            # Windows PowerShell needs its own module search path if started from PowerShell 7.
            build_env = {key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"}
            with (self.home / "forge-build.log").open("wb") as log:
                build = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "tools/m1.ps1",
                     "Build", "-JavaHome", str(self.environ["java"]), "-Offline"],
                    cwd=ROOT, env=build_env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW, timeout=240, check=False,
                )
            jar = bridge_revision()
            if build.returncode or not jar:
                raise ValueError("Forge Bridge 构建失败，请打开日志查看 forge-build.log。")
            signature["jar_sha256"] = jar
        write_json(stamp, signature)

    def spawn(self, command, log_name, mode="wb"):
        with (self.home / log_name).open(mode) as log:
            return subprocess.Popen([str(PYTHON), "-B", "-m", *command], cwd=ROOT, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)

    def check_login(self, login=False):
        if self.auth:
            return
        self.login_ok = False
        self.login_status = "浏览器中完成 ChatGPT 登录" if login else "正在核对 ChatGPT 登录"
        self.auth = self.spawn(["tools.codex_sdk_probe", "login" if login else "status"], "chatgpt-check.log")
        self.auth_deadline = monotonic() + (300 if login else 20)

    def start(self):
        if self.closing or self.exiting_game or (not self.want_agent and self.agent):
            return
        self.last_error = ""
        self.want_agent = True
        if self.game:
            if not self.login_ok:
                self.check_login()
            return
        self.publish("checking", "正在检查本地端口、Java 17、Blender 和 Forge 配置。")
        require_free_port(self.port)
        self.environ = find_setup(self.settings)
        self.prepare_bridge()
        self.live_site = None
        copy = self.selected_copy()
        pack = None
        survival = not self.checks and self.settings.get("survival", False) is True
        if copy:
            self.game_dir, marker = copy
            source = Path(marker["source_instance"]).resolve(strict=True)
            if not forge_profile(source):
                raise ValueError("副本关联的 Forge 实例已不可读。请在设置确认 PCL 实例。")
            self.environ["instance"] = source
            self.state["pack"] = f"BMC4 · {marker['enabled_jars']} 模组 · 生存供料"
            self.world_label = f"真实世界副本 · {marker['source_label']} · 仅写入 D 盘副本"
        else:
            pack = None if self.checks == "forge" else isolated_pack()
            if self.checks == "bmc4" and not pack:
                raise ValueError("找不到通过 M4 验收的 387 模组 BMC4 隔离副本。")
            name = f"m5-validation-{self.checks}" if self.checks else "m5-survival" if survival else "m5-player"
            self.game_dir = local_game(name)
            self.state["pack"] = "BMC4 · 387 模组" if pack else "Forge · 最小练习世界"
            if survival:
                self.state["pack"] += " · 生存供料"
            self.world_label = "隔离练习世界 · D 盘"
        stop = self.game_dir / ".mcaibuilder-stop"
        if copy or (self.game_dir / ".mcaibuilder-test").is_file():
            stop.unlink(missing_ok=True)
        self.publish("starting", f"正在启动 {self.state['pack']}。首次加载可能需要几分钟。")
        self.game = launch_client(self.environ["java"], self.environ["instance"], self.game_dir, self.port,
                                  architect=True, bmc4=bool(pack or copy), pack_source=pack, survival=survival,
                                  managed=True, m5_smoke=self.checks in ("forge", "bmc4"), hide_console=True,
                                  world_copy=bool(copy), copy_smoke=self.checks == "copy")
        configuration = "mcaibuilder-copy.json" if copy else "mcaibuilder.json"
        self.bridge = BridgeClient(self.game_dir / "config" / configuration, timeout=1.5)
        self.deadline = monotonic() + (600 if pack or copy else 150)
        self.check_login()

    def selected_copy(self):
        if self.checks == "copy":
            identity = os.environ.get("MC_AI_BUILDER_COPY_ID", "")
        elif not self.checks and self.settings.get("world_mode") == "copy":
            identity = self.settings.get("world_copy", "")
        else:
            return None
        if not re.fullmatch(r"[a-f0-9]{32}", identity):
            raise ValueError("请在「选择世界」中选取已核验的副本。")
        game = copy_root() / identity
        return game, read_copy(game)

    def choose_world(self, selection, path=None):
        if self.game or self.agent:
            raise ValueError("请先正常保存并退出当前游戏，再切换世界。")
        if selection == "new_copy":
            self.publish("copying", "正在只读复制原世界与模组，并校验所有文件。")
            instance = find_setup(self.settings)["instance"]
            path = create_copy(instance, path)
            selection = "copy"
        if selection == "copy":
            record = read_copy(path)
            self.settings = {**self.settings, "world_mode": "copy", "world_copy": path.name}
            self.world_label = f"已选副本 · {record['source_label']} · D 盘隔离"
        else:
            self.settings = {**self.settings, "world_mode": "practice"}
            self.world_label = "已选隔离练习世界 · D 盘"
        write_json(SETUP, self.settings)
        self.last_error = ""
        self.live_site = None

    def start_agent(self, runtime_polls):
        copied_world = (self.game_dir / MARKER).is_file()
        share = self.settings.get("share_observations") is True and copied_world
        planning = self.settings.get("parallel_planning") is True and copied_world
        self.agent_start_poll = runtime_polls
        self.stop_agent.unlink(missing_ok=True)
        self.agent = self.spawn([
            "src.architect", "--config", str(self.game_dir / "config" / "mcaibuilder-architect.json"),
            "--instance", str(self.environ["instance"]), "--blender", str(self.environ["blender"]),
            "--provider", "codex", "--timeout", "50", "--codex-model", DEFAULT_MODEL,
            "--codex-effort", DEFAULT_EFFORT, "--codex-max-turns", str(MAX_TURNS),
            "--codex-max-tokens", str(MAX_TOKENS), "--codex-budget-log", str(self.budget),
            "--stop-file", str(self.stop_agent),
            *(["--share-observations"] if share else []),
            *(["--planning"] if planning else []),
        ], "agent.log", "ab")
        self.agent_deadline = monotonic() + 30

    def stop_runtime(self):
        self.want_agent = False
        if self.agent:
            self.stop_agent.touch()

    def exit_game(self):
        self.stop_runtime()
        if self.game:
            self.exiting_game = True
            (self.game_dir / ".mcaibuilder-stop").touch()

    def update(self):
        if self.auth:
            code = self.auth.poll()
            if code is None and monotonic() >= self.auth_deadline:
                self.auth.terminate()
            elif code is not None:
                self.auth = None
                self.login_ok = code == 0
                self.login_status = "ChatGPT 已登录" if self.login_ok else "登录检查失败，可重试或点击「ChatGPT 登录」"
        if self.agent and (code := self.agent.poll()) is not None:
            if self.want_agent:
                self.last_error = f"Architect Runtime 已退出（{code}）。详情见 agent.log。"
                self.stop_runtime()
            self.agent_exit_code = code
            self.agent = None
        if self.game and (code := self.game.poll()) is not None:
            self.game_exit_code = code
            self.game = self.bridge = None
            self.exiting_game = False
            self.stop_runtime()
            if code:
                self.last_error = f"Minecraft 异常退出（{code}）。检查 console.log 后可重启隔离世界。"
        if self.closing:
            if self.auth and self.auth.poll() is None:
                self.auth.terminate()
            if not (self.game or self.agent or self.auth):
                self.publish("closed", "已释放 Minecraft 与 Architect Runtime。")
                return False
        if self.last_error and not self.exiting_game and not self.closing:
            self.publish("error", self.last_error)
        elif self.exiting_game or self.closing:
            self.publish("stopping", "正在按 Minecraft 正常流程保存，并等待 Agent 释放资源。")
        elif self.bridge:
            self.update_connections()
        elif self.agent:
            self.publish("stopping_agent", "正在等待 Agent 完成当前请求并退出。")
        else:
            self.publish("idle", "Minecraft 与 Agent 已停止。点击「启动 / 重连」打开练习世界。")
        return True

    def update_connections(self):
        status = {}
        try:
            status = self.bridge.health()
        except (OSError, BridgeError, ValueError):
            pass
        self.live_site = status.get("site_authorization")
        self.execution = status.get("execution")
        if not status.get("architect_enabled"):
            if monotonic() >= self.deadline:
                self.publish("bridge_wait", "Forge Bridge 尚未就绪。进入练习世界或查看 Minecraft 日志。")
            else:
                self.publish("starting", f"正在加载 {self.state['pack']}，等待世界和 Forge Bridge。")
            return
        if not status.get("architect_player_online"):
            self.publish("bridge_wait", "Forge Bridge 已启动，等待已授权玩家进入练习世界。")
            return
        polls = status["architect_runtime_polls"]
        if self.want_agent and self.login_ok and not self.agent:
            self.start_agent(polls)
        if self.agent and not self.want_agent:
            self.publish("stopping_agent", "正在停止 Agent。Minecraft 保持运行。")
        elif self.agent and status.get("architect_runtime_online") and polls > self.agent_start_poll:
            self.publish("ready", "Minecraft、Forge Bridge、Fake Players 和 Architect Runtime 已连接。")
        elif self.agent:
            if monotonic() >= self.agent_deadline:
                self.publish("agent_wait", "Agent 尚未连接 Forge Bridge。请查看 agent.log。")
            else:
                self.publish("agent_starting", "Forge Bridge 已就绪。正在等待 Agent 心跳。")
        elif self.want_agent:
            self.publish("auth_wait", "Minecraft 已就绪。Agent 等待 ChatGPT 登录检查。")
        else:
            self.publish("game_only", "Minecraft 正在运行，Agent 已停止。")

    def execute(self, action):
        if isinstance(action, tuple) and action[0] == "choose":
            self.choose_world(*action[1:])
        elif action == "start":
            self.start()
        elif action == "stop_agent":
            self.stop_runtime()
        elif action == "exit_game":
            self.exit_game()
        elif action == "login":
            if not self.agent and not self.closing:
                self.check_login(login=True)
        elif action == "close":
            self.closing = True
            self.exit_game()

    def run(self):
        while True:
            try:
                if self.checks:
                    request = self.home / "control.json"
                    if request.is_file():
                        action = read_object(request).get("action")
                        request.unlink(missing_ok=True)
                        if action in ACTIONS:
                            self.commands.put(action)
                try:
                    action = self.commands.get(timeout=0.3)
                except Empty:
                    action = None
                if action:
                    self.execute(action)
                if monotonic() >= self.next_poll or action:
                    self.next_poll = monotonic() + 0.75
                    if not self.update():
                        return
            except (OSError, KeyError, ValueError, subprocess.SubprocessError) as error:
                self.want_agent = False
                self.last_error = str(error)
                self.publish("error", self.last_error)
                sleep(0.25)


def window(app):
    import tkinter as tk
    from tkinter import filedialog, ttk

    root = tk.Tk()
    root.title("MC AI Builder")
    root.geometry("675x445")
    root.minsize(620, 405)
    frame = ttk.Frame(root, padding=20)
    frame.pack(expand=True, fill="both")
    ttk.Label(frame, text="MC AI Builder", font=("Segoe UI", 20, "bold")).pack(anchor="w")
    model_label = f"Codex · {DEFAULT_MODEL} · {DEFAULT_EFFORT} · ChatGPT 登录"
    ttk.Label(frame, text=model_label).pack(anchor="w", pady=(4, 14))
    world, authorization, status, execution, login, usage, detail = (tk.StringVar() for _ in range(7))
    for item in (world, status, authorization, execution, login, usage, detail):
        ttk.Label(frame, textvariable=item, wraplength=550).pack(anchor="w", pady=3)
    buttons = ttk.Frame(frame)
    buttons.pack(anchor="w", pady=(14, 0))
    shortcuts = ttk.Frame(frame)
    shortcuts.pack(anchor="w", pady=(8, 0))

    def button(parent, label, callback):
        control = ttk.Button(parent, text=label, command=callback)
        control.pack(side="left", padx=(0, 6))
        return control

    start_button = button(buttons, "启动 / 重连", lambda: app.commands.put("start"))
    agent_button = button(buttons, "停止 Agent", lambda: app.commands.put("stop_agent"))
    game_button = button(buttons, "保存并退出游戏", lambda: app.commands.put("exit_game"))
    button(buttons, "ChatGPT 登录", lambda: app.commands.put("login"))

    def open_local(path):
        try:
            os.startfile(path)
        except OSError as error:
            detail.set(str(error))

    def previews():
        game = app.state.get("game_dir")
        folder = Path(game) / "architect-previews" if game else None
        images = list(folder.glob("*/*/proposed.png")) if folder else []
        if images:
            open_local(max(images, key=lambda path: path.stat().st_mtime_ns).parent)
        else:
            detail.set("尚无设计预览。游戏内向 @Builder 提出设计需求。")

    def settings():
        dialog = tk.Toplevel(root)
        dialog.title("MC AI Builder 设置")
        dialog.transient(root)
        fields = {key: tk.StringVar(value=app.settings.get(key, "")) for key in INPUT_LABELS}
        for index, (key, label) in enumerate(INPUT_LABELS.items()):
            ttk.Label(dialog, text=label).grid(row=index, column=0, sticky="w", padx=8, pady=8)
            ttk.Entry(dialog, textvariable=fields[key], width=43).grid(row=index, column=1)
            def browse(key=key):
                result = (filedialog.askopenfilename(parent=dialog, filetypes=[("Blender", "blender.exe")])
                          if key == "blender" else filedialog.askdirectory(parent=dialog))
                if result:
                    fields[key].set(result)
            ttk.Button(dialog, text="选择…", command=browse).grid(row=index, column=2, padx=8)
        survival = tk.BooleanVar(value=app.settings.get("survival", False))
        ttk.Checkbutton(dialog, text="下次启动使用独立的生存供料练习世界", variable=survival).grid(
            row=3, column=0, columnspan=3, sticky="w", padx=8, pady=8)

        share_observations = tk.BooleanVar(value=app.settings.get("share_observations") is True)
        ttk.Checkbutton(dialog, text="允许副本中的主动观察与视觉监管将 NPC 图像发送给 Codex",
                        variable=share_observations).grid(row=4, column=0, columnspan=3, sticky="w", padx=8, pady=8)
        parallel_planning = tk.BooleanVar(value=app.settings.get("parallel_planning") is True)
        ttk.Checkbutton(dialog, text="副本中的住宅施工启用并行 Codex 规划", variable=parallel_planning).grid(
            row=5, column=0, columnspan=3, sticky="w", padx=8, pady=8)

        def save():
            values = {**app.settings, **{key: field.get().strip() for key, field in fields.items()}}
            values["survival"] = survival.get()
            values["share_observations"] = share_observations.get()
            values["parallel_planning"] = parallel_planning.get()
            try:
                write_json(SETUP, values)
                app.settings = values
            except OSError as error:
                detail.set(str(error))
                return
            dialog.destroy()
        ttk.Button(dialog, text="保存", command=save).grid(row=6, column=2, padx=8, pady=8)

    button(shortcuts, "最新预览", previews)
    button(shortcuts, "Agent / 启动日志", lambda: open_local(app.home))
    button(shortcuts, "Minecraft 日志", lambda: open_local(Path(app.state["game_dir"]) / "console.log")
           if app.state.get("game_dir") else detail.set("Minecraft 尚未启动。"))
    button(shortcuts, "设置", settings)

    def worlds():
        if app.game or app.agent:
            detail.set("请先正常保存并退出当前游戏，再切换世界。")
            return
        dialog = tk.Toplevel(root)
        dialog.title("选择 Minecraft 世界")
        dialog.transient(root)
        panel = ttk.Frame(dialog, padding=15)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="真实世界副本会完整复制到 D 盘。请先关闭源世界。").pack(anchor="w")
        choices = [("隔离练习世界", "practice", None)]
        try:
            choices.extend((f"已有副本 · {marker['source_label']} · {path.name[:8]}", "copy", path)
                           for path, marker in existing_copies())
            instance = find_setup(app.settings)["instance"]
            choices.extend((f"复制 BMC4 存档 · {path.name}", "new_copy", path)
                           for path in world_choices(instance))
        except (OSError, KeyError, ValueError) as error:
            ttk.Label(panel, text=str(error), wraplength=525).pack(anchor="w", pady=(8, 0))
        selector = ttk.Combobox(panel, state="readonly", values=[row[0] for row in choices], width=52)
        selector.pack(fill="x", pady=12)
        selector.current(0)

        def select():
            _, mode, path = choices[selector.current()]
            app.commands.put(("choose", mode, path))
            dialog.destroy()

        ttk.Button(panel, text="选择世界", command=select).pack(anchor="e")

    world_button = button(shortcuts, "选择世界", worlds)

    def refresh():
        try:
            while True:
                state = app.window_updates.get_nowait()
                phase = state["phase"]
                if phase == "closed":
                    root.destroy()
                    return
                world.set(state["world_label"] or "可选择隔离练习世界或真实世界副本")
                site = state.get("site")
                if site and site.get("approved"):
                    worksite = site.get("worksite", {})
                    modes = {"terrain": "清草与削平", "trees": "清草、削平与天然小树"}
                    autonomy = modes.get(worksite.get("mode"), "常规审批") if worksite.get("active") else "常规审批"
                    authorization.set("施工区已批准 · " + site["dimension"] + " · 供料箱"
                                      + ("已批准" if site["warehouse_approved"] else "待批准")
                                      + " · 区块" + ("已加载" if site["chunks_loaded"] else "未加载")
                                      + " · " + autonomy)
                elif site:
                    authorization.set("施工区和供料箱待批准 · 游戏内使用 /architect site")
                else:
                    authorization.set("")
                status.set(state["message"])
                execution.set(describe_execution(state.get("execution")))
                login.set(state["login"])
                usage.set("本地预算账本不可读取，请查看 Agent 日志。" if state.get("budget_error") else
                          f"本地预算已用 {state.get('turns', 0)}/{MAX_TURNS} 回合，"
                          f"{state.get('tokens', 0):,}/{MAX_TOKENS:,} Tokens")
                start_button.config(state="disabled" if phase in (
                    "checking", "building", "copying", "starting", "agent_starting", "ready", "stopping", "stopping_agent"
                ) else "normal")
                agent_button.config(state="normal" if state.get("agent_pid") else "disabled")
                game_button.config(state="normal" if state.get("game_pid") else "disabled")
                world_button.config(state="disabled" if state.get("game_pid") or phase in (
                    "checking", "copying", "building", "starting", "stopping"
                ) else "normal")
        except Empty:
            pass
        duplicate = app.home / "duplicate"
        if duplicate.is_file():
            duplicate.unlink(missing_ok=True)
            detail.set("启动器已经运行。请使用这个窗口。")
            root.deiconify()
            root.lift()
        root.after(200, refresh)

    root.protocol("WM_DELETE_WINDOW", lambda: app.commands.put("close"))
    Thread(target=app.run, name="mcai-launcher", daemon=True).start()
    if app.checks or app.settings.get("world_mode"):
        app.commands.put("start")
    root.after(100, refresh)
    root.mainloop()


def main():
    checks = os.environ.get("MC_AI_BUILDER_CHECKS", "")
    if checks not in ("", "forge", "bmc4", "copy"):
        raise ValueError("Unknown isolated acceptance profile.")
    app = Launcher(checks)
    handle = single_launcher()
    if handle is None:
        (app.home / "duplicate").touch()
        if checks:
            with (app.home / "duplicate.log").open("a", encoding="utf-8") as stream:
                print("Duplicate launcher refused", file=stream)
        return
    try:
        if checks:
            (app.home / "control.json").unlink(missing_ok=True)
        window(app)
    except Exception:
        import traceback
        with (app.home / "failure.log").open("a", encoding="utf-8") as stream:
            traceback.print_exc(file=stream)
        from tkinter import messagebox
        messagebox.showerror("MC AI Builder", "启动器遇到错误，请查看 .cache/m5-launcher 中的 failure.log。")
        raise
    finally:
        ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(handle))


if __name__ == "__main__":
    main()
