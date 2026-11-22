import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.architect.diagnostics import diagnose
from src.architect.planning import alignment, decode, run_once
from src.architect.providers import InvalidOutput, ModelBudgetExhausted, ModelTimeout
from src.architect.telemetry import Trace
from src.construction.client import BridgeError
from tools.mcai_launcher import describe_execution


OFFER = {
    "job_id": "local job", "lease": "local lease", "building_version": 2,
    "processed": 6, "total": 183, "user_hint": "prefer rear",
    "default_group": "roof_front", "groups": [{"group": "roof_front"}, {"group": "roof_rear"}],
    "roof_materials": {"minecraft:spruce_planks": 63},
}


class PlannerTest(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.provider = Mock()
        self.commands = []
        self.runtime = SimpleNamespace(client=self.client, provider=self.provider,
                                       trace=Trace(None), share_observations=False)

        def planning(operation, **data):
            self.commands.append((operation, data))
            if operation == "claim":
                return OFFER
            if operation == "resolve":
                return {"state": "applied" if "group" in data else "kept"}
            return {}

        self.client.planning.side_effect = planning

    def run_planner(self):
        run_once(self.runtime, lambda value: value)

    def test_only_bounded_group_and_local_binding_are_sent(self):
        self.provider.plan_with_metrics.return_value = ({"group": "roof_rear", "reason": "通行优先",
                                                         "decision": "prioritize"}, {})
        self.run_planner()
        payload, image = self.provider.plan_with_metrics.call_args.args
        self.assertIsNone(image)
        self.assertFalse(self.provider.plan_with_metrics.call_args.kwargs["allow_upload"])
        self.assertNotIn("job_id", payload)
        self.assertNotIn("lease", payload)
        self.assertNotIn("building_version", payload)
        self.assertEqual(self.commands, [
            ("claim", {}), ("thinking", {"job_id": OFFER["job_id"], "lease": OFFER["lease"]}),
            ("resolve", {"job_id": OFFER["job_id"], "lease": OFFER["lease"], "group": "roof_rear", "reason": "通行优先"}),
        ])

    def test_invalid_timeout_and_budget_preserve_deterministic_order(self):
        for exception in (ModelTimeout("time"), ModelBudgetExhausted("usage"), InvalidOutput("bad")):
            with self.subTest(exception=type(exception).__name__):
                self.commands.clear()
                self.provider.plan_with_metrics.reset_mock()
                self.provider.plan_with_metrics.side_effect = exception
                self.run_planner()
                self.assertEqual([action for action, _ in self.commands], ["claim", "thinking", "resolve"])
                self.assertNotIn("group", self.commands[-1][1])
                self.provider.plan_with_metrics.assert_called_once()

    def test_camera_loss_falls_back_to_structured_context_and_only_one_reobservation(self):
        self.runtime.share_observations = True
        self.provider.plan_with_metrics.return_value = ({"group": None, "reason": "查看通道", "decision": "observe"}, {})
        with patch("src.architect.planning.capture", side_effect=ValueError("moving_frame_mismatch")) as capture:
            self.run_planner()
        self.assertEqual(capture.call_count, 2)
        self.provider.plan_with_metrics.assert_called_once()
        self.assertIsNone(self.provider.plan_with_metrics.call_args.args[1])
        self.assertEqual(self.provider.plan_with_metrics.call_args.args[0]["observation_condition"], "no_paired_frame")
        self.assertNotIn("group", self.commands[-1][1])

    def test_stale_lease_and_disconnected_bridge_receive_no_retried_group(self):
        for failure in (BridgeError(409, {"error": "stale_planning"}), ConnectionRefusedError()):
            with self.subTest(failure=type(failure).__name__):
                self.client.planning.side_effect = [OFFER, {}, failure, failure]
                self.provider.plan_with_metrics.reset_mock()
                self.provider.plan_with_metrics.return_value = ({"group": "roof_rear", "reason": "通行优先",
                                                                  "decision": "prioritize"}, {})
                self.run_planner()
                self.provider.plan_with_metrics.assert_called_once()
                self.assertNotIn("group", self.client.planning.call_args.kwargs)

    def test_schema_rejects_direct_actions_extra_fields_duplicates_and_new_groups(self):
        invalid = [
            '{"decision":"prioritize","group":"elsewhere","reason":"invalid"}',
            '{"decision":"keep","group":null,"reason":"invalid","command":"say test"}',
            '{"decision":"keep","decision":"observe","group":null,"reason":"invalid"}',
            '{"decision":"observe","group":"roof_front","reason":"invalid"}',
        ]
        for response in invalid:
            with self.subTest(response=response), self.assertRaises(InvalidOutput):
                decode(response)
        self.assertEqual(decode('{"decision":"keep","group":null,"reason":"checked"}')["decision"], "keep")

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
        self.assertEqual(diagnose("moving_frame_mismatch").component, "vision")


if __name__ == "__main__":
    unittest.main()
