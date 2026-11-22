package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.Util;
import net.minecraft.core.BlockPos;
import net.minecraft.util.Mth;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.phys.Vec3;
import net.minecraftforge.fml.loading.FMLPaths;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.time.Duration;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.atomic.AtomicReference;

/** Server authorization and a single in-process handoff to the saved owner's integrated client. */
final class NpcObservations implements AutoCloseable {
    private static final long TIMEOUT = Duration.ofSeconds(5).toNanos();
    private static final int MAX_PAIR_TICKS = 24;
    private static final long MAX_PAIR_AGE = Duration.ofSeconds(60).toNanos();
    private static final double MAX_DRIFT = 0.20;
    private static final long INTERVAL = Duration.ofMillis(350).toNanos();
    private static final AtomicReference<Request> QUEUED = new AtomicReference<>();

    record Request(MinecraftServer game, UUID id, UUID owner, UUID npc, int entityId, ResourceLocation dimension,
                   long gameTick, Vec3 position, float yaw, float pitch, WorldObservation.Window window,
                   long started, CompletableFuture<Frame> result) {
        boolean expired() {
            return System.nanoTime() - started >= TIMEOUT;
        }

        void fail(String reason) {
            result.complete(new Frame(reason, null, null, null));
        }
    }

    record Frame(String error, JsonObject view, byte[] png, byte[] original) {}

    private record Binding(ConstructionService site, Request request, JsonObject job, String fingerprint) {}

    private final MinecraftServer game;
    private final Path directory;
    private Request active;
    private Binding binding;
    private Binding published;
    private long publishedAt;
    private CompletableFuture<JsonObject> saving;
    private long lastRequest;
    private JsonObject latest = state("idle");

    private NpcObservations(MinecraftServer game, Path directory) {
        this.game = game;
        this.directory = directory;
    }

    static NpcObservations open(MinecraftServer game) {
        if (game.isDedicatedServer()) {
            return null;
        }
        Path instance = FMLPaths.GAMEDIR.get().toAbsolutePath().normalize();
        Path tests = instance.getParent();
        if (tests == null) {
            return null;
        }
        boolean copy = CopyProfile.requested();
        if (!copy && !Boolean.getBoolean("mcaibuilder.localTest")) {
            return null;
        }
        if (copy && tests.getFileName().toString().equals("world-copies")) {
            tests = tests.getParent();
        }
        Path marker = instance.resolve(copy ? CopyProfile.MARKER : ".mcaibuilder-test");
        try {
            if (tests == null || !tests.getFileName().toString().equals("mc-test")
                    || !Files.isRegularFile(marker, LinkOption.NOFOLLOW_LINKS)
                    || !instance.equals(instance.toRealPath())) {
                return null;
            }
        } catch (IOException error) {
            return null;
        }
        return new NpcObservations(game, tests.getParent().resolve(".cache").resolve("observations")
                .resolve(instance.getFileName()));
    }

    static Request next() {
        return QUEUED.getAndSet(null);
    }

    static JsonObject state(String status) {
        JsonObject data = new JsonObject();
        data.addProperty("capture_status", status);
        return data;
    }

    JsonObject latest() {
        return latest.deepCopy();
    }

    UUID request(ServerPlayer player, ConstructionService site) {
        if (!game.isSingleplayerOwner(player.getGameProfile())) {
            throw new Wire.Problem(403, "observation_forbidden", "仅本机隔离世界的原玩家可请求 NPC 相机。");
        }
        long now = System.nanoTime();
        if (active != null || lastRequest != 0 && now - lastRequest < INTERVAL) {
            throw Wire.conflict("observation_busy", "上一轮观察正在结束，请稍后再试。");
        }
        LivingEntity target = site.observationTarget();
        var window = new WorldObservation.Window(target.blockPosition(), site.observationArea().halo());
        var sample = window.read(target.level());
        Request request = new Request(game, UUID.randomUUID(), player.getUUID(), target.getUUID(), target.getId(),
                target.level().dimension().location(), target.level().getGameTime(), target.position(),
                target.getViewYRot(1), target.getViewXRot(1), window, now, new CompletableFuture<>());
        if (!QUEUED.compareAndSet(null, request)) {
            throw Wire.conflict("observation_busy", "客户端还有一轮观察等待处理。");
        }
        lastRequest = now;
        binding = new Binding(site, request, site.observationJob(), sample.fingerprint());
        published = null;
        active = request;
        latest = result(request, "pending");
        return request.id();
    }

    void tick(ConstructionService site, ArchitectService architect) {
        if (active == null) {
            return;
        }
        ServerPlayer player = game.getPlayerList().getPlayer(active.owner());
        if (site != binding.site() || architect == null || !architect.permitted(player)) {
            fail("observation_scope_changed");
            return;
        }
        if (saving != null) {
            if (saving.isDone()) {
                finish(saving.getNow(null));
            }
            return;
        }
        if (!active.result().isDone()) {
            if (active.expired()) {
                fail("camera_timeout");
            }
            return;
        }
        Frame frame = active.result().getNow(null);
        String error = frame.error();
        if (error != null) {
            fail(error);
            return;
        }
        LivingEntity target;
        try {
            target = site.observationTarget();
            if (!active.npc().equals(target.getUUID()) || !active.dimension().equals(target.level().dimension().location())) {
                fail("observation_target_changed");
                return;
            }
        } catch (Wire.Problem failure) {
            fail(failure.code);
            return;
        }
        JsonObject data = result(active, "captured");
        frame.view().entrySet().forEach(entry -> data.add(entry.getKey(), entry.getValue()));
        long serverTick = player.serverLevel().getGameTime();
        data.addProperty("server_result_tick", serverTick);
        pair(data, target, serverTick);
        saving = CompletableFuture.supplyAsync(() -> {
            try {
                return ObservationFiles.save(directory, data, frame);
            } catch (IOException | RuntimeException failure) {
                ForgeBridge.LOGGER.warn("Local NPC observation could not be saved", failure);
                JsonObject failed = data.deepCopy();
                failed.addProperty("capture_status", "failed");
                failed.addProperty("capture_error", "observation_storage");
                failed.remove("image_path");
                failed.remove("image_sha256");
                failed.remove("world");
                return failed;
            }
        }, Util.ioPool());
    }

