package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.network.chat.ClickEvent;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.MutableComponent;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraftforge.common.util.FakePlayer;
import net.minecraftforge.fml.loading.FMLPaths;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.HashMap;
import java.util.Map;
import java.util.Objects;
import java.util.TreeMap;
import java.util.UUID;

/** All methods run on the Minecraft server thread. The model runtime only polls and stages. */
final class ArchitectService {
    private static final long REQUEST_LIFETIME = Duration.ofMinutes(3).toMillis();
    private static final long RUNTIME_HEARTBEAT = Duration.ofSeconds(6).toNanos();
    private static final long APPROVAL_LIFETIME = Duration.ofMinutes(10).toMillis();

    private record Draft(JsonObject plan, UUID base) {
        UUID id() { return BuildPlan.id(plan, "plan_id"); }
    }

    private record Ticket(UUID code, String hash, long expires) {}

    private static final class Request {
        final UUID id = UUID.randomUUID();
        final long expires = System.currentTimeMillis() + REQUEST_LIFETIME;
        final JsonObject input;
        final UUID siteJob;
        boolean staged;

        Request(JsonObject input, UUID siteJob) {
            this.input = input;
            this.siteJob = siteJob;
            input.addProperty("request_id", id.toString());
        }
    }

    private static final class Session {
        UUID active;
        Draft draft;
        Ticket ticket;
        Request request;
    }

    private record Pending(ServerPlayer player, Session session) {}

    private final ServerLevel level;
    private final BuildSite site;
    private final ConstructionService construction;
    private final JobJournal journal;
    private final Map<UUID, Session> sessions = new HashMap<>();
    private boolean writable = true;
    private long runtimePoll;

    ArchitectService(ServerLevel level, BuildSite site, ConstructionService construction) throws IOException {
        this.level = level;
        this.site = site;
        this.construction = construction;
        journal = new JobJournal(level, site, "mcaibuilder-architect.json");
        restore(journal.read());
    }

    void receive(ServerPlayer player, String message) {
        requirePlayer(player);
        Session session = sessions.computeIfAbsent(player.getUUID(), ignored -> new Session());
        String input = message.strip();
        String[] parts = input.split("\\s+", 2);
        String verb = parts[0];
        switch (verb) {
            case "link" -> {
                tell(player, runtimeOnline() ? "Agent 已连接。" : "Agent 未连接。请查看启动器。");
                return;
            }
            case "supply" -> {
                if (site.worldCopy() && input.equals("supply revoke")) {
                    construction.revokeWarehouse(player);
                    session.ticket = null;
                    session.request = null;
                    persist();
                    tell(player, "供料箱授权已撤销。");
                    return;
                }
                BlockPos chest = site.worldCopy() && input.equals("supply") ? lookedAt(player, 4.5) : supplyPosition(input);
                construction.authorizeWarehouse(player, chest);
                session.ticket = null;
                session.request = null;
                persist();
                tell(player, "供料箱已批准。");
                return;
            }
            case "materials" -> {
                JsonObject supply = supplyContext(session, player.getUUID());
                if (!supply.get("mode").getAsString().equals("survival")) {
                    tell(player, "当前使用隔离测试材料。");
                } else if (!supply.get("warehouse_approved").getAsBoolean()) {
                    tell(player, "请先用 /architect supply x y z 批准供料箱。");
                } else {
                    tell(player, supply.has("reason") ? "供料箱暂不可用。" : Warehouse.shortages(supply) + "。");
                }
                return;
            }
            case "approve", "reject", "resume", "cancel" -> {
                if (parts.length != 2) {
                    throw Wire.invalid("请使用聊天中的链接，或带上对应编号。");
                }
                UUID id = parseUuid(parts[1]);
                if (verb.equals("approve") || verb.equals("reject")) {
                    decide(player, session, id, verb.equals("approve"));
                } else {
                    control(player, session, id, verb);
                }
                return;
            }
            default -> { }
        }
        request(player, input);
    }

