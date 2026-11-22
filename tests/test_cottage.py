"""Pin the geometry and the boundary between survey data and model output."""

from collections import deque
import unittest

from src.architect.design import Cottage, DesignError
from src.architect.providers import InvalidOutput, parse_intent
from src.architect.survey import SiteView, checked_design
from src.construction.plan import make_plan
from src.construction.review_preview import interior_cutaway


class CottageTest(unittest.TestCase):
    def test_front_to_room_has_continuous_floor_and_roof(self):
        design = Cottage()
        blocks = design.building().blocks
        self.assertEqual(len(blocks), 183)
        self.assertEqual({pos[1] for pos in blocks}, {0, 1, 2, 3})
        self.assertTrue(all((x, 0, z) in blocks and (x, 3, z) in blocks
                            for x in range(-3, 4) for z in range(-4, 5)))
        start = (0, -4)
        queue, visited = deque([start]), {start}
        while queue:
            x, z = queue.popleft()
            for a, b in ((x+1, z), (x-1, z), (x, z-1), (x, z+1)):
                if ((a, b) not in visited and (a, 0, b) in blocks
                        and (a, 1, b) not in blocks and (a, 2, b) not in blocks):
                    queue.append((a, b))
                    visited.add((a, b))
        self.assertEqual(len(visited), 33)
        self.assertIn((2, 3), visited)
        self.assertEqual(blocks[-2, 1, 3].state.key, "minecraft:oak_slab[type=bottom,waterlogged=false]")

    def test_size_roof_entry_and_interior_compose(self):
        base = Cottage()
        alternate = Cottage.edit({"depth": 7, "entry": "right", "roof_form": "eaves",
                                  "windows": "compact", "interior": "bench"}, base)
        original, changed = base.building().blocks, alternate.building().blocks
        self.assertGreater(len(set(original) ^ set(changed)), 24)
        self.assertNotIn((1, 1, -3), changed)
        self.assertIn((0, 1, -3), changed)
        self.assertIn((0, 3, -4), changed)
        self.assertNotIn((0, 1, 2), changed)
        self.assertEqual(changed[0, 1, -3].state.block_id, "minecraft:glass")

    def test_local_revision_keeps_footing_and_all_non_window_states(self):
        base = Cottage()
        hi = base.building([[-1, -2], [0, 1]])
        updated = Cottage.edit({"glass": "minecraft:blue_stained_glass"}, base).building([[-1, -2], [0, 1]])
        self.assertEqual(set(hi.blocks), set(updated.blocks))
        self.assertEqual({(pos, cell.state.block_id, updated.blocks[pos].state.block_id)
                          for pos, cell in hi.blocks.items() if cell.state.block_id == "minecraft:glass"},
                         {(pos, hi.blocks[pos].state.block_id, cell.state.block_id)
                          for pos, cell in updated.blocks.items() if cell != hi.blocks[pos]})
        self.assertTrue(all(updated.blocks[pos].state.block_id == "minecraft:blue_stained_glass"
                            for pos, cell in hi.blocks.items() if cell.state.block_id == "minecraft:glass"))
        self.assertEqual(hi.blocks[-1, -1, -2].state.block_id, "minecraft:stone_bricks")
        for degrees in (0, 90, 180, 270):
            plan = make_plan(updated, building_id="cottage_test", version=2, origin=(14, 85, 11), rotation=degrees)
            self.assertEqual(len(plan["actions"]), 185)
            self.assertEqual(plan["local_bounds"]["min"], [-3, -1, -4])

    def test_survey_only_generates_footing_from_checked_forge_cells(self):
        fit = {"width": 7, "depth": 9, "entries": ["left", "right"], "eaves": False,
               "footing_rows": [0, 0, 0, 2, 0, 0, 32, 0, 0]}
        pad = {"id": 1, "offset_from_mark": [1, 0, -1], "rotation": 90, "sizes": [fit]}
        view = SiteView({}, b"", "", [0, 0, 0], [], {}, [pad])
        choice = {"kind": "cottage", "pad": 1, "changes": {"entry": "left"}}
        intent = checked_design(view, choice)
        self.assertEqual(intent["origin"], [1, 0, -1])
        self.assertEqual(intent["rotation"], 90)
        self.assertEqual(intent["_survey_footing"], [[-2, -1], [2, 2]])
        with self.assertRaises(InvalidOutput):
            parse_intent(intent)
        for changes in ({"entry": "center"}, {"roof_form": "eaves"}, {"depth": 7}, {"wood": "minecraft:bedrock"}):
            with self.subTest(changes=changes), self.assertRaises(DesignError):
                checked_design(view, {**choice, "changes": changes})

    def test_cutaway_keeps_exact_floor_furniture_and_states(self):
        model = Cottage(roof_form="eaves").building([(2, 1)])
        cut = interior_cutaway(model)
        self.assertTrue(all(model.blocks[pos] == state for pos, state in cut.blocks.items()))
        self.assertIn((2, -1, 1), cut.blocks)
        self.assertIn((-2, 1, 3), cut.blocks)
        self.assertEqual({pos for pos in model.blocks if pos[1] == 0},
                         {pos for pos in cut.blocks if pos[1] == 0})
        self.assertEqual(set(model.blocks) - set(cut.blocks),
                         {pos for pos in model.blocks if pos[1] == 3 or (pos[1] in (1, 2) and pos[2] == -4)})
        for changes in ({"width": True}, {"depth": 11}, {"height": 5}, {"entry": "floor"},
                        {"roof_form": "upper_storey"}, {"world_write": "anything"}):
            with self.subTest(changes=changes), self.assertRaises(DesignError):
                Cottage.edit(changes)


if __name__ == "__main__":
    unittest.main()
