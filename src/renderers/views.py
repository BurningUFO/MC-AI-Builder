"""Review cameras, Minecraft-coordinate sections and defaults for any blueprint."""

from math import dist, isfinite
import re
from typing import NamedTuple


class View(NamedTuple):
    image: str
    scene: str
    camera: str
    target: tuple[float, float, float]  # Minecraft coordinates
    offset: tuple[float, float, float]  # Blender camera offset from target
    scale: float
    lens_mm: float | None = None
    hide_water: bool = False
    clip_start: float = 0.1
    shift: tuple[float, float] = (0.0, 0.0)  # Frame offset in sensor widths, right/up.


class Preview(NamedTuple):
    scene_file: str
    views: tuple[View, ...]
    sections: tuple[tuple[int, str], ...]
    interior_lights: tuple[tuple[str, tuple[float, float, float], float, float, float], ...]
    water_level: float | None = None

    @property
    def full_scene(self) -> str:
        return self.views[0].scene

    @property
    def files(self) -> tuple[str, ...]:
        return (self.scene_file, *(view.image for view in self.views), VERIFICATION_FILE)


VERIFICATION_FILE = "verification.json"


class Box(NamedTuple):
    low: tuple = (None, None, None)
    high: tuple = (None, None, None)

    def contains(self, position):
        return all((lo is None or lo <= v) and (hi is None or v <= hi)
                   for v, lo, hi in zip(position, self.low, self.high, strict=True))


def matches_part(part, branches):
    return any(part == branch or part.startswith(branch + "/") for branch in branches)


def section_blocks(blocks, boxes, groups=None, *, include=(), exclude=()):
    """Clip whole source blocks. Surfaces at the cut are meshed from the retained blocks."""
    groups = groups or {}
    return {
        pos: block for pos, block in blocks.items()
        if any(box.contains(pos) for box in boxes)
        and (not include or matches_part(groups.get(pos, "Structure"), include))
        and not matches_part(groups.get(pos, "Structure"), exclude)
    }


def legacy_section(building, scene):
    from src.generators.medieval import DEPTH, EAVES, STAIR_X, UPPER_FLOOR, WIDTH

    if scene == "Ground floor cutaway":
        boxes = (
            Box((None, 0, None), (None, 0, None)),
            Box((0, None, 1), (WIDTH - 2, UPPER_FLOOR - 1, DEPTH - 1)),
            Box((min(STAIR_X), UPPER_FLOOR, 6), (max(STAIR_X), UPPER_FLOOR, 6)),
            Box((min(STAIR_X), UPPER_FLOOR, 7), (14, UPPER_FLOOR, 7)),
        )
    else:
        boxes = (Box((0, UPPER_FLOOR, 1), (WIDTH - 2, EAVES - 1, DEPTH - 1)),)
    selected = section_blocks(building.blocks, boxes)
    return {pos: block for pos, block in selected.items()
            if dict(block.state.properties).get("hanging") != "true"
            or (pos[0], pos[1] + 1, pos[2]) in selected}


def growing_tower(scene, filename):
    return Preview(
        filename,
        (
            View("01-perspective.png", scene, "Approach · northwest", (8.5, 11.4, 8.0), (-22, 39, 20), 36.4),
            View("02-rear.png", scene, "Service stair · southeast", (8.5, 11.2, 8.5), (28, -34, 18), 36.4),
            View("03-growing-floor.png", "Growing floor", "Beds and stairs", (8.5, 8.0, 8.2), (-8, 14, 32), 21.4),
            View("04-aisle.png", scene, "Ground floor aisle", (8.7, 2.9, 7.8), (-0.8, 4.5, 1.1), 0, 18.5),
        ),
        ((0, "Foundation and lower plots"), (7, "Middle growing floor"), (13, "Upper growing floor"), (19, "Glazed roof")),
        (),
    )


