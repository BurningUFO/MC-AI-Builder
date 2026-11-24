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
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.TreeMap;
import java.util.UUID;

/** All methods run on the Minecraft server thread. The model runtime only polls and stages. */
final class ArchitectService {
    private static final long REQUEST_LIFETIME = Duration.ofMinutes(3).toMillis();
    private static final long RUNTIME_HEARTBEAT = Duration.ofSeconds(6).toNanos();
    private static final Set<String> APPROVALS = Set.of("批准", "可以", "建吧", "同意", "开工", "approve", "go ahead");
    private static final Set<String> REJECTIONS = Set.of("拒绝", "先别建", "不批准", "取消", "取消方案", "reject", "cancel");
    private static final Set<String> RELOCATIONS = Set.of("换个地方", "换个位置", "换个施工点", "move it");

    private record Draft(JsonObject plan, UUID base, boolean surveyed, String visualGoal, boolean autonomous) {
        UUID id() { return BuildPlan.id(plan, "plan_id"); }
    }

    private record VisualWatch(String goal, boolean checkpoint, boolean failure, boolean finalReview) {
        VisualWatch(String goal, boolean checkpoint) {
            this(goal, checkpoint, false, false);
        }

        VisualWatch observed(String event) {
            return new VisualWatch(goal, checkpoint || event.equals("checkpoint"),
                    failure || event.equals("failure"), finalReview || event.equals("final"));
        }
    }

    private static final class Request {
        final UUID id = UUID.randomUUID();
        final long expires = System.currentTimeMillis() + REQUEST_LIFETIME;
        final long started = BuildPerformance.clock();
        final JsonObject input;
        final UUID siteJob;
        JsonObject stagedReport;

        Request(JsonObject input, UUID siteJob) {
            this.input = input;
            this.siteJob = siteJob;
            input.addProperty("request_id", id.toString());
        }
    }