    void request(ServerPlayer player, String message) {
        requirePlayer(player);
        String input = message.strip();
        if (input.isEmpty() || input.length() > 240) {
            throw Wire.invalid("请输入 1–240 个字符的建筑需求。");
        }
        Session session = sessions.computeIfAbsent(player.getUUID(), ignored -> new Session());
        UUID job = currentJob();
        if (session.active != null && session.active.equals(job)
                && input.matches("(?is)^(?:pause\\b|(?:先\\s*)?暂停(?:\\s*施工)?)(?:$|[\\s,，。;；：:、].*)")) {
            JsonObject current = ownedJob(session);
            if (current != null && current.get("state").getAsString().equals("running")) {
                construction.control("pause", job);
                tell(player, "施工已暂停。");
            }
        }
        if (!runtimeOnline()) {
            tell(player, "Agent 未连接。这条需求没有进入模型队列，请查看启动器。");
            return;
        }
        if (session.ticket != null) {
            session.ticket = null;
            persist();
        }
        session.request = null;
        JsonObject context = worldContext(player);
        context.addProperty("player_id", player.getUUID().toString());
        context.addProperty("message", input);
        context.add("available_mod_blocks", construction.moddedMaterials());
        if (site.survival()) {
            context.add("supply", supplyContext(session, player.getUUID()));
        }
        JsonObject owned = ownedJob(session);
        if (owned != null) {
            context.add("job", owned);
        }
        if (session.draft != null) {
            JsonObject draft = new JsonObject();
            for (String field : new String[]{"plan_id", "building_id", "building_version", "model_sha256", "origin", "rotation"}) {
                draft.add(field, session.draft.plan.get(field));
            }
            draft.addProperty("revision", session.draft.base != null);
            context.add("draft", draft);
        }
        session.request = new Request(context, job);
        tell(player, "收到，正在处理。");
    }

    private JsonObject supplyContext(Session session, UUID owner) {
        if (site.survival() && session.draft != null) {
            try {
                JsonObject supply = inspect(session.draft, false, owner).getAsJsonObject("supply");
                supply.addProperty("warehouse_approved", true);
                return supply;
            } catch (Wire.Problem error) {
                JsonObject unavailable = construction.materials(owner);
                unavailable.addProperty("reason", error.code);
                return unavailable;
            }
        }
        return construction.materials(owner);
    }

    private static BlockPos lookedAt(ServerPlayer player, double reach) {
        BlockHitResult hit = player.serverLevel().clip(new ClipContext(player.getEyePosition(),
                player.getEyePosition().add(player.getLookAngle().scale(reach)), ClipContext.Block.OUTLINE,
                ClipContext.Fluid.NONE, player));
        if (hit.getType() != HitResult.Type.BLOCK) {
            throw Wire.invalid("请看向附近的原版单箱。");
        }
        return hit.getBlockPos();
    }

    private static BlockPos supplyPosition(String input) {
        String[] parts = input.split("\\s+");
        if (parts.length == 4) {
            try {
                return new BlockPos(Integer.parseInt(parts[1]), Integer.parseInt(parts[2]), Integer.parseInt(parts[3]));
            } catch (NumberFormatException ignored) {
                // The command requires explicit integer coordinates.
            }
        }
        throw Wire.invalid("请使用 /architect supply x y z 指定眼前的单箱。");
    }

    boolean runtimeOnline() {
        return writable && runtimePoll != 0 && System.nanoTime() - runtimePoll < RUNTIME_HEARTBEAT;
    }

    boolean playerOnline() {
        return site.architect().players().stream()
                .anyMatch(id -> level.getServer().getPlayerList().getPlayer(id) != null);
    }