PREVIEWS = {
    "grain_tower": growing_tower("Grain Tower", "grain-tower.blend"),
    "crystal_tower": growing_tower("Crystal Tower", "crystal-tower.blend"),
    "ember_gate": Preview(
        "ember-gate.blend",
        (
            View("01-perspective.png", "Ember Gate", "Approach · northwest", (9.5, 6.2, 7.1), (-17, 40, 16), 24.4),
            View("02-elevation.png", "Ember Gate", "North elevation", (9.5, 7.2, 7.5), (0, 36, 0), 22.4),
            View("03-rear.png", "Ember Gate", "Rear · southeast", (9.5, 5.9, 7.9), (27, -38, 19), 25.0),
            View("04-plan.png", "Ember Gate", "Site plan", (9.5, 1.0, 7.5), (0, 0, 36), 22.4),
        ),
        ((0, "Terraces and steps"), (2, "Piers and corbeled arch"), (10, "Cornice and crown")),
        (),
    ),
    "medieval_house": Preview(
        "medieval-house.blend",
        (
            View("01-front.png", "Residence", "Front · northwest", (8, 7.2, 5.5), (-24, 31, 24), 30.8),
            View("02-rear.png", "Residence", "Rear · southeast", (8, 7.2, 5.8), (28, -29, 24), 30.8),
            View("03-ground-floor.png", "Ground floor cutaway", "Ground floor", (8, 1.9, 5.6), (19, 25, 27), 23.4),
            View("04-upper-floor.png", "Upper floor cutaway", "Upper floor", (8, 6.7, 5.6), (19, 25, 27), 23.4),
        ),
        ((0, "Foundation"), (1, "Ground storey"), (5, "Upper storey"), (9, "Roof and gables")),
        (
            ("Ground floor soft light", (7.8, 4.78, 6.0), 1.0, 750, 6.4),
            ("Upper floor soft light", (7.8, 8.85, 6.0), 6.0, 620, 6.0),
        ),
    ),
    "enchanting_library": Preview(
        "enchanting-library.blend",
        (
            View("01-front.png", "Enchanting library", "Front · northwest", (8, 4.8, 8), (-25, 32, 29), 36.5),
            View("02-rear.png", "Enchanting library", "Rear · southeast", (8, 4.5, 8), (26, -31, 22), 36.5),
            View("03-book-roof.png", "Enchanting library", "Open-book roof", (8, 8.35, 8), (-12, 11, 34), 25),
            View("04-interior.png", "Enchanting library", "Inside the library", (7.55, 2.25, 8.4), (1.15, 6.4, 0.9), 0, 22),
        ),
        ((0, "Foundation"), (1, "Timber hall"), (6, "Roof deck and open book")),
        (
            ("Reading room soft light", (7.8, 5.72, 7.8), 1.0, 1000, 7.4),
            ("Ceiling bounce light", (7.8, 2.8, 7.8), 5.5, 300, 5.0),
        ),
    ),
    "warehouse_bridge": Preview(
        "warehouse-bridge.blend",
        (
            View("01-overview.png", "Warehouse bridge", "Overview · west bank", (4.5, 2.3, 12.5), (-31, 25, 21), 33.2),
            View("02-approach.png", "Warehouse bridge", "Approach · axial", (4.5, 4.9, 12.5), (0, 22, 4.0), 0, 25),
            View("03-arches.png", "Warehouse bridge", "Side · three arches", (4.5, 2.6, 12.5), (-118, 0, 13.2), 29.4),
            View("04-deck-plan.png", "Warehouse bridge", "Deck plan", (4.5, 3.0, 12.5), (0.001, 0, 31), 33.2),
        ),
        ((-3, "Piers and vaults"), (3, "Seven-block walkway"), (4, "Stone railings and lights")),
        (),
        water_level=-0.025,
    ),
    "warehouse_flat_bridge": Preview(
        "warehouse-flat-bridge.blend",
        (
            View("01-overview.png", "Low flat bridge", "Overview · water level", (4.5, 1.0, 12.5), (-26, 25, 14.5), 29.8),
            View("02-approach.png", "Low flat bridge", "Approach · axial", (4.5, 0.8, 12.5), (0, 21, 4.8), 0, 25),
            View("03-supports.png", "Low flat bridge", "Side · submerged supports", (4.5, -0.7, 12.5), (-35, 0, 0), 28.6,
                 hide_water=True),
            View("04-deck-plan.png", "Low flat bridge", "Deck plan", (4.5, 1.0, 12.5), (0.001, 0, 31), 33.2),
        ),
        ((-4, "Submerged piers"), (0, "Single-layer deck"), (1, "Stone railings and lamps")),
        (),
        water_level=0.0,
    ),
}



def preview_for(sample, building, *, name=None):
    """Keep curated cameras and frame arbitrary projects from their actual voxel bounds."""
    if sample in PREVIEWS:
        return PREVIEWS[sample]
    low, high = building.bounds()
    target = tuple((lo + hi + 1) / 2 for lo, hi in zip(low, high))
    scale = 1.29 * dist(low, tuple(value + 1 for value in high))
    distance = max(6, scale)
    views = tuple(View(image, "Building", camera, target,
                       tuple(axis * distance for axis in offset), scale)
                  for image, camera, offset in (
                      ("01-front.png", "Front northwest", (-0.9, 1.1, 0.75)),
                      ("02-rear.png", "Rear southeast", (1, -1.1, 0.68)),
                      ("03-side.png", "Side northeast", (1.2, 0.82, 0.55)),
                      ("04-top.png", "Roof plan", (0, 0, 1.6)),
                  ))
    return Preview(f"{name or sample}.blend", views, ((low[1], "Structure"),), ())


FULL_SCENE = "Building"
VIEW_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}")


