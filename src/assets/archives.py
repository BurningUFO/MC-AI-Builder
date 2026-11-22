"""Locate installed BMC4 archives and read resources without running Minecraft."""

from contextlib import ExitStack
from dataclasses import dataclass
from functools import cached_property
import json
from pathlib import Path
import tomllib
from zipfile import BadZipFile, ZipFile

from src.core.building import resource_id


MODS = ("chipped", "handcrafted", "quark", "supplementaries", "twigs", "decorative_blocks", "biomesoplenty")


class AssetError(ValueError):
    """A resource is missing or needs an unsupported rendering path."""


def location(reference: str) -> tuple[str, str]:
    identifier = reference if ":" in reference else f"minecraft:{reference}"
    return tuple(resource_id(identifier).split(":", 1))


@dataclass
class Source:
    namespace: str
    path: Path
    archive: ZipFile
    version: str

    @cached_property
    def files(self) -> set[str]:
        return set(self.archive.namelist())

    @cached_property
    def english(self) -> dict:
        return self.read_optional_json(f"assets/{self.namespace}/lang/en_us.json") or {}

    def read_optional_json(self, path: str) -> dict | None:
        return json.loads(self.archive.read(path)) if path in self.files else None

    def block_info(self, name: str) -> dict:
        state_path = f"assets/{self.namespace}/blockstates/{name}.json"
        if state_path not in self.files:
            raise AssetError(f"{self.namespace}:{name}: no installed blockstate in {self.path.name}")
        label = self.english.get(f"block.{self.namespace}.{name.replace('/', '.')}")
        item = f"assets/{self.namespace}/models/item/{name}.json" in self.files
        loot = f"data/{self.namespace}/loot_tables/blocks/{name}.json" in self.files
        return {
            "source_jar": self.path.name,
            "blockstate": state_path,
            "display_name": label,
            "evidence": {"item_model": item, "block_loot_table": loot, "block_translation": label is not None},
        }


