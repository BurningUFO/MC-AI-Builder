"""Create complete, separately managed copies of closed BMC4 singleplayer saves."""

from hashlib import file_digest
import json
import os
from pathlib import Path
import re
import secrets
import shutil
from tempfile import NamedTemporaryFile, TemporaryDirectory
from uuid import UUID, uuid4

from src.core.building import write_json
from tools.m1_client import BRIDGE_JAR, LOCAL_PLAYER_ID, PLAYERS_JAR, TEST_ROOT
from tools.save_nbt import saved_player


COPIES = TEST_ROOT / "world-copies"
WORLD = "bmc4-copy"
MARKER = ".mcaibuilder-world-copy.json"
PACK_DIRS = ("config", "defaultconfigs", "configureddefaults", "data", "fancymenu_data",
             "generated_reeds", "moonlight-global-datapacks", "patchouli_books", "kubejs",
             "global_packs", "openloader", "resourcepacks", "shaderpacks", "scripts")
_REPARSE = 0x400
_NAME = re.compile(r"[A-Za-z0-9_]{1,16}\Z")


def world_choices(instance: Path):
    saves = instance.resolve(strict=True) / "saves"
    if not saves.is_dir():
        return []
    return sorted((child for child in saves.iterdir()
                   if child.is_dir() and not linked(child) and (child / "level.dat").is_file()),
                  key=lambda child: (child / "level.dat").stat().st_mtime_ns, reverse=True)


def linked(path: Path):
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & _REPARSE)


def discover_player(instance: Path, source: Path):
    identity, player = saved_player(source / "level.dat")
    name = None
    usercache = instance / "usercache.json"
    if usercache.is_file() and not linked(usercache):
        rows = json.loads(usercache.read_text(encoding="utf-8-sig"))
        name = next((row.get("name") for row in rows if row.get("uuid") == str(identity)), None)
    cache = instance / "usernamecache.json"
    if not name and cache.is_file() and not linked(cache):
        name = json.loads(cache.read_text(encoding="utf-8-sig")).get(str(identity))
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("无法从游戏记录匹配存档玩家名称。请选择能确认原玩家的单人存档。")
    return identity, name, player


def inventory(root: Path, *, world=False):
    """Enumerate the complete tree before any copy, rejecting reparse points."""
    if linked(root):
        raise ValueError("副本来源包含目录链接，无法保证完整隔离。")
    directories, files = [], []

    def raise_error(error):
        raise error

    for folder, names, entries in os.walk(root, onerror=raise_error):
        parent = Path(folder)
        for name in sorted(names):
            child = parent / name
            if linked(child):
                raise ValueError("副本来源包含目录链接，无法保证完整隔离。")
            directories.append(child.relative_to(root))
        for name in sorted(entries):
            child = parent / name
            relative = child.relative_to(root)
            if world and relative == Path("session.lock"):
                continue
            if linked(child) or not child.is_file():
                raise ValueError("副本来源包含链接或特殊文件。")
            files.append(relative)
    return directories, files


def file_manifest(root: Path, *, world=False):
    directories, files = inventory(root, world=world)
    manifest = {}
    for relative in files:
        with (root / relative).open("rb") as stream:
            digest = file_digest(stream, "sha256").hexdigest()
        manifest[relative.as_posix()] = digest
    return {"files": manifest, "directories": sorted(path.as_posix() for path in directories)}


def copy_tree(source: Path, target: Path, *, world=False, config=False):
    directories, files = inventory(source, world=world)
    target.mkdir(parents=True, exist_ok=True)
    for relative in directories:
        (target / relative).mkdir(parents=True, exist_ok=True)
    for relative in files:
        if config and relative.parts[0].startswith("mcaibuilder"):
            continue
        shutil.copy2(source / relative, target / relative)


def copy_root():
    if TEST_ROOT.resolve() != TEST_ROOT.absolute() or (COPIES.exists() or COPIES.is_symlink()) and linked(COPIES):
        raise ValueError("世界副本必须保存在项目存档副本目录中。")
    COPIES.mkdir(parents=True, exist_ok=True)
    return COPIES.resolve(strict=True)


def read_copy(game: Path):
    root = copy_root()
    if game.parent != root or linked(game) or game.resolve(strict=True) != game:
        raise ValueError("只能启动已有的受管理世界副本。")
    marker = game / MARKER
    if not marker.is_file() or linked(marker):
        raise ValueError("该目录缺少有效的隔离副本标记。")
    record = json.loads(marker.read_text(encoding="utf-8"))
    if record.get("schema") != 1 or UUID(record["copy_id"]).hex != game.name:
        raise ValueError("副本目录与标记不一致。")
    UUID(record["player_uuid"])
    if not _NAME.fullmatch(record["player_name"]):
        raise ValueError("副本玩家标记不正确。")
    for name in ("saves", "mods", "config"):
        path = game / name
        if not path.is_dir() or linked(path):
            raise ValueError("副本的世界、配置或模组目录无效。")
    world = game / "saves" / WORLD
    if linked(world) or not (world / "level.dat").is_file() or linked(world / "level.dat"):
        raise ValueError("隔离副本缺少有效的 Minecraft 世界。")
    return record


def existing_copies():
    result = []
    for folder in copy_root().iterdir():
        if not (folder / MARKER).is_file():
            continue
        try:
            result.append((folder, read_copy(folder)))
        except (OSError, KeyError, TypeError, ValueError):
            continue
    return sorted(result, key=lambda pair: (pair[0] / MARKER).stat().st_mtime_ns, reverse=True)


