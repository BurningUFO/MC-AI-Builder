"""Keep the Builder conversation, stage plans, and render optional previews."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
from dataclasses import asdict
from functools import wraps
from hashlib import sha256
import json
from pathlib import Path
import re
from queue import Empty, Full, Queue
import sqlite3
import subprocess
from threading import Lock, Thread
from time import monotonic, perf_counter_ns, sleep
from uuid import NAMESPACE_URL, UUID, uuid5
from zipfile import BadZipFile

from src.__main__ import render_project
from src.architect.design import DESIGNS, ROTATION, DesignError, fitted_origin, placement, voxel_footings
from src.architect.diagnostics import diagnose
from src.architect.providers import DESIGN, FixtureProvider, ModelError
from src.assets.projects import ProjectError
from src.construction.client import BridgeClient, BridgeError
from src.construction.plan import make_plan
from src.construction.review_preview import preview_review
from src.core.building import Building, write_json
from src.renderers.views import preview_for
from src.architect import feedback, planning, surgical, survey
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

    def inbox(self, *, task_brain=False):
        endpoint = "architect/inbox?task_brain=1" if task_brain else "architect/inbox"
        return self._call(endpoint)["requests"]

    def stage(self, request_id, mode, plan=None):
        body = {"request_id": request_id, "mode": mode}
        if plan is not None:
            body["plan"] = plan
        return self._call("architect/stage", body)

    def answer(self, request_id, message, *, review=False, control=None, visual=False, material_lines=()):
        body = {"request_id": request_id, "message": message}
        if review:
            body["review"] = True
        if control is not None:
            body["control"] = control
        if visual:
            body["visual_answer"] = True
        if material_lines:
            body["material_lines"] = material_lines
        return self._call("architect/answer", body)

    def preview_ready(self, plan_id, player_id, *, project=None):
        body = {"plan_id": plan_id, "player_id": player_id}
        if project is not None:
            body["project"] = project
        return self._call("architect/preview", body)

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
                    action TEXT NOT NULL, reply TEXT
                );
                CREATE TABLE IF NOT EXISTS task_memory (
                    session TEXT NOT NULL, job TEXT NOT NULL, decisions TEXT NOT NULL, PRIMARY KEY (session, job)
                );
            """)
            if not any(row[1] == "reply" for row in db.execute("PRAGMA table_info(turns)")):
                db.execute("ALTER TABLE turns ADD COLUMN reply TEXT")
            if not any(row[1] == "subgoals" for row in db.execute("PRAGMA table_info(task_memory)")):
                db.execute("ALTER TABLE task_memory ADD COLUMN subgoals TEXT")

    @local_db
    def design(self, db, plan_id):
        row = db.execute("SELECT spec FROM designs WHERE plan_id = ?", (plan_id,)).fetchone()
        return json.loads(row[0]) if row else None

    @local_db
    def reserve(self, db, plan_id, building_id, from_version, model, origin, rotation, parameters, action, *,
                kind="window_wall", footing=None, worksite_order=None, edits=(), project_name=None, source_project=None,
                goal="", player_update=""):
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT spec FROM designs WHERE plan_id = ?", (plan_id,)).fetchone()
        if row:
            return json.loads(row[0])
        largest, = db.execute("SELECT max(version) FROM designs WHERE building = ?", (building_id,)).fetchone()
        version = max(largest or 0, from_version) + 1
        params = parameters if isinstance(parameters, dict) else asdict(parameters)
        plan = make_plan(model, building_id=building_id, version=version, origin=origin, rotation=rotation,
                         lighting=params.get("lighting", "none") != "none", localized=action == "surgical_edit",
                         source_project=source_project)
        plan["plan_id"] = plan_id
        if worksite_order is not None:
            plan["worksite_order"] = worksite_order
        spec = {"kind": kind, "parameters": params, "plan": plan, "action": action, "name": model.name}
        if goal:
            spec["goal"] = goal
        if player_update:
            spec["player_update"] = player_update
        if project_name is not None:
            spec["project_name"] = project_name
        if edits:
            spec["edits"] = edits
        if footing is not None:
            spec["footing"] = footing
        folder = self.directory(plan_id)
        model.write(folder / "building.json")
        write_json(folder / "construction-plan.json", plan)
        db.execute("INSERT INTO designs VALUES (?, ?, ?, ?)", (plan_id, building_id, version, json.dumps(spec)))
        return spec

    @local_db
    def dialog(self, db, player_id):
        rows = db.execute("SELECT message, action, reply FROM turns WHERE player = ? ORDER BY rowid DESC LIMIT 4",
                          (player_id,)).fetchall()
        return [{"message": message, "action": action, **({"reply": reply} if reply else {})}
                for message, action, reply in reversed(rows)]

    @local_db
    def remember(self, db, request_id, player_id, message, action, reply=None):
        db.execute("INSERT OR IGNORE INTO turns (request_id, player, message, action, reply) VALUES (?, ?, ?, ?, ?)",
                   (request_id, player_id, message, action, reply))

    @local_db
    def task_memory(self, db, session, job):
        row = db.execute("SELECT decisions, subgoals FROM task_memory WHERE session = ? AND job = ?",
                         (session, job)).fetchone()
        return (json.loads(row[0]), json.loads(row[1]) if row[1] else {}) if row else ([], {})

    @local_db
    def save_work(self, db, session, job, decision=None, *, subgoals=None):
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT decisions, subgoals FROM task_memory WHERE session = ? AND job = ?",
                         (session, job)).fetchone()
        recent, saved = row if row else ("[]", None)
        if decision is not None:
            decisions = json.loads(recent)
            if decision not in decisions:
                recent = json.dumps([*decisions[-5:], decision], ensure_ascii=False)
        if subgoals is not None:
            saved = json.dumps(subgoals, ensure_ascii=False)
        if not row or (recent, saved) != row:
            db.execute("INSERT OR REPLACE INTO task_memory (session, job, decisions, subgoals) VALUES (?, ?, ?, ?)",
                       (session, job, recent, saved))
            db.execute("DELETE FROM task_memory WHERE session = ? AND rowid NOT IN "
                       "(SELECT rowid FROM task_memory WHERE session = ? ORDER BY rowid DESC LIMIT 8)", (session, session))

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
    rows = supply.get("requirements", {})

    def quantities(key):
        return "、".join(f"{item.removeprefix('minecraft:')} ×{row[key]}"
                        + (" 耐久" if row["unit"] == "durability" else "")
                        for item, row in sorted(rows.items()) if row.get(key, 0))

    required = quantities("required") or "无剩余材料需求"
    short = quantities("missing")
    crafted = quantities("craftable")
    inputs = "、".join(f"{item.removeprefix('minecraft:')} ×{count}"
                      for item, count in sorted(supply.get("recipe_inputs", {}).items()))
    production = f"我会{'用附近的 ' + inputs if inputs else '用手头的原料'}做出 {crafted}。" if crafted else ""
    if not rows and not supply.get("warehouse_approved"):
        return "准备好后，把材料放进附近箱子或木桶，再叫我来盘点。"
    if not supply.get("warehouse_approved") and rows:
        return f"需要 {required}。把材料放进附近箱子或木桶，跟我说「材料齐了」，我就去盘点。"
    if approval:
        return required + (f"。还差 {short}。" if short else "。附近库存够用。") + production
    if supply.get("reason") == "warehouse_unreachable":
        return "我暂时走不到存放下一批材料的容器。能把材料挪到附近容易走到的箱子吗？"
    if supply.get("reason") == "workstation_unreachable":
        return "我需要附近一处可以走到的工位或空地，才能接着处理原料。"
    if supply.get("reason") == "workshop_inventory":
        return "我需要再盘一遍背包和箱子里的原料或工具。"
    return production + (f"还差 {short}。补到附近的箱子后告诉我一声。" if short else "附近库存够用。")