    private static final class Session {
        UUID active;
        VisualWatch watch;
        Draft draft;
        boolean reviewing;
        long reviewStarted;
        String notifiedProblem;
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
    private SiteSurvey survey;

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
            case "preview" -> {
                if (parts.length == 2 && parts[1].equals("clear")) {
                    GhostPreview.clear(player);
                    tell(player, "已清除这次的施工投影。");
                    return;
                }
            }
            case "link" -> {
                tell(player, runtimeOnline() ? "Agent 已连接。" : "Agent 未连接。请查看启动器。");
                return;
            }
            case "supply" -> {
                if (input.equals("supply revoke")) {
                    construction.revokeWarehouse(player);
                    tell(player, "已停止从附近容器取料。");
                    return;
                }
                shareMaterials(player, session, parts.length == 1 ? player.blockPosition() : supplyPosition(input));
                return;
            }
            case "plan" -> {
                if (!construction.offerPlanning(session.active) || parts.length != 2 || parts[1].isBlank()
                        || parts[1].length() > 160 || parts[1].codePoints().anyMatch(Character::isISOControl)) {
                    throw Wire.conflict("planning_hint_unavailable", "在自己的施工任务中，可发送最多 160 字的施工顺序建议。");
                }
                construction.planningHint(parts[1].strip(), taskBrain && runtimeOnline());
                tell(player, "施工顺序建议已记录。");
                return;
            }
            case "work" -> {
                if (parts.length != 2) {
                    throw Wire.invalid("请提供自主建设的建筑需求。");
                }
                if (parts[1].equals("retry")) {
                    retryWorksitePreview(player, session);
                } else {
                    request(player, parts[1], true, false, false, !reviewOnly(parts[1]));
                }
                return;
            }
            case "worksite" -> {
                var state = construction.worksiteStatus();
                tell(player, "自主建设 " + state.get("state").getAsString() + " · "
                        + state.get("mode").getAsString() + " · "
                        + state.getAsJsonArray("protected_areas").size() + " 处保护区。");
                JsonObject task = ownedJob(session);
                if (task != null && task.has("preparation")) {
                    JsonObject preparation = task.getAsJsonObject("preparation");
                    tell(player, "清障 " + preparation.get("cleared").getAsInt() + " / "
                            + preparation.get("total").getAsInt()
                            + (preparation.has("next") ? "。待处理 " + preparation.get("next") + " · "
                                    + preparation.get("tool").getAsString() : "。"));
                }
                return;
            }
            case "materials" -> {
                reportMaterials(player, session);
                return;
            }
            case "approve", "reject" -> {
                UUID id = parts.length == 2 ? parseUuid(parts[1]) : session.draft == null ? null : session.draft.id();
                decide(player, session, id, verb.equals("approve"), false);
                return;
            }
            case "pause", "resume", "cancel" -> {
                UUID id = parts.length == 2 ? parseUuid(parts[1]) : session.active;
                control(player, session, id, verb);
                return;
            }
            default -> { }
        }
        request(player, input);
    }

    private void retryWorksitePreview(ServerPlayer player, Session session) {
        if (!construction.autonomousAvailable()) {
            throw Wire.conflict("worksite_permission_inactive", "请先审阅同一工作区的有效自主许可。");
        }
        if (session.draft == null || !session.draft.autonomous) {
            throw Wire.conflict("no_draft", "当前工作区没有可重新预览的自主方案。");
        }
        if (!runtimeOnline()) {
            tell(player, "Agent 未连接。请查看启动器。");
            return;
        }
        queue(player, session, "重新预览并核对现有自主建设方案", false, false, null);
        session.request.input.addProperty("worksite", true);
        session.request.input.addProperty("retry_worksite", true);
        tell(player, "正在重新核对同一方案和现场。");
    }

    void request(ServerPlayer player, String message) {
        request(player, message, false, false, false);
    }

    void request(ServerPlayer player, String message, boolean surveyed, boolean watch, boolean inspect) {
        request(player, message, surveyed, watch, inspect, false);
    }

    private static boolean reviewOnly(String message) {
        return message.matches("(?is).*(?:只设计|只要设计|只看|先预览|不(?:要|用|需要)\\s*(?:动工|施工|建)"
                + "|别\\s*(?:动工|施工|建)|先别建|do not build|don.t build|preview only).*");
    }

    static boolean isPublicReply(String message) {
        String reply = chatReply(message);
        return APPROVALS.contains(reply) || REJECTIONS.contains(reply) || RELOCATIONS.contains(reply) || materialNotice(message);
    }

    boolean handlesPublicChat(ServerPlayer player, String message) {
        if (!permitted(player)) {
            return false;
        }
        Session session = sessions.get(player.getUUID());
        return session != null && (session.reviewing && (APPROVALS.contains(chatReply(message))
                || REJECTIONS.contains(chatReply(message)) || RELOCATIONS.contains(chatReply(message))) || (session.draft != null || session.active != null)
                && materialNotice(message));
    }

    private static String chatReply(String input) {
        return input.strip().toLowerCase(java.util.Locale.ROOT).replaceFirst("[。.!！?？]+$", "").strip();
    }

    private static boolean materialNotice(String input) {
        return chatReply(input).matches("(?is)^(?:材料(?:都)?(?:备齐|准备好|齐|到齐|补齐|放(?:在|到)?箱子里)(?:了)?"
                + "|(?:我)?(?:把)?材料(?:都)?放(?:到|在)?(?:附近)?(?:箱子|木桶)里了"
                + "|补好了|materials (?:are )?ready|materials (?:are )?in the chests)$");
    }

    private void request(ServerPlayer player, String message, boolean surveyed, boolean watch, boolean inspect,
                         boolean autonomous) {
        requirePlayer(player);
        if (autonomous && !construction.autonomousAvailable()) {
            throw Wire.conflict("worksite_permission_inactive", "请先用 /architect site autonomy 选择权限并审阅施工区。");
        }
        if ((surveyed || watch || inspect) && !site.worldCopy()) {
            throw Wire.conflict("survey_requires_copy", "主动观察需要使用获准的世界副本。");
        }
        String input = message.strip();
        if (input.isEmpty() || input.length() > 240) {
            throw Wire.invalid("请输入 1–240 个字符的建筑需求。");
        }
        Session session = sessions.computeIfAbsent(player.getUUID(), ignored -> new Session());
        String reply = chatReply(input);
        if (!surveyed && !watch && !inspect && session.reviewing && session.draft != null
                && (APPROVALS.contains(reply) || REJECTIONS.contains(reply))) {
            decide(player, session, session.draft.id(), APPROVALS.contains(reply), false);
            return;
        }
        if (!surveyed && !watch && !inspect && session.reviewing && RELOCATIONS.contains(reply)) {
            decide(player, session, session.draft.id(), false, false);
            tell(player, "请看向新的工地，再告诉我你想在那里建什么。");
            return;
        }
        if (!surveyed && !watch && !inspect && materialNotice(input)) {
            shareMaterials(player, session, player.blockPosition());
            return;
        }
        UUID job = currentJob();
        boolean owned = session.active != null && session.active.equals(job);
        if (inspect && (!owned || session.watch == null)) {
            throw Wire.conflict("visual_job_required", "请先用 /architect supervise 批准一个视觉监管任务。");
        }
        if (inspect) {
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
        String feedback = inspect ? "requested" : null;
        if (owned) {
            construction.invalidatePlanning("new_player_request");
        }
        queue(player, session, input, surveyed, watch, feedback);
        if (autonomous) {
            session.request.input.addProperty("worksite", true);
        }
        tell(player, "收到，正在处理。");
    }

    private void queue(ServerPlayer player, Session session, String input,
                       boolean surveyed, boolean watch, String feedback) {
        JsonObject context = worldContext(player, surveyed);
        context.addProperty("player_id", player.getUUID().toString());
        context.addProperty("message", input);
        context.addProperty("phase", phase(session, true));
        context.add("available_mod_blocks", construction.moddedMaterials());
        if (site.survival()) {
            context.add("supply", supplyContext(session, player.getUUID()));
        }
        JsonObject owned = ownedJob(session);
        if (owned != null) {
            context.add("job", owned);
            context.add("task", construction.taskContext());
        }
        if (session.draft != null) {
            JsonObject draft = new JsonObject();
            for (String field : new String[]{"plan_id", "building_id", "building_version", "model_sha256", "origin", "rotation"}) {
                draft.add(field, session.draft.plan.get(field));
            }
            draft.addProperty("revision", session.draft.base != null);
            context.add("draft", draft);
        }
        if (surveyed) {
            context.addProperty("survey", true);
        }
        if (watch) {
            context.addProperty("watch_build", true);
        }
        if (feedback != null) {
            context.addProperty("feedback", feedback);
            context.addProperty("visual_goal", session.watch.goal);
            if (feedback.equals("final")) {
                context.addProperty("final_verified", construction.verifiedFinishedSite(session.active));
            }
        }
        session.request = new Request(context, currentJob());
    }

    private JsonObject supplyContext(Session session, UUID owner) {
        if (site.survival() && session.draft != null) {
            try {
                JsonObject supply = inspect(session.draft, false, owner).getAsJsonObject("supply");
                if (supply != null) {
                    return supply;
                }
            } catch (Wire.Problem error) {
                JsonObject unavailable = construction.materials(owner);
                unavailable.addProperty("reason", error.code);
                return unavailable;
            }
        }
        return construction.materials(owner);
    }

    private String phase(Session session, boolean handling) {
        if (session.draft != null && session.reviewing) {
            return "REVIEWING";
        }
        JsonObject job = ownedJob(session);
        if (job != null) {
            String reason = job.has("reason") ? job.get("reason").getAsString() : "";
            if (Set.of("awaiting_materials", "warehouse_unreachable", "scaffold_material",
                "workstation_unreachable", "workshop_inventory").contains(reason)) {
                return "SUPPLY_CHECK";
            }
            String state = job.get("state").getAsString();
            if (state.equals("running") || state.equals("paused")) {
                return "BUILDING";
            }
            if (state.equals("completed")) {
                return "DONE";
            }
        }
        return handling ? "DESIGNING" : "IDLE";
    }

    private void shareMaterials(ServerPlayer player, Session session, BlockPos center) {
        construction.authorizeWarehouse(player, center);
        JsonObject job = ownedJob(session);
        if (job != null && job.has("reason")) {
            session.notifiedProblem = session.active + "/" + job.get("reason").getAsString();
        }
        reportMaterials(player, session);
    }

    private void reportMaterials(ServerPlayer player, Session session) {
        JsonObject stock = supplyContext(session, player.getUUID());
        if (!site.survival()) {
            tell(player, "我有练习世界提供的材料。");
        } else if (runtimeOnline() && session.request == null) {
            queue(player, session, "请根据附近库存告诉我还缺什么材料。", false, false, null);
            session.request.input.addProperty("event", "supply_update");
            session.request.input.addProperty("phase", "SUPPLY_CHECK");
            tell(player, "我来看看附近的材料。");
        } else {
            tell(player, Warehouse.shortages(stock));
        }
    }

    private static BlockPos supplyPosition(String input) {
        String[] parts = input.split("\\s+");
        if (parts.length == 4) {
            try {
                return new BlockPos(Integer.parseInt(parts[1]), Integer.parseInt(parts[2]), Integer.parseInt(parts[3]));
            } catch (NumberFormatException ignored) {
                // The optional coordinates supply a scan center.
            }
        }
        throw Wire.invalid("说「材料齐了」或使用 /architect supply，Builder 会盘点附近的箱子。");
    }

    boolean runtimeOnline() {
        return writable && runtimePoll != 0 && System.nanoTime() - runtimePoll < RUNTIME_HEARTBEAT;
    }

    boolean playerOnline() {
        return site.architect().players().stream()
                .anyMatch(id -> level.getServer().getPlayerList().getPlayer(id) != null);
    }

    private boolean taskBrain;

    JsonObject inbox(boolean taskBrain) {
        runtimePoll = System.nanoTime();
        this.taskBrain = taskBrain;
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
                if (request.started != 0) {
                    request.input.addProperty("bridge_queue_ms", (System.nanoTime() - request.started) / 1_000_000.0);
                }
                requests.add(request.input);
            }
        }
        JsonObject result = new JsonObject();
        result.add("requests", requests);
        return result;
    }

    boolean awaitingApproval() {
        return sessions.values().stream().anyMatch(session -> session.reviewing);
    }

    JsonObject planning(JsonObject body, NpcObservations observations) {
        String operation = Wire.string(body.get("operation"), "operation");
        UUID job = currentJob();
        if (body.has("request_id")) {
            Pending pending = requireRequest(body);
            if (job == null || !job.equals(pending.session.active)) {
                throw Wire.conflict("no_job", "当前会话没有施工任务。");
            }
            if (pending.session.reviewing && !Set.of("inspect", "finish").contains(operation)) {
                throw Wire.conflict("change_pending", "请先处理待审阅方案。");
            }
            return construction.planning(operation, pending.player, body, observations);
        }
        if (writable && runtimeOnline() && job != null) {
            if (operation.equals("claim") && body.has("brain") && !taskBrain) {
                return new JsonObject();
            }
            for (var entry : sessions.entrySet()) {
                Session session = entry.getValue();
                if (!job.equals(session.active) || session.request != null || session.reviewing) {
                    continue;
                }
                ServerPlayer player = level.getServer().getPlayerList().getPlayer(entry.getKey());
                if (permitted(player)) {
                    return construction.planning(operation, player, body, observations);
                }
            }
        }
        if (operation.equals("claim")) {
            return new JsonObject();
        }
        throw Wire.conflict("stale_planning", "施工、玩家授权或连接状态已改变。");
    }

    JsonObject survey(JsonObject body, NpcObservations observations) {
        Pending pending = requireRequest(body);
        Request request = pending.session.request;
        boolean passive = request.input.has("feedback");
        if (observations == null || !(request.input.has("survey") || passive) || request.stagedReport != null
                || passive && !construction.observationCheckpoint(request.siteJob)) {
            throw Wire.conflict("survey_not_requested", "这条游戏内需求没有请求主动观察。");
        }
        UUID owner = pending.player.getUUID();
        if (survey == null || !survey.matches(request.id, owner)) {
            stopSurvey();
            survey = new SiteSurvey(construction, request.id, owner, passive, request.input.has("worksite"));
        }
        return survey.handle(body, pending.player);
    }

    void tick(NpcObservations observations) {
        tickSurvey(observations);
        if (!writable || currentJob() == null) {
            return;
        }
        for (var entry : sessions.entrySet()) {
            Session session = entry.getValue();
            if (!currentJob().equals(session.active)) {
                continue;
            }
            ServerPlayer player = level.getServer().getPlayerList().getPlayer(entry.getKey());
            if (!permitted(player)) {
                continue;
            }
            notifyJob(player, session);
            if (session.watch == null || observations == null || !runtimeOnline()
                    || session.request != null || session.draft != null) {
                continue;
            }
            try {
                VisualWatch watch = session.watch;
                String event = construction.visualEvent(session.active, watch.checkpoint, watch.failure, watch.finalReview);
                if (event != null) {
                    session.watch = watch.observed(event);
                    persist();
                    queue(player, session, "", false, false, event);
                    tell(player, event.equals("checkpoint") ? "已暂停在施工检查点。正在获取 NPC 观测。"
                            : "正在复查施工现场。");
                }
            } catch (Wire.Problem problem) {
                ForgeBridge.LOGGER.warn("Could not record the visual checkpoint: {}", problem.code);
            }
            return;
        }
    }

    private void notifyJob(ServerPlayer player, Session session) {
        JsonObject job = ownedJob(session);
        if (job == null) {
            return;
        }
        String state = job.get("state").getAsString();
        if (!state.equals("paused") || !job.has("reason")) {
            session.notifiedProblem = null;
            return;
        }
        if (taskBrain && runtimeOnline() && construction.brainControlling()) {
            return;
        }
        String reason = job.get("reason").getAsString();
        String notice = session.active + "/" + reason;
        if (notice.equals(session.notifiedProblem)
                || Set.of("paused_by_request", "visual_checkpoint", "change_pending", "change_rejected",
                        "restart_verification", "server_stopping").contains(reason)) {
            return;
        }
        if (Set.of("awaiting_materials", "warehouse_unreachable", "scaffold_material",
                "workstation_unreachable", "workshop_inventory").contains(reason)) {
            if (session.request != null) {
                return;
            }
            reportMaterials(player, session);
        } else {
            tell(player, switch (reason) {
                case "protected_facility", "protected_area", "outside_effect", "outside_worksite", "hazardous_terrain" ->
                        "这个位置可能影响保护的设施或区域边界。我先停一下，你可以调整方案后再继续。";
                case "site_not_loaded", "checkpoint_npc_unavailable" ->
                        "工地有些位置还没加载。回到工地后跟我说继续就好。";
                default -> "我需要你看看当前施工位置，再决定是继续还是调整方案。";
            });
        }
        session.notifiedProblem = notice;
    }

    private void tickSurvey(NpcObservations observations) {
        if (survey == null) {
            return;
        }
        for (var entry : sessions.entrySet()) {
            Request request = entry.getValue().request;
            if (request != null && survey.matches(request.id, entry.getKey()) && request.stagedReport == null
                    && request.expires > System.currentTimeMillis() && Objects.equals(request.siteJob, currentJob())) {
                ServerPlayer player = level.getServer().getPlayerList().getPlayer(entry.getKey());
                if (permitted(player) && runtimeOnline()) {
                    survey.tick(player, observations);
                    return;
                }
            }
        }
        stopSurvey();
    }

    void stopSurvey() {
        if (survey != null) {
            survey.stop();
            survey = null;
        }
    }

    void closePreviews() {
        for (UUID owner : sessions.keySet()) {
            ServerPlayer player = level.getServer().getPlayerList().getPlayer(owner);
            if (player != null && player.serverLevel() == level) {
                GhostPreview.clear(player);
            }
        }
    }

    JsonObject stage(JsonObject body, NpcObservations observations) {
        Pending pending = requireRequest(body);
        Session session = pending.session;
        Request request = session.request;
        String mode = Wire.string(body.get("mode"), "mode");
        if (request.input.has("feedback") && (!mode.equals("revise")
                || request.input.get("feedback").getAsString().equals("final"))) {
            throw Wire.conflict("visual_revision_only", "施工观测只能提出当前版本的修改。");
        }
        if (mode.equals("review")) {
            if (session.draft == null) {
                throw Wire.conflict("no_draft", "没有待审阅的设计。");
            }
            JsonObject report = inspect(session.draft, true, pending.player.getUUID());
            request.stagedReport = report;
            return report;
        }
        if (!mode.equals("new") && !mode.equals("revise")) {
            throw Wire.invalid("Only new, revise and review may stage a design");
        }
        JsonObject plan = Wire.object(body.get("plan"), "plan");
        if (request.stagedReport != null) {
            if (!session.draft.plan.equals(plan)) {
                throw Wire.conflict("request_already_staged", "本条需求已有候选。");
            }
            return request.stagedReport;
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
                if (session.draft.surveyed && (!previous.get("origin").equals(plan.get("origin"))
                        || !previous.get("rotation").equals(plan.get("rotation")))) {
                    throw Wire.conflict("placement_scope", "修改候选时须沿用已审阅的原点和朝向。");
                }
                base = session.draft.base;
            } else if (request.input.has("job")) {
                base = session.active;
            } else {
                throw Wire.conflict("no_draft", "没有可修改的设计。");
            }
        }
        boolean surveyed = request.input.has("survey")
                || mode.equals("revise") && session.draft != null && session.draft.surveyed;
        if (request.input.has("survey") || request.input.has("feedback")) {
            if (survey == null || !survey.matches(request.id, pending.player.getUUID())) {
                throw Wire.conflict("survey_frame_stale", "没有此需求的新鲜配对观察。");
            }
            survey.requireFresh(observations, this);
        }
        String visualGoal = base != null && session.watch != null ? session.watch.goal : null;
        if (mode.equals("revise") && session.draft != null) {
            visualGoal = session.draft.visualGoal;
        }
        if (request.input.has("watch_build")) {
            visualGoal = request.input.get("message").getAsString();
        }
        boolean autonomous = base == null && request.input.has("worksite");
        Draft next = new Draft(plan.deepCopy(), base, surveyed, visualGoal, autonomous);
        if (base == null) {
            construction.inspectInitial(plan, pending.player.getUUID(), surveyed, autonomous);
        } else {
            construction.validateRevision(base, plan);
        }

        if (session.draft != null && session.draft.base != null) {
            discardRevision(session.draft);
        }
        GhostPreview.clear(pending.player);
        session.draft = next;
        session.reviewing = false;
        persist();
        JsonObject report = inspect(next, true, pending.player.getUUID());
        request.stagedReport = report;
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

    JsonObject answer(JsonObject body, NpcObservations observations) {
        Pending pending = requireRequest(body);
        Session session = pending.session;
        ServerPlayer player = pending.player;
        String message = Wire.string(body.get("message"), "message").strip();
        if (message.isEmpty() || message.length() > 600 || message.chars().anyMatch(Character::isISOControl)) {
            throw Wire.invalid("Reply must be 1–600 printable characters");
        }
        List<String> materialLines = new ArrayList<>();
        if (body.has("material_lines")) {
            var rows = Wire.array(body.get("material_lines"), "material_lines");
            if (rows.size() > 16) {
                throw Wire.invalid("Too many material lines");
            }
            for (var row : rows) {
                String line = Wire.string(row, "material_line");
                if (line.isBlank() || line.length() > 300 || line.chars().anyMatch(Character::isISOControl)) {
                    throw Wire.invalid("Invalid material line");
                }
                materialLines.add(line);
            }
        }
        if (body.has("visual_answer")) {
            Request request = session.request;
            if (!request.input.has("feedback") || survey == null || !survey.matches(request.id, player.getUUID())) {
                throw Wire.conflict("survey_frame_stale", "本次视觉回复需要新鲜的配对观测。");
            }
            survey.requireFresh(observations, this);
            if (request.input.get("feedback").getAsString().equals("final")) {
                boolean passed = Boolean.TRUE.equals(construction.verifiedFinishedSite(request.siteJob));
                message = "完工复查。" + (passed ? "Forge 逐格核验已通过。 " : "Forge 未确认逐格一致。 ") + message;
            }
        }
        if (body.has("review") && body.get("review").getAsBoolean()) {
            if (session.request.stagedReport == null || session.draft == null) {
                throw Wire.conflict("no_draft", "这条请求没有已暂存的设计。");
            }
            session.reviewing = true;
            session.reviewStarted = BuildPerformance.clock();
            persist();
            tell(player, message);
            for (String line : materialLines) {
                tell(player, line);
            }
            if (session.draft.autonomous && session.request.input.has("worksite")) {
                stopSurvey();
                try {
                    decide(player, session, session.draft.id(), true, true);
                    return ok();
                } catch (Wire.Problem problem) {
                    tell(player, problem.getMessage());
                }
            }
            GhostPreview.show(player, session.request.stagedReport);
            UUID id = session.draft.id();
            tell(player, Component.literal("回复「建吧」或 ")
                    .append(button("批准", "approve " + id)).append("  ").append(button("拒绝", "reject " + id)));
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

    JsonObject preview(JsonObject body) {
        UUID owner = BuildPlan.id(body, "player_id");
        UUID id = BuildPlan.id(body, "plan_id");
        Session session = sessions.get(owner);
        ServerPlayer player = level.getServer().getPlayerList().getPlayer(owner);
        if (session != null && permitted(player)
                && (id.equals(session.active) || session.draft != null && id.equals(session.draft.id()))) {
            if (body.has("project")) {
                JsonObject project = Wire.object(body.get("project"), "project");
                var links = new GhostPreview.ProjectLinks(Wire.string(project.get("name"), "project.name"),
                        Wire.string(project.get("image"), "project.image"), Wire.string(project.get("scene"), "project.scene"));
                if (!GhostPreview.sendLinks(player, links)) {
                    tell(player, "Blender 工程已归档 previews/" + links.name() + "/");
                }
            } else {
                String file = FMLPaths.GAMEDIR.get().resolve("architect-previews").resolve(id.toString())
                        .resolve("proposed.png").toAbsolutePath().toString();
                tell(player, Component.literal("预览图已生成 ")
                        .append(Component.literal("[打开图片]").withStyle(style -> style.withUnderlined(true)
                                .withClickEvent(new ClickEvent(ClickEvent.Action.OPEN_FILE, file)))));
            }
        }
        return ok();
    }

    private void decide(ServerPlayer player, Session session, UUID id, boolean approve, boolean delegated) {
        if (!session.reviewing || session.draft == null || !session.draft.id().equals(id)) {
            throw Wire.conflict("stale_approval", "请先审阅当前方案，再回复「建吧」或点击批准。");
        }
        Draft draft = session.draft;
        if (approve) {
            JsonObject live = inspect(draft, false, player.getUUID());
            if (live.getAsJsonObject("counts").get("conflict").getAsInt() != 0) {
                throw Wire.conflict("change_conflicts", "方案会碰到保护资产。请让我换个位置或调整布局。");
            }
            if (draft.base == null) {
                construction.submit(draft.plan, player.getUUID(), draft.surveyed, draft.autonomous);
            } else {
                construction.approve(draft.id(), player.getUUID());
            }
            construction.recordApprovalWait(session.reviewStarted);
            session.watch = draft.visualGoal == null ? null : new VisualWatch(draft.visualGoal,
                    draft.base != null && session.watch != null && session.watch.checkpoint);
            session.active = draft.id();
            if (taskBrain && runtimeOnline()) {
                construction.requestBrain(draft.base == null ? "approved" : "revision_approved");
            }
            session.notifiedProblem = null;
        } else {
            discardRevision(draft);
        }
        stopSurvey();
        GhostPreview.clear(player);
        session.reviewing = false;
        session.draft = null;
        session.request = null;
        persist();
        ForgeBridge.LOGGER.info("architect_decision player={} candidate={} approved={}", player.getUUID(), draft.id(), approve);
        if (delegated) {
            ForgeBridge.LOGGER.info("worksite_delegated scope={} candidate={}", site.scope(), draft.id());
        }
        tell(player, approve ? (delegated ? "已按工作区许可接下 V" : "已批准 V")
                + draft.plan.get("building_version").getAsInt()
                + (phase(session, false).equals("SUPPLY_CHECK") ? "。我先核对材料。" : "。我来开工。")
                : "已放下这份方案。你可以让我重新设计，或继续之前的项目。");
    }

    private void control(ServerPlayer player, Session session, UUID id, String action) {
        if (id == null || !id.equals(session.active) || !id.equals(currentJob())) {
            throw Wire.conflict("architect_job_scope", "这个任务不属于你的当前会话。");
        }
        if (action.equals("resume") && session.draft != null) {
            throw Wire.conflict("change_pending", "请先批准或拒绝待审阅设计。");
        }
        stopSurvey();
        construction.control(action, id, player.getUUID());
        session.request = null;
        if (action.equals("cancel")) {
            GhostPreview.clear(player);
            session.draft = null;
            session.reviewing = false;
            persist();
        }
        tell(player, switch (action) {
            case "pause" -> "已暂停当前施工。";
            case "resume" -> "已继续施工。";
            default -> "已取消当前施工。";
        });
    }

    private JsonObject inspect(Draft draft, boolean stageIfMissing, UUID owner) {
        if (draft.base == null) {
            return construction.inspectInitial(draft.plan, owner, draft.surveyed, draft.autonomous);
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

    private JsonObject worldContext(ServerPlayer player, boolean surveyed) {
        BlockPos position = player.blockPosition();
        Direction facing = player.getDirection();
        BlockHitResult hit = level.clip(new ClipContext(player.getEyePosition(),
                player.getEyePosition().add(player.getLookAngle().scale(8)), ClipContext.Block.OUTLINE,
                ClipContext.Fluid.NONE, player));
        BlockPos suggested = hit.getBlockPos().above();
        if (hit.getType() != HitResult.Type.BLOCK || hit.getDirection() != Direction.UP || !site.canBuildAt(suggested)) {
            suggested = position.relative(facing, 4);
        }
        if (site.worldCopy() && (surveyed || !site.canBuildAt(suggested))) {
            suggested = site.origin();
            if (surveyed) {
                facing = site.rotation().rotate(Direction.NORTH);
            }
        }
        if (!site.canBuildAt(suggested)) {
            suggested = site.canBuildAt(position) ? position : site.spawn().relative(facing, 2);
        }
        JsonObject out = new JsonObject();
        out.addProperty("dimension", level.dimension().location().toString());
        out.addProperty("facing", facing.getName());
        if (site.worldCopy()) {
            out.addProperty("site_authorization", site.scope().toString());
            out.addProperty("site_rotation", site.rotation().ordinal() * 90);
        }
        out.add("position", Wire.position(position));
        if (hit.getType() == HitResult.Type.BLOCK && site.area().contains(hit.getBlockPos())) {
            JsonObject gaze = new JsonObject();
            gaze.add("position", Wire.position(hit.getBlockPos()));
            gaze.addProperty("face", hit.getDirection().getName());
            out.add("gaze", gaze);
        }
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
        out.add("sites", construction.scoutSites(suggested, position));
        return out;
    }

    private JsonObject currentJobState() {
        return construction.jobStatus();
    }

    private UUID currentJob() {
        return construction.currentJobId();
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
            if (data.has("visual_watch") && session.active != null) {
                JsonObject watch = Wire.object(data.get("visual_watch"), "visual_watch");
                session.watch = new VisualWatch(Wire.string(watch.get("goal"), "visual goal"),
                        watch.get("checkpoint").getAsBoolean(), watch.get("failure").getAsBoolean(),
                        watch.get("final").getAsBoolean());
            }
            if (data.has("plan")) {
                JsonObject plan = Wire.object(data.get("plan"), "plan");
                boolean surveyed = data.has("surveyed") && data.get("surveyed").getAsBoolean();
                BuildPlan.read(plan, site, BuildPlan.palette(), surveyed || data.has("base"));
                String goal = data.has("visual_goal") ? Wire.string(data.get("visual_goal"), "visual goal") : null;
                boolean autonomous = data.has("autonomous") && data.get("autonomous").getAsBoolean();
                session.draft = new Draft(plan, data.has("base") ? BuildPlan.id(data, "base") : null,
                        surveyed, goal, autonomous);
                session.reviewing = data.has("reviewing") ? data.get("reviewing").getAsBoolean() : data.has("code");
                if (session.draft.id().equals(currentJob())) {
                    session.active = session.draft.id();
                    session.draft = null;
                    session.reviewing = false;
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
            if (session.watch != null) {
                JsonObject watch = new JsonObject();
                watch.addProperty("goal", session.watch.goal);
                watch.addProperty("checkpoint", session.watch.checkpoint);
                watch.addProperty("failure", session.watch.failure);
                watch.addProperty("final", session.watch.finalReview);
                row.add("visual_watch", watch);
            }
            if (session.draft != null) {
                row.add("plan", session.draft.plan);
                if (session.draft.visualGoal != null) {
                    row.addProperty("visual_goal", session.draft.visualGoal);
                }
                if (session.draft.surveyed) {
                    row.addProperty("surveyed", true);
                }
                if (session.draft.autonomous) {
                    row.addProperty("autonomous", true);
                }
                if (session.draft.base != null) {
                    row.addProperty("base", session.draft.base.toString());
                }
            }
            if (session.reviewing) {
                row.addProperty("reviewing", true);
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

    private static MutableComponent button(String label, String command) {
        return Component.literal("[" + label + "]").withStyle(style -> style.withUnderlined(true)
                .withClickEvent(new ClickEvent(ClickEvent.Action.RUN_COMMAND, "/architect " + command)));
    }

    static void tell(ServerPlayer player, String message) {
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
