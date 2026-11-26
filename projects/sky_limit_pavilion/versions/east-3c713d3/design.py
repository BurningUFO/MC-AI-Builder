"""Extract the east pavilion in the 3c713d3 blueprint, in local coordinates."""
from pathlib import Path

import src
from src.core.building import Building
from src.design import digest, read_json


def build():
    source = Path(src.__file__).resolve().parents[1] / "projects" / "sky_limit_replica"
    blueprint = source / "building.json"
    expected = "a8b89aecb02c172c11e349ac7198609e9a0ea80664ade5aa96d107470854d07e"
    if digest(blueprint) != expected:
        raise ValueError("This baseline requires the sky_limit_replica blueprint from 3c713d3.")
    group_file = source / "design-groups.json"
    if digest(group_file) != "f329886f436972206f155acffa979ede2b6c6e913e0f3547e5ebb68ca196e626":
        raise ValueError("This baseline requires the collection assignments from 3c713d3.")
    tower = Building.read(blueprint)
    prefix = "03 Satellite pavilions/(40, 0)/"
    pavilion = Building("天空即为极限 · 东花亭")
    parts = {}
    for x, y, z, part in read_json(group_file)["groups"]:
        if part.startswith(prefix):
            p = x - 40, y - 45, z
            pavilion.blocks[p] = tower.blocks[x, y, z]
            leaf = part.removeprefix(prefix)
            parts[p] = "Lantern/" + leaf if leaf.startswith("Beacon") else leaf
    return pavilion, parts
