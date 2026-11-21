import unittest

from src.construction.plan import demo_wall, make_plan
from src.construction.revisions import revised_wall
from src.construction.review_preview import models_for_review
from src.core.building import Building


class ConstructionPlanTests(unittest.TestCase):
    def test_wall_carries_foundations_and_full_stair_state(self):
        model = demo_wall()
        plan = make_plan(model, building_id="m1_wall", version=1, origin=(6, -60, 6))
        self.assertEqual(len(plan["actions"]), 30)
        self.assertEqual(plan["local_bounds"], {"min": [0, 0, 0], "max": [7, 2, 1]})
        self.assertEqual([a["local"][1] for a in plan["actions"]], sorted(a["local"][1] for a in plan["actions"]))
        stairs = [a for a in plan["actions"] if a["id"] == "minecraft:oak_stairs"]
        self.assertEqual(len(stairs), 2)
        for stair in stairs:
            self.assertEqual(stair["item_id"], stair["id"])
            self.assertEqual(stair["properties"], {
                "facing": "north", "half": "bottom", "shape": "straight", "waterlogged": "false",
            })

    def test_export_keeps_negative_coordinates_and_optional_block_entity_data(self):
        model = Building("Existing map")
        model.place((-3, 2, -1), "minecraft:chest", block_entity={"id": "minecraft:chest"}, facing="west")
        plan = make_plan(model, building_id="map", version=2, origin=(15, -60, 10), rotation=90)
        self.assertEqual(plan["origin"], [15, -60, 10])
        self.assertEqual(plan["rotation"], 90)
        self.assertEqual(plan["actions"], [{
            "local": [-3, 2, -1], "id": "minecraft:chest", "properties": {"facing": "west"},
            "item_id": None, "block_entity": {"id": "minecraft:chest"},
        }])

    def test_rejects_bad_plan_envelope(self):
        model = demo_wall()
        for building_id, version, rotation in (("Wrong ID", 1, 0), ("wall", 0, 0), ("wall", 1, 45)):
            with self.subTest(building_id=building_id, version=version, rotation=rotation):
                with self.assertRaises(ValueError):
                    make_plan(model, building_id=building_id, version=version, origin=(0, 0, 0), rotation=rotation)


    def test_content_hash_is_independent_of_job_id_and_placement(self):
        wall = demo_wall()
        first = make_plan(wall, building_id="wall", version=1, origin=(6, -60, 6))
        again = make_plan(wall, building_id="wall", version=1, origin=(20, -60, 6), rotation=90)
        revised = make_plan(revised_wall(), building_id="wall", version=2, origin=(6, -60, 6))
        self.assertNotEqual(first["plan_id"], again["plan_id"])
        self.assertEqual(first["model_sha256"], again["model_sha256"])
        self.assertNotEqual(first["model_sha256"], revised["model_sha256"])

    def test_preview_models_use_observed_state_and_proposed_state(self):
        actual = {"id": "minecraft:air", "properties": {}}
        wanted = {"id": "minecraft:oak_stairs", "properties": {"facing": "west"}}
        current, proposed = models_for_review({"changes": [
            {"local": [-1, 2, 3], "actual": actual, "desired": wanted},
        ]})
        self.assertEqual(current.blocks, {})
        self.assertEqual(proposed.blocks[(-1, 2, 3)].state.key, "minecraft:oak_stairs[facing=west]")


if __name__ == "__main__":
    unittest.main()
