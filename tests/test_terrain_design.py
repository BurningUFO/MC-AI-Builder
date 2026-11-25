import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from src.architect.design import DesignError
from src.architect.grammar import generate_roof
from src.architect.runtime import SessionStore
from src.architect.terrain_design import ARTIFACTS, apply_patch, build_candidate, handle, json_hash, scene_terrain, verify_effect
from src.core.building import Building, write_json


def fill(block, start, end):
    return {"mode": "fill", "block": "minecraft:" + block, "from": start, "to": end}


class TerrainDesignTests(unittest.TestCase):
    def setUp(self):
        self.pier = {"id": "pier", "role": "foundation", "modules": [fill("stone", [0, -2, 0], [0, -1, 0])]}
        self.deck = {"id": "deck", "role": "structure", "modules": [fill("oak_planks", [0, 0, 0], [3, 0, 3])]}
        self.window = {"id": "window", "role": "structure", "modules": [fill("glass", [2, 1, 0], [2, 2, 0])]}
        self.design = {"name": "On the slope", "sections": [self.pier, self.deck, self.window],
                       "footprint": {"from": [0, 0, 0], "to": [3, 0, 3]},
                       "excavation": [{"from": [1, 1, 2], "to": [1, 2, 2]}],
                       "landings": [[2, 1, 2]]}

    def test_patch_preserves_deck_and_placement_after_rotation(self):
        identity = {"plan_id": str(uuid4()), "building_id": "voxel_local", "version": 1,
                    "origin": (34, 66, -11), "rotation": 90}
        before, old_plan = build_candidate(self.design, {}, **identity)
        patch = {**self.window, "modules": [fill("light_blue_stained_glass", [2, 1, 0], [2, 2, 0])]}
        edited, changed = apply_patch(self.design, {"sections": [patch]}, {})
        after, plan = build_candidate(edited, {}, **{**identity, "version": 2, "plan_id": str(uuid4())})
        self.assertEqual(changed, 2)
        self.assertEqual({pos for pos in before.blocks if before.blocks[pos] != after.blocks[pos]},
                         {(2, 1, 0), (2, 2, 0)})
        self.assertEqual(old_plan["terrain"], plan["terrain"])
        self.assertEqual(plan["terrain"]["foundation"], [[0, -2, 0], [0, -1, 0]])
        self.assertEqual(old_plan["rotation"], plan["rotation"])
        self.assertEqual(old_plan["origin"], plan["origin"])
        self.assertNotEqual(old_plan["model_sha256"], plan["model_sha256"])

    def test_patch_must_not_repaint_another_section(self):
        patch = {**self.window, "modules": [fill("glass", [2, 0, 0], [2, 2, 0])]}
        with self.assertRaisesRegex(DesignError, "未选中"):
            apply_patch(self.design, {"sections": [patch]}, {})

    def test_adjacent_tables_require_a_native_placement_alternative(self):
        table = {"mode": "fill", "block": "handcrafted:spruce_table", "from": [0, 1, 1], "to": [1, 1, 1]}
        sections = [*self.design["sections"], {"id": "tables", "role": "structure", "modules": [table]}]
        with self.assertRaisesRegex(DesignError, "自动合并状态"):
            build_candidate({**self.design, "sections": sections}, {"handcrafted:spruce_table": {}},
                            plan_id=str(uuid4()), building_id="voxel_local", version=1, origin=(34, 66, -11), rotation=0)

    def test_cut_preview_is_grounded_in_observed_block_beneath_cut(self):
        plan = {"origin": [10, 63, 2], "rotation": 90, "local_bounds": {"min": [0, 0, 0], "max": [1, 4, 1]}}
        field = {"min": [0, -6, 1], "surface_y": [[2]], "surface_block": [["minecraft:grass_block"]]}
        report = {"changes": [{"world": [10, 64, 3], "category": "remove"}],
                  "neighbors": [{"world": [10, 63, 3], "actual": {"id": "minecraft:dirt"}}]}
        self.assertEqual(scene_terrain(field, [10, 62, 2], plan, report), [[1, 0, 1, "grass", 0, "soil"]])

    def test_hollow_gable_keeps_ridge_and_backing_without_filling_attic(self):
        limits = ((0, 8, 0), (16, 8, 12))
        solid = generate_roof("gable", limits, ridge_axis="x")
        hollow = generate_roof("gable", limits, ridge_axis="x", hollow=True)
        self.assertLess(len(hollow), len(solid))
        self.assertEqual(solid[(8, 15, 6)], hollow[(8, 15, 6)])
        self.assertIn((8, 14, 6), hollow)
        self.assertNotIn((8, 9, 6), hollow)

    def test_barn_wall_effect_needs_the_blockstate_inside_the_reported_scope(self):
        before, unrelated, after = [Building("Barn partition") for _ in range(3)]
        for model, axis in ((before, "y"), (unrelated, "y"), (after, "x")):
            model.place((1, 3, 1), "minecraft:oak_log", axis=axis)
        unrelated.place((4, 3, 1), "minecraft:oak_log", axis="x")
        effect = {"kind": "blocks", "box": {"from": [1, 3, 1], "to": [1, 3, 1]},
                  "block": "minecraft:oak_log", "properties": [{"name": "axis", "value": "x"}]}
        with self.assertRaisesRegex(DesignError, "改图后符合 0 格"):
            verify_effect(effect, before, unrelated, {}, {})
        proof = verify_effect(effect, before, after, {}, {})
        self.assertEqual((proof["before"], proof["after"], proof["expected"]), (0, 1, "minecraft:oak_log[axis=x]"))

    def test_clearance_needs_a_real_obstacle_in_the_forge_delta(self):
        model = Building("Terrace")
        cell = [2, 1, 0]
        effect = {"kind": "clearance", "at": cell}
        air = {"id": "minecraft:air", "properties": {}}
        before = {"changes": []}
        fake = {"changes": [{"local": cell, "category": "remove", "actual": air, "desired": air}]}
        with self.assertRaisesRegex(DesignError, "未产生新的有效清障"):
            verify_effect(effect, model, model, before, fake)
        obstacle = {"id": "minecraft:grass", "properties": {}}
        changed = {"changes": [{**fake["changes"][0], "actual": obstacle}]}
        self.assertEqual(verify_effect(effect, model, model, before, changed)["before"], obstacle)

    def test_landing_metadata_alone_cannot_prove_existing_footing(self):
        model = Building("Shed")
        effect = {"kind": "landing", "at": [1, 0, -1], "footing": "existing"}

        def facts(pos, ground):
            return {"terrain": {"landings": [{"local": pos, "initially_standable": ground, "planned_standable": True}]}}

        before = facts([1, 0, -1], False)
        with self.assertRaisesRegex(DesignError, "更新点"):
            verify_effect(effect, model, model, before, facts([2, 0, -1], False))
        proof = verify_effect(effect, model, model, before, facts([2, -1, -1], True))
        self.assertEqual(proof["after"]["local"], [2, -1, -1])

    def test_reflection_gets_one_correction_for_the_same_unmet_condition(self):
        original = {**self.design, "action": "design", "reason": "地形设计",
                    "placement": {"origin": [0, 0, 0], "rotation": 0, "footprint": self.design["footprint"]}}
        area = {"from": [2, 1, 0], "to": [2, 2, 0]}

        def edit(material):
            return {"action": "edit", "reason": "窗玻璃",
                    "sections": [{**self.window, "modules": [fill(material, area["from"], area["to"])]}]}

        issue = {"finding": "窗玻璃颜色偏深", "effect": {"kind": "blocks", "box": area,
                 "block": "minecraft:light_blue_stained_glass", "properties": []}}

        def stage(_, mode, plan, **kwargs):
            return {"new_version": {"origin": plan["origin"], "rotation": plan["rotation"]},
                    "changes": [], "terrain_sha256": json_hash(plan["terrain"]),
                    "terrain": {"foundation_contacts": [], "structure_blocks": 18, "foundation_blocks": 2}}

        def render(_, folder, _field, _mark, _plan, report):
            write_json(folder / "review.json", report)
            for name in ARTIFACTS:
                (folder / name).write_bytes(b"test artifact")
            return ARTIFACTS

        for material, expected in (("light_blue_stained_glass", "revised"), ("brown_stained_glass", "retained_after_invalid_edit")):
            with self.subTest(expected=expected), TemporaryDirectory() as temp:
                identifier = str(uuid4())
                runtime = Mock(store=SessionStore(Path(temp)), projects=None, share_observations=False, clean=str)
                runtime.proposal_id.return_value = identifier
                runtime.client.survey.return_value = {"features": []}
                runtime.client.stage.side_effect = stage
                runtime.provider.terrain_with_metrics.side_effect = [
                    (original, {}), ({**edit("brown_stained_glass"), "issue": issue}, {}), (edit(material), {}),
                ]
                request = {"request_id": str(uuid4()), "terrain_design": "new", "suggested_origin": [0, 50, 0], "player_id": "tester"}
                with patch("src.architect.terrain_design.render", side_effect=render):
                    handle(runtime, request, "浅蓝窗")
                calls = runtime.provider.terrain_with_metrics.call_args_list
                self.assertEqual([call.args[0] for call in calls], ["design", "reflect", "correct"])
                self.assertEqual(calls[2].args[1]["issue"], issue)
                self.assertIn("改图后符合 0 格", calls[2].args[1]["failed_condition"])
                self.assertFalse(calls[2].kwargs)
                out = Path(temp) / identifier
                proof = json.loads((out / "reflection.json").read_bytes())
                self.assertEqual(proof["outcome"], expected)
                self.assertEqual(proof["attempts"][0]["outcome"], "rejected")
                saved = Building.read(out / "building.json")
                self.assertEqual(saved.blocks[(2, 1, 0)].state.block_id,
                                 "minecraft:light_blue_stained_glass" if expected == "revised" else "minecraft:glass")


if __name__ == "__main__":
    unittest.main()
