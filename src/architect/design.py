"""Generate supported Minecraft buildings from validated architectural components."""

from dataclasses import asdict, dataclass
from itertools import product
from math import prod

from src.architect.grammar import LIGHTING, generate_framing, generate_roof, illuminate
from src.core.building import Building
from src.construction.plan import MAX_ACTIONS, VANILLA_BLOCKS

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


MAX_MODULES = 96
MODULE_MODES = ("fill", "shell", "erase", "roof", "framing")


@dataclass(frozen=True)
class VoxelDesign:
    """Apply ordered cuboids to a sparse grid, with openings cut by erase modules."""

    modules: tuple[dict, ...]
    name: str = "Builder design"
    overlay: bool = False
    lighting: str = "none"

    @classmethod
    def edit(cls, changes: dict, base=None):
        if set(changes) - {"name", "modules", "overlay", "lighting"}:
            raise DesignError("体素方案使用建筑名称和填充、空壳、开洞模块。")
        name = changes.get("name", base.name if base else "Builder design")
        new = changes.get("modules")
        overlay = changes.get("overlay", False)
        if (not isinstance(name, str) or not name.strip() or len(name) > 80
                or type(overlay) is not bool or overlay and base is None):
            raise DesignError("请提供简短的建筑名称，增量模块需要一份现有设计。")
        if new is None and base is not None:
            new = base.modules
            overlay = False
        if not isinstance(new, (list, tuple)) or not 1 <= len(new) <= MAX_MODULES:
            raise DesignError(f"请将设计组合成 1–{MAX_MODULES} 个体素模块。")
        modules = (base.modules + tuple(new)) if overlay else tuple(new)
        if len(modules) > MAX_MODULES:
            raise DesignError("增量模块过多，请合并围墙、楼板和屋顶后重试。")
        default_light = base.lighting if base else "torch" if any(
            isinstance(module, dict) and module.get("mode") == "roof" for module in modules) else "none"
        lighting = changes.get("lighting", default_light)
        if lighting not in LIGHTING:
            raise DesignError("照明可用火把、灯笼或手动布置。")
        return cls(modules, name.strip(), lighting=lighting)

    generate_roof = staticmethod(generate_roof)
    generate_framing = staticmethod(generate_framing)

    def building(self, available_mod_blocks=()) -> Building:
        model = Building(self.name)
        materials = VANILLA_BLOCKS | set(available_mod_blocks)
        for raw in self.modules:
            module = {key: value for key, value in raw.items() if value is not None} if isinstance(raw, dict) else {}
            if set(module) - {"mode", "from", "to", "block", "properties", "style", "overhang", "ridge_axis", "spacing"}:
                raise DesignError("体素模块需要模式、起止位置和方块。")
            mode = module.get("mode")
            start, end = module.get("from"), module.get("to")
            if (mode not in MODULE_MODES
                    or any(not isinstance(point, (list, tuple)) or len(point) != 3
                           or any(type(axis) is not int or abs(axis) > 128 for axis in point)
                           for point in (start, end))):
                raise DesignError("模块需要有效模式和三维整数局部坐标。")
            low, high = tuple(map(min, start, end)), tuple(map(max, start, end))
            span = tuple(b - a + 1 for a, b in zip(low, high))
            if prod(span) > MAX_ACTIONS * 4:
                raise DesignError("请缩小单个填充区域。")
            block = module.get("block")
            properties = module.get("properties") or {}
            if mode != "erase" and (not isinstance(block, str) or block not in materials or not isinstance(properties, dict)):
                raise DesignError(f"当前实例没有提供这个模块使用的材料或状态 {block}。")
            if mode in ("roof", "framing"):
                try:
                    if mode == "roof":
                        pieces = self.generate_roof(module.get("style"), (low, high), module.get("overhang", 1),
                                                    block, module.get("ridge_axis"), available_mod_blocks=materials)
                    else:
                        pieces = self.generate_framing((low, high), block, module.get("spacing", 4),
                                                       available_mod_blocks=materials)
                except ValueError as error:
                    raise DesignError(str(error)) from error
                for point, (material, state) in pieces.items():
                    model.place(point, material, **state)
            else:
                if mode != "erase":
                    properties = self.block_properties(block, properties)
                    if isinstance(available_mod_blocks, dict) and block in available_mod_blocks:
                        properties = {**available_mod_blocks[block].get("properties", {}), **properties}
                for point in product(*(range(a, b + 1) for a, b in zip(low, high))):
                    if mode == "erase":
                        model.blocks.pop(point, None)
                    elif mode == "fill" or any(axis in (a, b) for axis, a, b in zip(point, low, high)):
                        model.place(point, block, **properties)
            if len(model.blocks) > MAX_ACTIONS * 2:
                raise DesignError("模块展开过大，请缩小占地或减少实体填充。")
        illuminate(model, self.lighting)
        if not 1 <= len(model.blocks) <= MAX_ACTIONS:
            raise DesignError(f"施工设计需要 1–{MAX_ACTIONS} 个实体方块。")
        return model

    @staticmethod
    def block_properties(block, supplied):
        choices = {}
        if block.endswith(("_log", "_wood", "_beam", "_stem", "_hyphae")):
            choices["axis"] = ("y", "x", "z")
        if block.endswith("_stairs"):
            choices.update(facing=("north", "east", "south", "west"),
                           half=("bottom", "top"), shape=("straight",), waterlogged=("false",))
        if block == "minecraft:wall_torch":
            choices["facing"] = ("north", "east", "south", "west")
        if block.endswith((":lantern", "_lantern")):
            choices.update(hanging=("false", "true"), waterlogged=("false",))
        if block.endswith("_slab"):
            choices.update(type=("bottom", "top"), waterlogged=("false",))
        if not block.startswith("minecraft:"):
            # The installed registry remains authoritative for optional mod properties.
            optional = {"axis": ("y", "x", "z"), "facing": ("north", "east", "south", "west"),
                        "half": ("bottom", "top"), "type": ("bottom", "top"), "hanging": ("false", "true")}
            choices.update({key: optional[key] for key in supplied.keys() & optional.keys() if key not in choices})
        if set(supplied) - choices.keys() or any(value not in choices[key] for key, value in supplied.items()):
            raise DesignError(f"{block} 的朝向或方块状态超出支持范围。")
        return {key: supplied.get(key, values[0]) for key, values in choices.items()}


