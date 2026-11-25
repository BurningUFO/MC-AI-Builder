"""Rasterize decorative planar curves on the Minecraft block grid."""

from itertools import pairwise
from math import ceil


def cubic_cells(start, control_a, control_b, end):
    """Yield a cubic Bezier stroke with consecutive cells sharing a face.

    Pass four (u, v) control points in block coordinates. The caller maps the
    returned cells to a wall, floor or height field and chooses block states.
    """
    points = (start, control_a, control_b, end)
    longest_step = max(abs(a - b) for first, second in pairwise(points)
                       for a, b in zip(first, second))
    samples = max(1, ceil(6 * longest_step))
    previous = tuple(map(round, start))
    yield previous

    for sample in range(1, samples + 1):
        t = sample / samples
        s = 1 - t
        weights = (s ** 3, 3 * s * s * t, 3 * s * t * t, t ** 3)
        u, v = (sum(weight * coordinate for weight, coordinate in zip(weights, axis))
                for axis in zip(*points))
        cell = round(u), round(v)
        if cell == previous:
            continue
        if cell[0] != previous[0] and cell[1] != previous[1]:
            # At a grid corner, use the intermediate cell closer to the curve.
            side_u = cell[0], previous[1]
            side_v = previous[0], cell[1]
            yield min((side_u, side_v), key=lambda side: (side[0] - u) ** 2 + (side[1] - v) ** 2)
        yield cell
        previous = cell
