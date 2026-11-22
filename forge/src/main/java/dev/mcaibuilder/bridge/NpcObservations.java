package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.Util;
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
    private static final long INTERVAL = Duration.ofMillis(350).toNanos();
    private static final AtomicReference<Request> QUEUED = new AtomicReference<>();

    record Request(MinecraftServer game, UUID id, UUID owner, UUID npc, int entityId, ResourceLocation dimension,
                   long gameTick, Vec3 position, float yaw, float pitch, long started, CompletableFuture<Frame> result) {
        boolean expired() {
            return System.nanoTime() - started >= TIMEOUT;
        }

        void fail(String reason) {
            result.complete(new Frame(reason, null, null, null));
        }
    }

    record Frame(String error, JsonObject view, byte[] png, byte[] original) {}

    private final MinecraftServer game;
    private final Path directory;
    private Request active;
    private ConstructionService authorizedSite;
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
        Request request = new Request(game, UUID.randomUUID(), player.getUUID(), target.getUUID(), target.getId(),
                target.level().dimension().location(), target.level().getGameTime(), target.position(),
                target.getViewYRot(1), target.getViewXRot(1), now, new CompletableFuture<>());
        if (!QUEUED.compareAndSet(null, request)) {
            throw Wire.conflict("observation_busy", "客户端还有一轮观察等待处理。");
        }
        lastRequest = now;
        authorizedSite = site;
        active = request;
        latest = result(request, "pending");
        return request.id();
    }

    void tick(ConstructionService site, ArchitectService architect) {
        if (active == null) {
            return;
        }
        ServerPlayer player = game.getPlayerList().getPlayer(active.owner());
        if (site != authorizedSite || architect == null || !architect.permitted(player)) {
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
        try {
            LivingEntity target = site.observationTarget();
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
        data.addProperty("server_result_tick", player.serverLevel().getGameTime());
        saving = CompletableFuture.supplyAsync(() -> {
            try {
                return ObservationFiles.save(directory, data, frame);
            } catch (IOException | RuntimeException failure) {
                ForgeBridge.LOGGER.warn("Local NPC observation could not be saved", failure);
                JsonObject failed = data.deepCopy();
                failed.addProperty("capture_status", "failed");
                failed.addProperty("capture_error", "observation_storage");
                return failed;
            }
        }, Util.ioPool());
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
        active = null;
        authorizedSite = null;
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
