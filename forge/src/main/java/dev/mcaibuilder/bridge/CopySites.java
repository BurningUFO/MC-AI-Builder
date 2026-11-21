package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.minecraft.ChatFormatting;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.Registries;
import net.minecraft.network.chat.ClickEvent;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.ResourceKey;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.Rotation;
import net.minecraft.world.phys.Vec3;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;

/** Player-owned site selection for a single isolated save. Called only on the server thread. */
final class CopySites {
    private static final long REVIEW_LIFETIME = Duration.ofMinutes(5).toMillis();

    private final MinecraftServer game;
    private final CopyProfile copy;
    private final Path journal;
    private Selection selection;
    private Review review;
    private Active active;

    private static final class Selection {
        final ServerLevel level;
        BlockPos first;
        BlockPos second;
        BlockPos spawn;
        BlockPos origin;
        Rotation rotation;

        Selection(ServerLevel level) {
            this.level = level;
        }

        Permit snapshot() {
            if (first == null || second == null || spawn == null || origin == null || rotation == null) {
                throw Wire.invalid("请标记 pos1、pos2、spawn 和 origin，再审阅施工区。");
            }
            return new Permit(UUID.randomUUID(), level.dimension(), box(first, second), spawn, origin, rotation);
        }
    }

    private record Permit(UUID id, ResourceKey<Level> dimension, BuildSite.Box area, BlockPos spawn,
                          BlockPos origin, Rotation rotation) {
        JsonObject json() {
            JsonObject data = new JsonObject();
            data.addProperty("authorization", id.toString());
            data.addProperty("dimension", dimension.location().toString());
            data.add("area", area.json());
            data.add("spawn", Wire.position(spawn));
            data.add("origin", Wire.position(origin));
            data.addProperty("rotation", rotation.ordinal() * 90);
            return data;
        }

        static Permit read(JsonObject data) {
            String name = Wire.string(data.get("dimension"), "dimension");
            ResourceLocation key = ResourceLocation.tryParse(name);
            int rotation = Wire.integer(data.get("rotation"), "rotation");
            if (key == null || !List.of(0, 90, 180, 270).contains(rotation)) {
                throw Wire.invalid("Invalid persisted site orientation or dimension");
            }
            return new Permit(BuildPlan.id(data, "authorization"), ResourceKey.create(Registries.DIMENSION, key),
                    BuildSite.Box.read(data.get("area"), "area"), Wire.position(data.get("spawn"), "spawn"),
                    Wire.position(data.get("origin"), "origin"), Rotation.values()[rotation / 90]);
        }
    }

    private record Review(Permit permit, String hash, long expires) {}
    private record Active(Permit permit, ConstructionService construction, ArchitectService architect) {}

    CopySites(MinecraftServer game, CopyProfile copy) throws IOException {
        this.game = game;
        this.copy = copy;
        journal = copy.world().resolve("data").resolve("mcaibuilder-site-authorization.json");
        if (Files.exists(journal)) {
            JsonObject saved = Wire.object(JsonParser.parseString(Files.readString(journal)), "site authorization");
            if (Wire.integer(saved.get("schema"), "schema") != 1
                    || !copy.id().equals(BuildPlan.id(saved, "copy_id"))
                    || !copy.owner().equals(BuildPlan.id(saved, "owner"))) {
                throw new IOException("Site authorization belongs to another copy or player");
            }
            if (saved.has("active")) {
                active = open(Permit.read(saved.getAsJsonObject("active")));
            }
        }
    }

    boolean owns(ServerPlayer player) {
        return copy.owns(game, player);
    }

    boolean playerOnline() {
        return owns(game.getPlayerList().getPlayer(copy.owner()));
    }

    ConstructionService construction() {
        return active == null ? null : active.construction();
    }

    ArchitectService architect() {
        return active == null ? null : active.architect();
    }

    JsonObject status() {
        JsonObject out = active == null ? new JsonObject() : active.permit().json();
        out.addProperty("mode", "world_copy");
        out.addProperty("approved", active != null);
        out.addProperty("warehouse_approved", active != null && active.construction().warehouseApproved());
        out.addProperty("chunks_loaded", active != null && active.construction().siteLoaded());
        return out;
    }

