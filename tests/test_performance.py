import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src.architect.diagnostics import diagnose
from src.architect.telemetry import Trace, model_turn, timed


class PerformanceTest(unittest.TestCase):
    def test_runtime_trace_links_model_and_render_without_request_payloads(self):
        with TemporaryDirectory() as directory, patch.dict("os.environ", {"MC_AI_BUILDER_PROFILE": "1"}):
            path = Path(directory) / "performance.jsonl"
            trace = Trace(path)
            trace.start()
            with trace.bind(request_id="request", job_id="job", building_version=2):
                with timed("blender_render", plan_id="plan"):
                    pass
                model_turn("planning", {"status": "completed", "elapsed_ms": 20,
                                        "intent": {"message": "not for this log"},
                                        "tokens": {"input_tokens": 30, "output_tokens": 8}})
            trace.close()
            render, model, end = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
            self.assertEqual((render["event"], render["request_id"], render["plan_id"]),
                             ("blender_render", "request", "plan"))
            self.assertEqual((model["job_id"], model["building_version"], model["elapsed_ms"]), ("job", 2, 20))
            self.assertNotIn("intent", model)
            self.assertEqual(end["dropped"], 0)

    def test_expected_rejections_and_failures_have_distinct_operator_actions(self):
        self.assertEqual(diagnose("stale_planning").kind, "rejected")
        self.assertEqual(diagnose("model_timeout").kind, "fault")
        self.assertEqual(diagnose("out_of_material").kind, "condition")
        for code in ("warehouse_changed", "navigation_timeout", "site_not_loaded", "world_observation_stale"):
            self.assertIn("请", diagnose(code).recovery)


if __name__ == "__main__":
    unittest.main()
