"""Stage and run the Forge 1.20.1 test client from a local PCL installation."""

from argparse import ArgumentParser
from hashlib import sha1, sha256
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
from zipfile import ZipFile

from src.core.building import write_json


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / "mc-test"
CACHE = ROOT / ".cache" / "m1"
RUNTIME = TEST_ROOT / "client-runtime"
DEFAULT_GAME = TEST_ROOT / "forge-client"
WORLD_NAME = "M1 Construction Lab"
FORGE = "1.20.1-47.4.20"
BRIDGE_JAR = ROOT / "forge" / "build" / "libs" / "mcaibuilder-bridge-0.1.0.jar"
PLAYERS_JAR = CACHE / "downloads" / "players-forge-1.20.1-3.0.0.jar"
PLAYERS_HASH = "5ae6b53d57d648fadc6851e5bba4d729fc64c36040727ff5dcc0632a66482104"


def _copy(source: Path, target: Path, size: int | None = None, digest: str | None = None) -> None:
    actual_size = source.stat().st_size
    if size is not None and size != actual_size:
        raise ValueError(f"Wrong size for {source}")
    if target.is_file() and target.stat().st_size == actual_size:
        if digest is None or sha1(target.read_bytes()).hexdigest() == digest:
            return
    if digest is not None and sha1(source.read_bytes()).hexdigest() != digest:
        raise ValueError(f"SHA-1 mismatch for {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(target.name + ".copying")
    shutil.copyfile(source, staged)
    staged.replace(target)


def _windows_rule(rules: list | None) -> bool:
    if not rules:
        return True
    allowed = False
    for rule in rules:
        options = rule.get("os", {})
        if rule.get("features") or options.get("name", "windows") != "windows" or options.get("arch") == "x86":
            continue
        allowed = rule["action"] == "allow"
    return allowed


def _asset_path(value: str) -> Path:
    path = Path(value)
    if path.anchor or ".." in path.parts:
        raise ValueError(f"Invalid library path: {value}")
    return path


def stage_runtime(source: Path) -> tuple[dict, str]:
    source = source.resolve(strict=True)
    shared = source.parent.parent
    profile = json.loads((source / f"{source.name}.json").read_text(encoding="utf-8"))
    game_args = profile["arguments"]["game"]
    if (profile["mainClass"] != "cpw.mods.bootstraplauncher.BootstrapLauncher"
            or "1.20.1" not in game_args or "47.4.20" not in game_args or "forgeclient" not in game_args):
        raise ValueError("Use a PCL Minecraft 1.20.1 instance with Forge 47.4.20.")
    client = source / f"{source.name}.jar"
    with ZipFile(client) as archive:
        if json.loads(archive.read("version.json"))["id"] != "1.20.1":
            raise ValueError("Client JAR is not Minecraft 1.20.1.")
    _copy(client, RUNTIME / "1.20.1.jar", digest="0c3ec587af28e5a785c0b4a7b8a30f9a8f78f838")
    libraries = RUNTIME / "libraries"
    classpath = []
    for library in profile["libraries"]:
        name = library["name"]
        if not _windows_rule(library.get("rules")) or name.endswith((":natives-windows-x86", ":natives-windows-arm64")):
            continue
        artifact = library["downloads"]["artifact"]
        path = _asset_path(artifact["path"])
        _copy(shared / "libraries" / path, libraries / path, artifact.get("size"), artifact.get("sha1"))
        classpath.append(libraries / path)
    for directory, names in (
        (f"net/minecraftforge/forge/{FORGE}", (f"forge-{FORGE}-client.jar", f"forge-{FORGE}-universal.jar")),
        ("net/minecraft/client/1.20.1-20230612.114412",
         ("client-1.20.1-20230612.114412-srg.jar", "client-1.20.1-20230612.114412-extra.jar")),
    ):
        for name in names:
            path = Path(directory) / name
            _copy(shared / "libraries" / path, libraries / path)
    for name in ("fmlcore", "javafmllanguage", "lowcodelanguage", "mclanguage"):
        path = Path("net", "minecraftforge", name, FORGE, f"{name}-{FORGE}.jar")
        _copy(shared / "libraries" / path, libraries / path)

    asset_source = shared / "assets"
    asset = profile["assetIndex"]
    index = Path("indexes") / f"{asset['id']}.json"
    _copy(asset_source / index, RUNTIME / "assets" / index, asset["size"], asset["sha1"])
    objects = json.loads((asset_source / index).read_text(encoding="utf-8"))["objects"]
    for info in {row["hash"]: row for row in objects.values()}.values():
        digest = info["hash"]
        path = Path("objects") / digest[:2] / digest
        _copy(asset_source / path, RUNTIME / "assets" / path, info["size"])
    return profile, os.pathsep.join(path.as_posix() for path in [*classpath, RUNTIME / "1.20.1.jar"])


def configure_world(game: Path, port: int) -> Path:
    if not 1024 <= port <= 65535:
        raise ValueError("Bridge port must be in 1024–65535.")
    game = game.resolve()
    if game == TEST_ROOT.resolve() or not game.is_relative_to(TEST_ROOT.resolve()):
        raise ValueError("The test client directory must be inside the project's mc-test directory.")
    marker = game / ".mcaibuilder-test"
    if game.exists() and any(game.iterdir()) and not marker.is_file():
        raise ValueError(f"Refusing to use an existing directory without the M1 fixture marker: {game}")
    game.mkdir(parents=True, exist_ok=True)
    marker.touch()
    if not BRIDGE_JAR.is_file():
        raise FileNotFoundError("Build the Forge Bridge before launching the M1 client.")
    with ZipFile(BRIDGE_JAR) as mod:
        if "dev/mcaibuilder/bridge/ForgeBridge.class" not in mod.namelist():
            raise ValueError("The Forge Bridge JAR is missing its compiled entry point.")
    if sha256(PLAYERS_JAR.read_bytes()).hexdigest() != PLAYERS_HASH:
        raise ValueError("Fake Players 3.0.0 JAR checksum mismatch.")
    mods = game / "mods"
    mods.mkdir(exist_ok=True)
    expected = {BRIDGE_JAR.name, PLAYERS_JAR.name}
    if any(path.name not in expected for path in mods.glob("*.jar")):
        raise ValueError(f"Use a minimal test mods directory: {mods}")
    for jar in (BRIDGE_JAR, PLAYERS_JAR):
        source_hash = sha1(jar.read_bytes()).hexdigest()
        _copy(jar, mods / jar.name, digest=source_hash)
    config = game / "config" / "mcaibuilder.json"
    if not config.exists():
        write_json(config, {
            "world": WORLD_NAME,
            "port": port,
            "token": secrets.token_hex(32),
            "area": {"min": [0, -60, 0], "max": [32, -50, 24]},
            "npc_spawn": [1, -60, 13],
        })
    elif json.loads(config.read_text(encoding="utf-8"))["port"] != port:
        raise ValueError(f"Use the configured bridge port in {config}, or a fresh test directory.")
    options = game / "options.txt"
    if not options.exists():
        options.write_text("version:3465\npauseOnLostFocus:false\nautoJump:false\nrenderDistance:6\n"
                           "simulationDistance:5\nsoundCategory_master:0.0\nmaxFps:40\n"
                           "fullscreen:false\nonboardAccessibility:false\ntutorialStep:none\n", encoding="utf-8")
    return config


def _arguments(rows: list, values: dict) -> list[str]:
    result = []
    for entry in rows:
        if isinstance(entry, dict):
            if not _windows_rule(entry["rules"]):
                continue
            entry = entry["value"]
        for argument in [entry] if isinstance(entry, str) else entry:
            result.append(re.sub(r"\$\{([^}]+)\}", lambda match: values[match[1]], argument))
    return result


def launch_client(java_home: Path, source: Path, game: Path, port: int, *, smoke: bool = False) -> subprocess.Popen:
    game = game.resolve()
    configure_world(game, port)
    profile, classpath = stage_runtime(source)
    native_dir = game / "natives"
    temp = CACHE / "tmp"
    home_dir = CACHE / "home"
    for directory in (native_dir, temp, home_dir):
        directory.mkdir(parents=True, exist_ok=True)
    values = {
        "auth_player_name": "BuilderTester",
        "version_name": "1.20.1",
        "game_directory": game.as_posix(),
        "assets_root": (RUNTIME / "assets").as_posix(),
        "assets_index_name": profile["assetIndex"]["id"],
        "auth_uuid": "db93c9a066974dccaf0c8621d028855e",
        "auth_access_token": "0",
        "clientid": "",
        "auth_xuid": "",
        "user_type": "legacy",
        "version_type": "M1 Test",
        "natives_directory": native_dir.as_posix(),
        "launcher_name": "MC-AI-Builder",
        "launcher_version": "0.1",
        "classpath": classpath,
        "library_directory": (RUNTIME / "libraries").as_posix(),
        "classpath_separator": os.pathsep,
    }
    arguments = [
        "-Xms512M", "-Xmx3G", f"-Djava.io.tmpdir={temp.as_posix()}", f"-Duser.home={home_dir.as_posix()}",
        "-Dmcaibuilder.localTest=true", f"-Dmcaibuilder.smoke={str(smoke).lower()}",
        *_arguments(profile["arguments"]["jvm"], values),
        profile["mainClass"], *_arguments(profile["arguments"]["game"], values), "--width", "1100", "--height", "650",
    ]
    argument_file = game / "launch.args"
    argument_file.write_text("\n".join('"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'
                                      for arg in arguments) + "\n", encoding="utf-8")
    java = (java_home / "bin" / "java.exe").resolve(strict=True)
    logs = game / "console.log"
    environment = dict(os.environ, TEMP=str(temp), TMP=str(temp))
    with logs.open("wb") as log:
        return subprocess.Popen([str(java), f"@{argument_file}"], cwd=game, env=environment,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)


def main() -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument("--source-instance", type=Path, required=True)
    parser.add_argument("--game", type=Path, default=DEFAULT_GAME)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    child = launch_client(args.java_home, args.source_instance, args.game, args.port)
    print(f"Forge client {child.pid}. Logs: {args.game / 'console.log'}", flush=True)
    return child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
