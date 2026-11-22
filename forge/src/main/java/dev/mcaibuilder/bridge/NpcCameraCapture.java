package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;
import com.mojang.blaze3d.platform.NativeImage;
import com.mojang.blaze3d.systems.RenderSystem;
import dev.duzo.players.entities.FakePlayerEntity;
import net.minecraft.Util;
import net.minecraft.client.CameraType;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Screenshot;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.util.Mth;
import net.minecraft.world.entity.Entity;
import net.minecraftforge.api.distmarker.Dist;
import net.minecraftforge.client.event.ClientPlayerNetworkEvent;
import net.minecraftforge.client.event.RenderLevelStageEvent;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;
import org.lwjgl.opengl.GL11;

import javax.imageio.ImageIO;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.time.Instant;
import java.util.concurrent.TimeUnit;

/** A one-frame readback from the tracked Fake Player's actual camera on the render thread. */
@Mod.EventBusSubscriber(modid = ForgeBridge.MOD_ID, value = Dist.CLIENT)
final class NpcCameraCapture {
    private static final int MAX_WIDTH = 768;
    private static final int MAX_HEIGHT = 432;
    private static final double AIM_TOLERANCE = 2.0;
    private static final long MAX_PIXELS = 16_000_000;
    private static NpcObservations.Request waiting;
    private static Capture capture;
    private static long frame;
    private static long previousFrame;

    private static final class Capture {
        final NpcObservations.Request request;
        final ClientLevel level;
        final Entity previousEntity;
        final CameraType previousType;
        final FakePlayerEntity target;
        final long switchFrame;
        NativeImage pixels;
        String failure;
        JsonObject view;

        Capture(Minecraft client, NpcObservations.Request request, FakePlayerEntity target) {
            this.request = request;
            this.target = target;
            level = client.level;
            previousEntity = client.getCameraEntity();
            previousType = client.options.getCameraType();
            switchFrame = frame;
        }
    }

    private NpcCameraCapture() {}

    @SubscribeEvent
    public static void tick(TickEvent.ClientTickEvent event) {
        if (event.phase != TickEvent.Phase.END) {
            return;
        }
        Minecraft client = Minecraft.getInstance();
        if (capture != null) {
            String reason = invalid(client, capture);
            if (reason != null) {
                abort(client, reason);
            }
            return;
        }
        if (waiting == null) {
            waiting = NpcObservations.next();
        }
        if (waiting == null) {
            return;
        }
        if (waiting.result().isDone() || waiting.expired()) {
            waiting.fail("camera_timeout");
            waiting = null;
            return;
        }
        if (client.level == null || client.player == null || client.screen != null) {
            return;
        }
        NpcObservations.Request request = waiting;
        waiting = null;
        if (!sessionMatches(client, request)) {
            request.fail("camera_session_changed");
            return;
        }
        Entity entity = client.level.getEntity(request.entityId());
        if (!(entity instanceof FakePlayerEntity target) || !request.npc().equals(target.getUUID())
                || !target.isAlive() || !client.level.hasChunkAt(target.blockPosition())) {
            request.fail("npc_not_visible");
            return;
        }
        capture = new Capture(client, request, target);
        try {
            client.options.setCameraType(CameraType.FIRST_PERSON);
            client.setCameraEntity(target);
        } catch (RuntimeException error) {
            abort(client, "camera_switch_failed");
        }
    }

    private static boolean sessionMatches(Minecraft client, NpcObservations.Request request) {
        return client.level != null && client.player != null && client.getSingleplayerServer() == request.game()
                && request.owner().equals(client.player.getUUID())
                && request.dimension().equals(client.level.dimension().location());
    }

