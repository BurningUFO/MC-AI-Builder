from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from src.assets.archives import Archives, AssetError, Source
from src.assets.catalog import build_index, choose_palette
from src.assets.models import ModelLibrary, bake_element, model_groups
from src.core.building import BlockState, Building
from src.generators.medieval import VANILLA


class AssetTests(unittest.TestCase):
    def test_variant_uses_exact_state_and_ignores_waterlogging(self):
        spec = {"variants": {"type=bottom": {"model": "block/slab"}, "type=top": {"model": "block/slab_top"}}}
        state = BlockState("minecraft:spruce_slab", (("type", "top"), ("waterlogged", "false")))
        self.assertEqual(model_groups(spec, state), [[{"model": "block/slab_top"}]])
        incomplete = BlockState("minecraft:spruce_slab")
        with self.assertRaises(AssetError):
            model_groups(spec, incomplete)

    def test_multipart_connections_and_runtime_loader(self):
        spec = {"multipart": [
            {"apply": {"model": "block/post"}},
            {"when": {"OR": [{"north": "true"}, {"south": "true"}]}, "apply": {"model": "block/rail"}},
        ]}
        state = BlockState("minecraft:spruce_fence", (("north", "false"), ("south", "true")))
        self.assertEqual([group[0]["model"] for group in model_groups(spec, state)], ["block/post", "block/rail"])
        with self.assertRaisesRegex(AssetError, "special loader"):
            model_groups(spec | {"athena:loader": "athena:ctm"}, state)

    def test_stair_rotates_high_half_and_slab_retains_height(self):
        high_half = {"from": [8, 8, 0], "to": [16, 16, 16],
                     "faces": {"up": {"texture": "#wood", "cullface": "up"}}}
        lower_half = {"from": [0, 0, 0], "to": [16, 8, 16], "faces": high_half["faces"]}
        textures = {"wood": "minecraft:block/spruce_planks"}
        [south] = bake_element(high_half, textures, {"y": 90, "uvlock": True})
        [north] = bake_element(high_half, textures, {"y": 270, "uvlock": True})
        [slab] = bake_element(lower_half, textures, {})
        self.assertEqual({v[2] for v in south["vertices"]}, {0.5, 1.0})
        self.assertEqual({v[2] for v in north["vertices"]}, {0.0, 0.5})
        self.assertEqual({v[1] for v in slab["vertices"]}, {0.5})
        self.assertEqual(south["texture"], "minecraft:block/spruce_planks")

    def test_backported_assets_inside_vanilla_namespace_are_discovered(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "mods").mkdir()
            base, extension = root / "client.jar", root / "mods" / "example-extension.jar"
            original = "assets/minecraft/blockstates/stone.json"
            backport = "assets/minecraft/blockstates/new_plant.json"
            with ZipFile(base, "w") as archive:
                archive.writestr(original, "{}")
            with ZipFile(extension, "w") as archive:
                archive.writestr(original, '{"unexpected_override": true}')
                archive.writestr(backport, "{}")
            with Archives(root) as archives:
                archives.sources["minecraft"] = Source("minecraft", base, archives.enter_context(ZipFile(base)), "test")
                self.assertEqual(archives.block_info("minecraft:new_plant")["source_jar"], extension.name)
                self.assertEqual(archives.read("minecraft:stone", "blockstates", "json"), b"{}")
                self.assertEqual(archives.read("minecraft:new_plant", "blockstates", "json"), b"{}")

    def test_unrenderable_observation_keeps_its_outline_without_relaxing_design_assets(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            scene = Building("Current worksite")
            scene.place((0, 0, 0), "example:unrenderable_vegetation")
            with Archives(root) as archives:
                archives.installation = {}
                library = ModelLibrary(archives)
                index = build_index(root, scene, library, {}, outline_observations=True)
                self.assertEqual(index["unrendered_observations"], ["example:unrenderable_vegetation"])
                self.assertEqual(index["states"], {})
                with self.assertRaises(AssetError):
                    build_index(root, scene, library, {})

    def test_missing_mod_falls_back_to_known_vanilla_blocks(self):
        class MissingLibrary:
            class archives:
                vanilla_only = False

                @staticmethod
                def block_info(block_id):
                    raise AssetError(f"No local JAR for {block_id}")

        palette, report = choose_palette(MissingLibrary())
        self.assertEqual(palette, VANILLA)
        self.assertTrue(all(row["status"] == "vanilla_fallback" for row in report.values()))


if __name__ == "__main__":
    unittest.main()

