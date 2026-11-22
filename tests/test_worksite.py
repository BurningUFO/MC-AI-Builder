import json
import unittest

from src.architect.codex_provider import DESIGN_FIELDS
from src.architect.design import DesignError
from src.architect.providers import InvalidOutput
from src.architect.survey import SiteView, checked_design, clearance_counts, decode, payload_size, site_clearance, worksite_facts
from src.architect.survey import worksite_findings, worksite_question
from tools.mcai_launcher import describe_execution


class WorksiteTest(unittest.TestCase):
    def test_grouped_findings_preserve_block_roles_and_relative_geometry(self):
        findings = worksite_findings([
            {"reason": "protected_area", "count": 4, "examples": ["minecraft:oak_log"], "sample_from_mark": [1, 0, 1]},
            {"reason": "hazardous_terrain", "count": 2, "examples": ["minecraft:lava"], "sample_from_mark": [-1, 0, 1]},
            {"reason": "protected_facility", "count": 2, "examples": ["minecraft:chest", "supplementaries:pedestal"],
             "sample_from_mark": [0, 0, 1]},
        ])
        self.assertNotEqual(findings[0]["reason"], findings[1]["reason"])
        question = worksite_question("可以挪一下地基吗", findings)
        self.assertIn("minecraft:chest", question)
        self.assertIn("supplementaries:pedestal", question)
        self.assertIn("minecraft:lava", question)

    def test_absolute_coordinates_unregistered_findings_and_excessive_clearance_are_rejected(self):
        for item in (
            {"reason": "protected_area", "examples": ["minecraft:oak_log"], "sample_from_mark": [610, 74, 509]},
            {"reason": "protected_facility", "examples": ["D:/private"], "sample_from_mark": [1, 0, 2]},
        ):
            with self.subTest(item=item), self.assertRaises(ValueError):
                worksite_findings([{**item, "count": 2}])
        for bad in ({"soil": 513}, {"protected_facility": 1}, {"leaves": True}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                clearance_counts(bad)
        self.assertEqual(clearance_counts({"vegetation": 16, "soil": 2}), {"vegetation": 16, "soil": 2})

    def test_owner_question_cannot_include_a_mutation(self):
        decision = {"action": "request_player_decision", "kind": None, "offset": None,
                    "pad": None, "changes": None, "reason": "有已登记的箱子"}
        self.assertEqual(decode(json.dumps(decision), DESIGN_FIELDS), decision)
        for extra in ({"pad": 0}, {"offset": [1, 0, 2]}, {"command": "fill"}):
            with self.subTest(extra=extra), self.assertRaises(InvalidOutput):
                decode(json.dumps(decision | extra), DESIGN_FIELDS)

    def test_native_capabilities_and_restricted_geometry_are_distinct(self):
        raw = {"id": "example:short_shrub", "offset_from_mark": [1, 0, 3], "replaceable": True,
               "collision_free": True, "hardness": 0, "has_loot_table": True, "requires_tool_for_drops": False,
               "managed": False, "block_entity": False, "liquid": False, "form": "vegetation", "group_cells": 1}
        self.assertEqual(worksite_facts([raw])[0], raw)
        restricted = site_clearance({"actionable": False, "clearance": {"vegetation": 9},
                                     "restrictions": {"protected_facility": 1}})
        self.assertEqual(restricted["clearance"], {"vegetation": 9})
        with self.assertRaises(ValueError):
            site_clearance({**restricted, "actionable": True})
        fit = {"width": 7, "depth": 9, "eaves": True, "entries": ["left", "center", "right"], "footing_rows": [0] * 9}
        pads = [{"id": 0, "offset_from_mark": [0, 0, 0], "rotation": 0, "sizes": [fit | restricted]},
                {"id": 1, "offset_from_mark": [1, 0, 0], "rotation": 0, "sizes": [fit | {"actionable": True}]}]
        view = SiteView({}, b"", "", [0, 0, 0], [], {}, pads)
        decision = {"kind": "cottage", "pad": 0, "changes": {}, "clearance_order": "tree_first"}
        with self.assertRaises(DesignError):
            checked_design(view, decision)
        intent = checked_design(view, {**decision, "pad": 1})
        self.assertEqual(intent["origin"], [1, 0, 0])
        self.assertEqual(intent["_worksite_order"], "tree_first")

    def test_model_chooses_a_supported_preparation_order(self):
        decision = {"action": "design", "kind": "cottage", "offset": None, "pad": 0,
                    "changes": {"width": 7, "depth": 9}, "reason": "保留主要入口。", "clearance_order": "route_first"}
        self.assertEqual(decode(json.dumps(decision), DESIGN_FIELDS, worksite=True), decision)
        with self.assertRaises(InvalidOutput):
            decode(json.dumps(decision | {"clearance_order": "break-anything"}), DESIGN_FIELDS, worksite=True)

    def test_cottage_candidates_and_obstacles_survive_the_model_context_limit(self):
        context = {"dimension": "minecraft:overworld", "approved_bounds": {"min": [-12, -3, -12], "max": [12, 7, 12]},
                   "construction": {"state": "survey", "building_version": 0, "processed": 0, "total": 0},
                   "unknown_outside_scope": 0, "unknown_unloaded": 0, "cells": [],
                   "terrain": {"footing_y_rows_z_then_x": [[0] * 9 for _ in range(9)],
                               "connected_footing": [[x, 0, z] for x in range(-4, 5) for z in range(-4, 5)],
                               "connected_relief": 0}}
        fit = {"width": 7, "depth": 9, "entries": ["left", "center", "right"], "eaves": True,
               "actionable": True, "clearance": {"logs": 8, "leaves": 57, "masonry": 2, "wood": 3, "soil": 10},
               "restrictions": {}, "footing_rows": [0] * 9}
        homes = [{"id": idx, "rotation": 0, "offset_from_mark": [idx % 5 - 2, 0, idx // 5 - 2],
                  "sizes": [fit]} for idx in range(16)]
        walls = [{"id": idx, "rotation": 0, "offset_from_mark": [idx - 4, 0, 0], "span": 8,
                  "actionable": False, "actionable_span": 6, "clearance": {"vegetation": 18, "wood": 8},
                  "restrictions": {"protected_facility": 2, "outside_effect": 10}} for idx in range(8)]
        observation = {"form": "wood", "id": "minecraft:oak_planks", "group_cells": 3, "offset_from_mark": [1, 0, 3]}
        view = SiteView(context, b"", "", [0, 0, 0], walls, {}, homes, (), (observation,))
        outcome = view.choices({"suggested_origin": [0, 0, 0], "site_rotation": 0, "worksite": True},
                               "建7格宽9格深的住宅", None, 3, None)
        self.assertEqual(len(outcome["cottage_foundations"]), 16)
        self.assertEqual(outcome["worksite_facts"], [observation])
        self.assertLessEqual(payload_size(outcome), 7600)

    def test_launcher_distinguishes_current_clearance_and_tool(self):
        description = describe_execution({"state": "running", "building_version": 1, "processed": 2, "total": 185,
                                          "preparation": {"cleared": 2, "total": 6, "phase": "clear",
                                                          "tool": "minecraft:iron_shovel", "next": [5, 6, 7]}})
        self.assertIn("清障 2/6", description)
        self.assertIn("铁铲", description)


if __name__ == "__main__":
    unittest.main()
