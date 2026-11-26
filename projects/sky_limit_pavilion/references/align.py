"""Recalculate the two fixed cameras from source pixels. Optional NumPy/SciPy.

Run directly to print the views and checks. No model or render enters the fit.
Coordinates are local Minecraft blocks; image coordinates are native pixels.
"""
import json
from itertools import product
from math import cos, sin

import numpy as np
from scipy.optimize import least_squares


def cropped_camera(eye, target, focal_px, principal, crop):
    x, y, width, height = crop
    return {
        "position": np.round(eye, 5).tolist(),
        "target": np.round(target, 5).tolist(),
        "lens": round(36 * focal_px / width, 6),
        "shift": [round((x + width / 2 - principal[0]) / width, 8),
                  round((principal[1] - y - height / 2) / width, 8)],
    }


def cover_camera(inward_bearing):
    # The four shaft axes at the lower orbit, in east, south, west, north order.
    # The tower axis is at u=764. A level view leaves the horizon row measurable.
    offsets = np.array([(40, 0), (0, 40), (-40, 0), (0, -40)])
    shaft_u = np.array([1182, 366, 572, 943])

    def shaft_residual(parameters):
        azimuth, distance, focal = parameters
        along = offsets @ [sin(azimuth), cos(azimuth)]
        across = offsets @ [cos(azimuth), -sin(azimuth)]
        return 764 + focal * across / (distance - along) - shaft_u

    fit = least_squares(shaft_residual, [np.pi / 4, 75, 680])
    angle, distance, focal = fit.x
    # Near and far inlays of the radius-46 ring fix eye height and horizon.
    def magnification(radius):
        return focal / (distance - radius)

    eye_height = (599 - 506) / (magnification(46) - magnification(-46))
    horizon = 599 - eye_height * magnification(46)
    tower = np.array([-39.5, eye_height + 0.5, 0.5])
    eye = tower + distance * np.array([sin(angle), 0, cos(angle)])
    # The cover fit uses orbital axes. The construction rings locate the tower
    # relative to the pavilion's own block axes, up to its fourfold symmetry.
    turn = inward_bearing - np.pi
    basis = np.array([[cos(turn), 0, -sin(turn)], [0, 1, 0], [sin(turn), 0, cos(turn)]])
    origin = np.array([0.5, 0, 0.5])
    eye, tower = (origin + basis @ (p - origin) for p in (eye, tower))
    print("cover shaft horizontal residuals (px)", np.round(fit.fun, 1))
    print("held-out inner rim near row (observed about 565)",
          round(horizon + eye_height * magnification(35), 1))
    return cropped_camera(eye, tower, focal, (764, horizon), (916, 367, 528, 440))


def construction_camera(focal_px=240):
    # Four exposed lanterns in sheet 2 R7C10, one at each end of a two-block arm.
    # A fixed focal length and a roll-free camera resolve the planar ambiguity
    # by assumption. The source camera and its absolute elevation are unknown.
    lamps = np.array([(2.5, 4.5, 0.5), (0.5, 4.5, 2.5),
                      (0.5, 4.5, -1.5), (-1.5, 4.5, 0.5)])
    pixels = np.array([(227, 142), (268, 142), (224, 171), (274, 172)])

    def basis(yaw, pitch):
        return np.array([[-cos(yaw), 0, sin(yaw)],
                         [-sin(yaw)*sin(pitch), cos(pitch), -cos(yaw)*sin(pitch)],
                         [sin(yaw)*cos(pitch), sin(pitch), cos(yaw)*cos(pitch)]])

    def project(points, pose):
        camera = (points - pose[:3]) @ basis(*pose[3:]).T
        return [240, 135] + [focal_px, -focal_px] * camera[:, :2] / camera[:, 2:]

    fit = least_squares(lambda pose: (project(lamps, pose) - pixels).ravel(),
                        [-9, 13, -9, np.pi/4, -0.55])
    eye = fit.x[:3]
    target = eye + 16 * basis(*fit.x[3:])[2]
    corners = lamps[-1] + np.array(list(product([-.5, .5], repeat=3)))
    print("construction lamp center residuals (px)", np.round(fit.fun.reshape(-1, 2), 1))
    print("front right lamp width/height (native px)", np.round(np.ptp(project(corners, fit.x), axis=0), 1))
    # Pixel centers on the two surrounding rings fix the vertical datum. Their
    # radii and the 40-block center distance come from the earlier tower study.
    outer = [(169, 221), (187, 168), (217, 116), (265, 80)]
    inner = [(320, 230), (310, 180), (313, 114), (344, 84)]
    rotation = basis(*fit.x[3:])

    def ring_errors(params, outer_pixels, inner_pixels):
        bearing, rise = params
        center = 0.5 + 40 * np.array([cos(bearing), sin(bearing)])
        errors = []
        for radius, uv in ((46, outer_pixels), (35, inner_pixels)):
            xy = (np.array(uv) - [240, 135]) / [focal_px, -focal_px]
            rays = np.column_stack((xy, np.ones(len(xy)))) @ rotation
            depth = (1 - eye[1] - rise) / rays[:, 1]
            hits = eye[[0, 2]] + depth[:, None] * rays[:, [0, 2]]
            errors.extend(np.linalg.norm(hits - center, axis=1) - radius)
        return np.array(errors)

    rings = least_squares(ring_errors, [3*np.pi/4, 2], args=(outer, inner))
    bearing, rise = rings.x
    held_out = ring_errors(rings.x, [(178, 195), (238, 96)], [(307, 160), (326, 98)])
    print("inward bearing (degrees), lamp height above datum", np.degrees(bearing), 4.5 + rise)
    print("held-out ring radial offsets (blocks)", np.round(held_out, 2))
    adjustment = np.array([0, rise, 0])
    return cropped_camera(eye + adjustment, target + adjustment, focal_px,
                          (240, 135), (96, 20, 288, 240)), bearing


if __name__ == "__main__":
    canopy, bearing = construction_camera()
    print(json.dumps({"cover": cover_camera(bearing), "canopy": canopy}, indent=2))
