"""Read a bounded, locally captured NPC image from the observation cache."""

from hashlib import sha256
from pathlib import Path
import re
import struct
from uuid import UUID
import zlib


MAX_IMAGE_BYTES = 1_200_000
MAX_WIDTH, MAX_HEIGHT = 768, 432
PNG = b"\x89PNG\r\n\x1a\n"


def rgb_png(image: bytes, *, max_bytes=MAX_IMAGE_BYTES, max_width=MAX_WIDTH, max_height=MAX_HEIGHT) -> tuple[bytes, int, int]:
    """Validate the complete RGB frame and retain only its pixel-bearing chunks."""
    if type(image) is not bytes or not 33 <= len(image) <= max_bytes or image[:16] != PNG + b"\x00\x00\x00\x0dIHDR":
        raise ValueError("Invalid observation PNG header or size.")
    width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", image[16:29])
    if not (1 <= width <= max_width and 1 <= height <= max_height
            and (depth, color, compression, filtering, interlace) == (8, 2, 0, 0, 0)):
        raise ValueError("Expected a bounded, non-interlaced RGB observation.")
    keep = [PNG]
    compressed = []
    offset = len(PNG)
    while offset + 12 <= len(image):
        length = int.from_bytes(image[offset:offset + 4], "big")
        end = offset + length + 12
        kind = image[offset + 4:offset + 8]
        if end > len(image) or zlib.crc32(image[offset + 4:end - 4]) != int.from_bytes(image[end - 4:end], "big"):
            raise ValueError("Invalid chunk in the observation PNG.")
        if kind in (b"IHDR", b"IDAT", b"IEND"):
            keep.append(image[offset:end])
            if kind == b"IDAT":
                compressed.append(image[offset + 8:end - 4])
            elif kind == b"IHDR" and offset != len(PNG):
                raise ValueError("Duplicate observation PNG header.")
            elif kind == b"IEND":
                if length or end != len(image) or not compressed:
                    raise ValueError("Invalid observation PNG end.")
                break
        elif kind[:1].isupper() and kind != b"PLTE":
            raise ValueError("Unsupported critical observation PNG chunk.")
        offset = end
    else:
        raise ValueError("Truncated observation PNG.")

    stride = width * 3 + 1
    expected = stride * height
    inflater = zlib.decompressobj()
    try:
        pixels = inflater.decompress(b"".join(compressed), expected + 1)
    except zlib.error:
        raise ValueError("Invalid observation PNG pixel data.") from None
    if len(pixels) != expected or not inflater.eof or inflater.unused_data or any(value > 4 for value in pixels[::stride]):
        raise ValueError("Invalid observation PNG scanlines.")
    return b"".join(keep), width, height


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
    clean, width, height = rgb_png(image)
    if (width, height) != (metadata.get("width"), metadata.get("height")):
        raise ValueError("The observation dimensions do not match the metadata.")
    return clean
