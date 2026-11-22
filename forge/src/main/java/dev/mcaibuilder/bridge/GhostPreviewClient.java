package dev.mcaibuilder.bridge;

import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.client.renderer.LevelRenderer;
import net.minecraft.client.renderer.RenderType;
import net.minecraft.world.level.EmptyBlockGetter;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;
import net.minecraftforge.api.distmarker.Dist;
import net.minecraftforge.client.event.ClientPlayerNetworkEvent;
import net.minecraftforge.client.event.RenderLevelStageEvent;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;

import java.util.ArrayList;
import java.util.List;

/** The preview is render-only. Minecraft continues to use its real client chunk states. */
@Mod.EventBusSubscriber(modid = ForgeBridge.MOD_ID, value = Dist.CLIENT)
final class GhostPreviewClient {
    private record Mesh(GhostPreview.Cell cell, List<AABB> boxes, int color) {}

    private static ClientLevel world;
    private static List<Mesh> meshes = List.of();

    private GhostPreviewClient() {}

    static void show(GhostPreview.Projection projection) {
        ClientLevel level = Minecraft.getInstance().level;
        if (level == null || !level.dimension().location().equals(projection.dimension()) || projection.cells().isEmpty()) {
            clear();
            return;
        }
        world = level;
        var next = new ArrayList<Mesh>();
        for (var cell : projection.cells()) {
            var shape = cell.state().getShape(EmptyBlockGetter.INSTANCE, cell.pos());
            var boxes = shape.isEmpty() ? List.of(new AABB(0, 0, 0, 1, 1, 1)) : shape.toAabbs();
            int color = cell.removal() ? 0xff7858 : cell.state().getMapColor(level, cell.pos()).col;
            next.add(new Mesh(cell, boxes, color == 0 ? 0x76dcff : color));
        }
        meshes = List.copyOf(next);
    }

    static int size() {
        return meshes.size();
    }

    private static void clear() {
        world = null;
        meshes = List.of();
    }

    @SubscribeEvent
    public static void logout(ClientPlayerNetworkEvent.LoggingOut event) {
        clear();
    }

    @SubscribeEvent
    public static void tick(TickEvent.ClientTickEvent event) {
        if (event.phase == TickEvent.Phase.END && world != null && world != Minecraft.getInstance().level) {
            clear();
        }
    }

    @SubscribeEvent
    public static void render(RenderLevelStageEvent event) {
        if (meshes.isEmpty() || event.getStage() != RenderLevelStageEvent.Stage.AFTER_TRANSLUCENT_BLOCKS
                || Minecraft.getInstance().level != world) {
            return;
        }
        Vec3 eye = event.getCamera().getPosition();
        var stack = event.getPoseStack();
        var buffers = Minecraft.getInstance().renderBuffers().bufferSource();
        var layer = RenderType.debugFilledBox();
        var vertices = buffers.getBuffer(layer);
        stack.pushPose();
        stack.translate(-eye.x, -eye.y, -eye.z);
        for (Mesh mesh : meshes) {
            var pos = mesh.cell().pos();
            if (eye.distanceToSqr(Vec3.atCenterOf(pos)) > 64 * 64 || !world.hasChunkAt(pos)) {
                continue;
            }
            int color = mesh.color();
            float red = 0.25F + ((color >> 16) & 255) / 510F;
            float green = 0.35F + ((color >> 8) & 255) / 510F;
            float blue = 0.45F + (color & 255) / 510F;
            for (AABB box : mesh.boxes()) {
                var bounds = box.move(pos).deflate(0.015);
                LevelRenderer.addChainedFilledBoxVertices(stack, vertices,
                        bounds.minX, bounds.minY, bounds.minZ, bounds.maxX, bounds.maxY, bounds.maxZ,
                        red, green, blue, 0.28F);
            }
        }
        stack.popPose();
        buffers.endBatch(layer);
    }
}
