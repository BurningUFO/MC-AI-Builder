import unittest
from uuid import uuid4

from src.architect.design import DesignError
from src.architect.grammar import generate_roof
from src.architect.terrain_design import apply_patch, build_candidate, scene_terrain


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


if __name__ == "__main__":
    unittest.main()