def project_material_lines(supply, blocks):
    rows = supply.get("requirements", {}) if supply else {}
    if rows:
        entries = []
        for item, row in sorted(rows.items()):
            name = item.removeprefix("minecraft:")
            units = " 耐久" if row["unit"] == "durability" else ""
            shortage = f"（还差 {row['missing']}）" if supply.get("warehouse_approved") and row["missing"] else ""
            entries.append(f"{name} ×{row['required']}{units}{shortage}")
    else:
        entries = [f"{item} ×{count}" for item, count in sorted(blocks.items())]
    lines = []
    current = ""
    for item in entries:
        if current and len(current) + len(item) > 235:
            lines.append(current)
            current = ""
        current += ("、" if current else "") + item
    if current:
        lines.append(current)
    if len(lines) > 16:
        lines = [*lines[:15], "完整清单已随本次审阅记录保存。"]
    return [f"用料 {index}/{len(lines)}  {line}" for index, line in enumerate(lines, 1)]


def project_origin(model, context, rotation):
    low, high = model.bounds()

    def rotate(x, z):
        return ((x, z), (-z, x), (-x, -z), (z, -x))[rotation // 90]

    corners = [rotate(x, z) for x in (low[0], high[0]) for z in (low[2], high[2])]
    horizontal_min = tuple(map(min, zip(*corners)))
    horizontal_max = tuple(map(max, zip(*corners)))
    minimum = (horizontal_min[0], low[1], horizontal_min[1])
    maximum = (horizontal_max[0], high[1], horizontal_max[1])
    allowed = context["approved_area"]
    first = tuple(a - b for a, b in zip(allowed["min"], minimum, strict=True))
    last = tuple(a - b for a, b in zip(allowed["max"], maximum, strict=True))
    if any(a > b for a, b in zip(first, last, strict=True)):
        raise ProjectError("这份原始工程放不进当前施工区。请为它标记一块更大的区域。")
    front_x, front_z = rotate((low[0] + high[0]) // 2, low[2])
    x, y, z = context["suggested_origin"]
    origin = x - front_x, y, z - front_z
    return tuple(max(a, min(b, value)) for a, b, value in zip(first, last, origin, strict=True))

class Runtime:
    def __init__(self, client, provider, store, instance, blender, *, render=preview_review, projects=None,
                 project_render=render_project, share_observations=False, parallel_planning=False):
        self.client = client
        self.provider = provider
        self.store = store
        self.instance = instance
        self.blender = blender
        self.render = render
        self.projects = projects
        self.project_render = project_render
        self.share_observations = share_observations
        self.parallel_planning = parallel_planning
        self.observation_cache = Path(__file__).resolve().parents[2] / ".cache" / "observations" / store.root.parent.name
        self.preview_queue = Queue(maxsize=8)
        self.preview_lock = Lock()
        self.preview_thread = None
        self.trace = Trace(store.root / "performance.jsonl")
        self.redactions = (client.token, getattr(provider, "key", None))

    @staticmethod
    def conversation_key(request):
        player = request["player_id"]
        return player + "/" + request["site_authorization"] if "site_authorization" in request else player

    def clean(self, text):
        return safe_message(text, self.redactions)

    def task_context(self, owner, facts):
        if not facts or "job_id" not in facts:
            return {}
        job_id = facts["job_id"]
        task = {key: facts[key] for key in (
            "state", "activity", "building_version", "processed", "total", "placed", "remaining", "target_count",
            "reason", "source_project", "next_step", "recent_steps", "last_failure", "npc", "strategy", "supply_phase",
            "temporary_blocks", "preparation", "revision_approved", "blueprint", "last_action",
        ) if key in facts}
        spec = self.store.design(job_id) or {}
        source = facts.get("source_project")
        title = spec.get("name") or spec.get("parameters", {}).get("name")
        if source and self.projects is not None:
            title = next((project.title for project in self.projects.catalog() if project.name == source), title)
        task["name"] = self.clean(title or source or {
            "cottage": "木石小屋", "window_wall": "窗墙",
        }.get(spec.get("kind"), "当前建筑"))[:100]
        task["goal"] = self.clean(spec.get("goal") or f"完成已批准的{task['name']}")[:240]
        if spec.get("player_update"):
            task["approved_player_update"] = self.clean(spec["player_update"])[:240]
        task["memory"], subgoals = self.store.task_memory(self.conversation_key(owner), job_id)
        if task.get("state") != "completed":
            task.update(subgoals)
        if "remaining" not in task and "total" in task and "processed" in task:
            task["remaining"] = task["total"] - task["processed"]
        return task

    def remember_work(self, offer, intent, result):
        entry = None
        if intent["action"] != "set_subgoals":
            entry = {"event": offer["event"], "action": intent["action"], "result": result["result"]}
            entry.update({key: intent[key] for key in ("strategy", "group", "stance") if key in intent})
            if "code" in result:
                entry["code"] = result["code"]
            if intent.get("message"):
                entry["reason"] = self.clean(intent["message"])[:140]
        subgoals = None
        if "current_subgoal" in intent and result["result"] in {"accepted", "completed"}:
            subgoals = {"current_subgoal": self.clean(intent["current_subgoal"])[:140],
                        "pending_subgoals": [self.clean(goal)[:140] for goal in intent.get("pending_subgoals", [])]}
            self.trace.emit("brain_subgoals", pending=len(subgoals["pending_subgoals"]))
        self.store.save_work(self.conversation_key(offer), offer["job_id"], entry, subgoals=subgoals)

    def remember_outcome(self, offer):
        outcome = offer["task"].get("last_action")
        if outcome and outcome["status"] in {"completed", "failed"}:
            self.store.save_work(self.conversation_key(offer), offer["job_id"], outcome)
            self.trace.emit("brain_outcome", **outcome)

    def remember_observation(self, offer, scene):
        frame = scene["scene_to_building"]
        self.store.save_work(self.conversation_key(offer), offer["job_id"], {
            "event": offer["event"], "observation": frame, "at_step": offer["task"]["processed"],
            "focus": scene.get("focus_sample"),
            "nearby_blocks": [{"offset": cell["offset"], "id": cell["id"]} for cell in scene["cells"][:4]],
        })

    def language_context(self, request: dict, message: str) -> dict:
        context = {key: request[key] for key in ("dimension", "position", "facing", "suggested_origin",
                                                 "approved_area", "ground_near_anchor")}
        context["message"] = message
        if self.projects is not None:
            context["workspace_projects"] = [project.context() for project in self.projects.catalog()[:40]]
        for key in ("phase", "event", "sites", "gaze"):
            if key in request:
                context[key] = request[key]
        context["recent"] = self.store.dialog(self.conversation_key(request))
        if "available_mod_blocks" in request:
            context["available_mod_blocks"] = sorted(request["available_mod_blocks"])
        if "supply" in request:
            supply = request["supply"]
            context["supply"] = {key: supply[key] for key in ("mode", "warehouse_approved", "containers",
                                                               "reason") if key in supply}
            rows = supply.get("requirements", {})
            context["supply"]["requirements"] = dict(sorted(rows.items(), key=lambda row: not row[1].get("missing"))[:16])
            context["supply"]["requirements_count"] = len(rows)
        job = request.get("job")
        visible = request.get("draft") or job
        if job is not None:
            context["construction"] = {key: job[key] for key in ("state", "building_version", "processed", "total")}
            if "supply" in job:
                context["construction"]["supply_phase"] = job["supply"]["phase"]
            context["task"] = self.task_context(request, request.get("task") or job)
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
            if "sites" in context:
                context["sites"] = [{**option, "origin": relative(option["origin"])} for option in context["sites"]]
            if "design" in context:
                context["design"]["origin"] = relative(context["design"]["origin"])
            if "gaze" in context:
                context["gaze"] = {**context["gaze"], "position": relative(context["gaze"]["position"])}
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
        reply = None
        try:
            prepared = self.store.design(self.proposal_id(request_id))
            if request.get("event") == "supply_update":
                reply = self.material_reply(request, message)
                action = "get_material_status"
            elif request.get("retry_worksite"):
                self.review(request_id, "review", player_id=request["player_id"])
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
                        self.review(request_id, "new", spec["plan"], observation_reason=reason, player_id=request["player_id"])
                        action = intent["action"]
                else:
                    self.review(request_id, "new", prepared["plan"], player_id=request["player_id"])
                    action = prepared["action"]
            elif prepared is None:
                context = self.language_context(request, message)
                with timed("intent_provider"):
                    library_intent = (self.projects.conversation_intent(message)
                                      if self.projects is not None and isinstance(self.provider, FixtureProvider) else None)
                    intent = library_intent or planning.decide(self, context, request=request)
                action = intent["action"]
            else:
                action = prepared["action"]
                intent = {"action": action}
            if not any(request.get(flag) for flag in ("survey", "feedback", "retry_worksite", "event")):
                reply = self.dispatch(request, intent, prepared)
        except BridgeError as error:
            code = error.body.get("error") or "bridge_error"
            diagnostic = self.record_diagnostic(code)
            if code == "stale_request":
                return
            message = error.body.get("message", diagnostic.recovery) if code.startswith("project_") else diagnostic.recovery
            self.client.answer(request_id, safe_message(message, self.redactions))
            action = code
        except (DesignError, ProjectError) as error:
            self.client.answer(request_id, safe_message(str(error), self.redactions))
            action = "design_error"
            self.record_diagnostic(action)
        except ModelError as error:
            action = planning.error_code(error)
            self.client.answer(request_id, self.record_diagnostic(action).recovery)
        except (subprocess.SubprocessError, BadZipFile, FileNotFoundError, ValueError):
            action = "design_error"
            reply = "这次没能整理出可施工的方案。可以再说一下你想保留的尺寸、材料或位置。"
            self.client.answer(request_id, reply)
        self.store.remember(request_id, self.conversation_key(request), message, action, reply)

    def material_reply(self, request, message):
        supply = request.get("supply", {})
        opening = ""
        try:
            intent = planning.decide(self, self.language_context(request, message), request=request)
            if intent["action"] in {"get_material_status", "chat", "clarify"}:
                opening = safe_message(intent.get("message", ""), self.redactions)
        except ModelError as error:
            opening = self.record_diagnostic(planning.error_code(error)).recovery
        reply = safe_message(" ".join(filter(None, (opening, describe_supply(supply)))), self.redactions)
        self.client.answer(request["request_id"], reply)
        return reply

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
        if action == "surgical_edit":
            return self.local_edit(request, intent)
        if action == "load_project":
            return self.load_project(request, intent)
        prior = None
        old_version = 0
        kind = intent.get("kind", "window_wall")
        factory = DESIGNS[kind]
        prefix = "wall" if kind == "window_wall" else kind
        building_id = prefix + "_" + UUID(request["request_id"]).hex
        if action == "revise_design":
            visible = request.get("draft") or request.get("job")
            if visible is None:
                raise DesignError("请先告诉我你想从哪一份建筑方案开始。")
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
        elif kind == "voxel":
            building = parameters.building(request.get("available_mod_blocks", {}))
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
        elif kind == "voxel" and prior is None:
            site_context = request
            if "site_authorization" in request and isinstance(intent.get("origin"), list):
                intent = {**intent, "origin": [a + b for a, b in zip(request["suggested_origin"], intent["origin"], strict=True)]}
            origin, rotation = placement(intent, site_context, None, False)
            if "origin" not in intent:
                origin = fitted_origin(building, request, rotation)
        elif kind == "voxel":
            origin, rotation = placement({"origin": prior["plan"]["origin"], "rotation": prior["plan"]["rotation"]},
                                         request, prior["plan"], True)
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
        if kind == "voxel":
            footing = voxel_footings(building, request.get("sites", ()), origin, rotation,
                                     prior=prior.get("footing", []) if prior else None)
        edits = prior.get("edits", []) if prior else []
        for edit in edits:
            surgical.apply_edit(building, {"kind": kind, "parameters": asdict(parameters)}, edit["edit"], edit["gaze"])
        worksite_order = intent.get("_worksite_order") if request.get("worksite") else None
        plan_id = self.proposal_id(request["request_id"])
        archive = self.projects.archive(building, plan_id, kind) if self.projects is not None and action == "create_design" else None
        spec = self.store.reserve(plan_id, building_id, old_version,
                                  building, origin, rotation, parameters, action, kind=kind, footing=footing,
                                  worksite_order=worksite_order, edits=edits, project_name=archive,
                                  goal=prior.get("goal", "") if prior else self.clean(
                                      request.get("pending_worksite_goal") or request.get("message", "")),
                                  player_update=self.clean(request.get("message", "")) if prior else "")
        return spec

    def load_project(self, request, intent):
        if self.projects is None:
            raise ProjectError("当前 Architect 会话没有工作区项目库。")
        name, model, _, _ = self.projects.load_building_project(intent["project_name"])
        place = dict(intent)
        if "site_authorization" in request and isinstance(place.get("origin"), list):
            place["origin"] = [base + offset for base, offset in zip(request["suggested_origin"], place["origin"], strict=True)]
        place.setdefault("rotation", request.get("site_rotation", (ROTATION[request["facing"]] + 180) % 360))
        origin, rotation = placement(place, request, None, False)
        if "origin" not in place:
            origin = project_origin(model, request, rotation)
        plan_id = self.proposal_id(request["request_id"])
        try:
            return self.store.reserve(plan_id, "project_" + UUID(plan_id).hex, 0, model, origin, rotation, {},
                                      "load_project", kind="project", project_name=name, source_project=name,
                                      goal=self.clean(request.get("message", "")))
        except ValueError as error:
            raise ProjectError(str(error)) from error

    def local_edit(self, request, intent):
        visible = request.get("draft") or request.get("job")
        plan_id = (visible.get("plan_id") or visible.get("job_id")) if visible else None
        prior = self.store.design(plan_id) if plan_id else None
        if prior is None:
            raise DesignError("我需要一份已有的方案，才能找出要改的那一处。")
        plan = prior["plan"]
        if intent["base_version"] != plan["building_version"]:
            raise DesignError("方案已经更新。请重新指一下你想改的地方。")
        model = Building.read(self.store.directory(plan_id) / "building.json")
        gaze = surgical.local_gaze(request.get("gaze"), plan)
        edit = intent["edit"]
        before = model.blocks.copy()
        surgical.apply_edit(model, prior, edit, gaze)
        if model.blocks == before:
            raise DesignError("这处已经用上了你选择的材料。")
        return self.store.reserve(self.proposal_id(request["request_id"]), plan["building_id"], plan["building_version"],
                                  model, plan["origin"], plan["rotation"], prior["parameters"], intent["action"],
                                  kind=prior["kind"], footing=prior.get("footing"),
                                  source_project=plan.get("source_project"), edits=[*prior.get("edits", []), {"edit": edit, "gaze": gaze}],
                                  goal=prior.get("goal", ""), player_update=self.clean(request.get("message", "")))

    def review(self, request_id, mode, plan=None, *, observation_reason="", player_id=None, proposal_message=""):
        with timed("bridge_review"):
            report = self.client.stage(request_id, mode, plan)
        version = report["new_version"]
        plan_id = version["plan_id"]
        self.trace.emit("proposal", request_id=request_id, plan_id=plan_id, building_version=version["building_version"])
        counts = report["counts"]
        materials = {}
        for row in report["changes"]:
            if row["category"] in ("add", "replace"):
                material = row["desired"]["id"].removeprefix("minecraft:")
                materials[material] = materials.get(material, 0) + 1
        details = "、".join(f"{kind} ×{count}" for kind, count in sorted(materials.items())) or "无需新增材料"
        reasons = {"protected_facility": "保护设施", "protected_area": "玩家保护区",
                   "outside_effect": "可能影响范围外方块", "outside_worksite": "超出施工区",
                   "hazardous_terrain": "液体或危险地形", "site_not_loaded": "区块尚未加载",
                   "terrain_effect_outside_scope": "会覆盖区域外的草方块"}
        conflicts = [str(tuple(row["world"])) + reasons.get(row.get("reason"), "")
                     for row in report["changes"] if row["category"] == "conflict"][:3]
        supply = report.get("supply")
        if supply:
            details = describe_supply(supply, approval=True)
        project = version.get("source_project") or (plan or {}).get("source_project")
        material_lines = project_material_lines(supply, materials) if project and len(details) > 235 else ()
        if material_lines:
            details = "明细见下方"
            if supply and not supply.get("warehouse_approved"):
                details += "。把材料放进附近箱子，再告诉我「材料齐了」"
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
        opening = safe_message(proposal_message, self.redactions)
        prefix = f"{opening} " if opening else ""
        if observed:
            prefix += f"现场判断 {observed}。 "
        reply = safe_message(prefix + f"候选 V{version['building_version']}。保留 {counts['keep']}，"
                             f"补建 {counts['add']}，拆除 {counts['remove']}，替换 {counts['replace']}，"
                             f"保护对象 {counts['conflict']}。材料 {details.rstrip('。')}{reason}。", self.redactions)
        if material_lines:
            self.client.answer(request_id, reply, review=True,
                               material_lines=[safe_message(line, self.redactions)[:300] for line in material_lines])
        else:
            self.client.answer(request_id, reply, review=True)
        self.queue_preview(report, player_id)
        return reply

    def queue_preview(self, report, player_id):
        with self.preview_lock:
            try:
                self.preview_queue.put_nowait((report, player_id))
            except Full:
                return
            if self.preview_thread is None:
                self.preview_thread = Thread(target=self.render_previews, name="architect-preview", daemon=True)
                self.preview_thread.start()

    def render_previews(self):
        while True:
            with self.preview_lock:
                try:
                    report, player_id = self.preview_queue.get_nowait()
                except Empty:
                    self.preview_thread = None
                    return
            plan_id = report["new_version"]["plan_id"]
            try:
                folder = self.store.directory(plan_id)
                report_file = folder / "review.json"
                write_json(report_file, report)
                spec = self.store.design(plan_id)
                project = spec.get("project_name") if spec else None
                if project is not None:
                    self.render_archived(plan_id, project, player_id)
                elif self.blender is not None:
                    with self.trace.bind(plan_id=plan_id), timed("blender_review"):
                        if not (folder / "proposed.png").is_file():
                            self.render(report_file, self.instance, folder, self.blender, multi_angle=True)
                    if player_id is not None and (folder / "proposed.png").is_file():
                        self.client.preview_ready(plan_id, player_id)
            except Exception as error:
                self.trace.emit("preview_background_failure", plan_id=plan_id, error_type=type(error).__name__)

    def render_archived(self, plan_id, name, player_id):
        _, model, data, output = self.projects.load_building_project(name)
        snapshot = Building.read(self.store.directory(plan_id) / "building.json")
        if model != snapshot or output.resolve() != output:
            return
        preview = preview_for(name, model, name=name)
        verification = output / "verification.json"
        try:
            ready = (json.loads(verification.read_text(encoding="utf-8")).get("building_sha256")
                     == sha256(data.read_bytes()).hexdigest() and all((output / file).is_file() for file in preview.files))
        except (OSError, ValueError, AttributeError):
            ready = False
        if not ready and self.blender is not None:
            with self.trace.bind(plan_id=plan_id), timed("blender_project"):
                self.project_render(self.blender, data, output, name, quality="draft", instance=self.instance, timeout=300)
            ready = all((output / file).is_file() for file in preview.files)
        if ready and player_id is not None:
            self.client.preview_ready(plan_id, player_id, project={
                "name": name, "image": preview.views[0].image, "scene": preview.scene_file,
            })

    def dispatch(self, request, intent, prepared):
        request_id = request["request_id"]
        action = intent["action"]
        if action in DESIGN:
            spec = prepared or self.design(request, intent)
            message = intent.get("message", "")
            if action == "load_project":
                name = spec["project_name"]
                project = self.projects.resolve(name)
                title = project.title if project else name
                message = (f"已载入《{title}》（共 {len(spec['plan']['actions'])} 个方块）。"
                           f"全息虚影就在施工区，预览工程位于 previews/{name}/。")
            return self.review(request_id, "new" if action in {"create_design", "load_project"} else "revise",
                               spec["plan"], player_id=request["player_id"], proposal_message=message)
        elif action in PREVIEW_ACTIONS:
            return self.review(request_id, "review", player_id=request["player_id"])
        if action in STATUS_ACTIONS and not intent.get("message"):
            job = request.get("job")
            phases = {"running": "施工中", "paused": "已暂停", "completed": "已完工", "cancelled": "已取消", "failed": "需要检查"}
            text = (f"V{job['building_version']} {phases.get(job['state'], '准备中')}，"
                    f"已完成 {job['processed']}/{job['total']} 步。" if job else "我还没有在建的项目。")
            if job and job.get("reason") and job["reason"] != "awaiting_materials":
                text += " " + diagnose(job["reason"]).recovery
            elif job and job.get("supply", {}).get("phase") == "processing":
                text += " 我在准备下一批方块，手头有合成和烧炼的活。"
        elif action == "list_projects":
            projects = self.projects.catalog() if self.projects is not None else ()
            entries = "、".join(f"{project.title}（{project.name}）" for project in projects[:8])
            more = f"一共 {len(projects)} 份。" if len(projects) > 8 else ""
            text = (f"现成项目有 {entries}。{more}报个名字，我就能在施工区投影给你看。"
                    if projects else "项目库里还没有可用的建筑。你可以先告诉我想设计什么。")
        elif action == "get_capabilities":
            text = "我能和你选址、设计建筑、盘点附近的材料，等你批准后动手施工。也可以边聊边改图。"
        elif action == "get_material_status":
            text = describe_supply(request.get("supply", {}))
        elif action == "get_world_context":
            text = f"我建议从 {tuple(request['suggested_origin'])} 开始勘察。"
        elif action == "pause_construction":
            self.client.pause(request_id)
            text = "我先停下来，等你下一步安排。"
        elif action in ("request_resume", "request_cancel"):
            verb = action.removeprefix("request_")
            self.client.answer(request_id, "请点击按钮确认。", control=verb)
            return "请点击按钮确认。"
        else:
            text = ""
        reply = safe_message(" ".join(filter(None, (intent.get("message"), text))), self.redactions)
        self.client.answer(request_id, reply)
        return reply

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
                    requests = self.client.inbox(task_brain=True) if self.parallel_planning else self.client.inbox()
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
                    if planner_task is None and not requests and not pending:
                        planner_task = planner_pool.submit(planning.run_once, self)
                        next_planner_poll = monotonic() + 1.25
                live = {request["request_id"] for request in requests}
                failures = {key: value for key, value in failures.items() if key in live and value > monotonic()}
                for request in requests:
                    request_id = request["request_id"]
                    if len(pending) < 2 and request_id not in pending and request_id not in failures:
                        pending[request_id] = pool.submit(self.handle, request, perf_counter_ns())
                sleep(0.2)
