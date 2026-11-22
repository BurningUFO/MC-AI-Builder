"""Create a normal-terrain synthetic BMC4 save and copy it through the launcher pipeline."""

from argparse import ArgumentParser
from uuid import uuid4

from src.core.building import write_json
from tests.m1_smoke import require, wait_for
from tests.m6_a_smoke import stop
from tests.m6_c_smoke import HOME
from tools.m1_client import launch_client
from tools.mcai_launcher import SETUP, find_setup, isolated_pack, local_game, read_object
from tools.world_copy import create_copy


def create_terrain_copies(count):
    settings = find_setup(read_object(SETUP))
    pack = isolated_pack()
    require(pack is not None, "A local isolated BMC4 pack is required")
    game = local_game("m6-terrain-source-" + uuid4().hex[:8])
    source = game / "saves" / "m1-construction"
    marker = HOME / "terrain-source.json"
    write_json(marker, {"source": str(game)})
    child = launch_client(settings["java"], settings["instance"], game, 18786, bmc4=True,
                          pack_source=pack, survival=True, smoke=True, terrain_fixture=True,
                          managed=True, hide_console=True)
    try:
        def ready():
            log = (game / "console.log").read_bytes()
            return (source / "level.dat").is_file() and b"BuilderTester joined the game" in log

        wait_for(child, ready, "the new normal-terrain BMC4 fixture", seconds=580)
    finally:
        stop(child, game)
    copies = [create_copy(game, source, terrain_fixture=True) for _ in range(count)]
    write_json(marker, {"source": str(game), "copies": [str(copy) for copy in copies]})
    print(f"Verified {len(copies)} complete synthetic BMC4 copies.", flush=True)


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--copies", type=int, choices=(1, 2, 3), default=1)
    create_terrain_copies(parser.parse_args().copies)
