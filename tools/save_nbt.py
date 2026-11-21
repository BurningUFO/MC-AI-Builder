"""Bounded read-only NBT metadata for selecting a singleplayer save identity."""

from gzip import open as gzip_open
from pathlib import Path
from struct import Struct
from uuid import UUID


_LIMIT = 16 * 1024 * 1024
_NUMBERS = {tag: Struct(">" + code) for tag, code in enumerate("bhiqfd", start=1)}


class _Reader:
    def __init__(self, data: bytes):
        self.data = memoryview(data)
        self.offset = 0

    def take(self, size):
        end = self.offset + size
        if size < 0 or end > len(self.data):
            raise ValueError("Incomplete Minecraft save metadata.")
        part = self.data[self.offset:end]
        self.offset = end
        return part

    def number(self, tag):
        codec = _NUMBERS[tag]
        return codec.unpack(self.take(codec.size))[0]

    def string(self):
        size = int.from_bytes(self.take(2), "big")
        # Only field names and an optional dimension ID are needed by the caller.
        return bytes(self.take(size)).decode("utf-8", errors="replace")

    def value(self, tag, depth):
        if depth > 96:
            raise ValueError("Minecraft save metadata is nested too deeply.")
        if tag in _NUMBERS:
            return self.number(tag)
        if tag == 8:
            return self.string()
        if tag == 10:
            rows = {}
            while child := self.number(1):
                name = self.string()
                rows[name] = self.value(child, depth + 1)
            return rows
        if tag == 9:
            child, length = self.number(1), self.number(3)
            if length > len(self.data) - self.offset or length < 0 or (child == 0 and length != 0):
                raise ValueError("Invalid Minecraft NBT list.")
            return [self.value(child, depth + 1) for _ in range(length)]
        if tag in (7, 11, 12):
            size = {7: 1, 11: 4, 12: 8}[tag]
            length = self.number(3)
            values = self.take(length * size)
            if tag == 11 and length == 4:
                return bytes(values)
            return None
        raise ValueError("Unknown Minecraft NBT tag.")


def read_nbt(path: Path) -> dict:
    with gzip_open(path, "rb") as stream:
        data = stream.read(_LIMIT + 1)
    if len(data) > _LIMIT:
        raise ValueError("Minecraft save metadata exceeds the supported size.")
    reader = _Reader(data)
    if reader.number(1) != 10:
        raise ValueError("Expected a named NBT compound.")
    reader.string()
    return reader.value(10, 0)


def saved_player(path: Path) -> tuple[UUID, dict]:
    player = read_nbt(path).get("Data", {}).get("Player")
    if not isinstance(player, dict) or not isinstance(player.get("UUID"), bytes):
        raise ValueError("找不到此单人存档的本地玩家记录。请先在原游戏正常保存后关闭该世界。")
    return UUID(bytes=player["UUID"]), player
