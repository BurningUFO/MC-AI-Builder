"""Poll Forge, resolve structured intents and publish local review artifacts."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict
from functools import wraps
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from threading import Lock
from time import monotonic, sleep
from uuid import NAMESPACE_URL, UUID, uuid5
from zipfile import BadZipFile

from src.architect.design import DesignError, Wall, placement
from src.architect.providers import DESIGN, InvalidOutput, ModelBudgetExhausted, ModelError, ModelTimeout, parse_intent
from src.construction.client import BridgeClient, BridgeError
from src.construction.plan import make_plan
from src.construction.review_preview import preview_review
from src.core.building import write_json

PREVIEW_ACTIONS = {"preview_design", "review_changes", "request_approval"}
STATUS_ACTIONS = {"get_build_status", "get_construction_progress"}
PRIVATE_TEXT = re.compile(
    r'''(?ix)
       (?:[A-Z]:[\\/]|\\\\[^\\\s]+\\)[^\r\n,，;；。"'<>]*
     | /(?:home|Users|mnt|private|tmp)/[^\r\n,，;；。"'<>]*
     | \b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{15,}|github_pat_[A-Za-z0-9_]{15,}|AIza[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16})\b
     | \b[A-Fa-f0-9]{64}\b
     | \b(?:bearer|api[_ -]?key|token)\s*[:= ]\s*[A-Za-z0-9_./+=-]{12,}
    '''
)


class ArchitectClient(BridgeClient):
    def observation(self):
        return self._call("architect/observation")

    def inbox(self):
        return self._call("architect/inbox")["requests"]

    def stage(self, request_id, mode, plan=None):
        body = {"request_id": request_id, "mode": mode}
        if plan is not None:
            body["plan"] = plan
        return self._call("architect/stage", body)

    def answer(self, request_id, message, *, review=None, control=None):
        body = {"request_id": request_id, "message": message}
        if review is not None:
            body["review_sha256"] = review
        if control is not None:
            body["control"] = control
        return self._call("architect/answer", body)

    def pause(self, request_id):
        return self._call("architect/pause", {"request_id": request_id})


def local_db(method):
    @wraps(method)
    def call(store, *args):
        with closing(sqlite3.connect(store.database, timeout=4)) as connection, connection:
            return method(store, connection, *args)
    return call


class SessionStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.database = root / "architect.sqlite3"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS designs (
                    plan_id TEXT PRIMARY KEY, building TEXT NOT NULL, version INTEGER NOT NULL,
                    spec TEXT NOT NULL, UNIQUE(building, version)
                );
                CREATE TABLE IF NOT EXISTS turns (
                    request_id TEXT PRIMARY KEY, player TEXT NOT NULL, message TEXT NOT NULL,
                    action TEXT NOT NULL
                );
            """)

    @local_db
    def design(self, db, plan_id):
        row = db.execute("SELECT spec FROM designs WHERE plan_id = ?", (plan_id,)).fetchone()
        return json.loads(row[0]) if row else None

    @local_db
    def reserve(self, db, plan_id, building_id, from_version, model, origin, rotation, wall, action):
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT spec FROM designs WHERE plan_id = ?", (plan_id,)).fetchone()
        if row:
            return json.loads(row[0])
        largest, = db.execute("SELECT max(version) FROM designs WHERE building = ?", (building_id,)).fetchone()
        version = max(largest or 0, from_version) + 1
        plan = make_plan(model, building_id=building_id, version=version, origin=origin, rotation=rotation)
        plan["plan_id"] = plan_id
        spec = {"parameters": asdict(wall), "plan": plan, "action": action}
        folder = self.directory(plan_id)
        model.write(folder / "building.json")
        write_json(folder / "construction-plan.json", plan)
        db.execute("INSERT INTO designs VALUES (?, ?, ?, ?)", (plan_id, building_id, version, json.dumps(spec)))
        return spec

    @local_db
    def dialog(self, db, player_id):
        rows = db.execute("SELECT message, action FROM turns WHERE player = ? ORDER BY rowid DESC LIMIT 4",
                          (player_id,)).fetchall()
        return [{"message": message, "action": action} for message, action in reversed(rows)]

    @local_db
    def remember(self, db, request_id, player_id, message, action):
        db.execute("INSERT OR IGNORE INTO turns VALUES (?, ?, ?, ?)", (request_id, player_id, message, action))

    def directory(self, plan_id):
        return self.root / str(UUID(plan_id))