def create_copy(instance: Path, source: Path, *, terrain_fixture=False):
    instance = instance.resolve(strict=True)
    if linked(source) or source.resolve(strict=True).parent != (instance / "saves").resolve(strict=True):
        raise ValueError("请选择这个 BMC4 实例 saves 文件夹中的世界。")
    source = source.resolve(strict=True)
    if source.is_relative_to(TEST_ROOT.resolve()):
        marker = instance / ".mcaibuilder-test"
        if (not terrain_fixture or instance.parent != TEST_ROOT.resolve()
                or not instance.name.startswith("m6-terrain-source-")
                or source.name != "m1-construction" or not marker.is_file() or linked(marker)):
            raise ValueError("请选择原始 BMC4 单人存档。")
    elif terrain_fixture:
        raise ValueError("The terrain fixture must use an isolated synthetic source.")
    if not (instance / "mods").is_dir() or not any((instance / "mods").glob("*.jar")):
        raise ValueError("所选 Forge 实例缺少启用的 BMC4 模组。")
    root = copy_root()
    identity = uuid4()
    game = root / identity.hex
    with TemporaryDirectory(prefix=".pending-", dir=root) as temporary:
        staged = Path(temporary) / "instance"
        if terrain_fixture:
            player_id, _ = saved_player(source / "level.dat")
            if player_id != UUID(LOCAL_PLAYER_ID):
                raise ValueError("Expected the synthetic BuilderTester save.")
            player_name = "BuilderTester"
        else:
            player_id, player_name, _ = discover_player(instance, source)
        before = file_manifest(source, world=True)
        mods = file_manifest(instance / "mods")
        required = sum((source / name).stat().st_size for name in before["files"])
        required += sum((instance / "mods" / name).stat().st_size for name in mods["files"])
        if shutil.disk_usage(root).free < required + 1024 ** 3:
            raise ValueError("D 盘空间不足，需为副本和后续世界存档预留空间。")
        world = staged / "saves" / WORLD
        copy_tree(source, world, world=True)
        copy_tree(instance / "mods", staged / "mods")
        if (file_manifest(world, world=True) != before or file_manifest(source, world=True) != before
                or file_manifest(staged / "mods") != mods or file_manifest(instance / "mods") != mods):
            raise ValueError("复制期间文件发生变化，请关闭原游戏后重试。")
        for name in PACK_DIRS:
            folder = instance / name
            if folder.is_dir():
                copy_tree(folder, staged / name, config=name == "config")
        (staged / "config").mkdir(exist_ok=True)
        if any((staged / "mods").glob("packanalytics-*.jar")):
            (staged / "config" / "packanalytics-common.toml").write_text(
                '"Metrics Endpoint URL" = ""\n"Pack ID" = ""\n"Update Rate" = 30\n', encoding="utf-8")
        if (instance / "options.txt").is_file() and not linked(instance / "options.txt"):
            shutil.copyfile(instance / "options.txt", staged / "options.txt")
        write_json(staged / MARKER, {"schema": 1, "copy_id": str(identity), "source_label": source.name,
                                     "source_world": str(source), "source_instance": str(instance),
                                     "player_uuid": str(player_id), "player_name": player_name,
                                     "enabled_jars": sum("/" not in name and name.lower().endswith(".jar") for name in mods["files"])})
        write_json(staged / "copy-integrity.json", {"world": before, "mods": mods}, compact=True)
        staged.rename(game)
    return game


def configure_copy(game: Path, port: int):
    record = read_copy(game)
    inventory(game / "saves" / WORLD, world=True)
    # Keep the integrated server available while its owner reviews Blender or the launcher.
    options = game / "options.txt"
    if options.is_file():
        if linked(options):
            raise ValueError("副本客户端选项不能使用链接。")
        content = options.read_text(encoding="utf-8")
    else:
        content = ""
    configured = re.sub(r"(?m)^pauseOnLostFocus:.*$", "pauseOnLostFocus:false", content)
    if "pauseOnLostFocus:" not in configured:
        configured = configured.rstrip() + "\npauseOnLostFocus:false\n"
    if configured != content:
        options.write_text(configured, encoding="utf-8", newline="\n")
    # Match only the deployed project JARs. The original pack is already copied.
    for jar in (BRIDGE_JAR, PLAYERS_JAR):
        destination = game / "mods" / jar.name
        if destination.exists() and linked(destination):
            raise ValueError("拒绝覆盖副本中带有链接的模组文件。")
        content = jar.read_bytes()
        if not destination.is_file() or destination.read_bytes() != content:
            temporary = None
            try:
                with NamedTemporaryFile(prefix=".builder-", suffix=".part", dir=destination.parent,
                                        delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(content)
                temporary.replace(destination)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
    config = game / "config" / "mcaibuilder-copy.json"
    scoped = config.with_name("mcaibuilder-architect.json")
    if config.exists():
        if linked(config):
            raise ValueError("副本 Bridge 配置不能使用链接。")
        settings = json.loads(config.read_text(encoding="utf-8"))
        if (settings["copy_id"], settings["player_uuid"]) != (record["copy_id"], record["player_uuid"]):
            raise ValueError("副本 Bridge 配置与玩家标记不一致。")
        if settings["port"] != port:
            settings["port"] = port
            write_json(config, settings)
    else:
        settings = {"copy_id": record["copy_id"], "player_uuid": record["player_uuid"], "port": port,
                    "token": secrets.token_hex(32), "architect_token": secrets.token_hex(32)}
        write_json(config, settings)
    if scoped.exists() and linked(scoped):
        raise ValueError("副本 Runtime 配置不能使用链接。")
    write_json(scoped, {"port": port, "token": settings["architect_token"]})
    return record, config