    private static String invalid(Minecraft client, Capture current) {
        if (current.request.result().isDone() || current.request.expired()) {
            return "camera_timeout";
        }
        if (!sessionMatches(client, current.request) || client.level != current.level || client.screen != null) {
            return "camera_session_changed";
        }
        if (!current.target.isAlive() || client.level.getEntity(current.target.getId()) != current.target
                || !client.level.hasChunkAt(current.target.blockPosition())) {
            return "npc_not_visible";
        }
        return client.getCameraEntity() == current.target && client.options.getCameraType().isFirstPerson()
                ? null : "camera_interrupted";
    }

    @SubscribeEvent
    public static void worldRendered(RenderLevelStageEvent event) {
        if (event.getStage() != RenderLevelStageEvent.Stage.AFTER_LEVEL) {
            return;
        }
        long now = System.nanoTime();
        long interval = previousFrame == 0 ? 0 : now - previousFrame;
        previousFrame = now;
        frame++;
        Capture current = capture;
        if (current == null || current.pixels != null || current.failure != null || frame - current.switchFrame < 2) {
            return;
        }
        Minecraft client = Minecraft.getInstance();
        String reason = invalid(client, current);
        if (reason != null) {
            current.failure = reason;
            return;
        }
        var camera = event.getCamera();
        float delta = event.getPartialTick();
        var feet = current.target.getPosition(delta);
        var eye = camera.getPosition();
        if (!camera.isInitialized() || camera.isDetached() || camera.getEntity() != current.target
                || eye.distanceToSqr(current.target.getEyePosition(delta)) > 0.035 * 0.035
                || Math.abs(Mth.wrapDegrees(camera.getYRot() - current.target.getViewYRot(delta))) > 0.1
                || Math.abs(camera.getXRot() - current.target.getViewXRot(delta)) > 0.1
                || !current.request.planning() && (Math.abs(Mth.wrapDegrees(camera.getYRot() - current.request.yaw())) > AIM_TOLERANCE
                || Math.abs(camera.getXRot() - current.request.pitch()) > AIM_TOLERANCE)) {
            return;
        }
        double fov = Math.toDegrees(2 * Math.atan(1.0 / event.getProjectionMatrix().m11()));
        if (!Double.isFinite(fov) || fov <= 0 || fov >= 180) {
            current.failure = "camera_projection_invalid";
            return;
        }
        var target = client.getMainRenderTarget();
        if (!RenderSystem.isOnRenderThread() || target.width < 2 || target.height < 2
                || (long) target.width * target.height > MAX_PIXELS) {
            current.failure = "camera_framebuffer_unavailable";
            return;
        }
        var worldSample = current.request.window().read(client.level);
        long start = System.nanoTime();
        int boundTexture = GL11.glGetInteger(GL11.GL_TEXTURE_BINDING_2D);
        try {
            current.pixels = Screenshot.takeScreenshot(target);
        } catch (RuntimeException error) {
            current.failure = "camera_readback_failed";
            return;
        } finally {
            RenderSystem.bindTexture(boundTexture);
        }
        JsonObject view = new JsonObject();
        view.add("position", NpcObservations.position(feet));
        view.add("eye_position", NpcObservations.position(eye));
        view.addProperty("yaw", camera.getYRot());
        view.addProperty("pitch", camera.getXRot());
        view.addProperty("fov", fov);
        view.addProperty("game_tick", client.level.getGameTime());
        view.addProperty("world_fingerprint", worldSample.fingerprint());
        view.addProperty("client_unloaded_cells", worldSample.unloadedCells());
        view.addProperty("capture_time", Instant.now().toString());
        view.addProperty("camera_source", "npc_first_person");
        view.addProperty("render_frame", frame);
        view.addProperty("rendered_frames_after_switch", frame - current.switchFrame);
        view.addProperty("fps", client.getFps());
        view.addProperty("frame_interval_ms", milliseconds(interval));
        view.addProperty("readback_ms", milliseconds(System.nanoTime() - start));
        view.addProperty("capture_ms", milliseconds(System.nanoTime() - current.request.started()));
        current.view = view;
    }