    JsonObject inbox() {
        runtimePoll = System.nanoTime();
        JsonArray requests = new JsonArray();
        long now = System.currentTimeMillis();
        UUID active = currentJob();
        for (var entry : sessions.entrySet()) {
            Session session = entry.getValue();
            Request request = session.request;
            if (request == null) {
                continue;
            }
            ServerPlayer player = level.getServer().getPlayerList().getPlayer(entry.getKey());
            if (request.expires <= now) {
                session.request = null;
                if (permitted(player)) {
                    tell(player, "这一条处理已超时，请再试一次。");
                }
            } else if (!Objects.equals(request.siteJob, active)) {
                session.request = null;
                if (permitted(player)) {
                    tell(player, "当前施工已切换，请对当前版本重新提出需求。");
                }
            } else if (permitted(player)) {
                requests.add(request.input);
            }
        }
        JsonObject result = new JsonObject();
        result.add("requests", requests);
        return result;
    }

    JsonObject stage(JsonObject body) {
        Pending pending = requireRequest(body);
        Session session = pending.session;
        Request request = session.request;
        String mode = Wire.string(body.get("mode"), "mode");
        if (mode.equals("review")) {
            if (session.draft == null) {
                throw Wire.conflict("no_draft", "没有待审阅的设计。");
            }
            JsonObject report = inspect(session.draft, true, pending.player.getUUID());
            request.staged = true;
            return report;
        }
        if (!mode.equals("new") && !mode.equals("revise")) {
            throw Wire.invalid("Only new, revise and review may stage a design");
        }
        JsonObject plan = Wire.object(body.get("plan"), "plan");
        if (request.staged) {
            if (!session.draft.plan.equals(plan)) {
                throw Wire.conflict("request_already_staged", "本条需求已有候选。");
            }
            return inspect(session.draft, false, pending.player.getUUID());
        }

        UUID base = null;
        if (mode.equals("revise")) {
            if (session.draft != null) {
                JsonObject previous = session.draft.plan;
                if (!previous.get("building_id").equals(plan.get("building_id"))
                        || Wire.integer(plan.get("building_version"), "building_version")
                        <= previous.get("building_version").getAsInt()) {
                    throw Wire.conflict("stale_version", "修改必须推进同一建筑的版本。");
                }
                base = session.draft.base;
            } else if (request.input.has("job")) {
                base = session.active;
            } else {
                throw Wire.conflict("no_draft", "没有可修改的设计。");
            }
        }
        Draft next = new Draft(plan.deepCopy(), base);
        if (base == null) {
            construction.inspectInitial(plan, pending.player.getUUID());
        } else {
            construction.validateRevision(base, plan);
        }

        if (session.draft != null && session.draft.base != null) {
            discardRevision(session.draft);
        }
        session.draft = next;
        session.ticket = null;
        persist();
        JsonObject report = inspect(next, true, pending.player.getUUID());
        request.staged = true;
        return report;
    }

    JsonObject pauseRequest(JsonObject body) {
        Pending pending = requireRequest(body);
        JsonObject job = ownedJob(pending.session);
        if (job == null) {
            throw Wire.conflict("no_job", "当前没有你的施工任务。");
        }
        String state = job.get("state").getAsString();
        if (state.equals("running") || state.equals("paused")) {
            construction.control("pause", pending.session.active);
        } else {
            throw Wire.conflict("job_finished", "这项施工已经结束。");
        }
        return ok();
    }

