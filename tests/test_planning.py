from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock
from uuid import uuid4

from src.architect.codex_provider import context_message, decode_intent
from src.architect.design import Wall
from src.architect.diagnostics import diagnose
from src.architect.planning import alignment, run_once
from src.architect.providers import InvalidOutput, ModelBudgetExhausted, ModelTimeout
from src.architect.runtime import ArchitectClient, Runtime, SessionStore
from src.assets.projects import ProjectLibrary
from src.construction.client import BridgeError
from tools.mcai_launcher import describe_execution


def inspect(topic):
    return {"action": "inspect_task", "topic": topic}


def choose(strategy, **kw):
    return {"action": "choose_work", "strategy": strategy, **kw}


def say(message):
    return {"action": "chat", "message": message}


class SimulatedBridge(ArchitectClient):
    """Exercise the real ArchitectClient adapter against an in-memory job and live task binding."""

    def __init__(self):
        self.token = "fixture"
        self.job_id, self.lease, self.player_id, self.site = [str(uuid4()) for _ in range(4)]
        self.version = 1
        self.queue = ["current", "west_a", "east_a", "west_b", "east_b"]
        self.processed = 507
        self.state = "paused"
        self.reason = "placement_stalled"
        self.strategies = ["prioritize", "upper_stance", "collect", "craft"]
        self.claimed = False
        self.commands = []
        self.answers = []
        self.supply = {"materials_prepared": None}
        self.strategy = {}

    def task(self):
        return {"job_id": self.job_id, "building_version": self.version, "source_project": "medieval_house",
                "state": self.state, "processed": self.processed, "total": 1478,
                "activity": "navigating" if self.state == "running" else "paused:" + self.reason,
                "npc": {"present": True, "local": [2, 3, 6], "moving": self.state == "running"},
                "next_step": {"local": [3, 4, 7], "block": "minecraft:spruce_log"},
                "last_failure": {"reason": self.reason, "route": {"failed_scaffold_offsets": [[0, -3, 1]]}},
                "strategy": self.strategy.copy()}

    def request(self, message):
        return {"request_id": str(uuid4()), "player_id": self.player_id, "site_authorization": self.site,
                "message": message, "phase": "BUILDING", "job": self.task(), "task": self.task(),
                "dimension": "minecraft:overworld", "position": [22, 61, 33], "facing": "west",
                "suggested_origin": [23, 60, 33], "ground_near_anchor": {},
                "approved_area": {"min": [0, 60, 0], "max": [40, 80, 40]}}

    def advance(self):
        assert self.state == "running"
        self.processed += 1

    def _call(self, endpoint, body=None):
        if endpoint == "architect/answer":
            self.answers.append(body)
            return {}
        assert endpoint == "architect/planning", endpoint
        operation = body["operation"]
        self.commands.append((operation, body))
        if operation == "claim":
            if self.claimed:
                return {}
            self.claimed = True
            return {"job_id": self.job_id, "lease": self.lease, "player_id": self.player_id, "building_version": self.version,
                    "site_authorization": self.site, "event": "failure:507", "task": self.task(), "can_observe": True,
                    "strategies": self.strategies,
                    "groups": [{"group": "g1", "placements": 2}, {"group": "g2", "placements": 2}]}
        if "lease" in body and (body["job_id"], body["building_version"], body["lease"]) != (
                self.job_id, self.version, self.lease):
            raise BridgeError(409, {"error": "stale_planning"})
        if operation == "inspect":
            result = {"result": "completed", "task": self.task()}
            if body["topic"] == "supply":
                result["supply"] = {"warehouse_approved": True, "requirements": {
                    "minecraft:spruce_planks": {"warehouse": 0, "craftable": 4, "missing": 0}}}
            return result
        if operation == "apply":
            if body["strategy"] == "upper_stance":
                raise BridgeError(409, {"error": "planning_group_no_stance"})
            if body["strategy"] == "prioritize":
                assert body["group"] in {"g1", "g2"}
                region = "east" if body["group"] == "g2" else "west"
                future = self.queue[1:]
                self.queue[1:] = [step for step in future if step.startswith(region)]
                self.queue.extend(step for step in future if not step.startswith(region))
            elif body["strategy"] in {"collect", "craft"}:
                self.supply["materials_prepared"] = body["strategy"]
            else:
                assert body["strategy"] == "scaffold_descent"
            self.state = "running"
            self.strategy = {"strategy": body["strategy"], "group": body.get("group")}
            return {"result": "running", "task": self.task()}
        if operation in {"thinking", "finish"}:
            return {}
        raise AssertionError(operation)


