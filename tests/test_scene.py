from copy import deepcopy
from datetime import datetime, timedelta, timezone
from importlib.util import find_spec
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from src.architect.codex_provider import CodexProvider, scene_input
from src.architect.observations import rgb_png as validate_png
from src.architect.providers import InvalidOutput
from src.architect.scene import decode_scene, scene_context_message, verify_scene, world_context
from src.architect.vision import Observation, analyze
from tests.test_observations import png_chunk, rgb_png


def paired(now):
    identity = str(uuid4())
    fingerprint = "a" * 64
    return {
        "observation_id": identity, "world_fingerprint": fingerprint, "dimension": "minecraft:overworld",
        "capture_status": "captured", "camera_source": "npc_first_person", "npc_uuid": str(uuid4()),
        "position": [1300., -59., 72.], "yaw": -180., "pitch": 0., "fov": 70.,
        "game_tick": 1004, "server_request_tick": 1000, "server_result_tick": 1007,
        "capture_time": now.isoformat(), "world": {
            "observation_id": identity, "fingerprint": fingerprint, "dimension": "minecraft:overworld",
            "sample_position": [1300., -59., 72.], "game_tick": 1007, "frame_tick_delta": 3,
            "sample_span_ticks": 7, "window_min": [-4, -2, -4], "window_max": [4, 4, 4],
            "approved_bounds": {"min": [-9, 0, -8], "max": [20, 9, 7]},
            "unknown_outside_scope": 81, "unknown_unloaded": 9,
            "terrain": {"footing_y_rows_z_then_x": [[0] * 8 + [None] for _ in range(9)],
                        "connected_footing": [[0, 0, 0], [1, 1, 0]], "connected_relief": 1},
            "cells": [{"offset": [0, 1, -3], "id": "minecraft:oak_stairs",
                       "properties": {"facing": "north", "half": "bottom"}, "solid": True}],
            "job": {"state": "completed", "building_version": 1, "processed": 3, "total": 3,
                    "job_id": str(uuid4())},
        },
    }


def scene_report():
    return {
        "scene_summary": "场地有一级台阶。",
        "focal_surface": {"color": "tan", "material": "wood", "evidence": "木纹"},
        "visible_objects": [{"label": "台阶", "source": "both"}],
        "terrain_assessment": {"kind": "stepped", "note": "一级高差"},
        "possible_obstacles": ["前方台阶"],
        "suggested_observations": [], "uncertainties": [],
        "evidence": [{"source": "rgb", "detail": "表面颜色"}, {"source": "forge", "detail": "台阶完整方块状态"}],
        "block_claims": [{"offset": [0, 1, -3], "id": "minecraft:oak_stairs",
                          "properties": [{"key": "facing", "value": "north"}]}],
        "consistency": "consistent",
    }


class SceneContractTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.metadata = paired(self.now)
        self.context = world_context(self.metadata, now=self.now)

    def test_projection_preserves_scope_facts_and_omits_private_fields(self):
        text = scene_context_message(self.context | {"credentials": "private", "message": "do not send"})
        for key in ("npc_uuid", "job_id", "capture_time", "world_fingerprint", "1300", "private", "do not send"):
            self.assertNotIn(key, text)
        self.assertEqual(self.context["cells"][0]["properties"]["facing"], "north")
        self.assertEqual(self.context["unknown_unloaded"], 9)
        self.assertEqual(self.context["view"]["yaw"], 180)

    def test_rejects_stale_or_incoherent_pairs(self):
        for field, override in (
            ("observation_id", str(uuid4())), ("fingerprint", "b" * 64), ("game_tick", 999),
            ("sample_span_ticks", 27), ("sample_position", [1310., -59., 72.]),
        ):
            bad = deepcopy(self.metadata)
            bad["world"][field] = override
            with self.subTest(field=field), self.assertRaises(ValueError):
                world_context(bad, now=self.now)
        with self.assertRaisesRegex(ValueError, "expired"):
            world_context(self.metadata, now=self.now + timedelta(seconds=61))

    def test_block_state_claims_are_verified_mismatched_or_unknown(self):
        report = scene_report()
        matching = verify_scene(decode_scene(json.dumps(report)), self.context)
        self.assertEqual(matching["block_checks"][0]["status"], "verified")
        report["block_claims"][0]["properties"][0]["value"] = "west"
        self.assertEqual(verify_scene(report, self.context)["consistency"], "conflict")
        report["block_claims"][0]["offset"] = [12, 0, 16]
        self.assertEqual(verify_scene(report, self.context)["consistency"], "uncertain")
        report["terrain_assessment"]["kind"] = "flat"
        self.assertEqual(verify_scene(report, self.context)["consistency"], "conflict")

    def test_model_text_does_not_replace_the_authoritative_check(self):
        report = scene_report()
        report["block_claims"][0]["id"] = "minecraft:cobblestone"
        report["block_claims"][0]["properties"] = []
        text = deepcopy(self.context)
        text["cells"][0]["id"] = "minecraft:cobblestone"
        text["cells"][0]["properties"] = {}
        self.metadata["image_sha256"] = "c" * 64
        provider = Mock()
        provider.analyze_scene_with_metrics.return_value = report, {"provider": "local_fixture"}
        result = analyze(Observation(self.metadata, text, rgb_png()), provider)
        self.assertEqual(result["forge_checks"]["block_checks"][0]["status"], "mismatch")
        self.assertEqual(result["forge_checks"]["consistency"], "conflict")

    def test_action_injection_and_invalid_blocks_fail_output_validation(self):
        report = scene_report()
        with self.assertRaises(InvalidOutput):
            decode_scene(json.dumps(report | {"action": "approve"}))
        report["block_claims"][0]["id"] = "../secrets"
        with self.assertRaises(InvalidOutput):
            decode_scene(json.dumps(report))

    def test_damaged_pixels_are_rejected_and_png_metadata_is_removed(self):
        png = rgb_png()
        with_text = png[:33] + png_chunk(b"tEXt", b"Comment\x00hidden") + png[33:]
        self.assertEqual(validate_png(with_text), (png, 1, 1))
        with self.assertRaises(ValueError):
            validate_png(png[:33] + png_chunk(b"IDAT", b"x") + png_chunk(b"IEND", b""))

    @unittest.skipUnless(find_spec("openai_codex"), "optional Codex SDK")
    def test_the_sdk_serializes_both_modalities_with_tool_authority(self):
        from openai_codex._inputs import _to_wire_turn_input

        direct, tool = _to_wire_turn_input(scene_input(scene_context_message(self.context), rgb_png()))
        self.assertEqual(direct, [])
        self.assertEqual(tool.name, "architect_scene")
        self.assertEqual([item.root.type for item in tool.output.root], ["input_text", "input_image"])
        self.assertTrue(tool.output.root[1].root.image_url.startswith("data:image/png;base64,"))

    @unittest.skipUnless(find_spec("openai_codex"), "optional Codex SDK")
    def test_no_provider_call_or_budget_charge_without_consent_and_valid_image(self):
        root = Path(__file__).resolve().parents[1] / ".cache" / "m6-b"
        root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=root) as directory:
            provider = CodexProvider(budget_log=Path(directory) / "fixture.sqlite")
            provider._scene_turn = AsyncMock(return_value=scene_report())
            identity = self.metadata["observation_id"]
            with self.assertRaisesRegex(ValueError, "approval"):
                provider.analyze_scene_with_metrics(self.context, rgb_png(), observation_id=identity)
            with self.assertRaises(ValueError):
                provider.analyze_scene_with_metrics(self.context, b"broken", observation_id=identity, allow_upload=True)
            provider._scene_turn.assert_not_called()
            self.assertEqual(provider.budget.records(), [])
            _, metrics = provider.analyze_scene_with_metrics(self.context, rgb_png(),
                                                             observation_id=identity, allow_upload=True)
            provider._scene_turn.assert_awaited_once()
            self.assertEqual(metrics["scene"]["center_material"], "wood")
            self.assertEqual(len(provider.budget.records()), 1)
