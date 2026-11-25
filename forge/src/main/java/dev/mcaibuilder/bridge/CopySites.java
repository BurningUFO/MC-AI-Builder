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
import java.util.ArrayList;
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
        String autonomy = "manual";
        int minutes = 480;
        final List<BuildSite.Box> protectedAreas = new ArrayList<>();

        Selection(ServerLevel level) {
            this.level = level;
        }

        Permit snapshot() {
            if (first == null || second == null || spawn == null || origin == null || rotation == null) {
                throw Wire.invalid("请标记 pos1、pos2、spawn 和 origin，再审阅施工区。");
            }
            long expiry = autonomy.equals("manual") ? 0 : System.currentTimeMillis() + Duration.ofMinutes(minutes).toMillis();
            return new Permit(UUID.randomUUID(), level.dimension(), box(first, second), spawn, origin, rotation,
                    new WorksitePermit(autonomy, expiry, protectedAreas));
        }
    }

    private record Permit(UUID id, ResourceKey<Level> dimension, BuildSite.Box area, BlockPos spawn,
                          BlockPos origin, Rotation rotation, WorksitePermit worksite) {
        boolean sameScope(Permit other) {
            return dimension.equals(other.dimension) && area.equals(other.area) && spawn.equals(other.spawn)
                    && origin.equals(other.origin) && rotation == other.rotation && worksite.mode().equals(other.worksite.mode())
                    && worksite.protectedAreas().equals(other.worksite.protectedAreas());
        }

        JsonObject json() {
            JsonObject data = new JsonObject();
            data.addProperty("authorization", id.toString());
            data.addProperty("dimension", dimension.location().toString());
            data.add("area", area.json());
            data.add("spawn", Wire.position(spawn));
            data.add("origin", Wire.position(origin));
            data.addProperty("rotation", rotation.ordinal() * 90);
            data.add("worksite", worksite.json());
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
                    Wire.position(data.get("origin"), "origin"), Rotation.values()[rotation / 90],
                    WorksitePermit.read(data.getAsJsonObject("worksite")));
        }
    }

    private record Review(Permit permit, long expires) {}
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
        if (active != null) {
            out.add("worksite", active.construction().worksiteStatus());
        }
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
            case "area", "look" -> selectArea(player, parts);
            case "autonomy" -> autonomy(player, parts);
            case "protect" -> protect(player, parts);
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

    boolean naturalArea(ServerPlayer player, String text) {
        var size = java.util.regex.Pattern.compile("^(以我为中心|围绕我|以视线为中心)\\s*(\\d+)\\s*[xX×*]\\s*(\\d+)\\s*(?:格)?[。！!]?$")
                .matcher(text.strip());
        if (!size.matches()) {
            return false;
        }
        selectArea(player, new String[]{size.group(1).contains("视线") ? "look" : "area", size.group(2) + "x" + size.group(3)});
        return true;
    }

    private void selectArea(ServerPlayer player, String[] command) {
        var size = java.util.regex.Pattern.compile("(\\d+)[xX×](\\d+)").matcher(command.length > 1 ? command[1] : "30x30");
        if (!size.matches() || command.length > 3) {
            throw Wire.invalid("使用 /architect site area 30x30 或 /architect site look 30x30，可在最后加上高度。");
        }
        ServerLevel level = player.serverLevel();
        BlockPos playerPos = player.blockPosition();
        var hit = level.clip(new net.minecraft.world.level.ClipContext(player.getEyePosition(),
                player.getEyePosition().add(player.getLookAngle().scale(24)),
                net.minecraft.world.level.ClipContext.Block.OUTLINE, net.minecraft.world.level.ClipContext.Fluid.NONE, player));
        BlockPos looked = hit.getType() == net.minecraft.world.phys.HitResult.Type.BLOCK
                ? hit.getBlockPos().above() : playerPos.relative(player.getDirection(), 4);
        BlockPos center = command[0].equals("look") ? looked : playerPos;
        int width, depth, height;
        try {
            width = Integer.parseInt(size.group(1));
            depth = Integer.parseInt(size.group(2));
            height = command.length == 3 ? Integer.parseInt(command[2]) : 32;
            if (width < 5 || depth < 5 || height < 4 || width > 60_000_000 || depth > 60_000_000
                    || height > level.getHeight()) {
                throw new NumberFormatException();
            }
        } catch (NumberFormatException invalid) {
            throw Wire.invalid("施工区宽深至少 5 格，高度至少 4 格，并需容纳在当前维度。");
        }
        int minY = Math.max(level.getMinBuildHeight(), Math.min(center.getY() - Math.min(6, height - 2), level.getMaxBuildHeight() - height));
        BlockPos first = new BlockPos(center.getX() - width / 2, minY, center.getZ() - depth / 2);
        BlockPos second = first.offset(width - 1, height - 1, depth - 1);
        BuildSite.Box area = new BuildSite.Box(first, second);
        if (!area.contains(playerPos)) {
            throw Wire.invalid("请走进要标记的施工区，或使用以你为中心的区域。");
        }
        Selection draft = editable(player);
        draft.first = first;
        draft.second = second;
        draft.origin = area.contains(looked) ? looked : center;
        draft.rotation = rotation(player.getDirection());
        Direction back = player.getDirection().getOpposite();
        BlockPos nearEdge = center.relative(back, Math.min(8, (back.getAxis() == Direction.Axis.X ? width : depth) / 2 - 1));
        draft.spawn = null;
        for (BlockPos option : BlockPos.withinManhattan(nearEdge, 4, 4, 4)) {
            if (area.contains(option) && area.contains(option.above()) && !option.equals(draft.origin)
                    && !option.above().equals(draft.origin) && BuildTerrain.canStand(level, option)) {
                draft.spawn = option.immutable();
                break;
            }
        }
        if (draft.spawn == null) {
            throw Wire.invalid("我没找到可站立的出发点。可用 /architect site spawn 标记一个位置。");
        }
        review = null;
        review(player);
    }

    private void show(ServerPlayer player) {
        if (active == null) {
            tell(player, "尚无施工授权。用 /architect site pos1、pos2、spawn、origin 标记位置，再用 site review 审阅。");
            return;
        }
        tell(player, summary(active.permit()) + "。供料箱" + (active.construction().warehouseApproved() ? "已授权" : "待授权")
                + "，区块" + (active.construction().siteLoaded() ? "已加载" : "未加载")
                + "。自主建设 " + active.construction().worksiteStatus().get("state").getAsString() + "。");
    }

    private Selection editable(ServerPlayer player) {
        if (selection == null) {
            selection = new Selection(player.serverLevel());
            if (active != null && active.permit().dimension().equals(player.serverLevel().dimension())) {
                Permit permit = active.permit();
                selection.first = permit.area().min();
                selection.second = permit.area().max();
                selection.spawn = permit.spawn();
                selection.origin = permit.origin();
                selection.rotation = permit.rotation();
                selection.protectedAreas.addAll(permit.worksite().protectedAreas());
            }
        }
        if (selection.level != player.serverLevel()) {
            throw Wire.invalid("编辑施工区时需在同一维度。");
        }
        return selection;
    }

    private void autonomy(ServerPlayer player, String[] parts) {
        if (parts.length == 2 && parts[1].equals("revoke")) {
            if (active == null) {
                throw Wire.conflict("site_not_approved", "当前没有施工区。");
            }
            active.architect().requirePlayer(player);
            active.construction().revokeAutonomy();
            tell(player, "已撤销自主建设许可。");
            return;
        }
        if (parts.length < 2 || parts.length > 3 || !WorksitePermit.MODES.contains(parts[1])) {
            throw Wire.invalid("使用 /architect site autonomy auto、manual 或 revoke。");
        }
        int minutes;
        try {
            minutes = parts.length == 3 ? Integer.parseInt(parts[2]) : 480;
        } catch (NumberFormatException error) {
            throw Wire.invalid("许可时长应为 1–480 分钟。");
        }
        if (minutes < 1 || minutes > 480) {
            throw Wire.invalid("许可时长应为 1–480 分钟。");
        }
        Selection draft = editable(player);
        draft.autonomy = parts[1];
        draft.minutes = minutes;
        review = null;
        tell(player, "已标记自主权限 " + draft.autonomy + "。请用 /architect site review 审阅。");
    }

    private void protect(ServerPlayer player, String[] parts) {
        Selection draft = editable(player);
        if (parts.length == 2 && parts[1].equals("reset")) {
            draft.protectedAreas.clear();
        } else {
            if ((parts.length != 4 && parts.length != 7) || draft.protectedAreas.size() >= 8) {
                throw Wire.invalid("用 site protect x y z 或两组三维坐标，最多八处。");
            }
            try {
                BlockPos first = position(parts, 1);
                BlockPos last = parts.length == 7 ? position(parts, 4) : first;
                draft.protectedAreas.add(box(first, last));
            } catch (NumberFormatException error) {
                throw Wire.invalid("保护范围需要方块整数坐标。");
            }
        }
        review = null;
        tell(player, "已标记 " + draft.protectedAreas.size() + " 处保护区。请审阅施工区。");
    }

    private static BlockPos position(String[] parts, int offset) {
        return new BlockPos(Integer.parseInt(parts[offset]), Integer.parseInt(parts[offset + 1]),
                Integer.parseInt(parts[offset + 2]));
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
        tell(player, "批准后，Builder 可在区域内整理普通地形并施工。箱子、红石、床、传送门和标记保护区会保留。");
        if (!permit.worksite().mode().equals("manual")) {
            tell(player, "自主建设许可允许 Builder 在此区域自行清障、挖掘普通木石、取料和建造。"
                    + "箱子、机器和标记保护区需要你决定。时长 " + selection.minutes + " 分钟，保留 " + permit.worksite().protectedAreas().size()
                    + " 处保护区。");
        }
        review = new Review(permit, System.currentTimeMillis() + REVIEW_LIFETIME);
        tell(player, summary(permit) + "。");
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
        if (active != null && !permit.worksite().mode().equals("manual") && permit.sameScope(active.permit())) {
            Permit renewed = new Permit(active.permit().id(), permit.dimension(), permit.area(), permit.spawn(),
                    permit.origin(), permit.rotation(), permit.worksite());
            persist(renewed);
            active.construction().renewAutonomy(renewed.worksite());
            active = new Active(renewed, active.construction(), active.architect());
            review = null;
            selection = null;
            tell(player, "施工区已批准续期。当前工程与供料授权已保留。");
            return;
        }
        Active next;
        try {
            next = open(permit);
        } catch (IOException error) {
            throw new Wire.Problem(503, "site_storage_unavailable", "无法打开副本施工记录。");
        }
        persist(permit);
        deactivate();
        active = next;
        ForgeBridge.LOGGER.info("copy_site_approved scope={}", permit.id());
        review = null;
        selection = null;
        tell(player, "施工区已批准。可以用 @Builder 提出需求，准备好附近箱子的材料后说「材料齐了」。");
    }

    private BuildSite site(Permit permit) {
        if (!permit.area().contains(permit.spawn()) || !permit.area().contains(permit.spawn().above())
                || !permit.area().contains(permit.origin()) || permit.origin().equals(permit.spawn())
                || permit.origin().equals(permit.spawn().above())) {
            throw Wire.invalid("区域需容纳原点、NPC 脚下与头部。");
        }
        if (permit.worksite().protectedAreas().stream().anyMatch(protectedArea ->
                !permit.area().contains(protectedArea.min()) || !permit.area().contains(protectedArea.max())
                        || protectedArea.contains(permit.spawn()) || protectedArea.contains(permit.spawn().above()))) {
            throw Wire.invalid("保护区需在施工区内，且应留出 NPC 出发位置。");
        }
        return new BuildSite(copy.id().toString(), copy.port(), copy.token(), permit.area(), permit.spawn(),
                copy.architect(), true, permit.id(), permit.origin(), permit.rotation(), permit.worksite());
    }

    private void validateLive(Permit permit, ServerPlayer player) {
        BuildSite site = site(permit);
        ServerLevel level = player.serverLevel();
        if (permit.dimension() != level.dimension() || !site.area().contains(player.blockPosition())) {
            throw Wire.conflict("site_player_outside", "请在待授权区域内确认。");
        }
        if (!permit.worksite().mode().equals("manual") && !permit.worksite().active(System.currentTimeMillis())) {
            throw Wire.conflict("worksite_permission_expired", "本次自主许可已超时，请重新审阅。");
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
            active.architect().stopSurvey();
            active.architect().closePreviews();
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