DESIGNS = {"voxel": VoxelDesign, "window_wall": Wall, "cottage": Cottage}


def voxel_footings(model, sites, origin, rotation, prior=None):
    if prior is not None:
        positions = prior
    else:
        anchor = next((site for site in sites if tuple(site["origin"]) == origin), {})
        positions = []
        for x, depth, z in anchor.get("foundation_gaps", ()):
            local_x, local_z = ((x, z), (z, -x), (-x, -z), (-z, x))[rotation // 90]
            positions.extend((local_x, -y, local_z) for y in range(1, depth + 1))
    added = []
    for x, y, z in positions:
        if (x, 0, z) in model.blocks and (x, y, z) not in model.blocks:
            model.place((x, y, z), "minecraft:dirt")
            added.append((x, y, z))
    if len(model.blocks) > MAX_ACTIONS:
        raise DesignError("补齐地基后方块总数超过施工计划上限，请缩小建筑占地。")
    return added


def fitted_origin(model, context, rotation):
    """Pick a surveyed anchor whose transformed bounds fit the authorized area."""
    radians = rotation // 90
    points = list(model.bounds())
    min_pos, max_pos = context["approved_area"]["min"], context["approved_area"]["max"]
    bounds = []
    for x, _, z in product(*(sorted({a, b}) for a, b in zip(*points))):
        bounds.append(((x, z), (-z, x), (-x, -z), (z, -x))[radians])
    bottom, top = points[0][1], points[1][1]
    candidates = context.get("sites", ()) or ({"origin": context["suggested_origin"]},)
    for candidate in candidates:
        ox, oy, oz = candidate["origin"]
        if (min_pos[1] <= oy + bottom <= oy + top <= max_pos[1]
                and all(min_pos[0] <= ox + x <= max_pos[0] and min_pos[2] <= oz + z <= max_pos[2]
                        for x, z in bounds)):
            return ox, oy, oz
    raise DesignError("这份方案超出已批准的施工区。我们可以缩小建筑，或为它标记一块更大的区域。")


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
