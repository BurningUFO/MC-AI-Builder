"""Targeted regressions for the companion protocol and voxel plans."""

from collections import Counter
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest import TestCase
from unittest.mock import Mock
from uuid import uuid4

from src.architect.design import DesignError, VoxelDesign, fitted_origin, voxel_footings
from src.architect.providers import FixtureProvider, parse_intent
from src.architect.runtime import Runtime, SessionStore


class CompanionTests(TestCase):
    def test_cabin_has_a_door_glass_and_chimney(self):
        intent = FixtureProvider().decide({"message": "帮我在眼前这块地盖一个带烟囱的小木屋，用深色橡木风格"})
        self.assertEqual(parse_intent(intent)["kind"], "voxel")
        model = VoxelDesign.edit(intent["changes"]).building()
        self.assertNotIn((0, 1, -3), model.blocks)
        self.assertNotIn((0, 2, -3), model.blocks)
        self.assertEqual(model.blocks[(2, 7, 2)].state.block_id, "minecraft:stone_bricks")
        materials = Counter(block.state.block_id for block in model.blocks.values())
        self.assertGreater(materials["minecraft:dark_oak_planks"], 100)
        self.assertEqual(materials["minecraft:glass"], 4)

    def test_modules_support_revisions_and_reject_protected_placements(self):
        base = VoxelDesign.edit({"modules": [
            {"mode": "fill", "from": [0, 0, 0], "to": [2, 0, 0], "block": "minecraft:stone"}
        ]})
        added = {"mode": "fill", "from": [0, 1, 0], "to": [0, 3, 0],
                 "block": "minecraft:oak_log", "properties": {"axis": "y"}}
        revised = VoxelDesign.edit({"overlay": True, "modules": [added]}, base).building()
        self.assertEqual(len(revised.blocks), 6)
        self.assertIn(("axis", "y"), revised.blocks[(0, 3, 0)].state.properties)
        with self.assertRaises(DesignError):
            VoxelDesign.edit({"modules": [{**added, "block": "minecraft:chest"}]}).building()
        self.assertEqual(fitted_origin(revised, {
            "approved_area": {"min": [0, 0, 0], "max": [3, 4, 3]},
            "sites": [{"origin": [0, 0, 0]}, {"origin": [2, 0, 0]}],
        }, 180), (2, 0, 0))

    def test_surveyed_gaps_are_filled_in_rotated_build_coordinates(self):
        model = VoxelDesign.edit({"modules": [
            {"mode": "fill", "from": [0, 0, -1], "to": [0, 0, 0], "block": "minecraft:stone"}
        ]}).building()
        added = voxel_footings(model, [{"origin": [10, 65, 10], "foundation_gaps": [[1, 2, 0]]}],
                               (10, 65, 10), 90)
        self.assertEqual(added, [(0, -1, -1), (0, -2, -1)])
        self.assertEqual(model.blocks[(0, -2, -1)].state.block_id, "minecraft:dirt")

    def test_approval_is_delivered_while_preview_is_blocked(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            preview_started, release_renderer, notified = Event(), Event(), Event()
            player, plan, request = map(str, (uuid4(), uuid4(), uuid4()))
            client = Mock(token="fixture")
            client.stage.return_value = {
                "new_version": {"plan_id": plan, "building_version": 1},
                "counts": {"keep": 0, "add": 1, "remove": 0, "replace": 0, "conflict": 0},
                "changes": [{"category": "add", "desired": {"id": "minecraft:glass"}}],
            }

            def render(report, instance, folder, blender, *, multi_angle):
                self.assertEqual(json.loads(report.read_text("utf-8"))["new_version"]["plan_id"], plan)
                preview_started.set()
                if release_renderer.wait(5):
                    (folder / "proposed.png").touch()

            client.preview_ready.side_effect = lambda *_: notified.set()
            runtime = Runtime(client, FixtureProvider(), SessionStore(root), root, root / "blender", render=render)
            try:
                runtime.review(request, "new", {}, player_id=player)
                self.assertTrue(preview_started.wait(2))
                client.answer.assert_called_once()
                self.assertTrue(client.answer.call_args.kwargs["review"])
                client.preview_ready.assert_not_called()
            finally:
                release_renderer.set()
            self.assertTrue(notified.wait(2))
            client.preview_ready.assert_called_once_with(plan, player)
