from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, Mock, call, patch
from uuid import uuid4

from src.architect.codex_budget import TOKEN_RESERVATION, CodexBudget
from src.architect.codex_provider import MAX_CONTEXT_BYTES, CodexProvider, context_message, decode_intent
from src.architect.design import CHIPPED_WOOD, QUARK_ROOF, QUARK_STAIR, DesignError, Wall, placement
from src.architect.providers import FixtureProvider, HttpProvider, InvalidOutput, ModelError, parse_intent
from src.architect.runtime import Runtime, SessionStore, scrub
from src.construction.plan import make_plan


class ArchitectTests(unittest.TestCase):
    def test_natural_changes_generate_different_blocks_and_dimensions(self):
        provider = FixtureProvider()
        first = provider.decide({"message": "在这里建一面 7 格宽的中世纪窗墙，用云杉与石砖"})
        wall = Wall.edit(first["changes"])
        visible = {"version": 1, "parameters": {"window_width": 2}}
        revised = provider.decide({"message": "把这面窗墙改成 8 格宽，把木材换成深色橡木", "design": visible})
        next_wall = Wall.edit(revised["changes"], wall)
        self.assertEqual(first["origin"], "here")
        self.assertEqual(revised["base_version"], 1)
        self.assertEqual(wall.building().bounds()[1][0], 6)
        self.assertEqual(next_wall.building().bounds()[1][0], 7)
        self.assertEqual(next_wall.building().blocks[(0, 1, 0)].state.block_id, "minecraft:dark_oak_planks")

    def test_glass_only_revision_preserves_all_non_glass_positions(self):
        first = Wall()
        target = replace(first, glass="minecraft:blue_stained_glass")
        existing, revised = first.building().blocks, target.building().blocks
        self.assertEqual(set(existing), set(revised))
        changed = {pos for pos in revised if revised[pos] != existing[pos]}
        self.assertEqual(changed, {(x, 1, 0) for x in (1, 2, 5, 6)})
        args = {"building_id": "wall_test", "origin": (6, -60, 6)}
        self.assertNotEqual(make_plan(first.building(), version=1, **args)["model_sha256"],
                            make_plan(target.building(), version=2, **args)["model_sha256"])

    def test_rejects_unknown_intents_illegal_blocks_and_bad_dimensions(self):
        for payload in ({"action": "approve"}, {"action": "get_build_status", "command": "give"},
                        {"action": "revise_design", "kind": "window_wall", "changes": {}},
                        {"action": []}):
            with self.subTest(payload=payload), self.assertRaises(InvalidOutput):
                parse_intent(payload)
        for change in ({"wood": "minecraft:tnt"}, {"width": True}, {"height": 0},
                       {"window_width": 3}, {"window_height": 2}, {"commands": []}):
            with self.subTest(change=change), self.assertRaises(DesignError):
                Wall.edit(change)

    def test_default_placement_follows_context_and_existing_placement_locks(self):
        site = {"facing": "east", "suggested_origin": [6, -60, 6]}
        self.assertEqual(placement({}, site, None, False), ((6, -60, 6), 90))
        with self.assertRaises(DesignError):
            placement({"origin": [8, -60, 6]}, site, {"origin": [6, -60, 6], "rotation": 0}, True)

    def test_store_reopens_isolated_context_and_idempotent_design_versions(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            store = SessionStore(root)
            request = str(uuid4())
            key = Runtime.proposal_id(request)
            wall = Wall()
            spec = store.reserve(key, "wall_test", 0, wall.building(), (6, -60, 6), 0, wall, "create_design")
            store.remember(request, "actor-a", "设计窗墙", "create_design")
            store.remember(request, "actor-a", "重复传入不改历史", "create_design")
            reopened = SessionStore(root)
            self.assertEqual(reopened.dialog("actor-b"), [])
            self.assertEqual(reopened.dialog("actor-a"), [{"message": "设计窗墙", "action": "create_design"}])
            self.assertEqual(reopened.design(key), spec)
            self.assertEqual(reopened.reserve(key, "wall_test", 0, wall.building(), (7, -60, 6), 0,
                                              wall, "create_design"), spec)
            revision = reopened.reserve(str(uuid4()), "wall_test", 1, wall.building(), (6, -60, 6), 0,
                                        wall, "revise_design")
            self.assertEqual(revision["plan"]["building_version"], 2)

    def test_roof_change_uses_explicit_modded_stair_states_and_keeps_other_materials(self):
        wall = Wall(wood=CHIPPED_WOOD)
        changed = Wall.edit({"roof": QUARK_ROOF}, wall)
        first, second = wall.building().blocks, changed.building().blocks
        self.assertEqual(set(first), set(second))
        self.assertEqual({pos for pos in first if first[pos] != second[pos]}, {(x, 2, 0) for x in range(8)})
        stairs = [dict(block.state.properties) for block in second.values() if block.state.block_id == QUARK_STAIR]
        self.assertEqual({state["facing"] for state in stairs}, {"east", "west"})
        plan = make_plan(changed.building(), building_id="modded", version=2, origin=(6, -60, 6))
        self.assertTrue(all(row["item_id"] is None for row in plan["actions"] if not row["id"].startswith("minecraft:")))

    def test_codex_context_is_bounded_and_keeps_only_recent_architect_data(self):
        context = {
            "message": "窗户改为蓝色", "player_id": "excluded", "request_id": "excluded",
            "recent": [{"message": "旧" * 1400, "action": "preview_design"},
                       {"message": "刚设计好窗墙", "action": "create_design", "extra": "excluded"}],
        }
        message = context_message(context)
        self.assertLessEqual(len(message.encode("utf-8")), MAX_CONTEXT_BYTES)
        self.assertEqual(json.loads(message), {
            "message": "窗户改为蓝色", "recent": [{"message": "刚设计好窗墙", "action": "create_design"}],
        })

    def test_codex_schema_normalizes_nulls_before_the_existing_validator(self):
        result = decode_intent(json.dumps({
            "action": "revise_design", "kind": "window_wall", "base_version": 1,
            "changes": {"glass": "minecraft:blue_stained_glass", "width": None, "porch": False},
            "origin": None, "rotation": None, "message": None,
        }))
        self.assertEqual(result["changes"], {"glass": "minecraft:blue_stained_glass", "porch": False})
        self.assertNotIn("origin", result)
        for raw in ('{"action":"get_build_status","command":"give"}',
                    '{"action":"approve"}', '{"action":"get_build_status","action":"request_resume"}'):
            with self.subTest(raw=raw), self.assertRaises(InvalidOutput):
                decode_intent(raw)

    def test_codex_budget_survives_restarts_and_accounts_for_missing_usage(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "budget.sqlite3"
            ledger = CodexBudget(path, 2, TOKEN_RESERVATION + 500)
            first = ledger.reserve({"status": "reserved"})
            with self.assertRaises(ModelError):
                CodexBudget(path, 2, TOKEN_RESERVATION + 500).reserve({})
            self.assertTrue(ledger.finish(first, {"status": "completed", "tokens": {"total_tokens": 400}}))
            second = ledger.reserve({"status": "reserved"})
            ledger.finish(second, {"status": "timeout"})
            self.assertEqual([row["charged_tokens"] for row in ledger.records()], [400, TOKEN_RESERVATION])
            self.assertFalse(ledger.finish(first, {"tokens": {"total_tokens": 600}}))
            with self.assertRaises(ModelError):
                ledger.reserve({})

    @patch("src.architect.codex_provider.find_spec", return_value=True)
    def test_codex_errors_cannot_stage_or_control_a_job(self, _):
        context = {
            "dimension": "minecraft:overworld", "position": [6, -60, 6], "facing": "north",
            "approved_area": {"min": [0, -60, 0], "max": [32, -50, 24]},
            "suggested_origin": [6, -60, 6], "ground_near_anchor": [],
            "job": {"job_id": str(uuid4()), "building_version": 2, "state": "paused",
                    "processed": 17, "total": 30},
        }
        for outcome, response in (
            (TimeoutError(), "响应超时"),
            ({"action": "approve"}, "未通过建筑意图校验"),
            ({"action": "create_design", "kind": "window_wall", "changes": {"wood": "minecraft:tnt"}}, "请选择橡木"),
            ({"action": "create_design", "kind": "window_wall", "changes": {"width": 50}}, "宽度需在 5–12 格"),
        ):
            with self.subTest(response=response), TemporaryDirectory() as folder:
                path = Path(folder)
                provider = CodexProvider(budget_log=path / "turns.sqlite3")
                provider._turn = (AsyncMock(side_effect=outcome) if isinstance(outcome, Exception)
                                  else AsyncMock(return_value=outcome))
                client = Mock(token="fixture")
                runtime = Runtime(client, provider, SessionStore(path), path, path)
                request_id = str(uuid4())
                runtime.handle(dict(context, request_id=request_id, player_id="tester", message="改窗墙"))
                self.assertEqual(client.mock_calls, [call.answer(request_id, client.answer.call_args.args[1])])
                self.assertIn(response, client.answer.call_args.args[1])
                self.assertEqual(len(provider.budget.records()), 1)

    def test_privacy_filter_and_endpoint_validation(self):
        text = r"窗墙，D:\Example\Sensitive.csv，token=abcdef123456abcdef，custom-key"
        self.assertEqual(scrub(text, ("custom-key",)), "窗墙，[已隐去]，[已隐去]，[已隐去]")
        with self.assertRaises(ValueError):
            HttpProvider("http://example.invalid/v1", "demo", "key")
        with self.assertRaises(ValueError):
            HttpProvider("https://user:secret@example.invalid/v1", "demo", "key")
        with self.assertRaises(ValueError):
            HttpProvider("https://example.invalid/v1", "demo", "")
        self.assertEqual(HttpProvider("http://127.0.0.1:9999/v1", "demo", "").timeout, 15.0)


if __name__ == "__main__":
    unittest.main()