    private void pair(JsonObject data, LivingEntity target, long serverTick) {
        long frameTick = data.get("game_tick").getAsLong();
        var sample = active.window().read(target.level());
        String fingerprint = sample.fingerprint();
        JsonArray feet = data.getAsJsonArray("position");
        Vec3 framePosition = new Vec3(feet.get(0).getAsDouble(), feet.get(1).getAsDouble(), feet.get(2).getAsDouble());
        if (frameTick < active.gameTick() - 2 || frameTick > serverTick + 2
                || serverTick - active.gameTick() > MAX_PAIR_TICKS || active.expired()) {
            data.addProperty("world_error", "tick_skew");
        } else if (!poseMatches(active, target) || framePosition.distanceTo(active.position()) > MAX_DRIFT) {
            data.addProperty("world_error", "pose_changed");
        } else if (!fingerprint.equals(binding.fingerprint()) || !fingerprint.equals(data.get("world_fingerprint").getAsString())
                || !binding.job().equals(binding.site().observationJob())) {
            data.addProperty("world_error", "world_changed");
        } else {
            try {
                var world = sample.describe(binding.site().observationArea(), active.window().origin());
                world.addProperty("observation_id", active.id().toString());
                world.addProperty("dimension", active.dimension().toString());
                world.addProperty("game_tick", serverTick);
                world.addProperty("frame_tick_delta", serverTick - frameTick);
                world.addProperty("sample_span_ticks", serverTick - active.gameTick());
                world.addProperty("sample_span_ms", (System.nanoTime() - active.started()) / 1_000_000.0);
                world.addProperty("fingerprint", fingerprint);
                world.add("sample_position", position(target.position()));
                world.add("anchor", Wire.position(active.window().origin()));
                world.add("job", binding.job().deepCopy());
                data.add("world", world);
            } catch (RuntimeException error) {
                data.addProperty("world_error", "unsupported_block_shape");
                ForgeBridge.LOGGER.warn("Could not describe the bounded NPC observation", error);
            }
        }
    }

    JsonObject observeWorld(ConstructionService site, ArchitectService architect) {
        if (published == null) {
            throw Wire.conflict("world_observation_required", "请重新观测，当前没有已对齐的图像与场地样本。");
        }
        Request request = published.request();
        ServerPlayer player = game.getPlayerList().getPlayer(request.owner());
        if (site == null || site != published.site() || architect == null || !architect.permitted(player)
                || System.nanoTime() - publishedAt >= MAX_PAIR_AGE) {
            throw stale();
        }
        LivingEntity target = site.observationTarget();
        if (!poseMatches(request, target) || !published.job().equals(site.observationJob())
                || !request.window().read(target.level()).fingerprint().equals(published.fingerprint())) {
            throw stale();
        }
        return latest.deepCopy();
    }

    private static Wire.Problem stale() {
        return Wire.conflict("world_observation_stale", "NPC、场地或观测时间已变化，请重新观测。");
    }

    private static boolean poseMatches(Request request, LivingEntity target) {
        return request.npc().equals(target.getUUID()) && request.dimension().equals(target.level().dimension().location())
                && target.position().distanceTo(request.position()) <= MAX_DRIFT
                && Math.abs(Mth.wrapDegrees(target.getViewYRot(1) - request.yaw())) < 3.0
                && Math.abs(target.getViewXRot(1) - request.pitch()) < 3.0;
    }

    private void fail(String reason) {
        active.fail(reason);
        JsonObject data = result(active, "failed");
        data.addProperty("capture_error", reason);
        finish(data);
    }

    private void finish(JsonObject data) {
        ServerPlayer player = game.getPlayerList().getPlayer(active.owner());
        if (player != null) {
            String message = data.has("image_path") ? "观测已保存 " + active.id()
                    : "观测未完成 [" + data.get("capture_error").getAsString() + "]";
            player.sendSystemMessage(Component.literal("[Architect] " + message));
        }
        latest = data;
        QUEUED.compareAndSet(active, null);
        published = data.has("world") ? binding : null;
        publishedAt = active.started();
        active = null;
        binding = null;
        saving = null;
    }

    @Override
    public void close() {
        if (active != null) {
            active.fail("observation_disconnected");
            QUEUED.compareAndSet(active, null);
        }
    }

    private static JsonObject result(Request request, String status) {
        JsonObject data = state(status);
        data.addProperty("observation_id", request.id().toString());
        data.addProperty("npc_uuid", request.npc().toString());
        data.addProperty("dimension", request.dimension().toString());
        data.addProperty("server_request_tick", request.gameTick());
        data.add("server_request_position", position(request.position()));
        data.addProperty("server_request_yaw", request.yaw());
        data.addProperty("server_request_pitch", request.pitch());
        return data;
    }

    static JsonArray position(Vec3 pos) {
        JsonArray values = new JsonArray(3);
        values.add(pos.x);
        values.add(pos.y);
        values.add(pos.z);
        return values;
    }
}