    JsonObject answer(JsonObject body) {
        Pending pending = requireRequest(body);
        Session session = pending.session;
        ServerPlayer player = pending.player;
        String message = Wire.string(body.get("message"), "message").strip();
        if (message.isEmpty() || message.length() > 600 || message.chars().anyMatch(Character::isISOControl)) {
            throw Wire.invalid("Reply must be 1–600 printable characters");
        }
        if (body.has("review_sha256")) {
            if (!session.request.staged || session.draft == null) {
                throw Wire.conflict("no_draft", "这条请求没有已暂存的设计。");
            }
            String hash = Wire.string(body.get("review_sha256"), "review_sha256");
            JsonObject live = inspect(session.draft, false, pending.player.getUUID());
            if (!hash.equals(reviewHash(live))) {
                throw reviewChanged();
            }
            String folder = previewFolder(session.draft.id(), hash);
            Path root = FMLPaths.GAMEDIR.get().resolve(folder);
            if (!Files.isRegularFile(root.resolve("revision-review.blend"))
                    || !Files.isRegularFile(root.resolve("proposed.png"))
                    || !Files.isRegularFile(root.resolve("proposed-rear.png"))) {
                throw Wire.conflict("preview_unavailable", "设计预览尚未就绪，请重新预览。");
            }
            session.ticket = new Ticket(UUID.randomUUID(), hash, System.currentTimeMillis() + APPROVAL_LIFETIME);
            persist();
            tell(player, message);
            tell(player, "预览 " + folder + "/revision-review.blend（含两角度图片）。");
            var buttons = Component.literal("");
            if (live.getAsJsonObject("counts").get("conflict").getAsInt() == 0) {
                buttons.append(button("批准 " + session.ticket.code, "approve " + session.ticket.code)).append("  ");
            }
            buttons.append(button("拒绝 " + session.ticket.code, "reject " + session.ticket.code));
            tell(player, buttons);
        } else {
            tell(player, message);
            if (body.has("control")) {
                String control = Wire.string(body.get("control"), "control");
                if (ownedJob(session) != null && (control.equals("resume") || control.equals("cancel"))) {
                    tell(player, button(control.equals("resume") ? "继续当前施工" : "取消当前施工",
                            control + " " + session.active));
                }
            }
        }
        session.request = null;
        return ok();
    }

    private void decide(ServerPlayer player, Session session, UUID code, boolean approve) {
        if (session.ticket == null || session.draft == null || !session.ticket.code.equals(code)
                || session.ticket.expires <= System.currentTimeMillis() || session.request != null) {
            throw Wire.conflict("stale_approval", "这个确认已失效，请审阅当前候选。");
        }
        Draft draft = session.draft;
        Ticket ticket = session.ticket;
        if (approve) {
            JsonObject live;
            try {
                live = inspect(draft, false, player.getUUID());
            } catch (Wire.Problem changed) {
                session.ticket = null;
                persist();
                throw reviewChanged();
            }
            if (!reviewHash(live).equals(ticket.hash)) {
                session.ticket = null;
                persist();
                throw reviewChanged();
            }
            if (live.getAsJsonObject("counts").get("conflict").getAsInt() != 0) {
                throw Wire.conflict("change_conflicts", "现场有冲突，施工保持暂停。");
            }
        }

        // Consume the human authorization before a potentially persistent M2 mutation.
        // On an ambiguous failure the code stays consumed, and M2 owns recovery of the world.
        session.ticket = null;
        session.draft = null;
        session.request = null;
        UUID previous = session.active;
        if (approve) {
            session.active = draft.id();
        }
        persist();
        try {
            if (!approve) {
                discardRevision(draft);
            } else if (draft.base == null) {
                construction.submit(draft.plan, player.getUUID());
            } else {
                construction.approve(draft.id(), ticket.hash, player.getUUID());
            }
        } catch (Wire.Problem error) {
            session.active = previous;
            session.draft = draft;
            persist();
            throw error;
        }
        ForgeBridge.LOGGER.info("architect_decision player={} candidate={} approved={} hash={}",
                player.getUUID(), draft.id(), approve, ticket.hash);
        tell(player, approve ? "已批准 V" + draft.plan.get("building_version").getAsInt() + "，NPC 开始施工。"
                : "已拒绝这个候选。你可以询问进度或确认继续旧任务。");
    }

    private void control(ServerPlayer player, Session session, UUID id, String action) {
        if (!id.equals(session.active) || !id.equals(currentJob())) {
            throw Wire.conflict("architect_job_scope", "这个任务不属于你的当前会话。");
        }
        if (action.equals("resume") && session.draft != null) {
            throw Wire.conflict("change_pending", "请先批准或拒绝待审阅设计。");
        }
        construction.control(action, id, player.getUUID());
        session.request = null;
        if (action.equals("cancel")) {
            session.draft = null;
            session.ticket = null;
            persist();
        }
        tell(player, action.equals("resume") ? "已继续施工。" : "已取消当前施工。");
    }