    void command(ServerPlayer player, String input) {
        if (!owns(player)) {
            throw new Wire.Problem(403, "site_forbidden", "需要由副本中的原存档玩家确认施工授权。");
        }
        String[] parts = input.strip().split("\\s+");
        switch (parts[0]) {
            case "", "status" -> show(player);
            case "reset" -> {
                selection = null;
                review = null;
                tell(player, "已清空本次标记。用 pos1 和 pos2 重新选择。");
            }
            case "review" -> review(player);
            case "confirm" -> {
                if (parts.length != 2) {
                    throw Wire.invalid("请使用施工区审阅中的批准按钮。");
                }
                confirm(player, parts[1]);
            }
            case "revoke" -> {
                persist(null);
                deactivate();
                selection = null;
                review = null;
                tell(player, "已撤销施工区和供料箱。原施工记录仍保留在副本内。");
            }
            case "pos1", "pos2", "spawn", "origin" -> mark(player, parts, parts[0]);
            default -> throw Wire.invalid("请用 /architect site status 查看施工区操作。");
        }
    }

    private void show(ServerPlayer player) {
        if (active == null) {
            tell(player, "尚无施工授权。用 /architect site pos1、pos2、spawn、origin 标记位置，再用 site review 审阅。");
            return;
        }
        tell(player, summary(active.permit()) + "。供料箱" + (active.construction().warehouseApproved() ? "已授权" : "待授权")
                + "，区块" + (active.construction().siteLoaded() ? "已加载" : "未加载") + "。");
    }

    private void mark(ServerPlayer player, String[] parts, String kind) {
        if (selection == null) {
            selection = new Selection(player.serverLevel());
        }
        if (selection.level != player.serverLevel()) {
            throw Wire.invalid("所有标记必须在同一维度。跨维度重选请先用 site reset。");
        }
        boolean facing = kind.equals("origin") && (parts.length == 2 || parts.length == 5);
        int positionLength = parts.length - (facing ? 1 : 0);
        BlockPos position = player.blockPosition();
        if (positionLength == 4) {
            try {
                position = new BlockPos(Integer.parseInt(parts[1]), Integer.parseInt(parts[2]), Integer.parseInt(parts[3]));
            } catch (NumberFormatException error) {
                throw Wire.invalid("请选择当前位置或输入三个整数坐标。");
            }
        } else if (positionLength != 1) {
            throw Wire.invalid("请选择当前位置或输入三个整数坐标。");
        }
        if (!selection.level.isInWorldBounds(position) || !selection.level.hasChunkAt(position)
                || player.position().distanceToSqr(Vec3.atCenterOf(position)) > 32 * 32) {
            throw Wire.invalid("标记需在同一维度已加载且距玩家不超过 32 格的位置。");
        }
        switch (kind) {
            case "pos1" -> selection.first = position;
            case "pos2" -> selection.second = position;
            case "spawn" -> selection.spawn = position;
            case "origin" -> {
                Direction direction = facing ? Direction.byName(parts[parts.length - 1].toLowerCase(Locale.ROOT))
                        : player.getDirection();
                if (direction == null || direction.getAxis().isVertical()) {
                    throw Wire.invalid("朝向请选择 north、east、south 或 west。");
                }
                selection.rotation = rotation(direction);
                selection.origin = position;
            }
            default -> throw new IllegalStateException("Unexpected marking verb");
        }
        review = null;
        tell(player, "已标记 " + kind + " " + position.toShortString() + "。");
    }

    private void review(ServerPlayer player) {
        if (selection == null) {
            throw Wire.invalid("请先标记本次施工区。");
        }
        Permit permit = selection.snapshot();
        validateLive(permit, player);
        JsonObject hashBody = permit.json();
        hashBody.addProperty("owner", copy.owner().toString());
        hashBody.addProperty("copy_id", copy.id().toString());
        review = new Review(permit, Wire.sha256(hashBody), System.currentTimeMillis() + REVIEW_LIFETIME);
        tell(player, "施工区审阅 " + review.hash().substring(0, 16) + "。 " + summary(permit) + "。");
        player.sendSystemMessage(Component.literal("[Architect] ").append(Component.literal("[批准施工区 " + permit.id() + "]")
                .withStyle(style -> style.withColor(ChatFormatting.GREEN).withUnderlined(true)
                        .withClickEvent(new ClickEvent(ClickEvent.Action.RUN_COMMAND,
                                "/architect site confirm " + permit.id())))));
    }

