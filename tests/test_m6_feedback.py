"""Protect the decision boundary for a supervised construction checkpoint."""

import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from src.architect.codex_provider import WALL_FIELDS
from src.architect.design import DesignError
from src.architect.feedback import check, decode
from src.architect.providers import InvalidOutput


class VisualFeedbackTest(unittest.TestCase):
    def decode(self, decision, changes=None, **extra):
        return decode(json.dumps({"decision": decision, "reason": "局部观测判断", "changes": changes} | extra),
                      WALL_FIELDS)

    def test_only_supported_design_parameters_survive(self):
        value = self.decode("revise", {"glass": "minecraft:blue_stained_glass", "width": None})
        self.assertEqual(value["changes"], {"glass": "minecraft:blue_stained_glass"})

    def test_rejects_action_fields_and_unsupported_decisions(self):
        for dec, changes, extra in (
            ("approve", None, {}), ("continue", {"width": 8}, {}), ("report", None, {"tool": "clear"}),
            ("revise", {"world": [1, 0, 0]}, {}), ("revise", {"glass": None}, {}), ("report", None, {"reason": ""}),
        ):
            with self.subTest(dec=dec, changes=changes, extra=extra), self.assertRaises(InvalidOutput):
                self.decode(dec, changes, **extra)

    def test_unsafe_reframing_uses_one_paired_stationary_frame_and_waits_for_the_player(self):
        identity = str(uuid4())
        provider = Mock(spec=["feedback_with_metrics"])
        provider.feedback_with_metrics.return_value = ({"decision": "continue", "reason": "已核对", "changes": None}, {})
        client = Mock()
        client.observe_world.return_value = {"observation_id": identity}
        store = SimpleNamespace(root=Path("test"), design=lambda _: {"parameters": {"width": 5}})
        runtime = SimpleNamespace(share_observations=True, provider=provider, client=client, store=store)
        request = {"feedback": "checkpoint", "request_id": identity, "visual_goal": "验收",
                   "job": {"state": "paused", "building_version": 1, "job_id": identity}}
        sample = SimpleNamespace(context={"construction": {"building_version": 1}}, image=b"local", observation_id=identity,
                                 performance={})
        with (patch("src.architect.feedback.collect", side_effect=[
                  {"capture_status": "failed", "capture_error": "survey_unsafe_footing"},
                  {"capture_status": "captured", "observation_id": identity},
              ]) as collect, patch("src.architect.feedback.SiteView.read", return_value=sample),
              patch("src.architect.feedback.scene_context_message", return_value="{}"),
              patch("src.architect.feedback.write_json")):
            self.assertEqual(check(runtime, request, "", safe=str), "continue")
        self.assertEqual([call.args[-1] for call in collect.call_args_list], ["inspect_build_site", "observe_here"])
        provider.feedback_with_metrics.assert_called_once()
        self.assertEqual(provider.feedback_with_metrics.call_args.args[0]["remaining_observations"], 0)
        client.answer.assert_called_once_with(identity, "施工复查。已核对", control="resume", visual=True)

    def test_disconnected_or_running_operation_cannot_capture_or_request_a_model(self):
        for sharing, state in ((False, "paused"), (True, "running")):
            with self.subTest(sharing=sharing, state=state), self.assertRaises(DesignError):
                check(SimpleNamespace(share_observations=sharing, provider=object()),
                      {"feedback": "checkpoint", "request_id": "local", "job": {"state": state}}, "", safe=str)
