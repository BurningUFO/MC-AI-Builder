"""Discretize a roof surface sampled at half-block heights."""


NEIGHBORS = {"east": (1, 0), "west": (-1, 0), "south": (0, 1), "north": (0, -1)}


def roof_courses(heights, *, connect_steps=False):
    """Yield (position, form, state) for a roof skin and its seam backing.

    A height gives the top of a column in half blocks above world Y=0.
    The caller chooses materials for block, slab, stairs and backing cells.
    Connect steps with overlapping courses on thin freestanding surfaces.
    """
    for (x, z), top in heights.items():
        pos = x, (top - 1) // 2, z
        neighbors = {facing: heights.get((x + dx, z + dz), top) for facing, (dx, dz) in NEIGHBORS.items()}
        lowest = min(top, *neighbors.values())
        backing_y = (lowest - int(connect_steps)) // 2
        for y in range(backing_y, pos[1]):
            yield (x, y, z), "backing", {}
        if top % 2:
            yield pos, "slab", {"type": "bottom", "waterlogged": "false"}
            continue
        facing = max(neighbors, key=neighbors.get)
        if neighbors[facing] > top > lowest:
            yield pos, "stairs", {"facing": facing, "shape": "straight", "half": "bottom", "waterlogged": "false"}
        else:
            yield pos, "block", {}
