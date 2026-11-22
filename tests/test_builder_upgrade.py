"""Regression tests for architectural pieces and scoped blueprint edits."""

from dataclasses import asdict
from itertools import product
import json
import unittest

from src.architect.codex_provider import decode_intent
from src.architect.design import DesignError, VoxelDesign
from src.architect.grammar import LIGHT_BLOCKS, generate_framing, generate_roof, walkable_cells
from src.architect.providers import FixtureProvider, InvalidOutput, parse_intent
from src.architect.runtime import describe_supply
from src.architect.surgical import apply_edit, local_gaze, roof_cells


class ArchitecturalUpgradeTest(unittest.TestCase):
    @staticmethod
    def cabin(lighting="torch"):
        design = VoxelDesign.edit({
            "name": "Spruce cabin", "lighting": lighting,
            "modules": [
                {"mode": "fill", "from": [-3, 0, -4], "to": [3, 0, 4], "block": "minecraft:stone"},
                {"mode": "shell", "from": [-3, 1, -4], "to": [3, 4, 4], "block": "minecraft:spruce_planks"},
                {"mode": "erase", "from": [-2, 1, -3], "to": [2, 4, 3]},
                {"mode": "framing", "from": [-3, 1, -4], "to": [3, 4, 4], "block": "minecraft:spruce_log"},
                {"mode": "erase", "from": [0, 1, -4], "to": [0, 2, -4]},
                {"mode": "roof", "style": "gable", "from": [-3, 6, -4], "to": [3, 6, 4],
                 "overhang": None, "block": "minecraft:spruce_planks"},
                {"mode": "fill", "from": [2, 5, 2], "to": [2, 13, 2], "block": "minecraft:stone_bricks"},
            ],
        })
        return design.building(), {"kind": "voxel", "parameters": asdict(design)}

    def test_roofs_have_sealed_decks_symmetry_and_mirrored_stairs(self):
        for style, width, depth in product(("gable", "hip", "parapet"), (5, 6), (7, 8)):
            with self.subTest(style=style, width=width, depth=depth):
                roof = generate_roof(style, ((0, 9, 0), (width - 1, 9, depth - 1)))
                deck = 9 if style == "parapet" else 8
                for x, z in product(range(-1, width + 1), range(-1, depth + 1)):
                    self.assertEqual(roof[x, deck, z], ("minecraft:spruce_planks", {}))
                for (x, y, z), (kind, state) in roof.items():
                    for point, swap in (((width - 1 - x, y, z), {"east": "west", "west": "east"}),
                                        ((x, y, depth - 1 - z), {"north": "south", "south": "north"})):
                        mirrored = {**state}
                        if "facing" in state:
                            mirrored["facing"] = swap.get(state["facing"], state["facing"])
                        self.assertEqual(roof[point], (kind, mirrored))
                slabs = {pos for pos, (_, state) in roof.items() if state.get("type") == "top"}
                self.assertTrue(slabs)
                # Flood each top-slab band from its adjacent solid starter block.
                reachable = {pos for pos in roof.keys() - slabs if not roof[pos][1]}
                while slabs:
                    frontier = {point for point in slabs if any((point[0] + dx, point[1], point[2] + dz) in reachable
                                                                 for dx, dz in ((0, 1), (0, -1), (1, 0), (-1, 0)))}
                    self.assertTrue(frontier, slabs)
                    reachable.update(frontier)
                    slabs -= frontier

    def test_frame_bays_and_axes(self):
        frame = generate_framing(((0, 1, 0), (9, 4, 7)), "minecraft:spruce_log")
        posts = {x for (x, y, z), (_, state) in frame.items() if y == 1 and z == 0 and state["axis"] == "y"}
        self.assertEqual({9 - x for x in posts}, posts)
        self.assertEqual(frame[1, 4, 0][1], {"axis": "x"})
        self.assertEqual(frame[0, 4, 1][1], {"axis": "z"})
        self.assertEqual(frame[0, 4, 0][1], {"axis": "y"})

    def test_light_layout_covers_floor_roof_and_entry(self):
        for kind in ("torch", "lantern"):
            building, spec = self.cabin(kind)
            emitters = {pos for pos, block in building.blocks.items() if block.state.block_id in LIGHT_BLOCKS}
            self.assertTrue(emitters)
            self.assertTrue({(-1, 2, -5), (1, 2, -5)} <= emitters)
            for pos in walkable_cells(building):
                self.assertTrue(any(sum(abs(a - b) for a, b in zip(pos, lamp)) <= 6 for lamp in emitters), pos)
            roof = roof_cells(spec, building)
            self.assertTrue(roof)
            self.assertTrue(any((x, y - 1, z) in roof for x, y, z in emitters))

    def test_window_diff_preserves_rest_of_model_and_uses_inverse_view_rotation(self):
        building, spec = self.cabin("none")
        before = building.blocks.copy()
        gaze = local_gaze({"position": [6, 65, 20], "face": "west"}, {"origin": [10, 64, 20], "rotation": 90})
        self.assertEqual(gaze, {"position": [0, 1, 4], "face": "south"})
        apply_edit(building, spec, {"surface": "window", "material": "minecraft:glass",
                                   "width": 2, "height": 3, "floor_to_ceiling": True}, gaze)
        changes = {pos for pos in before if before[pos] != building.blocks[pos]}
        self.assertEqual(len(changes), 6)
        self.assertEqual({z for _, _, z in changes}, {4})
        self.assertEqual(before.keys(), building.blocks.keys())
        self.assertTrue(all(building.blocks[pos].state.block_id == "minecraft:glass" for pos in changes))

    def test_wall_preserves_openings_and_roof_edit_preserves_chimney(self):
        building, spec = self.cabin("none")
        gaze = {"position": [1, 2, -4], "face": "north"}
        previous = building.blocks.copy()
        apply_edit(building, spec, {"surface": "wall", "material": "minecraft:stone_bricks"}, gaze)
        self.assertNotIn((0, 1, -4), building.blocks)
        self.assertEqual(previous[-3, 2, -4], building.blocks[-3, 2, -4])
        self.assertEqual(previous[0, 4, 4], building.blocks[0, 4, 4])
        previous = building.blocks.copy()
        apply_edit(building, spec, {"surface": "roof", "material": "minecraft:dark_oak_planks"}, None)
        self.assertEqual(previous[2, 8, 2], building.blocks[2, 8, 2])
        self.assertEqual(dict(previous[-4, 6, 0].state.properties),
                         dict(building.blocks[-4, 6, 0].state.properties))
        self.assertEqual(building.blocks[-4, 6, 0].state.block_id, "minecraft:dark_oak_stairs")
        with self.assertRaises(DesignError):
            apply_edit(building, spec, {"surface": "wall", "material": "minecraft:stone_bricks"}, None)

    def test_local_intent_cannot_send_coordinates_and_preserves_reply(self):
        raw = {"action": "surgical_edit", "base_version": 2, "edit": {
            "surface": "roof", "material": "minecraft:dark_oak_planks", "width": None, "height": None,
            "floor_to_ceiling": None,
        }, "message": "我来换屋顶。"}
        reply = decode_intent(json.dumps(raw))
        self.assertEqual(reply["message"], raw["message"])
        self.assertEqual(set(reply["edit"]), {"material", "surface"})
        with self.assertRaises(InvalidOutput):
            parse_intent({**reply, "origin": [0, 0, 0]})
        fixture = FixtureProvider().decide({"message": "把这面墙换成石砖", "design": {"version": 3},
                                          "gaze": {"position": [0, 2, 4], "face": "south"}})
        self.assertEqual(fixture["edit"], {"surface": "wall", "material": "minecraft:stone_bricks"})

    def test_material_reply_names_processed_goods_and_raw_stock(self):
        supply = {"mode": "survival", "warehouse_approved": True,
                  "requirements": {"minecraft:spruce_stairs": {"unit": "items", "required": 8,
                                                              "missing": 0, "craftable": 8}},
                  "recipe_inputs": {"minecraft:spruce_log": 3}}
        reply = describe_supply(supply)
        self.assertIn("spruce_log ×3", reply)
        self.assertIn("spruce_stairs ×8", reply)
        self.assertIn("附近库存够用", reply)


if __name__ == "__main__":
    unittest.main()