def point(value, label, *, bounds=False):
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError(f"{label} needs three coordinates in Minecraft x, y, z order.")
    for item in value:
        valid = (item is None or type(item) is int) if bounds else (
            type(item) in (int, float) and isfinite(item))
        if not valid:
            raise ValueError(f"Invalid coordinate in {label}: {item!r}")
    return tuple(value)


def auto_views(building):
    low, high = building.bounds()
    w, h, d = (b - a + 1 for a, b in zip(low, high))
    target = [(a + b + 1) / 2 for a, b in zip(low, high)]
    radius = max(6.0, dist(low, tuple(n + 1 for n in high)))
    return {
        name: {"target": target, "position": [p + radius * v for p, v in zip(target, direction)],
               "scale": 1.18 * span}
        for name, direction, span in (
            ("overview", (-0.9, 0.72, -1.14), radius),
            ("front", (0, 0, -1), max(w, 1.2 * h)),
            ("rear", (0, 0, 1), max(w, 1.2 * h)),
            ("west", (-1, 0, 0), max(d, 1.2 * h)),
            ("east", (1, 0, 0), max(d, 1.2 * h)),
            ("top", (0, 1, 0), max(w, 1.2 * d)),
        )
    }


def view_catalog(settings, building):
    auto = auto_views(building)
    source = settings.get("views", auto)
    sections = settings.get("sections", {})
    if not isinstance(source, dict) or not source or not isinstance(sections, dict):
        raise ValueError("views.json needs a nonempty views object and an optional sections object.")
    if any(not isinstance(value, dict) for value in source.values()):
        raise ValueError("Every camera in views.json must be an object.")
    if all("section" in value for value in source.values()):
        source = {"overview": auto["overview"]} | source
        if "section" in source["overview"]:
            source = {"uncut": auto["overview"]} | source
            if "section" in source["uncut"]:
                raise ValueError("Include a camera without a section in views.json.")
    for name, spec in sections.items():
        if not isinstance(name, str) or not name.strip() or len(name.encode()) > 45 or name == FULL_SCENE:
            raise ValueError(f"Invalid section name: {name!r}")
        if not isinstance(spec, dict) or set(spec) - {"min", "max", "include", "exclude"}:
            raise ValueError(f"Section {name!r} accepts min, max, include and exclude.")
        box = section_box(spec)
        if any(a is not None and b is not None and a > b for a, b in zip(*box)):
            raise ValueError(f"Inverted limits in section {name}.")
        for key in ("include", "exclude"):
            if key in spec and (not isinstance(spec[key], list)
                                or any(not isinstance(p, str) or not p for p in spec[key])):
                raise ValueError(f"Section {name} {key} must list collection paths.")
    views = {}
    for key, row in source.items():
        if not VIEW_ID.fullmatch(key):
            raise ValueError(f"Camera ID must use lowercase ASCII letters, digits, _ or -: {key}")
        if set(row) - {"position", "target", "scale", "lens", "section", "clip_start", "shift"}:
            raise ValueError(f"Unknown camera setting in {key}.")
        target = point(row["target"], f"{key} target")
        position = point(row["position"], f"{key} position")
        x, y, z = (p - t for p, t in zip(position, target))
        if position == target:
            raise ValueError(f"Camera {key} has the same position and target.")
        if "scale" in row and "lens" in row:
            raise ValueError(f"Choose either scale or lens for camera {key}.")
        scale = row.get("scale", auto["overview"]["scale"])
        lens = row.get("lens")
        near = row.get("clip_start", 0.05)
        shift = row.get("shift", (0.0, 0.0))
        if (not isinstance(shift, (list, tuple)) or len(shift) != 2
                or any(type(n) not in (int, float) or not isfinite(n) for n in shift)):
            raise ValueError(f"Camera {key} shift needs two finite numbers (right, up).")
        if (any(type(n) not in (int, float) or not isfinite(n) or n <= 0 for n in (scale, near))
                or lens is not None and (type(lens) not in (int, float) or not 1 <= lens <= 5000)):
            raise ValueError(f"Invalid scale, lens or near clip in camera {key}.")
        scene = row.get("section", FULL_SCENE)
        if not isinstance(scene, str) or scene != FULL_SCENE and scene not in sections:
            raise ValueError(f"Camera {key} refers to unknown section {scene!r}.")
        views[key] = View(f"{key}.png", scene, key, target, (x, -z, y), scale, lens,
                          clip_start=near, shift=tuple(shift))
    # Put the full scene first so a saved file opens on a complete building.
    return dict(sorted(views.items(), key=lambda item: item[1].scene != FULL_SCENE)), sections


def section_box(spec):
    return Box(point(spec.get("min", (None, None, None)), "section min", bounds=True),
               point(spec.get("max", (None, None, None)), "section max", bounds=True))
