"""Generate supported Minecraft buildings from validated architectural components."""

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
COTTAGE_SIZES = (7, 9)
ENTRIES = ("left", "center", "right")
WINDOW_LAYOUTS = ("compact", "paired", "wide")
ROOF_FORMS = ("flat", "eaves")
COTTAGE_ROOFS = ("minecraft:spruce_planks", "minecraft:dark_oak_planks", "minecraft:stone_bricks",
                 "minecraft:stone_brick_slab", QUARK_ROOF)
INTERIORS = ("bench", "bench_and_table", "open")
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


@dataclass(frozen=True)
class Cottage:
    """One centered floor plan, a two-block walkable room, and a separately chosen roof."""

    width: int = 7
    depth: int = 9
    entry: str = "center"
    windows: str = "paired"
    roof_form: str = "flat"
    wood: str = "minecraft:spruce_planks"
    masonry: str = "minecraft:stone_bricks"
    glass: str = "minecraft:glass"
    roof: str = "minecraft:spruce_planks"
    interior: str = "bench_and_table"

    @classmethod
    def edit(cls, changes: dict, base=None):
        if not isinstance(changes, dict) or set(changes) - set(cls.__dataclass_fields__):
            raise DesignError("住宅参数仅支持占地、入口、窗组、屋顶、木石玻璃和基本室内布局。")
        config = asdict(base or cls())
        config.update(changes)
        cottage = cls(**config)
        cottage.validate()
        return cottage

    def validate(self):
        if any(type(n) is not int or n not in COTTAGE_SIZES for n in (self.width, self.depth)):
            raise DesignError("单层住宅的宽和深各选 7 格或 9 格。")
        if self.entry not in ENTRIES or self.windows not in WINDOW_LAYOUTS:
            raise DesignError("住宅入口可居左、居中或居右。窗组可选紧凑、成对或宽窗。")
        if self.roof_form not in ROOF_FORMS or self.roof not in COTTAGE_ROOFS:
            raise DesignError("住宅可用平屋顶或前檐屋顶，并使用受支持的覆顶材料。")
        if self.wood not in WOODS or self.masonry not in MASONRY or self.glass not in GLASS:
            raise DesignError("请选择已支持的木材、石材和普通或蓝色玻璃。")
        if self.interior not in INTERIORS:
            raise DesignError("室内支持长凳、长凳和桌台或开放布局。")

    def building(self, footing=()) -> Building:
        self.validate()
        model = Building("Timber and stone cottage")
        x_edge, z_edge = self.width // 2, self.depth // 2
        xs, zs = range(-x_edge, x_edge + 1), range(-z_edge, z_edge + 1)
        for x, z in footing:
            if x not in xs or z not in zs:
                raise DesignError("地基支脚超出已核对的住宅占地。")
            model.place((x, -1, z), self.masonry)
        # Left and right are measured from inside the cottage looking out its front.
        door_x = {"left": -x_edge + 2, "center": 0, "right": x_edge - 2}[self.entry]
        windows = {"compact": (0,), "paired": (-2, 2), "wide": (-1, 0, 1)}[self.windows]

        for z in zs:
            for x in xs:
                boundary = abs(x) == x_edge or abs(z) == z_edge
                model.place((x, 0, z), self.masonry if boundary else self.wood)
                if not boundary or (x == door_x and z == -z_edge):
                    continue
                corner = abs(x) == x_edge and abs(z) == z_edge
                position = x if abs(z) == z_edge else z
                is_window = not corner and position in windows
                model.place((x, 1, z), self.glass if is_window else self.masonry)
                model.place((x, 2, z), self.glass if is_window else self.wood)

        if self.interior != "open":
            for x in (-x_edge + 1, -x_edge + 2):
                model.place((x, 1, z_edge - 1), "minecraft:oak_slab", type="bottom", waterlogged="false")
        if self.interior == "bench_and_table":
            model.place((0, 1, z_edge - 1), self.masonry)

        roof_front = -z_edge - (self.roof_form == "eaves")
        roof_state = {"type": "bottom", "waterlogged": "false"} if self.roof == "minecraft:stone_brick_slab" else {}
        for z in range(roof_front, z_edge + 1):
            for x in xs:
                model.place((x, 3, z), self.roof, **roof_state)
        return model


DESIGNS = {"window_wall": Wall, "cottage": Cottage}


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
