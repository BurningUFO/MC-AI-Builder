from gzip import compress
from pathlib import Path
from struct import pack
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from uuid import UUID

from tools.save_nbt import saved_player
from tools.world_copy import copy_tree, file_manifest


class WorldCopyTests(TestCase):
    def test_nested_world_data_and_empty_folders_survive_copy(self):
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "original"
            target = Path(temporary) / "copy"
            for relative in ("region", "DIM-1/entities", "DIM1/playerdata", "dimensions/example/place",
                             "serverconfig", "playerdata"):
                (source / relative).mkdir(parents=True)
            (source / "level.dat").write_bytes(b"save")
            (source / "level.dat_old").write_bytes(b"previous save")
            (source / "session.lock").write_bytes(b"closed handle")
            (source / "DIM-1/entities/r.0.0.mca").write_bytes(b"entities")
            (source / "serverconfig/example.toml").write_bytes(b"value = 1")
            manifest = file_manifest(source, world=True)
            copy_tree(source, target, world=True)
            self.assertEqual(file_manifest(target, world=True), manifest)
            self.assertEqual(file_manifest(source, world=True), manifest)
            self.assertFalse((target / "session.lock").exists())
            with patch("tools.world_copy.linked", side_effect=lambda p: p == source / "DIM1"):
                with self.assertRaises(ValueError):
                    copy_tree(source, target, world=True)

    def test_saved_owner_is_read_from_embedded_player_only(self):
        def tag(code, key, value):
            name = key.encode()
            return bytes([code]) + pack(">H", len(name)) + name + value

        owner = UUID("01234567-89ab-cdef-0123-456789abcdef")
        passenger = UUID("ff000000-89ab-cdef-0123-456789abcdef")
        player = tag(11, "UUID", pack(">i", 4) + owner.bytes) + b"\0"
        metadata = tag(11, "UUID", pack(">i", 4) + passenger.bytes) + tag(10, "Player", player) + b"\0"
        with TemporaryDirectory() as temporary:
            level = Path(temporary) / "level.dat"
            level.write_bytes(compress(tag(10, "", tag(10, "Data", metadata) + b"\0")))
            recovered, _ = saved_player(level)
            self.assertEqual(recovered, owner)
