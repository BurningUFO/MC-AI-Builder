"""Verify workspace project resolution, unmodified plans, and asynchronous rendering."""

from collections import Counter
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest import TestCase
from unittest.mock import Mock
from uuid import uuid4
import json

from src.architect.codex_provider import decode_intent
from src.architect.design import DesignError, VoxelDesign
from src.architect.providers import FixtureProvider, InvalidOutput, parse_intent
from src.architect.runtime import Runtime, SessionStore
from src.assets.projects import ProjectLibrary, load_building_project
from src.core.building import Building, write_json
from src.renderers.views import preview_for, view_catalog


def request(message):
    return {
        "request_id": str(uuid4()), "player_id": str(uuid4()), "message": message, "position": [24, 65, 35],
        "dimension": "minecraft:overworld", "facing": "north", "suggested_origin": [24, 65, 31],
        "approved_area": {"min": [0, 60, 0], "max": [50, 90, 50]}, "ground_near_anchor": {},
    }


def stage(request_id, mode, plan=None):
    return {"new_version": {"plan_id": plan["plan_id"], "building_version": plan["building_version"]},
            "counts": {"keep": 0, "add": len(plan["actions"]), "remove": 0, "replace": 0, "conflict": 0},
            "changes": [{"category": "add", "desired": row} for row in plan["actions"]]}