def scrub(text: str, redactions=()) -> str:
    for secret in redactions:
        if secret:
            text = text.replace(secret, "[已隐去]")
    return PRIVATE_TEXT.sub("[已隐去]", text)


def safe_message(text: str, redactions=()) -> str:
    return "".join(" " if ord(char) < 32 or ord(char) == 127 else char
                   for char in scrub(text, redactions)).strip()[:580]


def describe_supply(supply, *, approval=False):
    if not supply or supply.get("mode") != "survival":
        return "当前使用隔离测试材料。"
    if supply.get("warehouse_approved") is False:
        return "请先批准一个原版供料箱。"
    if supply.get("reason"):
        return "供料箱暂不可用，请核对获准的箱子。"
    rows = supply["requirements"]

    def quantities(key):
        return "、".join(f"{item.removeprefix('minecraft:')} ×{row[key]}"
                        + (" 耐久" if row["unit"] == "durability" else "")
                        for item, row in sorted(rows.items()) if row[key])

    required = quantities("required") or "无剩余材料需求"
    short = quantities("missing")
    if approval:
        return required + (f"。缺料 {short}，需玩家补给" if short else "。获准库存可满足")
    return f"剩余需求 {required}。" + (f"缺料 {short}。请补入同一个供料箱。" if short else "当前无材料缺口。")


