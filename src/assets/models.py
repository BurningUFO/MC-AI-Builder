"""Resolve standard Minecraft JSON models to textured faces in Minecraft space."""

from dataclasses import dataclass
from functools import cache
import json
from math import cos, radians, sin

from src.assets.archives import Archives, AssetError, location
from src.core.building import BlockState


AXES = {
    "north": (0, 0, -1), "south": (0, 0, 1), "west": (-1, 0, 0),
    "east": (1, 0, 0), "up": (0, 1, 0), "down": (0, -1, 0),
}
DIRECTIONS = {vector: side for side, vector in AXES.items()}
# Each face is wound outward in the order top-left, bottom-left, bottom-right, top-right.
CORNERS = {
    "north": ((1, 1, 0), (1, 0, 0), (0, 0, 0), (0, 1, 0)),
    "south": ((0, 1, 1), (0, 0, 1), (1, 0, 1), (1, 1, 1)),
    "west": ((0, 1, 0), (0, 0, 0), (0, 0, 1), (0, 1, 1)),
    "east": ((1, 1, 1), (1, 0, 1), (1, 0, 0), (1, 1, 0)),
    "up": ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)),
    "down": ((0, 0, 1), (0, 0, 0), (1, 0, 0), (1, 0, 1)),
}
NON_VISUAL_PROPERTIES = {"waterlogged", "powered", "has_book"}


def check_loader(data: dict, description: str) -> None:
    loader = next((key for key in data if key in {"loader", "forge_marker"} or key.endswith(":loader")), None)
    if loader is not None:
        raise AssetError(f"{description}: special loader {loader} ({data[loader]})")


def property_matches(actual: str, expected: str) -> bool:
    terms = expected.split("|")
    return any((actual != term[1:] if term.startswith("!") else actual == term) for term in terms)


def condition_matches(condition: dict, properties: dict[str, str]) -> bool:
    return all(
        any(condition_matches(part, properties) for part in value) if key == "OR"
        else all(condition_matches(part, properties) for part in value) if key == "AND"
        else key in properties and property_matches(properties[key], value)
        for key, value in condition.items()
    )


def condition_keys(condition: dict) -> set[str]:
    keys = set()
    for key, value in condition.items():
        if key in {"OR", "AND"}:
            for part in value:
                keys.update(condition_keys(part))
        else:
            keys.add(key)
    return keys


def model_groups(spec: dict, state: BlockState) -> list[list[dict]]:
    check_loader(spec, state.block_id)
    properties = dict(state.properties)
    ignored = NON_VISUAL_PROPERTIES
    if state.block_id.endswith("_leaves"):
        if (properties.get("persistent", "false") not in {"true", "false"}
                or properties.get("distance", "7") not in set("1234567")):
            raise AssetError(f"{state.key}: invalid leaf persistence or distance")
        ignored = ignored | {"persistent", "distance"}
    if "variants" in spec:
        options = []
        for text, apply in spec["variants"].items():
            condition = dict(part.split("=", 1) for part in text.split(",") if part)
            if properties.keys() - condition.keys() <= ignored and condition_matches(condition, properties):
                options.append(apply)
        if len(options) != 1:
            raise AssetError(f"{state.key}: expected one matching variant, found {len(options)}")
    elif "multipart" in spec:
        parts = spec["multipart"]
        keys = set().union(*(condition_keys(part.get("when", {})) for part in parts))
        if keys - properties.keys() or properties.keys() - keys - ignored:
            raise AssetError(f"{state.key}: incomplete or unexpected multipart properties, expected {sorted(keys)}")
        options = [part["apply"] for part in parts if condition_matches(part.get("when", {}), properties)]
    else:
        raise AssetError(f"{state.key}: no standard variant or multipart model")
    if not options:
        raise AssetError(f"{state.key}: no model applies")
    return [option if isinstance(option, list) else [option] for option in options]


def subtract(left, right):
    return tuple(a - b for a, b in zip(left, right))


def dot(left, right):
    return sum(a * b for a, b in zip(left, right))