class ProjectLibraryTest(TestCase):
    def test_reference_crop_preserves_pose_and_defaults_to_centered(self):
        model = Building("Camera reference")
        model.place((0, 0, 0), "minecraft:stone")
        pose = {"position": [4, 3, 5], "target": [0, 1, 0], "lens": 32}
        views, _ = view_catalog({"views": {"full": pose, "crop": {**pose, "shift": [0.6, -0.2]}}}, model)
        self.assertEqual(views["crop"].offset, views["full"].offset)
        self.assertEqual(views["crop"].shift, (0.6, -0.2))
        self.assertEqual(views["full"].shift, (0, 0))
        for invalid in ([0], [True, 0], [0, float("nan")]):
            with self.subTest(shift=invalid), self.assertRaisesRegex(ValueError, "shift"):
                view_catalog({"views": {"crop": {**pose, "shift": invalid}}}, model)

    def test_built_in_aliases_and_standard_paths(self):
        cases = {"medieval_house": ("medieval house", "中世纪双层木屋", "建个双层别墅"),
                 "enchanting_library": ("复现附魔台", "图书室"),
                 "warehouse_bridge": ("Warehouse-Bridge", "石桥"),
                 "warehouse_flat_bridge": ("flat_bridge", "木石平桥")}
        for name, aliases in cases.items():
            for alias in aliases:
                with self.subTest(alias=alias):
                    normalized, model, data, previews = load_building_project(alias)
                    self.assertEqual(normalized, name)
                    self.assertEqual(data.parts[-3:], ("projects", name, "building.json"))
                    self.assertEqual(previews.parts[-2:], ("previews", name))
                    self.assertEqual(model, Building.read(data))

    def test_discovery_rescans_valid_custom_projects(self):
        with TemporaryDirectory() as temp:
            library = ProjectLibrary(Path(temp))
            (library.projects / "broken").mkdir(parents=True)
            (library.projects / "broken/building.json").write_text('{"blocks": []}', encoding="utf-8")
            self.assertEqual(library.catalog(), ())
            custom = Building("蓝色温室")
            custom.place((1, 0, 0), "minecraft:glass")
            data = library.projects / "glass_house_v2/building.json"
            custom.write(data)
            self.assertEqual(library.conversation_intent("把 glass house v2 朝东盖在这"),
                             {"action": "load_project", "project_name": "glass_house_v2", "rotation": 90})
            self.assertEqual(library.load_building_project("复现蓝色温室")[1], custom)
            self.assertEqual(library.conversation_intent("你有哪些现成的建筑？"), {"action": "list_projects"})
            self.assertIsNone(library.conversation_intent("设计一个蓝色温室风格的新屋"))
            data.unlink()
            self.assertEqual(library.catalog(), ())

    def test_provider_validates_project_intents(self):
        intent = decode_intent('{"action":"load_project","project_name":"medieval_house","origin":null,"rotation":90}')
        self.assertEqual(intent, {"action": "load_project", "project_name": "medieval_house", "rotation": 90})
        self.assertEqual(parse_intent({"action": "list_projects", "message": "我给你介绍一下。"})["action"], "list_projects")
        with self.assertRaises(InvalidOutput):
            parse_intent({"action": "load_project", "project_name": "../outside"})
        with self.assertRaises(InvalidOutput):
            parse_intent({"action": "list_projects", "project_name": "medieval_house"})

    def test_import_plan_keeps_original_cells_mod_states_and_bounds(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            library = ProjectLibrary()
            client = Mock(token="local", stage=Mock(side_effect=stage))
            runtime = Runtime(client, FixtureProvider(), SessionStore(root), root, None, projects=library)
            msg = request("帮我建一下 medieval_house")
            runtime.handle(msg)
            _, mode, plan = client.stage.call_args.args
            self.assertEqual(mode, "new")
            self.assertEqual(plan["source_project"], "medieval_house")
            expected = library.load_building_project("medieval_house")[1]
            result = {tuple(row["local"]): (row["id"], row["properties"]) for row in plan["actions"]}
            self.assertEqual(result, {pos: (block.state.block_id, dict(block.state.properties))
                                      for pos, block in expected.blocks.items()})
            self.assertTrue(any(name.startswith("quark:") for name, _ in result.values()))
            self.assertEqual(plan["rotation"], 180)
            self.assertTrue(client.answer.call_args.kwargs["review"])
            self.assertIn("共 1478 个方块", client.answer.call_args.args[1])
            breakdown = client.answer.call_args.kwargs["material_lines"]
            self.assertTrue(any("twigs:spruce_table ×2" in line for line in breakdown))
            self.assertTrue(all(len(line) <= 300 for line in breakdown))
            runtime.preview_thread and runtime.preview_thread.join(timeout=3)

    def test_create_archives_before_background_blender_completes(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            library = ProjectLibrary(root)
            client = Mock(token="local", stage=Mock(side_effect=stage))
            started, release, done = Event(), Event(), Event()

            def renderer(_, data, output, sample, **kwargs):
                started.set()
                if not release.wait(4):
                    raise AssertionError("The chat thread waited for Blender.")
                model = Building.read(data)
                for name in preview_for(sample, model).files:
                    output.mkdir(parents=True, exist_ok=True)
                    (output / name).touch()
                write_json(output / "verification.json", {"building_sha256": sha256(data.read_bytes()).hexdigest()})

            client.preview_ready.side_effect = lambda *args, **kwargs: done.set()
            runtime = Runtime(client, FixtureProvider(), SessionStore(root / "session"), root, root / "blender.exe",
                              projects=library, project_render=renderer)
            msg = request("设计一个带斜顶的深色橡木凉亭")
            try:
                runtime.handle(msg)
                self.assertTrue(started.wait(3))
                client.preview_ready.assert_not_called()
                self.assertTrue(client.answer.call_args.kwargs["review"])
                project, = library.catalog()
                self.assertTrue(project.name.startswith("custom_pavilion_"))
                staged = runtime.store.directory(runtime.proposal_id(msg["request_id"])) / "building.json"
                self.assertEqual(Building.read(staged), Building.read(project.structure))
            finally:
                release.set()
                self.assertTrue(done.wait(3))
                if runtime.preview_thread:
                    runtime.preview_thread.join(timeout=3)
            links = client.preview_ready.call_args.kwargs["project"]
            self.assertEqual(links["scene"], project.name + ".blend")
            self.assertEqual(list(sorted(file.suffix for file in project.preview.iterdir())).count(".png"), 4)

    def test_design_mod_selection_depends_on_forge_advertisement(self):
        block = "biomesoplenty:jacaranda_log"
        module = {"mode": "fill", "from": [0, 0, 0], "to": [0, 0, 0], "block": block,
                  "properties": {"axis": "x"}}
        design = VoxelDesign.edit({"modules": [module]})
        with self.assertRaises(DesignError):
            design.building()
        model = design.building({block: {"properties": {"axis": "y"}}})
        self.assertEqual(model.state_counts(), Counter({model.blocks[0, 0, 0].state: 1}))
        self.assertEqual(model.blocks[0, 0, 0].state.key, block + "[axis=x]")

    def test_roof_and_framing_keep_installed_modded_families(self):
        roof = "quark:red_shingles"
        log = "biomesoplenty:stripped_jacaranda_log"
        design = VoxelDesign.edit({"modules": [
            {"mode": "framing", "from": [0, 0, 0], "to": [4, 3, 4], "block": log},
            {"mode": "roof", "from": [0, 5, 0], "to": [4, 5, 4], "style": "gable", "block": roof},
        ]})
        with self.assertRaises(DesignError):
            design.building({roof, log})
        model = design.building({roof, roof + "_slab", roof + "_stairs", log})
        self.assertTrue({roof, roof + "_slab", roof + "_stairs", log}
                        <= {block.state.block_id for block in model.blocks.values()})