    private void confirm(ServerPlayer player, String id) {
        if (review == null || review.expires() <= System.currentTimeMillis()
                || !review.permit().id().toString().equals(id)) {
            throw Wire.conflict("stale_site_approval", "施工区确认已过期或标记已改变，请重新审阅。");
        }
        Permit permit = review.permit();
        validateLive(permit, player);
        Active next;
        try {
            next = open(permit);
        } catch (IOException error) {
            throw new Wire.Problem(503, "site_storage_unavailable", "无法打开副本施工记录。");
        }
        persist(permit);
        deactivate();
        active = next;
        ForgeBridge.LOGGER.info("copy_site_approved scope={} review={}", permit.id(), review.hash());
        review = null;
        selection = null;
        tell(player, "施工区已批准。请到供料箱旁执行 /architect supply，随后可以提出建筑需求。");
    }

    private BuildSite site(Permit permit) {
        if (!permit.area().contains(permit.spawn()) || !permit.area().contains(permit.spawn().above())
                || !permit.area().contains(permit.origin()) || permit.origin().equals(permit.spawn())
                || permit.origin().equals(permit.spawn().above()) || permit.area().halo().volume() > 24_000) {
            throw Wire.invalid("区域需容纳原点、NPC 脚下与头部，包含边缘最多 24,000 格。");
        }
        return new BuildSite(copy.id().toString(), copy.port(), copy.token(), permit.area(), permit.spawn(),
                copy.architect(), true, permit.id(), permit.origin(), permit.rotation());
    }

    private void validateLive(Permit permit, ServerPlayer player) {
        BuildSite site = site(permit);
        ServerLevel level = player.serverLevel();
        if (permit.dimension() != level.dimension() || !site.area().contains(player.blockPosition())) {
            throw Wire.conflict("site_player_outside", "请在待授权区域内确认。");
        }
        site.requireLoaded(level);
        if (!BuildTerrain.canStand(level, permit.spawn())) {
            throw Wire.conflict("spawn_blocked", "NPC 出生点需要有结实地面、两格通道且无液体。");
        }
    }

    private Active open(Permit permit) throws IOException {
        ServerLevel level = game.getLevel(permit.dimension());
        if (level == null) {
            throw new IOException("Approved dimension is unavailable in this copy");
        }
        BuildSite site = site(permit);
        var palette = BuildPlan.palette();
        ConstructionService construction = new ConstructionService(level, site, palette,
                new FakePlayersWorker(level, site, palette));
        return new Active(permit, construction, new ArchitectService(level, site, construction));
    }

    private void persist(Permit permit) {
        JsonObject state = new JsonObject();
        state.addProperty("schema", 1);
        state.addProperty("copy_id", copy.id().toString());
        state.addProperty("owner", copy.owner().toString());
        if (permit != null) {
            state.add("active", permit.json());
        }
        try {
            JobJournal.write(journal, state);
        } catch (IOException error) {
            throw new Wire.Problem(503, "site_storage_unavailable", "施工区授权未能保存。");
        }
    }

    void deactivate() {
        if (active != null) {
            active.construction().close();
            active = null;
        }
    }

    private static BuildSite.Box box(BlockPos first, BlockPos second) {
        return new BuildSite.Box(
                new BlockPos(Math.min(first.getX(), second.getX()), Math.min(first.getY(), second.getY()),
                        Math.min(first.getZ(), second.getZ())),
                new BlockPos(Math.max(first.getX(), second.getX()), Math.max(first.getY(), second.getY()),
                        Math.max(first.getZ(), second.getZ())));
    }

    private static Rotation rotation(Direction facing) {
        return switch (facing) {
            case NORTH -> Rotation.NONE;
            case EAST -> Rotation.CLOCKWISE_90;
            case SOUTH -> Rotation.CLOCKWISE_180;
            case WEST -> Rotation.COUNTERCLOCKWISE_90;
            default -> throw Wire.invalid("Expected horizontal facing");
        };
    }

    private static String summary(Permit permit) {
        return "维度 " + permit.dimension().location() + "，区域 " + permit.area().min().toShortString()
                + " 到 " + permit.area().max().toShortString() + "，NPC " + permit.spawn().toShortString()
                + "，原点 " + permit.origin().toShortString() + "，朝向 " + permit.rotation().rotate(Direction.NORTH).getName();
    }

    private static void tell(ServerPlayer player, String message) {
        player.sendSystemMessage(Component.literal("[Architect] MC Builder " + message));
    }
}
