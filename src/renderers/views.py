"""Review cameras and architectural sections for the included buildings."""

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
PREVIEWS = {
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

