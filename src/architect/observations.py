"""Read a bounded, locally captured NPC image from the observation cache."""

from hashlib import sha256
from pathlib import Path
import re
import struct
from uuid import UUID


MAX_IMAGE_BYTES = 1_200_000
MAX_WIDTH, MAX_HEIGHT = 768, 432
PNG = b"\x89PNG\r\n\x1a\n"


def observation_image(root: Path, metadata: dict) -> bytes:
    if metadata.get("capture_status") != "captured" or metadata.get("camera_source") != "npc_first_person":
        raise ValueError("The NPC observation has no captured image.")
    identity = metadata.get("observation_id")
    if not isinstance(identity, str) or str(UUID(identity)) != identity:
        raise ValueError("Invalid observation ID.")
    name = identity + ".png"
    if metadata.get("image_path") != name:
        raise ValueError("The observation image name does not match its ID.")
    digest = metadata.get("image_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Invalid observation digest.")
    path = root.absolute() / name
    if not path.is_file() or path.resolve(strict=True) != path:
        raise ValueError("Observation image must stay in the local cache.")
    with path.open("rb") as stream:
        image = stream.read(MAX_IMAGE_BYTES + 1)
    if len(image) > MAX_IMAGE_BYTES or sha256(image).hexdigest() != digest:
        raise ValueError("The observation image size or digest is invalid.")
    if len(image) < 33 or image[:16] != PNG + b"\x00\x00\x00\x0dIHDR":
        raise ValueError("The observation is missing a PNG header.")
    width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", image[16:29])
    if not (1 <= width <= MAX_WIDTH and 1 <= height <= MAX_HEIGHT
            and width == metadata.get("width") and height == metadata.get("height")
            and (depth, color, compression, filtering, interlace) == (8, 2, 0, 0, 0)):
        raise ValueError("Expected a bounded, non-interlaced RGB observation.")
    return image