class Archives(ExitStack):
    """Search the vanilla client and the requested mods by their declared namespace."""

    def __init__(self, instance: Path, *, vanilla_only: bool = False, extra_mods: tuple[str, ...] = ()):
        super().__init__()
        self.instance = instance.resolve(strict=True)
        self.vanilla_only = vanilla_only
        self.mod_ids = tuple(dict.fromkeys((*MODS, *extra_mods)))
        self.sources: dict[str, Source] = {}
        self.mod_issues: dict[str, str] = {}
        self._embedded_sources: dict[tuple[Path, str], Source] = {}

    def discover(self) -> None:
        root = self.instance
        launch_path = root / f"{root.name}.json"
        client_path = root / f"{root.name}.jar"
        launch = json.loads(launch_path.read_text(encoding="utf-8-sig"))
        args = launch["arguments"]["game"]
        forge = args[args.index("--fml.forgeVersion") + 1]
        minecraft = args[args.index("--fml.mcVersion") + 1]
        archive = self.enter_context(ZipFile(client_path))
        client_version = json.loads(archive.read("version.json"))
        if (minecraft, forge, client_version["id"], client_version["world_version"]) != ("1.20.1", "47.4.20", "1.20.1", 3465):
            raise AssetError(f"Expected Minecraft 1.20.1 / Forge 47.4.20, found {minecraft} / {forge}, client {client_version['id']}")
        self.sources["minecraft"] = Source("minecraft", client_path, archive, minecraft)
        shared_root = root.parent.parent
        forge_library = (
            shared_root / "libraries" / "net" / "minecraftforge" / "fmlloader"
            / f"{minecraft}-{forge}" / f"fmlloader-{minecraft}-{forge}.jar"
        )
        packs = root / "resourcepacks"
        self.installation = {
            "instance": str(root),
            "minecraft_version": minecraft,
            "forge_version": forge,
            "data_version": client_version["world_version"],
            "launcher_manifest": str(launch_path),
            "vanilla_client": str(client_path),
            "forge_library": str(forge_library) if forge_library.is_file() else None,
            "mods": str(root / "mods"),
            "instance_resourcepacks": str(packs),
            "local_resourcepack_entries": sum(1 for path in packs.iterdir() if path.is_file() or path.is_dir()) if packs.is_dir() else 0,
            "shared_assets": str(shared_root / "assets"),
            "shared_asset_index": (launch.get("assetIndex") or {}).get("id"),
        }
        if self.vanilla_only:
            return

        for namespace in self.mod_ids:
            try:
                self.sources[namespace] = self.find_mod(namespace)
            except (AssetError, OSError, BadZipFile, tomllib.TOMLDecodeError) as error:
                self.mod_issues[namespace] = str(error)

    def find_mod(self, namespace: str) -> Source:
        matches = []
        for path in sorted((self.instance / "mods").glob("*.jar")):
            if not path.name.lower().startswith(f"{namespace}-"):
                continue
            archive = self.enter_context(ZipFile(path))
            try:
                metadata = tomllib.loads(archive.read("META-INF/mods.toml").decode("utf-8-sig"))
            except KeyError:
                continue
            for item in metadata.get("mods", []):
                if item["modId"] == namespace:
                    version = item.get("version", "unspecified")
                    if version == "${file.jarVersion}":
                        manifest = archive.read("META-INF/MANIFEST.MF").decode("utf-8", errors="replace") if "META-INF/MANIFEST.MF" in archive.namelist() else ""
                        version = next((line.partition(": ")[2] for line in manifest.splitlines()
                                        if line.startswith("Implementation-Version: ")), version)
                    matches.append(Source(namespace, path, archive, version))
        if len(matches) != 1:
            raise AssetError(f"{namespace}: expected one installed JAR with matching modId, found {len(matches)}")
        return matches[0]

    @cached_property
    def _mod_entries(self) -> dict[Path, set[str]]:
        entries = {}
        for path in sorted((self.instance / "mods").glob("*.jar")):
            try:
                with ZipFile(path) as archive:
                    entries[path] = {name for name in archive.namelist() if name.startswith("assets/")}
            except (OSError, BadZipFile):
                continue
        return entries

    def source_for(self, namespace: str, path: str) -> Source | None:
        source = self.sources.get(namespace)
        if source is not None and path in source.files:
            return source
        if self.vanilla_only:
            return None
        # A mod may ship a missing resource in another namespace, including minecraft.
        candidates = [jar for jar, files in self._mod_entries.items() if path in files]
        if len(candidates) != 1:
            return None
        jar = candidates[0]
        key = (jar, namespace)
        if key not in self._embedded_sources:
            self._embedded_sources[key] = Source(namespace, jar, self.enter_context(ZipFile(jar)), "embedded")
        return self._embedded_sources[key]

    def read(self, reference: str, folder: str, suffix: str) -> bytes:
        namespace, name = location(reference)
        path = f"assets/{namespace}/{folder}/{name}.{suffix}"
        source = self.source_for(namespace, path)
        if source is None:
            raise AssetError(f"Missing {folder} resource: {reference} ({path})")
        return source.archive.read(path)

    def block_info(self, block_id: str) -> dict:
        namespace, name = location(block_id)
        path = f"assets/{namespace}/blockstates/{name}.json"
        source = self.source_for(namespace, path)
        if source is None:
            raise AssetError(f"{block_id}: no unambiguous installed blockstate")
        return source.block_info(name)

    def mod_summary(self) -> dict:
        summary = {}
        for namespace in self.mod_ids:
            source = self.sources.get(namespace)
            if source is None:
                summary[namespace] = {"status": "skipped" if self.vanilla_only else "unavailable",
                                      "detail": self.mod_issues.get(namespace)}
                continue
            prefix = f"assets/{namespace}/blockstates/"
            count = sum(path.startswith(prefix) and path.endswith(".json") for path in source.files)
            summary[namespace] = {"status": "read", "jar": source.path.name,
                                  "declared_version": source.version, "static_blockstate_files": count}
        return summary