    private JsonObject inspect(Draft draft, boolean stageIfMissing, UUID owner) {
        if (draft.base == null) {
            return construction.inspectInitial(draft.plan, owner);
        }
        if (!draft.base.equals(currentJob())) {
            throw Wire.conflict("architect_job_scope", "基础施工任务已经变化。");
        }
        JsonObject change = construction.change();
        if (!change.get("pending").getAsBoolean() && stageIfMissing) {
            return construction.propose(draft.base, draft.plan, owner);
        }
        if (!change.get("pending").getAsBoolean() || !draft.id().equals(BuildPlan.id(change, "change_id"))) {
            throw Wire.conflict("change_not_found", "此候选已被替换或结束。");
        }
        return change;
    }

    private void discardRevision(Draft draft) {
        if (draft.base != null) {
            JsonObject change = construction.change();
            if (change.get("pending").getAsBoolean() && draft.id().equals(BuildPlan.id(change, "change_id"))) {
                construction.reject(draft.id());
            }
        }
    }

    private Pending requireRequest(JsonObject body) {
        UUID id = BuildPlan.id(body, "request_id");
        for (var entry : sessions.entrySet()) {
            Session session = entry.getValue();
            Request request = session.request;
            if (request == null || !id.equals(request.id) || request.expires <= System.currentTimeMillis()) {
                continue;
            }
            ServerPlayer player = level.getServer().getPlayerList().getPlayer(entry.getKey());
            requirePlayer(player);
            if (!Objects.equals(request.siteJob, currentJob())) {
                throw Wire.conflict("architect_job_scope", "施工任务已变化，请重新提出需求。");
            }
            return new Pending(player, session);
        }
        throw Wire.conflict("stale_request", "该需求已完成、过期或被后续需求取代。");
    }

    boolean permitted(ServerPlayer player) {
        return player != null && !(player instanceof FakePlayer)
                && site.architect().players().contains(player.getUUID())
                && player.serverLevel() == level && site.area().contains(player.blockPosition());
    }

    void requirePlayer(ServerPlayer player) {
        if (!permitted(player)) {
            throw new Wire.Problem(403, "architect_forbidden", "需要由授权玩家在批准施工区内操作。");
        }
        if (!writable) {
            throw new Wire.Problem(503, "architect_journal_unavailable", "建筑师记录暂不可用。");
        }
    }

    private JsonObject worldContext(ServerPlayer player) {
        BlockPos position = player.blockPosition();
        Direction facing = player.getDirection();
        BlockHitResult hit = level.clip(new ClipContext(player.getEyePosition(),
                player.getEyePosition().add(player.getLookAngle().scale(8)), ClipContext.Block.OUTLINE,
                ClipContext.Fluid.NONE, player));
        BlockPos suggested = hit.getBlockPos().above();
        if (hit.getType() != HitResult.Type.BLOCK || hit.getDirection() != Direction.UP || !site.canBuildAt(suggested)) {
            suggested = position.relative(facing, 4);
        }
        if (site.worldCopy()) {
            suggested = site.origin();
            facing = site.rotation().rotate(Direction.NORTH);
        }
        JsonObject out = new JsonObject();
        out.addProperty("dimension", level.dimension().location().toString());
        out.addProperty("facing", facing.getName());
        if (site.worldCopy()) {
            out.addProperty("site_authorization", site.scope().toString());
            out.addProperty("site_rotation", site.rotation().ordinal() * 90);
        }
        out.add("position", Wire.position(position));
        out.add("suggested_origin", Wire.position(site.canBuildAt(suggested) ? suggested : position));
        out.add("approved_area", site.area().json());
        Map<String, Integer> terrain = new TreeMap<>();
        for (int dx = -2; dx <= 2; dx++) {
            for (int dz = -2; dz <= 2; dz++) {
                BlockPos foot = suggested.offset(dx, 0, dz);
                if (site.area().contains(foot) && level.hasChunkAt(foot)) {
                    String surface = Wire.state(level.getBlockState(foot.below())).get("id").getAsString();
                    terrain.merge(surface, 1, Integer::sum);
                }
            }
        }
        JsonObject layout = new JsonObject();
        terrain.forEach(layout::addProperty);
        out.add("ground_near_anchor", layout);
        return out;
    }