def state_rotation(vector, placement):
    x, y, z = vector
    for axis in "xy":
        degrees = placement.get(axis, 0)
        if degrees % 90 != 0:
            raise AssetError(f"Unsupported blockstate rotation: {axis}={degrees}")
        for _ in range(degrees // 90 % 4):
            x, y, z = (x, z, -y) if axis == "x" else (-z, y, x)
    return x, y, z


def element_rotation(vertex, rotation):
    if not rotation:
        return vertex
    pivot = rotation["origin"]
    x, y, z = subtract(vertex, pivot)
    angle = radians(rotation["angle"])
    c, s = cos(angle), sin(angle)
    scale = 1 / c if rotation.get("rescale", False) else 1
    match rotation["axis"]:
        case "x":
            rotated = x, (c * y - s * z) * scale, (s * y + c * z) * scale
        case "y":
            rotated = (c * x + s * z) * scale, y, (-s * x + c * z) * scale
        case "z":
            rotated = (c * x - s * y) * scale, (s * x + c * y) * scale, z
        case other:
            raise AssetError(f"Unknown model element rotation axis: {other}")
    return tuple(a + b for a, b in zip(rotated, pivot))


def face_uv(side, lower, upper):
    x0, y0, z0 = lower
    x1, y1, z1 = upper
    return {
        "north": (16 - x1, 16 - y1, 16 - x0, 16 - y0),
        "south": (x0, 16 - y1, x1, 16 - y0),
        "west": (z0, 16 - y1, z1, 16 - y0),
        "east": (16 - z1, 16 - y1, 16 - z0, 16 - y0),
        "up": (x0, z0, x1, z1),
        "down": (x0, 16 - z1, x1, 16 - z0),
    }[side]


def face_basis(side):
    top_left, bottom_left, _, top_right = CORNERS[side]
    return subtract(top_right, top_left), subtract(bottom_left, top_left)


def locked_uv(uv, side, placement):
    before_u, before_v = face_basis(side)
    after_u, after_v = face_basis(DIRECTIONS[state_rotation(AXES[side], placement)])
    u, v = uv
    in_plane = tuple((u - 8) * a + (v - 8) * b for a, b in zip(before_u, before_v))
    rotated = state_rotation(in_plane, placement)
    return 8 + dot(rotated, after_u), 8 + dot(rotated, after_v)


def texture_reference(reference: str, textures: dict) -> str:
    variables = set()
    while reference.startswith("#"):
        name = reference[1:]
        if name in variables or name not in textures:
            raise AssetError(f"Unresolved model texture: {reference}")
        variables.add(name)
        reference = textures[name]
    namespace, name = location(reference)
    return f"{namespace}:{name}"


def bake_element(element: dict, textures: dict, placement: dict):
    lower, upper = element["from"], element["to"]
    corners_at = lower, upper
    for side, face in element["faces"].items():
        vertices = []
        for corner in CORNERS[side]:
            vertex = tuple(corners_at[choice][axis] for axis, choice in enumerate(corner))
            vertex = element_rotation(vertex, element.get("rotation"))
            rotated = state_rotation(tuple(axis - 8 for axis in vertex), placement)
            vertices.append(tuple(round((axis + 8) / 16, 7) for axis in rotated))
        u0, v0, u1, v1 = face["uv"] if "uv" in face else face_uv(side, lower, upper)
        uv = [(u0, v0), (u0, v1), (u1, v1), (u1, v0)]
        shift = face.get("rotation", 0) // 90
        uv = uv[shift:] + uv[:shift]
        if placement.get("uvlock"):
            uv = [locked_uv(point, side, placement) for point in uv]
        cull_side = face.get("cullface")
        yield {
            "vertices": vertices, "uv": [(u / 16, 1 - v / 16) for u, v in uv],
            "texture": texture_reference(face["texture"], textures),
            "cull": DIRECTIONS[state_rotation(AXES[cull_side], placement)] if cull_side else None,
            "tint": face.get("tintindex"),
        }


@dataclass
class Model:
    elements: list[dict]
    textures: dict[str, str]
    chain: tuple[str, ...]


class ModelLibrary:
    def __init__(self, archives: Archives):
        self.archives = archives
        self.model = cache(self.model)
        self.bake = cache(self.bake)
        self.blockstate = cache(self.blockstate)
        self.texture = cache(self.texture)

    def model(self, reference: str, parents: tuple[str, ...] = ()) -> Model:
        namespace, name = location(reference)
        reference = f"{namespace}:{name}"
        if reference in parents:
            raise AssetError(f"Cyclic model parent reference: {reference}")
        data = json.loads(self.archives.read(reference, "models", "json"))
        check_loader(data, reference)
        if "parent" not in data:
            return Model(data.get("elements", []), data.get("textures", {}), (reference,))
        parent = self.model(data["parent"], parents + (reference,))
        return Model(data.get("elements", parent.elements), parent.textures | data.get("textures", {}),
                     (reference,) + parent.chain)

    def blockstate(self, block_id: str) -> dict:
        return json.loads(self.archives.read(block_id, "blockstates", "json"))

    def texture(self, reference: str) -> bytes:
        raw = self.archives.read(reference, "textures", "png")
        if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise AssetError(f"Invalid PNG texture: {reference}")
        return raw

    def bake(self, state: BlockState) -> dict:
        faces = []
        chains = []
        full_cube = False
        groups = model_groups(self.blockstate(state.block_id), state)
        for group in groups:
            # Use the first weighted entry so previews are reproducible.
            placement = group[0]
            model = self.model(placement["model"])
            if not model.elements:
                raise AssetError(f"{state.key}: no static geometry in {model.chain[0]}")
            for element in model.elements:
                faces.extend(bake_element(element, model.textures, placement))
            chains.append(model.chain)
            if len(groups) == len(model.elements) == 1:
                element = model.elements[0]
                full_cube = (element["from"] == [0, 0, 0] and element["to"] == [16, 16, 16]
                             and len(faces) == 6 and not element.get("rotation"))
        for face in faces:
            self.texture(face["texture"])
        return {"model_chains": chains, "weighted_choices": [len(group) for group in groups],
                "full_cube": full_cube, "faces": faces}

