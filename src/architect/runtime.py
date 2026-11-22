"""Poll Forge, resolve structured intents and publish local review artifacts."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
from dataclasses import asdict
from functools import wraps
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from threading import Lock
from time import monotonic, perf_counter_ns, sleep
from uuid import NAMESPACE_URL, UUID, uuid5
from zipfile import BadZipFile

from src.architect.design import DESIGNS, DesignError, placement
from src.architect.diagnostics import diagnose
from src.architect.providers import DESIGN, InvalidOutput, ModelBudgetExhausted, ModelError, ModelTimeout, parse_intent
from src.construction.client import BridgeClient, BridgeError
from src.construction.plan import make_plan
from src.construction.review_preview import preview_review
from src.core.building import write_json
from src.architect import feedback, planning, survey
from src.architect.telemetry import Trace, timed

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

    def observe_world(self):
        return self._call("architect/observe_world")

    def survey(self, request_id, *, step=None, action=None, offset=None):
        body = {"request_id": request_id}
        if action is not None:
            body.update(step=step, action=action)
        if offset is not None:
            body["offset"] = offset
        return self._call("architect/survey", body)

    def planning(self, operation, **data):
        return self._call("architect/planning", {"operation": operation, **data})

    def inbox(self):
        return self._call("architect/inbox")["requests"]

    def stage(self, request_id, mode, plan=None):
        body = {"request_id": request_id, "mode": mode}
        if plan is not None:
            body["plan"] = plan
        return self._call("architect/stage", body)

    def answer(self, request_id, message, *, review=None, control=None, visual=False):
        body = {"request_id": request_id, "message": message}
        if review is not None:
            body["review_sha256"] = review
        if control is not None:
            body["control"] = control
        if visual:
            body["visual_answer"] = True
        return self._call("architect/answer", body)

    def pause(self, request_id):
        return self._call("architect/pause", {"request_id": request_id})


def local_db(method):
    @wraps(method)
    def call(store, *args, **kwargs):
        with closing(sqlite3.connect(store.database, timeout=4)) as connection, connection:
            return method(store, connection, *args, **kwargs)
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
    def reserve(self, db, plan_id, building_id, from_version, model, origin, rotation, parameters, action, *,
                kind="window_wall", footing=None, worksite_order=None):
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT spec FROM designs WHERE plan_id = ?", (plan_id,)).fetchone()
        if row:
            return json.loads(row[0])
        largest, = db.execute("SELECT max(version) FROM designs WHERE building = ?", (building_id,)).fetchone()
        version = max(largest or 0, from_version) + 1
        plan = make_plan(model, building_id=building_id, version=version, origin=origin, rotation=rotation)
        plan["plan_id"] = plan_id
        if worksite_order is not None:
            plan["worksite_order"] = worksite_order
        spec = {"kind": kind, "parameters": asdict(parameters), "plan": plan, "action": action}
        if footing is not None:
            spec["footing"] = footing
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
    def __init__(self, client, provider, store, instance, blender, *, render=preview_review,
                 share_observations=False, parallel_planning=False):
        self.client = client
        self.provider = provider
        self.store = store
        self.instance = instance
        self.blender = blender
        self.render = render
        self.share_observations = share_observations
        self.parallel_planning = parallel_planning
        self.observation_cache = Path(__file__).resolve().parents[2] / ".cache" / "observations" / store.root.parent.name
        if parallel_planning and not callable(getattr(provider, "plan_with_metrics", None)):
            raise ValueError("Parallel planning requires a structured planning provider.")
        self.render_lock = Lock()
        self.trace = Trace(store.root / "performance.jsonl")
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
                context["design"] = {"kind": spec.get("kind", "window_wall"), "version": plan["building_version"],
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

    def handle(self, request, queued=None):
        job = request.get("job", {})
        with self.trace.bind(request_id=request["request_id"], job_id=job.get("job_id"),
                             building_version=job.get("building_version")):
            if queued is not None:
                self.trace.emit("runtime_queue", duration_ms=(perf_counter_ns() - queued) / 1e6,
                                bridge_queue_ms=request.get("bridge_queue_ms"))
            with timed("runtime_request"):
                self._handle(request)

    def _handle(self, request):
        request_id = request["request_id"]
        message = scrub(request["message"], self.redactions)
        action = "error"
        try:
            prepared = self.store.design(self.proposal_id(request_id))
            if request.get("retry_worksite"):
                self.review(request_id, "review")
                action = "preview_design"
            elif request.get("feedback"):
                action = feedback.check(self, request, message, prepared=prepared,
                                        safe=lambda value: safe_message(value, self.redactions))
            elif request.get("survey"):
                if prepared is None:
                    if request.get("worksite"):
                        recent = self.store.dialog(self.conversation_key(request))
                        if recent and recent[-1]["action"] == "worksite_question":
                            request = {**request, "pending_worksite_goal": recent[-1]["message"][:240]}
                    intent, reason = survey.suggest(self, request, message)
                    if intent is None:
                        self.client.answer(request_id, safe_message(reason, self.redactions))
                        action = "worksite_question" if request.get("worksite") else "unsuitable"
                    else:
                        spec = self.design(request, intent)
                        self.review(request_id, "new", spec["plan"], observation_reason=reason)
                        action = intent["action"]
                else:
                    self.review(request_id, "new", prepared["plan"])
                    action = prepared["action"]
            elif prepared is None:
                context = self.language_context(request, message)
                with timed("intent_provider"):
                    intent = parse_intent(self.provider.decide(context))
                action = intent["action"]
            else:
                action = prepared["action"]
                intent = {"action": action}
            if not any(request.get(flag) for flag in ("survey", "feedback", "retry_worksite")):
                self.dispatch(request, intent, prepared)
        except BridgeError as error:
            code = error.body.get("error") or "bridge_error"
            diagnostic = self.record_diagnostic(code)
            if code == "stale_request":
                return
            self.client.answer(request_id, diagnostic.recovery)
            action = code
        except DesignError as error:
            self.client.answer(request_id, safe_message(str(error), self.redactions))
            action = "design_error"
            self.record_diagnostic(action)
        except (ModelTimeout, InvalidOutput, ModelBudgetExhausted, ModelError) as error:
            action = {ModelTimeout: "model_timeout", InvalidOutput: "invalid_model_output",
                      ModelBudgetExhausted: "model_budget_exhausted"}.get(type(error), "model_unavailable")
            self.client.answer(request_id, self.record_diagnostic(action).recovery)
        except (subprocess.SubprocessError, BadZipFile, FileNotFoundError, ValueError):
            action = "preview_error"
            recovery = self.record_diagnostic(action).recovery
            if request.get("worksite"):
                recovery += "修复预览后可用 /architect work retry 复核同一方案。"
            self.client.answer(request_id, recovery)
        self.store.remember(request_id, self.conversation_key(request), message, action)

    def record_diagnostic(self, code):
        info = diagnose(code)
        self.trace.emit("diagnostic", code=code, component=info.component, kind=info.kind)
        return info

    @staticmethod
    def proposal_id(request_id):
        return str(uuid5(NAMESPACE_URL, "minecraft-architect:" + str(UUID(request_id))))

    def design(self, request, intent):
        with timed("building_generation"):
            return self._design(request, intent)

    def _design(self, request, intent):
        action = intent["action"]
        prior = None
        old_version = 0
        kind = intent.get("kind", "window_wall")
        factory = DESIGNS[kind]
        prefix = "wall" if kind == "window_wall" else kind
        building_id = prefix + "_" + UUID(request["request_id"]).hex
        if action == "revise_design":
            visible = request.get("draft") or request.get("job")
            if visible is None:
                raise DesignError("请先设计一面窗墙或一栋住宅。")
            prior = self.store.design(visible.get("plan_id") or visible["job_id"])
            if prior is None:
                raise DesignError("找不到本地设计参数，请重新发起设计。")
            if kind != prior.get("kind", "window_wall"):
                raise DesignError("原建筑和本次改图的类型必须一致。")
            plan = prior["plan"]
            old_version = plan["building_version"]
            if old_version != intent["base_version"]:
                raise DesignError("引用的建筑版本已过期。请描述对当前版本的改动。")
            building_id = plan["building_id"]
        base = factory.edit(prior["parameters"]) if prior else None
        parameters = factory.edit(intent["changes"], base)
        footing = None
        if kind == "cottage":
            footing = prior.get("footing") if prior else intent.get("_survey_footing") if request.get("survey") else None
            if footing is not None and base and (parameters.width, parameters.depth) != (base.width, base.depth):
                raise DesignError("更改已勘察住宅的占地需要重新观察地基。")
            building = parameters.building(footing or ())
        else:
            building = parameters.building()
        blocks = {block.state.block_id for block in building.blocks.values()}
        missing = sorted(block for block in blocks if not block.startswith("minecraft:")
                         and block not in request.get("available_mod_blocks", {}))
        if missing:
            raise DesignError("当前世界未启用这些受支持的模组材料 " + "、".join(missing) + "。")
        draft = request.get("draft")
        locked = draft["revision"] if draft is not None else "job" in request
        if request.get("survey"):
            origin = tuple(base + offset for base, offset in zip(request["suggested_origin"], intent["origin"], strict=True))
            rotation = intent["rotation"]
        elif "site_authorization" in request:
            rotation = request["site_rotation"]
            if intent.get("origin") not in (None, "here", [0, 0, 0]) or intent.get("rotation") not in (None, "here", rotation):
                raise DesignError("副本使用玩家已授权的原点和朝向。更换位置时请重新审阅施工区。")
            origin = tuple(request["suggested_origin"])
            if prior:
                origin, rotation = tuple(prior["plan"]["origin"]), prior["plan"]["rotation"]
        else:
            origin, rotation = placement(intent, request, prior["plan"] if prior else None,
                                         action == "revise_design" and locked)
        worksite_order = intent.get("_worksite_order") if request.get("worksite") else None
        spec = self.store.reserve(self.proposal_id(request["request_id"]), building_id, old_version,
                                  building, origin, rotation, parameters, action, kind=kind, footing=footing,
                                  worksite_order=worksite_order)
        return spec

    def review(self, request_id, mode, plan=None, *, observation_reason=""):
        with timed("bridge_review"):
            report = self.client.stage(request_id, mode, plan)
        version = report["new_version"]
        plan_id = version["plan_id"]
        self.trace.emit("proposal", request_id=request_id, plan_id=plan_id, building_version=version["building_version"])
        digest = report["review_sha256"]
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Bridge returned an invalid review hash")
        folder = self.store.directory(plan_id) / digest[:16]
        folder.mkdir(parents=True, exist_ok=True)
        report_file = folder / "review.json"
        previews = ("revision-review.blend", "proposed.png", "proposed-rear.png")
        if version["building_id"].startswith("cottage_"):
            previews += ("proposed-interior.png",)
        with self.render_lock:
            if not all((folder / name).is_file() for name in previews):
                write_json(report_file, report)
                with timed("blender_review", plan_id=plan_id):
                    self.render(report_file, self.instance, folder, self.blender, multi_angle=True)
        counts = report["counts"]
        materials = {}
        for row in report["changes"]:
            if row["category"] in ("add", "replace"):
                material = row["desired"]["id"].removeprefix("minecraft:")
                materials[material] = materials.get(material, 0) + 1
        details = "、".join(f"{kind} ×{count}" for kind, count in sorted(materials.items())) or "无需新增材料"
        reasons = {"occupied_target": "已有方块", "missing_terrain_support": "缺少支撑",
                   "unsafe_site_boundary": "边缘有敏感方块", "terrain_effect_outside_scope": "会覆盖区域外的草方块",
                   "protected_facility": "保护设施", "protected_area": "玩家保护区", "player_structure": "玩家改动",
                   "outside_effect": "可能影响范围外或保护对象", "unsafe_interaction": "原生挖掘不可用",
                   "hazardous_terrain": "液体或危险地形", "worksite_preparation_changed": "清理现场已变化"}
        conflicts = [str(tuple(row["world"])) + reasons.get(row.get("reason"), "")
                     for row in report["changes"] if row["category"] == "conflict"][:3]
        if "supply" in report:
            details = describe_supply(report["supply"], approval=True)
        reason = f"。冲突位置 {'、'.join(conflicts)}，请先处理" if conflicts else ""
        if grass := report.get("covered_grass"):
            reason += f"。建筑覆盖的 {len(grass)} 处区域内草方块可能自然变为泥土，位置见审阅报告"
        if preparation := report.get("preparation"):
            summary = preparation["counts"]
            labels = {"vegetation": "草木", "soil": "泥土", "stone": "天然石块", "logs": "原木", "leaves": "树叶",
                      "wood": "木料", "masonry": "砌块", "hand": "其他可清理障碍"}
            groups = "、".join(f"{labels[kind]} {count}" for kind, count in summary.items())
            reason += "。现场准备 " + (groups or "无需清理")
        observed = safe_message(observation_reason, self.redactions)[:160].rstrip("。.!！")
        prefix = f"现场判断 {observed}。 " if observed else ""
        self.client.answer(request_id, prefix + f"候选 V{version['building_version']}。保留 {counts['keep']}，"
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
                text += f" [{reason}] " + diagnose(reason).recovery
            self.client.answer(request_id, text)
        elif action == "get_capabilities":
            self.client.answer(request_id, "我是 MC Builder。我能设计窗墙和单层住宅、生成 Blender 预览、"
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
        self.trace.start()
        try:
            self._poll(should_stop)
        finally:
            self.trace.close()

    def _poll(self, should_stop):
        pending = {}
        failures = {}
        disconnected = False
        planner_task = None
        next_planner_poll = 0
        with ExitStack() as stack:
            pool = stack.enter_context(ThreadPoolExecutor(max_workers=2, thread_name_prefix="architect"))
            planner_pool = stack.enter_context(ThreadPoolExecutor(max_workers=1, thread_name_prefix="architect-plan")) \
                if self.parallel_planning else None
            while not should_stop():
                for request_id, future in list(pending.items()):
                    if not future.done():
                        continue
                    del pending[request_id]
                    try:
                        future.result()
                    except (OSError, BridgeError, sqlite3.Error, ValueError) as error:
                        self.trace.emit("runtime_delivery", request_id=request_id, error_type=type(error).__name__)
                        failures[request_id] = monotonic() + 1
                try:
                    requests = self.client.inbox()
                except (OSError, BridgeError, ValueError):
                    if not disconnected:
                        self.record_diagnostic("bridge_unavailable")
                        disconnected = True
                    sleep(1)
                    continue
                if disconnected:
                    self.trace.emit("bridge_restored")
                    disconnected = False
                if planner_pool is not None and monotonic() >= next_planner_poll:
                    if planner_task is not None and planner_task.done():
                        try:
                            planner_task.result()
                        except BridgeError as error:
                            self.record_diagnostic(error.body.get("error", "bridge_unavailable"))
                        except (OSError, ValueError, KeyError):
                            self.record_diagnostic("bridge_unavailable")
                        planner_task = None
                    if planner_task is None:
                        planner_task = planner_pool.submit(planning.run_once, self, lambda value: scrub(value, self.redactions))
                        next_planner_poll = monotonic() + 1.25
                live = {request["request_id"] for request in requests}
                failures = {key: value for key, value in failures.items() if key in live and value > monotonic()}
                for request in requests:
                    request_id = request["request_id"]
                    if len(pending) < 2 and request_id not in pending and request_id not in failures:
                        pending[request_id] = pool.submit(self.handle, request, perf_counter_ns())
                sleep(0.2)
