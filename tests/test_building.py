import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.core.building import Building, resource_id
from src.generators.medieval import STAIR_OPEN_Z, STAIR_X, UPPER_FLOOR, generate_house


PROJECT = Path(__file__).resolve().parents[1]


class BuildingTests(unittest.TestCase):
    def test_round_trip_with_state_and_block_entity(self):
        house = Building("Test")
        entity = {"id": "minecraft:barrel", "Items": []}
        house.place((-2, 0, 3), "minecraft:barrel", block_entity=entity, open="false", facing="up")
        with TemporaryDirectory(dir=PROJECT / "tests") as temp:
            path = Path(temp) / "building.json"
            house.write(path)
            loaded = Building.read(path)
            self.assertEqual(loaded.blocks, house.blocks)
            self.assertEqual(loaded.bounds(), ((-2, 0, 3), (-2, 0, 3)))
            self.assertEqual(loaded.blocks[(-2, 0, 3)].state.key, "minecraft:barrel[facing=up,open=false]")

    def test_duplicate_coordinates_are_rejected_on_load(self):
        house = Building("Test")
        house.place((0, 0, 0), "minecraft:stone")
        with TemporaryDirectory(dir=PROJECT / "tests") as temp:
            path = Path(temp) / "building.json"
            house.write(path)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["blocks"].append(data["blocks"][0])
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate block"):
                Building.read(path)

    def test_resource_id_rejects_parent_segments(self):
        with self.assertRaises(ValueError):
            resource_id("minecraft:../../stone")

    def test_stair_opening_landing_and_two_storeys(self):
        house = generate_house()
        for x in STAIR_X:
            for rise in range(5):
                y, z = 1 + rise, 2 + rise
                self.assertEqual(house.blocks[(x, y, z)].state.block_id, "minecraft:spruce_stairs")
                self.assertEqual(dict(house.blocks[(x, y, z)].state.properties)["facing"], "south")
                self.assertNotIn((x, y + 1, z), house.blocks)
            for z in STAIR_OPEN_Z:
                if z != 6:
                    self.assertNotIn((x, UPPER_FLOOR, z), house.blocks)
            self.assertIn((x, UPPER_FLOOR, 7), house.blocks)
            self.assertNotIn((x, UPPER_FLOOR + 1, 7), house.blocks)
        for floor in (0, UPPER_FLOOR):
            self.assertIn((6, floor, 2), house.blocks)
            self.assertNotIn((6, floor + 1, 2), house.blocks)
            self.assertNotIn((6, floor + 2, 2), house.blocks)

    def test_double_door_halves_and_mirrored_roof(self):
        house = generate_house()
        for x, hinge in ((7, "left"), (8, "right")):
            for y, half in ((1, "lower"), (2, "upper")):
                block = house.blocks[(x, y, 0)]
                self.assertEqual(block.state.block_id, "minecraft:spruce_door")
                self.assertEqual(dict(block.state.properties)["half"], half)
                self.assertEqual(dict(block.state.properties)["hinge"], hinge)
        for z, expected in ((-1, "south"), (12, "north")):
            roof = house.blocks[(8, 9, z)].state
            self.assertEqual(roof.block_id, "minecraft:brick_stairs")
            self.assertEqual(dict(roof.properties)["facing"], expected)
        self.assertEqual(house.bounds(), ((-1, 0, -2), (16, 17, 12)))


if __name__ == "__main__":
    unittest.main()