    private JsonObject currentJobState() {
        return construction.status(false).getAsJsonObject("job");
    }

    private UUID currentJob() {
        JsonObject job = currentJobState();
        return job == null ? null : BuildPlan.id(job, "job_id");
    }

    private JsonObject ownedJob(Session session) {
        JsonObject job = currentJobState();
        return job != null && BuildPlan.id(job, "job_id").equals(session.active) ? job : null;
    }

    private void restore(JsonObject saved) {
        if (saved == null) {
            return;
        }
        for (var entry : Wire.object(saved.get("players"), "players").entrySet()) {
            UUID owner = parseUuid(entry.getKey());
            if (!site.architect().players().contains(owner)) {
                continue;
            }
            Session session = new Session();
            JsonObject data = Wire.object(entry.getValue(), "session");
            if (data.has("active")) {
                session.active = BuildPlan.id(data, "active");
            }
            if (data.has("plan")) {
                JsonObject plan = Wire.object(data.get("plan"), "plan");
                BuildPlan.read(plan, site, BuildPlan.palette());
                session.draft = new Draft(plan, data.has("base") ? BuildPlan.id(data, "base") : null);
                if (data.has("code")) {
                    session.ticket = new Ticket(BuildPlan.id(data, "code"), Wire.string(data.get("hash"), "hash"),
                            data.get("expires").getAsLong());
                }
            }
            sessions.put(owner, session);
        }
    }

    private void persist() {
        JsonObject data = new JsonObject();
        JsonObject players = new JsonObject();
        for (var entry : sessions.entrySet()) {
            Session session = entry.getValue();
            JsonObject row = new JsonObject();
            if (session.active != null) {
                row.addProperty("active", session.active.toString());
            }
            if (session.draft != null) {
                row.add("plan", session.draft.plan);
                if (session.draft.base != null) {
                    row.addProperty("base", session.draft.base.toString());
                }
            }
            if (session.ticket != null) {
                row.addProperty("code", session.ticket.code.toString());
                row.addProperty("hash", session.ticket.hash);
                row.addProperty("expires", session.ticket.expires);
            }
            players.add(entry.getKey().toString(), row);
        }
        data.add("players", players);
        try {
            journal.write(data);
        } catch (IOException error) {
            writable = false;
            ForgeBridge.LOGGER.error("Could not save architect state", error);
            throw new Wire.Problem(503, "architect_journal_unavailable", "无法保存建筑师记录。");
        }
    }

    private static UUID parseUuid(String text) {
        try {
            UUID parsed = UUID.fromString(text);
            if (parsed.toString().equals(text)) {
                return parsed;
            }
        } catch (IllegalArgumentException ignored) {
            // The exact lowercase confirmation code is required.
        }
        throw Wire.invalid("需要聊天中给出的完整编号。");
    }

    private static String reviewHash(JsonObject report) {
        return report.get("review_sha256").getAsString();
    }

    private static Wire.Problem reviewChanged() {
        return Wire.conflict("review_changed", "现场已变化，原确认失效。请先重新审阅。");
    }

    private static String previewFolder(UUID id, String hash) {
        return "architect-previews/" + id + "/" + hash.substring(0, 16);
    }

    private static MutableComponent button(String label, String command) {
        return Component.literal("[" + label + "]").withStyle(style -> style.withUnderlined(true)
                .withClickEvent(new ClickEvent(ClickEvent.Action.RUN_COMMAND, "/architect " + command)));
    }

    private static void tell(ServerPlayer player, String message) {
        tell(player, Component.literal(message));
    }

    private static void tell(ServerPlayer player, Component message) {
        player.sendSystemMessage(Component.literal("[Architect] ")
                .append(Component.literal("MC Builder ").withStyle(net.minecraft.ChatFormatting.AQUA))
                .append(message));
    }

    private static JsonObject ok() {
        JsonObject result = new JsonObject();
        result.addProperty("ok", true);
        return result;
    }
}
