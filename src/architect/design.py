"""Generate supported wall designs from validated parameters, never model-authored code."""

from dataclasses import asdict, dataclass

from src.core.building import Building

CHIPPED_WOOD = "chipped:stacked_spruce_planks"
QUARK_ROOF = "quark:red_shingles"
QUARK_STAIR = "quark:red_shingles_stairs"
ROOFS = ("minecraft:stone_brick_slab", QUARK_ROOF)
WOODS = ("minecraft:oak_planks", "minecraft:spruce_planks", "minecraft:dark_oak_planks", CHIPPED_WOOD)
MASONRY = ("minecraft:stone_bricks", "minecraft:cobblestone", "minecraft:stone")
GLASS = ("minecraft:glass", "minecraft:light_blue_stained_glass", "minecraft:blue_stained_glass")
STYLES = ("medieval", "plain")
ROTATION = {"north": 0, "east": 90, "south": 180, "west": 270}


class DesignError(ValueError):
    pass


@dataclass(frozen=True)
class Wall:
    width: int = 8
    height: int = 3
    window_width: int = 2
    window_height: int = 1
    style: str = "medieval"
    wood: str = "minecraft:spruce_planks"
    masonry: str = "minecraft:stone_bricks"
    glass: str = "minecraft:glass"
    porch: bool = True
    roof: str = ROOFS[0]

    @classmethod
    def edit(cls, changes: dict, base=None):
        if not isinstance(changes, dict) or set(changes) - set(cls.__dataclass_fields__):
            raise DesignError("窗墙只支持调整宽高、窗洞、风格、木材、石材、玻璃和门前台阶。")
        fields = asdict(base or cls())
        fields.update(changes)
        if base is None and "window_width" not in changes and type(fields["width"]) is int:
            fields["window_width"] = min(2, (fields["width"] - 3) // 2)
        if base is None and "porch" not in changes:
            fields["porch"] = fields["style"] == "medieval"
        next_wall = cls(**fields)
        next_wall.validate()
        return next_wall

    def validate(self):
        for value, bounds in (
            (self.width, (5, 12)),
            (self.height, (3, 5)),
        ):
            if type(value) is not int or not bounds[0] <= value <= bounds[1]:
                raise DesignError("窗墙宽度需在 5–12 格，高度需在 3–5 格。")
        if (type(self.window_width) is not int or not 1 <= self.window_width <= (self.width - 3) // 2
                or type(self.window_height) is not int or not 1 <= self.window_height <= self.height - 2):
            raise DesignError("这一窗洞超出墙体可用尺寸。请缩小窗洞，或明确指定更大的墙体尺寸。")
        if self.style not in STYLES or type(self.porch) is not bool:
            raise DesignError("可用风格为中世纪和简洁木石，请明确是否需要门前台阶。")
        if self.roof not in ROOFS:
            raise DesignError("屋顶可用石砖台阶或已验证的 Quark 红瓦。")
        if self.wood not in WOODS or self.masonry not in MASONRY or self.glass not in GLASS:
            raise DesignError("请选择橡木、云杉或深色橡木，石砖、圆石或石头，以及透明、淡蓝或蓝色玻璃。")

    def building(self) -> Building:
        wall = Building(f"{self.style.title()} window wall")
        for x in range(self.width):
            wall.place((x, 0, 0), self.masonry)
            window = 1 <= x <= self.window_width or self.width - 1 - self.window_width <= x <= self.width - 2
            for y in range(1, self.height - 1):
                wall.place((x, y, 0), self.glass if window and y <= self.window_height else self.wood)
            top = (x, self.height - 1, 0)
            if self.roof == QUARK_ROOF:
                if x in (0, self.width - 1):
                    wall.place(top, QUARK_STAIR, facing="east" if x == 0 else "west",
                               half="bottom", shape="straight", waterlogged="false")
                else:
                    wall.place(top, QUARK_ROOF)
            elif self.style == "medieval":
                wall.place(top, self.roof,
                           type="bottom", waterlogged="false")
            else:
                wall.place(top, self.wood)
        if self.porch:
            for x in range(2, self.width - 2):
                wall.place((x, 0, 1), self.masonry)
            for x in (1, self.width - 2):
                wall.place((x, 0, 1), "minecraft:oak_stairs", facing="north",
                           half="bottom", shape="straight", waterlogged="false")
        return wall


def placement(intent: dict, context: dict, prior: dict | None, locked: bool) -> tuple[tuple[int, int, int], int]:
    direction = context["facing"]
    origin = intent.get("origin", prior["origin"] if prior else context["suggested_origin"])
    rotation = intent.get("rotation", prior["rotation"] if prior else ROTATION[direction])
    if origin == "here":
        origin = context["suggested_origin"]
    if rotation == "here":
        rotation = ROTATION[direction]
    if (not isinstance(origin, list) or len(origin) != 3
            or any(type(axis) is not int or abs(axis) > 30_000_000 for axis in origin)):
        raise DesignError("请提供明确的三维坐标，或用「这里」标记施工点。")
    if type(rotation) is not int or rotation not in ROTATION.values():
        raise DesignError("请指定北、东、南、西中的一个方向。")
    if locked and (origin != prior["origin"] or rotation != prior["rotation"]):
        raise DesignError("施工中的修改保留原点和朝向。若需换位置，请先明确结束当前项目。")
    return tuple(origin), rotation