    @SubscribeEvent
    public static void renderEnd(TickEvent.RenderTickEvent event) {
        if (event.phase != TickEvent.Phase.END || capture == null
                || capture.pixels == null && capture.failure == null) {
            return;
        }
        Minecraft client = Minecraft.getInstance();
        Capture completed = capture;
        if (completed.failure != null) {
            abort(client, completed.failure);
            return;
        }
        boolean restored = restore(client);
        if (!restored) {
            completed.pixels.close();
            completed.request.fail("camera_restore_failed");
            return;
        }
        completed.view.addProperty("camera_restored", true);
        Util.ioPool().execute(() -> encode(completed));
    }

    @SubscribeEvent
    public static void logout(ClientPlayerNetworkEvent.LoggingOut event) {
        if (capture != null) {
            abort(Minecraft.getInstance(), "observation_disconnected");
        }
        if (waiting != null) {
            waiting.fail("observation_disconnected");
            waiting = null;
        }
    }

    private static void abort(Minecraft client, String reason) {
        Capture aborted = capture;
        if (aborted.pixels != null) {
            aborted.pixels.close();
        }
        aborted.request.fail(restore(client) ? reason : "camera_restore_failed");
    }

    private static boolean restore(Minecraft client) {
        Capture current = capture;
        capture = null;
        Entity previous = current.previousEntity;
        if (previous == null || client.level != current.level || previous.isRemoved()) {
            previous = client.player;
        }
        try {
            client.options.setCameraType(current.previousType);
            client.setCameraEntity(previous);
            return client.getCameraEntity() == previous && client.options.getCameraType() == current.previousType;
        } catch (RuntimeException error) {
            ForgeBridge.LOGGER.warn("NPC observation could not restore the saved camera", error);
            return false;
        }
    }

    private static void encode(Capture capture) {
        long start = System.nanoTime();
        try (NativeImage raw = capture.pixels) {
            int sourceWidth = raw.getWidth();
            int sourceHeight = raw.getHeight();
            double scale = Math.min(1, Math.min((double) MAX_WIDTH / sourceWidth, (double) MAX_HEIGHT / sourceHeight));
            int width = Math.max(1, (int) Math.round(sourceWidth * scale));
            int height = Math.max(1, (int) Math.round(sourceHeight * scale));
            try (NativeImage reduced = new NativeImage(width, height, false)) {
                raw.resizeSubRectTo(0, 0, sourceWidth, sourceHeight, reduced);
                capture.view.addProperty("width", width);
                capture.view.addProperty("height", height);
                capture.view.addProperty("source_width", sourceWidth);
                capture.view.addProperty("source_height", sourceHeight);
                byte[] original = Boolean.getBoolean("mcaibuilder.cameraSmoke") ? png(raw) : null;
                byte[] png = png(reduced);
                capture.view.addProperty("encode_ms", milliseconds(System.nanoTime() - start));
                capture.request.result().complete(new NpcObservations.Frame(null, capture.view, png, original));
            }
        } catch (IOException | RuntimeException error) {
            capture.request.fail("camera_encode_failed");
        }
    }

    private static byte[] png(NativeImage image) throws IOException {
        int width = image.getWidth();
        int height = image.getHeight();
        BufferedImage rgb = new BufferedImage(width, height, BufferedImage.TYPE_INT_RGB);
        int[] row = new int[width];
        for (int y = 0; y < height; y++) {
            for (int x = 0; x < width; x++) {
                int abgr = image.getPixelRGBA(x, y);
                row[x] = (abgr & 0xff) << 16 | (abgr & 0xff00) | (abgr >>> 16 & 0xff);
            }
            rgb.setRGB(0, y, width, 1, row, 0, width);
        }
        try (ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            if (!ImageIO.write(rgb, "png", out)) {
                throw new IOException("The Java runtime has no PNG writer");
            }
            return out.toByteArray();
        }
    }

    private static double milliseconds(long nanos) {
        return nanos / (double) TimeUnit.MILLISECONDS.toNanos(1);
    }
}