class Runtime:
    def __init__(self, client, provider, store, instance, blender, *, render=preview_review):
        self.client = client
        self.provider = provider
        self.store = store
        self.instance = instance
        self.blender = blender
        self.render = render
        self.render_lock = Lock()
        self.redactions = (client.token, getattr(provider, "key", None))

    @staticmethod
    def conversation_key(request):
        player = request["player_id"]
        return player + "/" + request["site_authorization"] if "site_authorization" in request else player

    def language_context(self, request: dict, message: str) -> dict:
        context = {key: request[key] for key in ("dimension", "position", "facing", "suggested_origin",
                                                 "approved_area", "ground_near_anchor")}
        context["message"] = message
        context["recent"] = self.store.dialog(self.conversation_key(request))
        if "available_mod_blocks" in request:
            context["available_mod_blocks"] = sorted(request["available_mod_blocks"])
        if "supply" in request:
            supply = request["supply"]
            context["supply"] = {key: supply[key] for key in ("mode", "warehouse_approved")}
        job = request.get("job")
        visible = request.get("draft") or job
        if job is not None:
            context["construction"] = {key: job[key] for key in ("state", "building_version", "processed", "total")}
        if visible:
            spec = self.store.design(visible.get("plan_id") or visible["job_id"])
            if spec is not None:
                plan = spec["plan"]
                context["design"] = {"kind": "window_wall", "version": plan["building_version"],
                                     "parameters": spec["parameters"], "origin": plan["origin"],
                                     "rotation": plan["rotation"]}
        if "site_authorization" in request:
            anchor = request["suggested_origin"]

            def relative(position):
                return [axis - origin for axis, origin in zip(position, anchor, strict=True)]

            context["fixed_site"] = True
            context["position"] = relative(context["position"])
            context["suggested_origin"] = [0, 0, 0]
            context["approved_area"] = {key: relative(position) for key, position in context["approved_area"].items()}
            if "design" in context:
                context["design"]["origin"] = relative(context["design"]["origin"])
        return context

    def handle(self, request):
        request_id = request["request_id"]
        message = scrub(request["message"], self.redactions)
        action = "error"
        try:
            prepared = self.store.design(self.proposal_id(request_id))
            if prepared is None:
                context = self.language_context(request, message)
                intent = parse_intent(self.provider.decide(context))
                action = intent["action"]
            else:
                action = prepared["action"]
                intent = {"action": action}
            self.dispatch(request, intent, prepared)
        except BridgeError as error:
            code = error.body.get("error")
            if code == "stale_request":
                return
            messages = {
                "review_changed": "现场已变化。请重新审阅，生成的旧确认无法施工。",
                "occupied_target": "目标位置已有不相符的方块。请指定空闲施工位置。",
                "invalid_request": "方案无法通过施工范围或方块约束。请调整尺寸、坐标或材料。",
                "job_active": "已有施工正在进行。请修改属于你的当前任务，或等待当前任务结束。",
                "no_draft": "目前没有候选设计。请先描述要建的窗墙。",
                "warehouse_not_approved": "请先到供料箱旁，用 /architect supply x y z 批准一个原版单箱。",
                "warehouse_unavailable": "供料箱暂不可用。请核对获准的原版单箱。",
                "warehouse_job_scope": "这项施工使用了另一份供料授权。",
                "warehouse_player_approval": "需要由供料箱的授权玩家批准。",
                "checkpoint_npc_unavailable": "当前无法核对原 NPC 或其库存。请回到已批准区域等区块加载，再审阅。",
                "site_not_loaded": "批准的施工区尚未加载，请回到附近后重新审阅。",
            }
            self.client.answer(request_id, messages.get(code, "本次无法继续。请重新询问进度或审阅当前设计。"))
            action = code or "bridge_error"
        except DesignError as error:
            self.client.answer(request_id, safe_message(str(error), self.redactions))
            action = "design_error"
        except ModelTimeout:
            self.client.answer(request_id, "模型服务响应超时。请重发需求，施工状态保持原样。")
            action = "model_timeout"
        except InvalidOutput:
            self.client.answer(request_id, "模型输出未通过建筑意图校验。请重述尺寸或材料。")
            action = "invalid_model_output"
        except ModelBudgetExhausted:
            self.client.answer(request_id, "本地 Codex 预算已用完。请在启动器查看请求数和 Token 用量。")
            action = "model_budget_exhausted"
        except ModelError:
            self.client.answer(request_id, "模型服务暂时不可用。请稍后重发需求。")
            action = "model_unavailable"
        except (subprocess.SubprocessError, BadZipFile, FileNotFoundError, ValueError):
            self.client.answer(request_id, "暂时无法生成三维预览。请检查本地 Blender 和资源后重新预览。")
            action = "preview_error"
        self.store.remember(request_id, self.conversation_key(request), message, action)

    @staticmethod
    def proposal_id(request_id):
        return str(uuid5(NAMESPACE_URL, "minecraft-architect:" + str(UUID(request_id))))

    def design(self, request, intent):
        action = intent["action"]
        prior = None
        old_version = 0
        building_id = "wall_" + UUID(request["request_id"]).hex
        if action == "revise_design":
            visible = request.get("draft") or request.get("job")
            if visible is None:
                raise DesignError("请先设计一面窗墙。")
            prior = self.store.design(visible.get("plan_id") or visible["job_id"])
            if prior is None:
                raise DesignError("找不到本地设计参数，请重新发起一面窗墙。")
            plan = prior["plan"]
            old_version = plan["building_version"]
            if old_version != intent["base_version"]:
                raise DesignError("引用的建筑版本已过期。请描述对当前版本的改动。")
            building_id = plan["building_id"]
        bases = Wall.edit(prior["parameters"]) if prior else None
        wall = Wall.edit(intent["changes"], bases)
        building = wall.building()
        blocks = {block.state.block_id for block in building.blocks.values()}
        missing = sorted(block for block in blocks if not block.startswith("minecraft:")
                         and block not in request.get("available_mod_blocks", {}))
        if missing:
            raise DesignError("当前世界未启用这些受支持的模组材料 " + "、".join(missing) + "。")
        draft = request.get("draft")
        locked = draft["revision"] if draft is not None else "job" in request
        if "site_authorization" in request:
            rotation = request["site_rotation"]
            if intent.get("origin") not in (None, "here", [0, 0, 0]) or intent.get("rotation") not in (None, "here", rotation):
                raise DesignError("副本使用玩家已授权的原点和朝向。更换位置时请重新审阅施工区。")
            origin = tuple(request["suggested_origin"])
        else:
            origin, rotation = placement(intent, request, prior["plan"] if prior else None,
                                         action == "revise_design" and locked)
        spec = self.store.reserve(self.proposal_id(request["request_id"]), building_id, old_version,
                                  building, origin, rotation, wall, action)
        return spec

    def review(self, request_id, mode, plan=None):
        report = self.client.stage(request_id, mode, plan)
        version = report["new_version"]
        plan_id = version["plan_id"]
        digest = report["review_sha256"]
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Bridge returned an invalid review hash")
        folder = self.store.directory(plan_id) / digest[:16]
        folder.mkdir(parents=True, exist_ok=True)
        report_file = folder / "review.json"
        with self.render_lock:
            if not all((folder / name).is_file() for name in ("revision-review.blend", "proposed.png", "proposed-rear.png")):
                write_json(report_file, report)
                self.render(report_file, self.instance, folder, self.blender, multi_angle=True)
        counts = report["counts"]
        materials = {}
        for row in report["changes"]:
            if row["category"] in ("add", "replace"):
                material = row["desired"]["id"].removeprefix("minecraft:")
                materials[material] = materials.get(material, 0) + 1
        details = "、".join(f"{kind} ×{count}" for kind, count in sorted(materials.items())) or "无需新增材料"
        reasons = {"occupied_target": "已有方块", "missing_terrain_support": "缺少支撑",
                   "unsafe_site_boundary": "边缘有敏感方块", "terrain_effect_outside_scope": "会覆盖区域外的草方块"}
        conflicts = [str(tuple(row["world"])) + reasons.get(row.get("reason"), "")
                     for row in report["changes"] if row["category"] == "conflict"][:3]
        if "supply" in report:
            details = describe_supply(report["supply"], approval=True)
        reason = f"。冲突位置 {'、'.join(conflicts)}，请先处理" if conflicts else ""
        if grass := report.get("covered_grass"):
            reason += f"。墙体覆盖的 {len(grass)} 处区域内草方块可能自然变为泥土，位置见审阅报告"
        self.client.answer(request_id, f"候选 V{version['building_version']}。保留 {counts['keep']}，"
                           f"补建 {counts['add']}，拆除 {counts['remove']}，替换 {counts['replace']}，"
                           f"冲突 {counts['conflict']}。材料 {details}{reason}。",
                           review=digest)

    def dispatch(self, request, intent, prepared):
        request_id = request["request_id"]
        action = intent["action"]
        if action in DESIGN:
            spec = prepared or self.design(request, intent)
            self.review(request_id, "new" if action == "create_design" else "revise", spec["plan"])
        elif action in PREVIEW_ACTIONS:
            self.review(request_id, "review")
        elif action in STATUS_ACTIONS:
            job = request.get("job")
            text = (f"V{job['building_version']} {job['state']}，已处理 {job['processed']}/{job['total']} 步。"
                    if job else "当前没有属于你的施工任务。")
            if job and job.get("reason"):
                reason = job["reason"]
                description = {
                    "site_not_loaded": "施工区未加载。请回到区域附近后重新确认继续",
                    "warehouse_unreachable": "NPC 无法安全到达供料箱。请检查区域内的通道",
                    "no_reachable_placement_stance": "目标没有可达的放置位置",
                    "no_reachable_break_stance": "目标没有可达的拆除位置",
                    "navigation_timeout": "NPC 导航超时。请检查高低差和障碍物",
                }.get(reason, reason)
                text += " " + description + "。"
            self.client.answer(request_id, text)
        elif action == "get_capabilities":
            self.client.answer(request_id, "我是 MC Builder。我能设计窗墙、生成 Blender 预览、"
                               "检查材料和施工进度。你批准具体方案后，我会施工，也能暂停并按新方案改建。")
        elif action == "get_material_status":
            self.client.answer(request_id, describe_supply(request.get("supply", {})))
        elif action == "get_world_context":
            self.client.answer(request_id, f"维度 {request['dimension']}，当前位置 {tuple(request['position'])}，"
                               f"建议原点 {tuple(request['suggested_origin'])}，朝向 {request['facing']}。")
        elif action == "pause_construction":
            self.client.pause(request_id)
            self.client.answer(request_id, "施工已暂停。")
        elif action in ("request_resume", "request_cancel"):
            verb = action.removeprefix("request_")
            self.client.answer(request_id, "请点击操作以确认。", control=verb)
        else:
            self.client.answer(request_id, safe_message(intent["message"], self.redactions))

    def run(self, should_stop=lambda: False):
        pending = {}
        failures = {}
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="architect") as pool:
            while not should_stop():
                for request_id, future in list(pending.items()):
                    if not future.done():
                        continue
                    del pending[request_id]
                    try:
                        future.result()
                    except (OSError, BridgeError, sqlite3.Error, ValueError):
                        failures[request_id] = monotonic() + 1
                try:
                    requests = self.client.inbox()
                except (OSError, BridgeError, ValueError):
                    sleep(1)
                    continue
                live = {request["request_id"] for request in requests}
                failures = {key: value for key, value in failures.items() if key in live and value > monotonic()}
                for request in requests:
                    request_id = request["request_id"]
                    if len(pending) < 2 and request_id not in pending and request_id not in failures:
                        pending[request_id] = pool.submit(self.handle, request)
                sleep(0.2)
