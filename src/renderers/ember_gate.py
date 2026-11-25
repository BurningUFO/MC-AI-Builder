"""Lighting and the lit portal surface for the Ember Gate preview."""

import bpy

from src.generators.ember_gate import PORTAL_X, PORTAL_Y, PORTAL_Z
from src.renderers.blender_preview import blender_space, collection_for, light


def portal_material():
    material = bpy.data.materials.new("Portal · violet currents")
    material.diffuse_color = (0.36, 0.055, 0.86, 1)
    tree = material.node_tree
    nodes, links = tree.nodes, tree.links
    nodes.clear()

    uv = nodes.new("ShaderNodeTexCoord")
    uv.location = (-1180, 140)
    center = nodes.new("ShaderNodeVectorMath")
    center.operation = "SUBTRACT"
    center.inputs[1].default_value = (0.5, 0.5, 0)
    center.location = (-970, 140)
    links.new(uv.outputs["UV"], center.inputs[0])
    stretch = nodes.new("ShaderNodeVectorMath")
    stretch.operation = "MULTIPLY"
    stretch.inputs[1].default_value = (1, len(PORTAL_Y) / len(PORTAL_X), 1)
    stretch.location = (-760, 140)
    links.new(center.outputs["Vector"], stretch.inputs[0])

    radius = nodes.new("ShaderNodeVectorMath")
    radius.operation = "LENGTH"
    radius.location = (-550, -80)
    links.new(stretch.outputs["Vector"], radius.inputs[0])
    twist = nodes.new("ShaderNodeMath")
    twist.operation = "MULTIPLY"
    twist.inputs[1].default_value = -7.8
    twist.location = (-350, -80)
    links.new(radius.outputs["Value"], twist.inputs[0])
    swirl = nodes.new("ShaderNodeVectorRotate")
    swirl.rotation_type = "Z_AXIS"
    swirl.location = (-160, 140)
    links.new(stretch.outputs["Vector"], swirl.inputs["Vector"])
    links.new(twist.outputs["Value"], swirl.inputs["Angle"])

    wave = nodes.new("ShaderNodeTexWave")
    wave.bands_direction = "DIAGONAL"
    wave.inputs["Scale"].default_value = 1.45
    wave.inputs["Distortion"].default_value = 5.6
    wave.inputs["Detail"].default_value = 3.0
    wave.inputs["Detail Scale"].default_value = 1.4
    wave.location = (50, 300)
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 4.6
    noise.inputs["Detail"].default_value = 2.0
    noise.location = (50, -40)
    for node in (wave, noise):
        links.new(swirl.outputs["Vector"], node.inputs["Vector"])

    blend = nodes.new("ShaderNodeMixRGB")
    blend.inputs[0].default_value = 0.39
    blend.location = (270, 180)
    links.new(wave.outputs["Fac"], blend.inputs[1])
    links.new(noise.outputs["Fac"], blend.inputs[2])

    spectrum = nodes.new("ShaderNodeValToRGB")
    spectrum.location = (480, 180)
    colors = spectrum.color_ramp
    colors.elements[0].color = (0.008, 0.001, 0.033, 1)
    colors.elements[1].color = (0.98, 0.39, 1.0, 1)
    colors.elements.new(0.38).color = (0.033, 0.003, 0.16, 1)
    colors.elements.new(0.62).color = (0.13, 0.012, 0.52, 1)
    colors.elements.new(0.81).color = (0.58, 0.046, 0.98, 1)
    links.new(blend.outputs["Color"], spectrum.inputs["Fac"])

    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Strength"].default_value = 1.9
    emission.location = (760, 180)
    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (960, 180)
    links.new(spectrum.outputs["Color"], emission.inputs["Color"])
    links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return material


def add_active_portal(collection):
    left, right = PORTAL_X.start, PORTAL_X.stop
    sill, lintel = PORTAL_Y.start, PORTAL_Y.stop
    center = PORTAL_Z + 0.5
    corners = ((left, sill, center), (left, lintel, center), (right, lintel, center), (right, sill, center))
    mesh = bpy.data.meshes.new(f"{len(PORTAL_X)} × {len(PORTAL_Y)} portal aperture")
    mesh.from_pydata([blender_space(point) for point in corners], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new()
    for uv_loop, xy in zip(uv.data, ((0, 0), (0, 1), (1, 1), (1, 0))):
        uv_loop.uv = xy
    mesh.materials.append(portal_material())
    portal = bpy.data.objects.new("Active portal", mesh)
    collection.objects.link(portal)


def add_glow(scene):
    tree = bpy.data.node_groups.new("Soft bloom", "CompositorNodeTree")
    scene.compositing_node_group = tree
    tree.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    source = tree.nodes.new("CompositorNodeRLayers")
    source.location = (-260, 120)
    glare = tree.nodes.new("CompositorNodeGlare")
    glare.inputs["Type"].default_value = "Fog Glow"
    glare.inputs["Threshold"].default_value = 0.85
    glare.inputs["Strength"].default_value = 0.3
    glare.inputs["Size"].default_value = 0.30
    output = tree.nodes.new("NodeGroupOutput")
    output.location = (260, 120)
    tree.links.new(source.outputs["Image"], glare.inputs["Image"])
    tree.links.new(glare.outputs["Image"], output.inputs["Image"])


def stage_ember_gate(scene, materials):
    stage = scene.collection.children["Studio"]
    matte = stage.objects["Matte background"].data.materials[0]
    matte.diffuse_color = (0.029, 0.025, 0.043, 1)
    floor = matte.node_tree.nodes["Principled BSDF"]
    floor.inputs["Base Color"].default_value = matte.diffuse_color
    floor.inputs["Roughness"].default_value = 0.56
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.60

    sun = stage.objects["Warm daylight"].data
    sun.name = "Cool key"
    sun.energy = 1.0
    sun.color = (0.81, 0.86, 1.0)
    stage.objects["Soft skylight"].data.energy = 3100
    center_x = (PORTAL_X.start + PORTAL_X.stop) / 2
    center_y = -(PORTAL_Z + 0.5)
    for direction in (-1, 1):
        glow = light(stage, "Soft warm fill", "AREA",
                     (center_x, center_y + 16 * direction, 12), 2600,
                     (1.0, 0.81, 0.68), (center_x, center_y, 5.2))
        glow.shape = "DISK"
        glow.size = 17

    lamp = materials["minecraft:block/shroomlight"][0].node_tree
    shader = lamp.nodes["Principled BSDF"]
    lamp.links.new(lamp.nodes["Image Texture"].outputs["Color"], shader.inputs["Emission Color"])
    shader.inputs["Emission Strength"].default_value = 2.1

    active = collection_for(scene, "Activated portal · preview")
    add_active_portal(active)
    for direction in (-1, 1):
        spill = light(active, "Violet light on terrace", "AREA",
                      (center_x, center_y + 0.55 * direction, 4.6), 125,
                      (0.51, 0.12, 1.0), (center_x, center_y + 4 * direction, 2.0))
        spill.shape = "DISK"
        spill.size = 4
    add_glow(scene)
