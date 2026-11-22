import hashlib
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4
import zlib

from src.architect.observations import PNG, observation_image


def png_chunk(kind, payload):
    content = kind + payload
    return struct.pack(">I", len(payload)) + content + struct.pack(">I", zlib.crc32(content))


def rgb_png():
    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return PNG + png_chunk(b"IHDR", header) + png_chunk(b"IDAT", zlib.compress(b"\x00\x01\x02\x03")) + png_chunk(b"IEND", b"")


class ObservationImageTests(unittest.TestCase):
    def setUp(self):
        temporary = Path(__file__).resolve().parents[1] / ".cache" / "m6-a"
        temporary.mkdir(parents=True, exist_ok=True)
        self.directory = self.enterContext(TemporaryDirectory(dir=temporary))
        self.root = Path(self.directory).resolve()
        identity = str(uuid4())
        self.image = rgb_png()
        self.metadata = {
            "observation_id": identity, "image_path": identity + ".png",
            "image_sha256": hashlib.sha256(self.image).hexdigest(), "capture_status": "captured",
            "camera_source": "npc_first_person", "width": 1, "height": 1,
        }
        self.file = self.root / self.metadata["image_path"]
        self.file.write_bytes(self.image)

    def test_accepts_only_an_identified_local_rgb_image(self):
        self.assertEqual(observation_image(self.root, self.metadata), self.image)
        for altered in (
            {"image_path": "../" + self.file.name},
            {"observation_id": self.metadata["observation_id"].upper()},
            {"camera_source": "player"},
            {"capture_status": "pending"},
            {"width": 2},
        ):
            with self.subTest(altered=altered), self.assertRaises(ValueError):
                observation_image(self.root, self.metadata | altered)

    def test_rejects_tampered_and_non_png_frames(self):
        self.file.write_bytes(self.image + b"modified")
        with self.assertRaises(ValueError):
            observation_image(self.root, self.metadata)
        invalid = b"not a PNG"
        self.file.write_bytes(invalid)
        with self.assertRaises(ValueError):
            observation_image(self.root, self.metadata | {"image_sha256": hashlib.sha256(invalid).hexdigest()})