class CheckpointBridge(SimulatedBridge):
    def __init__(self):
        super().__init__()
        self.npc_local = [2, 3, 6]
        self.stance = [4, 3, 6]
        self.event = "approved"
        self.execution = None
        self.tried = set()

    def task(self):
        task = super().task()
        task["npc"]["local"] = self.npc_local
        if self.execution:
            task["last_action"] = self.execution.copy()
        return task

    def resolve(self, *, code=None):
        if code is None and self.execution["action"] == "move_to":
            self.npc_local = self.execution["stance"]
        elif code is None:
            self.processed += 2
        self.execution.update(status="failed" if code else "completed", processed=self.processed)
        if code:
            self.execution["code"] = code
        self.event = "action_failed" if code else "action_completed"
        self.claimed = False

    def _call(self, endpoint, body=None):
        assert endpoint == "architect/planning"
        operation = body["operation"]
        if operation == "claim":
            if self.event is None:
                return {}
            reply = super()._call(endpoint, body)
            if reply:
                reply.update(brain=True, event=self.event, can_move=True)
            return reply
        if operation == "inspect" and body["topic"] == "world":
            assert body.get("group") in {None, "g2"}
            result = super()._call(endpoint, body)
            result.update(cells=[{"local": self.stance, "actual": "minecraft:air"}],
                          stances=[{"local": self.stance, "can_reach_target": True}])
            return result
        if operation == "act":
            self.commands.append((operation, body))
            if body["building_version"] != self.version:
                raise BridgeError(409, {"error": "stale_planning"})
            action = body["action"]
            key = (self.processed, action, body.get("group"), tuple(body.get("stance", ())))
            if key in self.tried:
                raise BridgeError(409, {"error": "planning_attempt_exhausted"})
            self.tried.add(key)
            self.execution = {"id": str(uuid4()), "action": action, "status": "accepted",
                              "processed_before": self.processed}
            if action == "move_to":
                assert body["stance"] == self.stance
                self.execution["stance"] = body["stance"]
            else:
                region = {"g1": "west", "g2": "east"}[body["group"]]
                self.queue.sort(key=lambda step: not step.startswith(region))
            self.event = None
            return {"result": "accepted", "execution": self.execution.copy(), "task": self.task()}
        if operation == "finish" and body.get("reason"):
            self.event = None
        return super()._call(endpoint, body)


class PlannerTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.client = SimulatedBridge()
        self.provider = Mock(key=None)
        self.contexts = []
        self.runtime = self.open_runtime()
        model = Wall().building()
        self.runtime.store.reserve(self.client.job_id, "medieval_house", 0, model, (23, 60, 33), 0, {},
                                   "load_project", kind="project", source_project="medieval_house",
                                   goal="在屋后留出通道，完工后保留原结构")

    def open_runtime(self):
        return Runtime(self.client, self.provider, SessionStore(self.root), self.root, None, projects=ProjectLibrary())

    def script(self, *decisions):
        queue = iter(decisions)

        def decide(context):
            self.contexts.append(deepcopy(context))
            decision = next(queue)
            if isinstance(decision, Exception):
                raise decision
            return decision

        self.provider.decide.side_effect = decide

    def test_chat_sees_live_job_and_queries_details_before_replying(self):
        self.client.state = "running"
        self.script(inspect("failure"), say("我在建中世纪双层民居，已完成 507 步。屋后站位刚碰到障碍。"))
        # Mentioning the library project during a question still goes through the model.
        request = self.client.request("Builder，你现在在建造 medieval_house 吗？怎么一直在屋后走？")
        self.runtime.handle(request)
        task = self.contexts[0]["task"]
        self.assertEqual((task["name"], task["processed"], task["total"], task["activity"]),
                         ("中世纪双层民居", 507, 1478, "navigating"))
        self.assertEqual(task["npc"]["local"], [2, 3, 6])
        self.assertNotIn(self.client.job_id, context_message(self.contexts[0]))
        self.assertEqual(self.contexts[1]["task"]["last_failure"]["reason"], "placement_stalled")
        self.assertEqual(self.contexts[1]["tool_results"][0]["result"], "completed")
        self.assertEqual([op for op, _ in self.client.commands], ["inspect"])
        self.assertEqual(self.client.answers[0]["request_id"], request["request_id"])
        self.assertIn("507 步", self.client.answers[0]["message"])

    def test_recovery_uses_a_different_skill_after_a_blocked_result(self):
        self.script(inspect("failure"), choose("upper_stance"), choose("prioritize", group="g2"),
                    inspect("status"), say("屋后站位仍然走不到，我先做东侧支撑。"))
        run_once(self.runtime)
        self.assertEqual(self.client.queue, ["current", "east_a", "east_b", "west_a", "west_b"])
        self.assertEqual(self.client.state, "running")
        self.assertEqual(self.contexts[2]["tool_results"][-1]["code"], "planning_group_no_stance")
        self.assertEqual(self.contexts[3]["tool_results"][-1]["result"], "running")
        self.assertEqual(self.contexts[4]["task"]["processed"], 507)
        self.assertEqual(self.contexts[4]["task"]["state"], "running")
        self.assertEqual(self.client.commands[-1][0], "finish")
        self.assertTrue(self.client.commands[-1][1]["notify"])
        self.assertEqual(self.client.commands[-1][1]["reason"], "屋后站位仍然走不到，我先做东侧支撑。")
        self.script(say("我先做东侧支撑。"))
        self.open_runtime().handle(self.client.request("恢复后接着做哪一组？"))
        self.assertEqual(self.contexts[-1]["task"]["strategy"]["group"], "g2")
        self.assertEqual(self.contexts[-1]["task"]["memory"][-1]["group"], "g2")
        self.assertEqual(self.client.processed, 507)

    def test_scaffold_cleanup_recovery_passes_the_bridge_receipt_to_the_model(self):
        self.client.reason = "scaffold_cleanup_unreachable"
        self.client.strategies = ["scaffold_descent", "prioritize"]
        self.script(choose("scaffold_descent"), say("我先从低处拆除临时脚手架。"))
        run_once(self.runtime)
        action = next(body for operation, body in self.client.commands if operation == "apply")
        self.assertEqual(action["strategy"], "scaffold_descent")
        self.assertEqual(action["job_id"], self.client.job_id)
        self.assertEqual(self.contexts[0]["task"]["last_failure"]["reason"], "scaffold_cleanup_unreachable")
        self.assertEqual(self.contexts[1]["tool_results"][-1]["result"], "running")
        self.assertEqual(self.contexts[1]["task"]["state"], "running")

    def test_supply_choices_reach_the_real_adapter(self):
        for strategy in ("collect", "craft"):
            with self.subTest(strategy=strategy):
                self.client.claimed = False
                self.client.state = "paused"
                self.script(inspect("supply"), choose(strategy), say("下一批材料已安排。"))
                run_once(self.runtime)
                self.assertEqual(self.client.supply["materials_prepared"], strategy)
                self.assertEqual(self.contexts[-2]["tool_results"][-1]["supply"]["requirements"]
                                 ["minecraft:spruce_planks"]["craftable"], 4)
                self.assertEqual(self.contexts[-1]["tool_results"][-1]["result"], "running")

    def test_expired_plan_cannot_change_a_running_job_or_block_chat(self):
        self.client.state = "running"
        planning_started, release_planning = Event(), Event()

        def slow_model(context):
            if context.get("event") == "construction" and not context["tool_results"]:
                planning_started.set()
                if not release_planning.wait(5):
                    raise AssertionError("planner was not released")
                return choose("prioritize", group="g2")
            if context.get("event") == "construction":
                self.assertEqual(context["tool_results"][-1]["code"], "stale_planning")
                return say("这次策略已经过期。")
            return say("施工继续，已经完成 508 步。")

        self.provider.decide.side_effect = slow_model
        initial = self.client.queue.copy()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run_once, self.runtime)
            try:
                self.assertTrue(planning_started.wait(5))
                self.client.advance()
                self.runtime.handle(self.client.request("进度如何？"))
                self.assertIn("508", self.client.answers[0]["message"])
                self.client.version += 1
            finally:
                release_planning.set()
            future.result()
        self.assertEqual(self.client.processed, 508)
        self.assertEqual(self.client.queue, initial)

    def test_a_repeated_failed_choice_is_not_sent_twice_and_memory_survives_restart(self):
        self.script(choose("upper_stance"), choose("upper_stance"), say("这个站位不可达。"))
        run_once(self.runtime)
        self.assertEqual([op for op, _ in self.client.commands].count("apply"), 1)
        self.assertEqual(self.contexts[-1]["tool_results"][-1]["code"], "strategy_already_attempted")
        run_once(self.runtime)
        self.assertEqual(self.provider.decide.call_count, 3)
        self.script(say("施工仍停在屋后。"))
        self.open_runtime().handle(self.client.request("上次任务到哪里了？"))
        task = self.contexts[-1]["task"]
        self.assertEqual(task["goal"], "在屋后留出通道，完工后保留原结构")
        self.assertEqual(task["processed"], 507)
        self.assertTrue(any(row.get("code") == "planning_group_no_stance" for row in task["memory"]))
        self.assertEqual([op for op, _ in self.client.commands].count("apply"), 1)

    def test_provider_failure_reports_the_block_without_mutating_the_queue(self):
        for error in (ModelTimeout("time"), ModelBudgetExhausted("usage"), InvalidOutput("bad")):
            with self.subTest(error=type(error).__name__):
                self.client.commands.clear()
                self.client.claimed = False
                self.script(error)
                run_once(self.runtime)
                self.assertEqual([op for op, _ in self.client.commands], ["claim", "thinking", "finish"])
                self.assertTrue(self.client.commands[-1][1]["reason"])
                self.assertEqual(self.client.state, "paused")

    def test_vision_requires_consent_and_the_model_gets_the_rejection(self):
        self.script({"action": "observe_task"}, say("我只能读取现场记录。"))
        run_once(self.runtime)
        self.assertNotIn("capture", [op for op, _ in self.client.commands])
        self.assertEqual(self.contexts[-1]["tool_results"][-1]["result"], "blocked")

    def test_brain_reads_then_moves_and_builds_across_runtime_restart(self):
        self.client = CheckpointBridge()
        self.runtime = self.open_runtime()
        self.script({**inspect("world"), "group": "g2"},
                    {"action": "move_to", "stance": [4, 3, 6], "current_subgoal": "完成东侧支撑",
                     "pending_subgoals": ["接上西侧横梁"]})
        run_once(self.runtime)
        self.assertTrue(self.contexts[-1]["tool_results"][-1]["stances"][0]["can_reach_target"])
        self.assertEqual((self.client.npc_local, self.client.processed), ([2, 3, 6], 507))
        self.assertEqual(self.client.execution["status"], "accepted")
        self.assertEqual(self.client.commands[-1][0], "act")
        self.client.execution["status"] = "running"
        self.runtime = self.open_runtime()
        run_once(self.runtime)
        self.assertEqual(self.provider.decide.call_count, 2)

        self.client.resolve()
        self.script({"action": "check_result"}, {"action": "execute_group", "group": "g2"})
        run_once(self.runtime)
        self.assertEqual(self.contexts[-1]["task"]["current_subgoal"], "完成东侧支撑")
        self.assertEqual(self.contexts[-1]["tool_results"][-1]["execution"]["status"], "completed")
        self.assertEqual(self.contexts[-1]["task"]["npc"]["local"], [4, 3, 6])
        self.assertEqual(self.client.queue[:2], ["east_a", "east_b"])
        self.assertEqual(self.client.processed, 507)

        self.client.resolve()
        self.script({"action": "execute_group", "group": "g1", "current_subgoal": "接上西侧横梁"})
        run_once(self.runtime)
        task = self.contexts[-1]["task"]
        self.assertEqual((task["processed"], task["last_action"]["status"]), (509, "completed"))
        self.assertEqual(task["pending_subgoals"], ["接上西侧横梁"])
        self.assertEqual(self.client.queue[:2], ["west_a", "west_b"])
        self.assertEqual(self.runtime.task_context({"player_id": self.client.player_id,
                                                    "site_authorization": self.client.site},
                                                   self.client.task())["current_subgoal"], "接上西侧横梁")

    def test_brain_replans_after_failure_without_repeating_the_same_move(self):
        self.client = CheckpointBridge()
        self.runtime = self.open_runtime()
        move = {"action": "move_to", "stance": self.client.stance}
        self.script(inspect("world"), move)
        run_once(self.runtime)
        self.client.resolve(code="survey_unreachable")
        self.script(inspect("world"), move, move, {"action": "execute_group", "group": "g2"})
        run_once(self.runtime)
        self.assertEqual(self.contexts[-4]["task"]["last_action"]["code"], "survey_unreachable")
        self.assertEqual(self.contexts[-2]["tool_results"][-1]["code"], "planning_attempt_exhausted")
        self.assertEqual(self.contexts[-1]["tool_results"][-1]["code"], "action_already_attempted")
        self.assertEqual([row["action"] for op, row in self.client.commands if op == "act"],
                         ["move_to", "move_to", "execute_group"])

    def test_stale_brain_result_cannot_change_work_or_persist_subgoals(self):
        self.client = CheckpointBridge()
        self.runtime = self.open_runtime()

        def expired(_):
            self.client.version += 1
            return {"action": "execute_group", "group": "g2", "current_subgoal": "已经过期的目标"}

        self.provider.decide.side_effect = expired
        before = self.client.queue.copy()
        run_once(self.runtime)
        self.assertIsNone(self.client.execution)
        self.assertEqual(self.client.queue, before)
        memory = self.runtime.store.task_memory(self.client.player_id + "/" + self.client.site, self.client.job_id)
        self.assertEqual(memory, ([], {}))

    def test_brain_can_recover_once_from_invalid_model_output(self):
        self.client = CheckpointBridge()
        self.runtime = self.open_runtime()
        self.script(InvalidOutput("unknown output fields"), {**inspect("world"), "current_subgoal": "检查东侧支撑"},
                    {"action": "execute_group", "group": "g2"})
        run_once(self.runtime)
        self.assertEqual(self.contexts[-2]["tool_results"][-1]["code"], "invalid_model_output")
        self.assertEqual(self.contexts[-1]["task"]["current_subgoal"], "检查东侧支撑")
        self.assertEqual(self.client.execution["status"], "accepted")

    def test_schema_rejects_direct_actions_and_untyped_strategies(self):
        invalid = [
            '{"action":"choose_work","strategy":"prioritize","group":"../roof"}',
            '{"action":"choose_work","strategy":"execute_command","group":"g1"}',
            '{"action":"inspect_task","topic":"world","command":"say test"}',
            '{"action":"choose_work","strategy":"craft","group":"g1"}',
            '{"action":"inspect_task","topic":"supply","topic":"status"}',
            '{"action":"move_to","stance":[0,true,0]}',
            '{"action":"set_subgoals","current_subgoal":"梁","pending_subgoals":["A","B","C","D"]}',
        ]
        for response in invalid:
            with self.subTest(response=response), self.assertRaises(InvalidOutput):
                decode_intent(response)
        self.assertEqual(decode_intent('{"action":"choose_work","strategy":"prioritize","group":"g2"}'),
                         choose("prioritize", group="g2"))

    def test_frame_registration_exports_only_bounded_relative_coordinates(self):
        data = {"world": {"anchor": [1000, 78, 1200]},
                "planning_frame": {"origin_from_scene": [2, 0, -3], "clockwise_degrees": 90}}
        self.assertEqual(alignment(data), {"building_origin_relative_to_scene": [2, 0, -3],
                                           "building_clockwise_degrees": 90})
        for point in ([1000, 78, 1200], [True, 0, 0], [0, 0, 0, 0]):
            with self.subTest(point=point), self.assertRaises(ValueError):
                alignment({"planning_frame": {"origin_from_scene": point, "clockwise_degrees": 90}})

    def test_running_and_safe_pause_are_distinct_in_status(self):
        execution = {"building_version": 2, "state": "running", "activity": "to_warehouse",
                     "processed": 8, "total": 183, "planning": {"state": "thinking"}}
        message = describe_execution(execution)
        self.assertIn("前往仓库取料", message)
        self.assertIn("NPC 按当前顺序施工", message)
        execution.update(state="paused", reason="out_of_material", planning={"state": "discarded"})
        self.assertIn("等待材料 · 安全暂停", describe_execution(execution))
        execution.update(activity="planning_checkpoint", planning={"state": "thinking", "brain_event": "action_completed"})
        self.assertIn("已到施工检查点 · 模型正在安排后续工作", describe_execution(execution))
        self.assertEqual(diagnose("moving_frame_mismatch").component, "vision")


if __name__ == "__main__":
    unittest.main()
