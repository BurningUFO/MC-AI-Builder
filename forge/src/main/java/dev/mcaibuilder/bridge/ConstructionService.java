package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import dev.duzo.players.entities.FakePlayerEntity;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.level.LightLayer;
import net.minecraft.world.entity.MobCategory;
import net.minecraft.world.entity.MobSpawnType;
import net.minecraftforge.event.entity.living.MobSpawnEvent;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.DoorBlock;
import net.minecraft.world.level.block.Rotation;
import net.minecraft.world.item.Item;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.shapes.CollisionContext;

import java.io.IOException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

import dev.mcaibuilder.bridge.ConstructionJob.State;

final class ConstructionService {
    private static final int ACTION_INTERVAL_TICKS = 6;
    private static final int NEARBY_PLACEMENT_INTERVAL_TICKS = 2;
    private static final int PLACEMENT_LIMIT_TICKS = 1200;
    private static final String SCAFFOLD_CLEANUP_UNREACHABLE = "scaffold_cleanup_unreachable";
    private static final String SCAFFOLD_CLEANUP_BLOCKED = "scaffold_cleanup_blocked";
    private static final String SCAFFOLD_DESCENT = "scaffold_descent";
    private static final String PLANNING_BLOCKED = "planning_blocked";
    private static final Set<String> REOPENED_CHECKPOINT = Set.of("restart_verification", "server_stopping", "worksite_permission_inactive");
    private static final BlockState AIR = Blocks.AIR.defaultBlockState();
    private static final Set<String> PLACEMENT_FAILURES = Set.of(
            "no_reachable_placement_stance", "no_reachable_break_stance", "navigation_timeout", "placement_stalled",
            "scaffold_route_blocked", "scaffold_aim_changed", "placement_rejected", "waiting_for_space", "project_pair_pending");
    private static final Set<String> SUPPLY_FAILURES = Set.of(
            "awaiting_materials", "warehouse_unreachable", "out_of_material", "out_of_tool",
            "scaffold_material", "workstation_unreachable", "workshop_inventory");

    private record Registration(BuildPlan plan, JsonObject payload, String disposition) {
        Registration withDisposition(String next) {
            return new Registration(plan, payload, next);
        }
    }

    private final ServerLevel level;
    private final BuildSite site;
    private WorksitePermit worksite;
    private final Set<Block> palette;
    private final ConstructionWorker worker;
    private final JobJournal journal;
    private final Map<UUID, Registration> registered = new LinkedHashMap<>();
    private final Map<String, BuildPlan> completedLighting = new HashMap<>();
    private JsonObject history = new JsonObject();
    private ConstructionJob current;
    private UUID surveyNpc;
    private Registration candidate;
    private final Map<UUID, Warehouse.Source> warehouses = new HashMap<>();
    private long nextCheckpoint;
    private BlockPos pendingStockVisit;
    private long stockVisitUntil;
    private boolean journalBlocked;
    private boolean autonomyRevoked;

    ConstructionService(ServerLevel level, BuildSite site, Set<Block> palette, ConstructionWorker worker) throws IOException {
        this.level = level;
        this.site = site;
        worksite = site.worksite();
        this.palette = palette;
        this.worker = worker;
        journal = new JobJournal(level, site);
        restore(journal.read());
    }

    JsonObject submit(JsonObject payload) {
        return submit(payload, null);
    }

    JsonObject submit(JsonObject payload, UUID owner) {
        return submit(payload, owner, false);
    }

    JsonObject submit(JsonObject payload, UUID owner, boolean surveyed) {
        return submit(payload, owner, surveyed, false);
    }

    JsonObject submit(JsonObject payload, UUID owner, boolean surveyed, boolean autonomous) {
        long approvalStarted = BuildPerformance.clock();
        requireWritable();
        requireSupplyOwner(owner);
        UUID id = BuildPlan.id(payload, "plan_id");
        if (current != null && current.plan.id().equals(id) && registered.get(id).payload().equals(payload)) {
            return status(false);
        }
        InitialBuild initial = validateInitial(payload, false, surveyed, autonomous);
        BuildPlan plan = initial.plan();
        List<WorkStep> steps = initial.steps();
        SiteSnapshot snapshot = new SiteSnapshot();
        snapshot.track(plan);
        snapshot.track(steps);
        if (current != null) {
            requireCheckpointWorker();
            if (current.supply != null && !current.supply.owner.equals(owner) && !worker.inventory().isEmpty()) {
                throw Wire.conflict("warehouse_previous_cargo", "NPC 仍带着上一位玩家的材料。");
            }
        }
        if (current == null && surveyNpc != null && !worker.attach(surveyNpc)) {
            throw Wire.conflict("observation_npc_unavailable", "获准的原 NPC 尚未加载。");
        }
        prepare(steps);
        archiveCurrent(null);
        registered.put(id, new Registration(plan, payload.deepCopy(), "approved"));
        current = new ConstructionJob(plan, worker.id(), null, null, snapshot, steps);
        current.autonomous = autonomous;
        current.supply = site.survival() ? new SupplyCheckpoint(owner) : null;
        updateSupplyArea();
        worker.useScaffolds(current.scaffolds, plan.byPosition().keySet(), plan.terrain() != null);
        checkSupply();
        startPerformance(approvalStarted);
        schedule();
        persist();
        ForgeBridge.LOGGER.info("m1_submit {} {} targets={}", id, plan.buildingId(), plan.targets().size());
        return status(false);
    }

    private record InitialBuild(BuildPlan plan, List<WorkStep> placements, List<WorkStep> clearance,
                                Map<BlockPos, String> obstacles, String strategy) {
        List<WorkStep> steps() {
            if (clearance.isEmpty()) {
                return placements;
            }
            List<WorkStep> steps = new ArrayList<>(clearance);
            steps.addAll(placements);
            return steps;
        }
    }

    boolean autonomousAvailable() {
        return site.worldCopy() && !autonomyRevoked && worksite.active(System.currentTimeMillis());
    }

    JsonObject worksiteStatus() {
        JsonObject value = worksite.json();
        value.addProperty("active", autonomousAvailable());
        value.addProperty("state", autonomyRevoked ? "revoked" : autonomousAvailable() ? "active"
                : worksite.mode().equals("manual") ? "manual" : "expired");
        if (site.scope() != null) {
            value.addProperty("authorization", site.scope().toString());
        }
        return value;
    }

    private void requireAutonomy() {
        if (!autonomousAvailable()) {
            throw Wire.conflict("worksite_permission_inactive", "自主建设权限已过期或撤销，请重新审阅工作区。");
        }
    }

    void renewAutonomy(WorksitePermit renewed) {
        requireWritable();
        if (autonomyRevoked) {
            autonomyRevoked = false;
            persist();
        }
        worksite = renewed;
    }

    void revokeAutonomy() {
        requireWritable();
        autonomyRevoked = true;
        if (current != null && current.autonomous && current.state == State.RUNNING) {
            pause("worksite_permission_inactive");
        }
        persist();
    }

    private Set<BlockPos> worksiteEnvelope(BuildPlan plan) {
        Set<BlockPos> cells = new java.util.TreeSet<>(BuildPlan.BUILD_ORDER);
        for (BuildPlan.Target target : plan.targets()) {
            cells.add(target.world());
            if ((plan.voxel() || plan.buildingId().startsWith("cottage_")) && target.local().getY() == 0) {
                for (int y = 1; y <= 3; y++) {
                    BlockPos interior = target.world().above(y);
                    if (site.canBuildAt(interior)) {
                        cells.add(interior);
                    }
                }
            }
        }
        return cells;
    }

    private Map<BlockPos, BlockState> clearance(BuildPlan plan, Map<BlockPos, String> obstacles) {
        var inspect = new Clearance(level, site);
        Map<BlockPos, BlockState> cells = new LinkedHashMap<>();
        for (BlockPos pos : worksiteEnvelope(plan)) {
            if (!level.hasChunkAt(pos)) {
                throw Wire.conflict("site_not_loaded", "请走近预计施工的位置，让工地加载后再继续。");
            }
            BlockState state = level.getBlockState(pos);
            BuildPlan.Target target = plan.at(pos);
            if (state.isAir() || (target != null && target.state() == state) || cells.containsKey(pos)) {
                continue;
            }
            Clearance.Finding finding = inspect.inspect(pos);
            if (finding.risk() != null) {
                obstacles.put(pos, finding.risk());
            } else {
                cells.putAll(inspect.states(finding));
            }
        }
        if (!Clearance.limited(cells)) {
            obstacles.put(plan.origin(), "clearance_limit");
            return Map.of();
        }
        return cells;
    }

    JsonObject surveyTerrain() {
        requireWritable();
        site.requireLoaded(level);
        return TerrainDesign.survey(level, site);
    }

    private Map<BlockPos, BlockState> explicitClearance(BuildPlan plan, Map<BlockPos, String> obstacles, boolean autonomous) {
        var clearance = new Clearance(level, site);
        var cells = new LinkedHashMap<BlockPos, BlockState>();
        var survey = TerrainDesign.surveyBox(site);
        for (BlockPos pos : plan.terrain().excavation().stream().sorted(BuildPlan.BUILD_ORDER).toList()) {
            if (!site.canWorkAt(level, pos)) {
                obstacles.put(pos, "site_not_loaded");
                continue;
            }
            BlockState actual = level.getBlockState(pos);
            if (actual.isAir() || cells.containsKey(pos) || plan.at(pos) != null && plan.at(pos).state() == actual) {
                continue;
            }
            Clearance.Finding finding = clearance.inspect(pos);
            String reason = finding.risk();
            if (reason == null && (!autonomous || !autonomousAvailable())) {
                reason = "worksite_permission_inactive";
            }
            if (reason == null) {
                for (BlockPos affected : finding.cells()) {
                    if (!survey.contains(affected) || !clearanceAllowed(level.getBlockState(affected))) {
                        reason = "terrain_clearance_scope";
                        break;
                    }
                }
            }
            if (reason == null) {
                cells.putAll(clearance.states(finding));
            } else {
                obstacles.put(pos, reason);
            }
        }
        for (BuildPlan.Target target : plan.targets()) {
            BlockPos pos = target.world();
            if (!site.canWorkAt(level, pos)) {
                obstacles.put(pos, "site_not_loaded");
                continue;
            }
            BlockState state = level.getBlockState(pos);
            if (!state.isAir() && target.state() != state && !cells.containsKey(pos)) {
                Clearance.Finding finding = clearance.inspect(pos);
                obstacles.putIfAbsent(pos, finding.risk() == null ? "excavation_required" : finding.risk());
            }
        }
        if (!Clearance.limited(cells)) {
            obstacles.put(plan.origin(), "clearance_limit");
            return Map.of();
        }
        return cells;
    }

    private boolean clearanceAllowed(BlockState state) {
        return switch (Clearance.kind(state)) {
            case "vegetation" -> true;
            case "soil", "stone" -> !site.worksite().mode().equals("auto");
            case "logs", "leaves" -> site.worksite().mode().equals("trees");
            default -> false;
        };
    }

    private void checkTerrainSupport(BuildPlan plan, Set<BlockPos> cleared, Map<BlockPos, String> obstacles) {
        Set<BlockPos> attached = new HashSet<>();
        var queue = new java.util.ArrayDeque<BlockPos>();
        for (BuildPlan.Target target : plan.targets()) {
            if (obstacles.containsKey(target.world())) continue;
            for (Direction side : Direction.values()) {
                BlockPos neighbor = target.world().relative(side);
                if (!level.hasChunkAt(neighbor) || cleared.contains(neighbor) || plan.at(neighbor) != null) continue;
                BlockState state = level.getBlockState(neighbor);
                if (state.getFluidState().isEmpty() && !WorldObservation.hazard(state)
                        && state.isFaceSturdy(level, neighbor, side.getOpposite())) {
                    attached.add(target.world());
                    queue.add(target.world());
                    break;
                }
            }
        }
        while (!queue.isEmpty()) {
            BlockPos pos = queue.removeFirst();
            for (Direction side : Direction.values()) {
                BlockPos next = pos.relative(side);
                if (plan.at(next) != null && !obstacles.containsKey(next) && attached.add(next)) {
                    queue.addLast(next);
                }
            }
        }
        for (BuildPlan.Target target : plan.targets()) {
            if (!attached.contains(target.world())) obstacles.putIfAbsent(target.world(), "terrain_unsupported");
        }
    }

    private InitialBuild validateInitial(JsonObject payload, boolean reportObstacles, boolean surveyed,
                                         boolean autonomous) {
        requireWritable();
        if (autonomous) {
            requireAutonomy();
        }
        UUID id = BuildPlan.id(payload, "plan_id");
        requireUnused(id);
        if (current != null && current.state.active()) {
            throw Wire.conflict("job_active", "Pause and propose a change, or finish the active job");
        }
        BuildPlan plan = BuildPlan.read(payload, site, palette, surveyed);
        requireImmutableVersion(plan);
        int latest = registered.values().stream().filter(entry -> entry.disposition().equals("approved")
                        && plan.samePlacement(entry.plan())).mapToInt(entry -> entry.plan().version()).max().orElse(0);
        if (latest > 0) {
            throw Wire.conflict(plan.version() < latest ? "stale_version" : "change_required",
                    "Use version review for an existing placement");
        }

        site.requireLoaded(level);
        List<WorkStep> placements = new ArrayList<>();
        Map<BlockPos, String> obstacles = new LinkedHashMap<>();
        Map<BlockPos, BlockState> preparation = plan.terrain() == null ? clearance(plan, obstacles)
                : explicitClearance(plan, obstacles, autonomous);
        if (plan.terrain() != null) {
            checkTerrainSupport(plan, preparation.keySet(), obstacles);
            for (BlockPos landing : plan.terrain().landings()) {
                if (!TerrainDesign.plannedStandable(level, plan, preparation.keySet(), landing)) {
                    obstacles.putIfAbsent(landing, "terrain_landing_unusable");
                }
            }
        }
        if (!reportObstacles && !obstacles.isEmpty()) {
            throw Wire.conflict(obstacles.values().iterator().next(), "现场存在须由玩家决定的障碍，请重新勘察。");
        }
        String strategy = autonomous && payload.has("worksite_order")
                ? Wire.string(payload.get("worksite_order"), "worksite_order") : "route_first";
        if (!strategy.equals("route_first") && !strategy.equals("tree_first")) {
            throw Wire.invalid("Unknown worksite clearance order");
        }
        List<WorkStep> removal = clearanceSteps(plan, preparation, strategy);
        for (BuildPlan.Target target : plan.targets()) {
            BlockPos pos = target.world();
            BlockState actual = preparation.containsKey(pos) ? AIR : level.getBlockState(pos);
            WorkStep step = new WorkStep(target, actual, false);
            if (actual != target.state() && !obstacles.containsKey(pos)) {
                try {
                    requirePlacementSite(step);
                } catch (Wire.Problem obstacle) {
                    if (!reportObstacles) {
                        throw obstacle;
                    }
                    obstacles.put(pos, obstacle.code);
                }
            }
            placements.add(step);
        }
        return new InitialBuild(plan, placements, removal, obstacles, strategy);
    }

    private static List<WorkStep> clearanceSteps(BuildPlan plan, Map<BlockPos, BlockState> cells, String strategy) {
        return cells.entrySet().stream()
                .filter(entry -> !Clearance.upperHalf(entry.getValue()) || !cells.containsKey(entry.getKey().below()))
                .map(entry -> WorkStep.clear(plan, entry.getKey(), entry.getValue()))
                .sorted(Clearance.order(strategy)).toList();
    }

    JsonArray scoutSites(BlockPos focus, BlockPos player) {
        record Option(BlockPos origin, int supported, int obstacles, double distance, JsonArray gaps) {}
        List<Option> options = new ArrayList<>();
        Map<BlockPos, Integer> terrain = new HashMap<>();
        for (BlockPos point : BlockPos.withinManhattan(focus, 4, 1, 4)) {
            if ((point.getX() - focus.getX()) % 2 != 0 || (point.getZ() - focus.getZ()) % 2 != 0
                    || !site.canWorkAt(level, point) || !surveyFoundation(point)) {
                continue;
            }
            int footing = 0;
            int protectedCells = 0;
            var gaps = new JsonArray();
            for (int x = -3; x <= 3; x++) {
                for (int z = -3; z <= 3; z++) {
                    BlockPos base = point.offset(x, 0, z);
                    int depth = terrain.computeIfAbsent(base, this::foundationGap);
                    if (depth == 0) {
                        footing++;
                    } else if (depth > 0) {
                        gaps.add(Wire.position(new BlockPos(x, depth, z)));
                    }
                    for (int y = 0; y <= 3; y++) {
                        BlockPos cell = base.above(y);
                        if (!site.canWorkAt(level, cell) || Clearance.managed(level.getBlockState(cell))
                                || !level.getBlockState(cell).getFluidState().isEmpty()) {
                            protectedCells++;
                        }
                    }
                }
            }
            options.add(new Option(point.immutable(), footing, protectedCells,
                    point.distSqr(focus) + 0.1 * point.distSqr(player), gaps));
        }
        options.sort(java.util.Comparator.comparingInt(Option::obstacles)
                .thenComparingDouble(option -> option.distance() + (49 - option.supported()) * 0.5));
        var result = new JsonArray();
        options.stream().limit(6).forEach(option -> {
            JsonObject site = new JsonObject();
            site.add("origin", Wire.position(option.origin()));
            site.addProperty("supported_columns", option.supported());
            site.addProperty("protected_cells", option.obstacles());
            site.add("foundation_gaps", option.gaps());
            result.add(site);
        });
        return result;
    }

    private int foundationGap(BlockPos base) {
        for (int depth = 0; depth <= 3; depth++) {
            BlockPos foot = base.below(depth);
            if (surveyFoundation(foot)) {
                return depth;
            }
            if (!site.canWorkAt(level, foot.below()) || !level.getBlockState(foot.below()).isAir()) {
                break;
            }
        }
        return -1;
    }

    BlockPos surveyFocus() {
        return site.origin();
    }

    JsonArray surveyPads(boolean autonomous) {
        var pads = new JsonArray();
        Clearance clearance = autonomous ? new Clearance(level, site) : null;
        var center = site.origin();
        for (var rotation : net.minecraft.world.level.block.Rotation.values()) {
            // Try the marked spot first, then look for a short shift to flat terrain.
            int alternatives = 0;
            for (BlockPos offset : BlockPos.withinManhattan(BlockPos.ZERO, 4, 2, 4)) {
                BlockPos origin = center.offset(offset);
                int span = 0;
                int readySpan = 0;
                Map<BlockPos, BlockState> clearing = new HashMap<>();
                Map<BlockPos, String> blockers = new LinkedHashMap<>();
                for (int x = 0; x < 12; x++) {
                    BlockPos pos = origin.offset(new BlockPos(x, 0, 0).rotate(rotation));
                    if (!surveyFoundation(pos) || !site.area().contains(pos.above(2))) {
                        break;
                    }
                    if (clearance == null) {
                        if (!fillable(pos) || !fillable(pos.above()) || !fillable(pos.above(2))) {
                            break;
                        }
                    } else {
                        for (int y = 0; y <= 2; y++) {
                            inspectSurveyCell(pos.above(y), clearance, clearing, blockers);
                        }
                        if (!Clearance.limited(clearing)) {
                            blockers.putIfAbsent(pos, "clearance_limit");
                        }
                        if (blockers.isEmpty()) {
                            readySpan++;
                        }
                    }
                    span++;
                }
                if (span >= 5) {
                    var pad = new JsonObject();
                    pad.add("offset", Wire.position(offset));
                    pad.addProperty("rotation", rotation.ordinal() * 90);
                    pad.addProperty("span", span);
                    if (clearance != null) {
                        addSurveyClearance(pad, clearing, blockers);
                        pad.addProperty("actionable_span", readySpan);
                    }
                    pads.add(pad);
                    if (++alternatives >= 2) {
                        break;
                    }
                }
            }
        }
        return pads;
    }

    JsonArray cottagePads(boolean autonomous) {
        JsonArray result = new JsonArray();
        Map<BlockPos, Integer> foundation = new HashMap<>();
        Clearance clearance = autonomous ? new Clearance(level, site) : null;
        int maximum = autonomous ? 16 : 8;
        int radius = autonomous ? 4 : 2;
        Map<String, Integer> viableSizes = new HashMap<>();
        for (BlockPos offset : BlockPos.withinManhattan(BlockPos.ZERO, radius, autonomous ? 0 : 1, radius)) {
            BlockPos origin = site.origin().offset(offset);
            for (Rotation rotation : Rotation.values()) {
                if (autonomous && !offset.equals(BlockPos.ZERO) && rotation != site.rotation()) {
                    continue;
                }
                JsonArray sizes = new JsonArray();
                for (int width : new int[]{7, 9}) {
                    for (int depth : new int[]{7, 9}) {
                        JsonObject fit = cottageFit(origin, rotation, width, depth, foundation, clearance);
                        if (fit != null) {
                            sizes.add(fit);
                        }
                    }
                }
                if (!sizes.isEmpty() && (!autonomous || offerCottage(sizes, result.size(), viableSizes))) {
                    JsonObject pad = new JsonObject();
                    pad.add("offset", Wire.position(offset));
                    pad.addProperty("rotation", rotation.ordinal() * 90);
                    pad.add("sizes", sizes);
                    result.add(pad);
                    if (result.size() == maximum) {
                        return result;
                    }
                }
            }
        }
        return result;
    }

    private static boolean offerCottage(JsonArray sizes, int offered, Map<String, Integer> viableSizes) {
        List<String> available = new ArrayList<>();
        for (JsonElement row : sizes) {
            JsonObject fit = row.getAsJsonObject();
            if (fit.get("actionable").getAsBoolean()) {
                available.add(fit.get("width").getAsInt() + "x" + fit.get("depth").getAsInt());
            }
        }
        if (offered >= 8 && available.stream().allMatch(size -> viableSizes.getOrDefault(size, 0) >= 2)) {
            return false;
        }
        available.forEach(size -> viableSizes.merge(size, 1, Integer::sum));
        return true;
    }

    private JsonObject cottageFit(BlockPos origin, Rotation rotation, int width, int depth,
                                   Map<BlockPos, Integer> foundation, Clearance clearance) {
        int xEdge = width / 2;
        int zEdge = depth / 2;
        int footingCount = 0;
        Map<BlockPos, BlockState> clearing = new HashMap<>();
        Map<BlockPos, String> blockers = new LinkedHashMap<>();
        JsonArray footingRows = new JsonArray();
        for (int z = -zEdge; z <= zEdge; z++) {
            int mask = 0;
            for (int x = -xEdge; x <= xEdge; x++) {
                BlockPos floor = origin.offset(new BlockPos(x, 0, z).rotate(rotation));
                int depthBelowFloor = foundation.computeIfAbsent(floor, pos -> cottageColumn(pos, clearance));
                if (depthBelowFloor < 0) {
                    return null;
                }
                if (depthBelowFloor == 1) {
                    mask |= 1 << (x + xEdge);
                    footingCount++;
                }
                if (clearance != null) {
                    for (int y = 0; y <= 3; y++) {
                        inspectSurveyCell(floor.above(y), clearance, clearing, blockers);
                    }
                }
            }
            footingRows.add(mask);
        }
        if (footingCount > width * depth / 2) {
            return null;
        }
        if (!Clearance.limited(clearing)) {
            blockers.put(origin, "clearance_limit");
        }
        JsonArray entrances = new JsonArray();
        String[] names = {"left", "center", "right"};
        int[] doorway = {-xEdge + 2, 0, xEdge - 2};
        for (int i = 0; i < names.length; i++) {
            BlockPos approach = origin.offset(new BlockPos(doorway[i], 0, -zEdge - 1).rotate(rotation));
            if (site.area().contains(approach.above(2)) && site.area().contains(approach)
                    && (BuildTerrain.canStand(level, approach) || BuildTerrain.canStand(level, approach.above()))) {
                entrances.add(names[i]);
            }
        }
        if (entrances.isEmpty()) {
            if (clearance == null) {
                return null;
            }
            blockers.putIfAbsent(origin, "blocked_approach");
        }
        boolean eaves = true;
        for (int x = -xEdge; x <= xEdge && eaves; x++) {
            BlockPos edge = origin.offset(new BlockPos(x, 3, -zEdge - 1).rotate(rotation));
            eaves = site.canBuildAt(edge) && level.hasChunkAt(edge) && level.getBlockState(edge).isAir();
        }
        JsonObject fit = new JsonObject();
        fit.addProperty("width", width);
        fit.addProperty("depth", depth);
        fit.addProperty("eaves", eaves);
        fit.add("entries", entrances);
        fit.add("footing_rows", footingRows);
        if (clearance != null) {
            addSurveyClearance(fit, clearing, blockers);
        }
        return fit;
    }

    private void inspectSurveyCell(BlockPos pos, Clearance clearance, Map<BlockPos, BlockState> clearing,
                                   Map<BlockPos, String> blockers) {
        if (!site.canBuildAt(pos)) {
            blockers.put(pos, site.worksite().protectedAt(pos) ? "protected_area" : "worker_access");
        } else if (!clearing.containsKey(pos) && !level.getBlockState(pos).isAir()) {
            Clearance.Finding finding = clearance.inspect(pos);
            if (finding.risk() != null) {
                blockers.put(pos, finding.risk());
            } else {
                clearing.putAll(clearance.states(finding));
            }
        }
    }

    private void addSurveyClearance(JsonObject option, Map<BlockPos, BlockState> clearing,
                                           Map<BlockPos, String> blockers) {
        option.add("clearance", Clearance.counts(clearing));
        Map<String, Integer> codes = new java.util.TreeMap<>();
        blockers.values().forEach(reason -> codes.merge(reason, 1, Integer::sum));
        JsonObject risks = new JsonObject();
        codes.forEach(risks::addProperty);
        option.add("restrictions", risks);
        option.addProperty("actionable", blockers.isEmpty());
    }

    private boolean fillable(BlockPos pos) {
        return site.canBuildAt(pos) && level.hasChunkAt(pos) && level.getBlockState(pos).isAir();
    }

    JsonArray worksiteFacts() {
        requireAutonomy();
        site.requireLoaded(level);
        var clearance = new Clearance(level, site);
        var samples = new LinkedHashMap<String, JsonObject>();
        BlockPos origin = site.origin();
        for (BlockPos pos : BlockPos.withinManhattan(origin, 6, 4, 6)) {
            if (pos.getY() < origin.getY() || !site.area().contains(pos) || level.getBlockState(pos).isAir()) {
                continue;
            }
            String kind = Clearance.kind(level.getBlockState(pos));
            Clearance.Finding finding = clearance.inspect(pos);
            if (finding.risk() == null && kind.equals("leaves")) {
                continue;
            }
            JsonObject fact = clearance.describe(pos);
            String key = fact.get("id").getAsString() + "/" + finding.risk();
            if (!samples.containsKey(key)) {
                fact.add("offset_from_mark", Wire.position(pos.subtract(origin)));
                fact.addProperty("group_cells", finding.cells().size());
                if (finding.risk() != null) {
                    fact.addProperty("risk", finding.risk());
                }
                samples.put(key, fact);
            }
            if (samples.size() == 8) {
                break;
            }
        }
        var data = new JsonArray();
        samples.values().forEach(data::add);
        return data;
    }

    JsonArray worksiteFindings() {
        requireAutonomy();
        site.requireLoaded(level);
        var findings = new java.util.TreeMap<String, java.util.TreeMap<String, Integer>>();
        var representative = new java.util.TreeMap<String, BlockPos>();
        BlockPos origin = site.origin();
        for (BlockPos pos : BlockPos.betweenClosed(origin.offset(-6, -1, -6), origin.offset(6, 5, 6))) {
            if (!site.area().contains(pos)) {
                continue;
            }
            BlockState state = level.getBlockState(pos);
            if (state.isAir()) {
                continue;
            }
            String reason = site.worksite().protectedAt(pos) ? "protected_area"
                    : Clearance.managed(state) || level.getBlockEntity(pos) != null ? "protected_facility"
                    : !state.getFluidState().isEmpty() || WorldObservation.hazard(state) ? "hazardous_terrain" : null;
            if (reason != null) {
                String id = Wire.state(state).get("id").getAsString();
                findings.computeIfAbsent(reason, ignored -> new java.util.TreeMap<>()).merge(id, 1, Integer::sum);
                representative.putIfAbsent(reason, pos.subtract(origin));
            }
        }
        JsonArray ret = new JsonArray();
        findings.forEach((reason, members) -> {
            JsonObject group = new JsonObject();
            group.addProperty("reason", reason);
            group.addProperty("count", members.values().stream().mapToInt(Integer::intValue).sum());
            JsonArray examples = new JsonArray();
            members.keySet().stream().limit(4).forEach(examples::add);
            group.add("examples", examples);
            group.add("sample_from_mark", Wire.position(representative.get(reason)));
            ret.add(group);
        });
        return ret;
    }

    private int cottageColumn(BlockPos floor, Clearance clearance) {
        if (!level.hasChunkAt(floor)) {
            return -1;
        }
        int gap = 0;
        if (!surveyFoundation(floor)) {
            BlockPos footing = floor.below();
            if (!site.canBuildAt(footing) || !level.getBlockState(footing).isAir() || !surveyFoundation(footing)) {
                return -1;
            }
            gap = 1;
        }
        for (int y = 0; y <= 3; y++) {
            BlockPos pos = floor.above(y);
            if (clearance == null ? !fillable(pos) : !site.area().contains(pos) || !level.hasChunkAt(pos)) {
                return -1;
            }
        }
        return gap;
    }

    private boolean surveyFoundation(BlockPos target) {
        if (!site.area().contains(target) || !level.hasChunkAt(target)) {
            return false;
        }
        BlockPos floor = target.below();
        BlockState state = level.getBlockState(floor);
        return !Clearance.managed(state) && !WorldObservation.hazard(state) && state.getFluidState().isEmpty()
                && state.isFaceSturdy(level, floor, Direction.UP);
    }

    JsonObject inspectInitial(JsonObject payload, UUID owner, boolean surveyed) {
        return inspectInitial(payload, owner, surveyed, false);
    }

    JsonObject inspectInitial(JsonObject payload, UUID owner, boolean surveyed, boolean autonomous) {
        requireSupplyOwner(owner);
        InitialBuild initial = validateInitial(payload, true, surveyed, autonomous);
        JsonArray changes = new JsonArray();
        var context = new java.util.TreeSet<>(BuildPlan.BUILD_ORDER);
        int keep = 0;
        int replace = 0;
        int remove = 0;
        Set<BlockPos> shown = new HashSet<>();
        Set<BlockPos> clearance = new HashSet<>();
        initial.clearance().forEach(step -> clearance.addAll(step.affected()));
        for (WorkStep step : initial.placements()) {
            shown.add(step.pos());
            boolean retained = step.before() == step.after();
            if (retained) {
                keep++;
            }
            JsonObject row = new JsonObject();
            row.add("local", Wire.position(step.target().local()));
            row.add("world", Wire.position(step.pos()));
            String obstacle = initial.obstacles().get(step.pos());
            boolean replacement = !retained && obstacle == null && clearance.contains(step.pos());
            if (replacement) {
                replace++;
            }
            row.addProperty("category", obstacle != null ? "conflict" : retained ? "keep" : replacement ? "replace" : "add");
            if (obstacle != null) {
                row.addProperty("reason", obstacle);
            }
            row.add("actual", Wire.state(level.getBlockState(step.pos())));
            row.add("desired", Wire.state(step.after()));
            if (initial.plan().terrain() != null && initial.plan().rotation() != Rotation.NONE) {
                row.add("local_actual", Wire.state(level.getBlockState(step.pos()).rotate(initial.plan().inverseRotation())));
                row.add("local_desired", Wire.state(step.after().rotate(initial.plan().inverseRotation())));
            }
            changes.add(row);
            for (Direction side : Direction.values()) {
                context.add(step.pos().relative(side));
            }
        }
        for (BlockPos pos : clearance.stream().sorted(BuildPlan.BUILD_ORDER).toList()) {
            if (!shown.contains(pos)) {
                JsonObject row = new JsonObject();
                row.add("world", Wire.position(pos));
                row.add("local", Wire.position(initial.plan().localPosition(pos)));
                row.addProperty("category", "remove");
                row.add("actual", Wire.state(level.getBlockState(pos)));
                row.add("desired", Wire.state(AIR));
                changes.add(row);
                remove++;
            }
            for (Direction side : Direction.values()) {
                context.add(pos.relative(side));
            }
        }
        for (var entry : initial.obstacles().entrySet()) {
            if (!shown.contains(entry.getKey())) {
                BlockPos pos = entry.getKey();
                JsonObject row = new JsonObject();
                row.add("world", Wire.position(pos));
                row.add("local", Wire.position(WorkStep.clear(initial.plan(), pos, AIR).target().local()));
                row.addProperty("category", "conflict");
                row.addProperty("reason", entry.getValue());
                row.add("actual", Wire.state(level.getBlockState(pos)));
                row.add("desired", Wire.state(AIR));
                changes.add(row);
            }
        }
        JsonArray neighbors = new JsonArray();
        for (BlockPos pos : context) {
            JsonObject row = new JsonObject();
            row.add("world", Wire.position(pos));
            row.add("actual", Wire.state(level.getBlockState(pos)));
            neighbors.add(row);
        }
        JsonObject counts = new JsonObject();
        counts.addProperty("keep", keep);
        counts.addProperty("add", changes.size() - keep - replace - remove - initial.obstacles().size());
        counts.addProperty("remove", remove);
        counts.addProperty("replace", replace);
        counts.addProperty("conflict", initial.obstacles().size());
        JsonObject report = new JsonObject();
        report.add("new_version", initial.plan().versionInfo());
        report.add("approved_area", site.area().json());
        report.addProperty("dimension", level.dimension().location().toString());
        if (site.worldCopy()) {
            report.addProperty("authorization", site.scope().toString());
            report.add("covered_grass", BuildTerrain.coveredGrass(level, site, initial.plan(), level::getBlockState));
        }
        report.add("counts", counts);
        report.add("changes", changes);
        report.add("neighbors", neighbors);
        if (!initial.clearance().isEmpty()) {
            JsonObject preparation = new JsonObject();
            preparation.addProperty("order", initial.strategy());
            JsonArray planned = new JsonArray();
            Map<BlockPos, BlockState> states = new LinkedHashMap<>();
            for (WorkStep step : initial.clearance()) {
                planned.add(step.json("pending"));
                step.affected().forEach(pos -> states.put(pos, level.getBlockState(pos)));
            }
            preparation.add("actions", planned);
            preparation.add("counts", Clearance.counts(states));
            report.add("preparation", preparation);
        }
        if (site.survival()) {
            report.add("supply", supplySummary(owner, initial.steps(), 0));
        }
        if (initial.plan().terrain() != null) {
            addTerrainReport(report, initial.plan(), clearance);
        }
        return report;
    }

    JsonObject propose(UUID baseId, JsonObject payload) {
        return propose(baseId, payload, null);
    }

    JsonObject propose(UUID baseId, JsonObject payload, UUID owner) {
        requireWritable();
        requireCurrent(baseId);
        requireCurrentWarehouse(owner);
        if (candidate != null) {
            if (candidate.payload().equals(payload)) {
                return change();
            }
            throw Wire.conflict("change_pending", "Resolve the pending review first");
        }
        BuildPlan next = validateRevision(baseId, payload);
        pause("change_pending");
        if (current.supply != null) {
            current.supply.phase = "paused";
        }
        candidate = new Registration(next, payload.deepCopy(), "proposed");
        registered.put(next.id(), candidate);
        persist();
        return change();
    }

    BuildPlan validateRevision(UUID baseId, JsonObject payload) {
        requireWritable();
        requireCurrent(baseId);
        if (!current.state.active() && current.state != State.COMPLETED) {
            throw Wire.conflict("job_finished", "Only the current running, paused, or completed placement can be changed");
        }
        BuildPlan next = BuildPlan.read(payload, site, palette, true);
        if ((current.plan.terrain() != null || next.terrain() != null)
                && (!java.util.Objects.equals(current.plan.terrain(), next.terrain())
                    || !current.plan.byPosition().keySet().equals(next.byPosition().keySet()) || current.preparing())) {
            throw Wire.conflict("terrain_amendment_scope", "开工后只能在清障完成后修改原目标格的材料或状态，沿用基础和开挖范围。");
        }
        requireUnused(next.id());
        if (!current.plan.samePlacement(next)) {
            throw Wire.invalid("A revision must keep the building ID, origin, and rotation");
        }
        if (next.version() <= current.plan.version()) {
            throw Wire.conflict("stale_version", "A candidate must advance the active building version");
        }
        requireImmutableVersion(next);
        site.requireLoaded(level);
        return next;
    }

    JsonObject change() {
        if (candidate == null) {
            JsonObject response = new JsonObject();
            response.addProperty("pending", false);
            return response;
        }
        JsonObject response = review().report();
        response.addProperty("pending", true);
        return response;
    }

    JsonObject inspectTerrainRevision(UUID base, JsonObject plan, UUID owner) {
        requireCurrentWarehouse(owner);
        return review(validateRevision(base, plan)).report();
    }

    JsonObject approve(UUID id, UUID owner) {
        long approvalStarted = BuildPerformance.clock();
        requireWritable();
        if (site.survival()) {
            requireCurrentWarehouse(owner);
        }
        if (candidate == null && current != null && current.plan.id().equals(id)) {
            return status(false);
        }
        requireCandidate(id);
        ChangeReview check = review();
        if (check.conflicts() != 0) {
            throw Wire.conflict("change_conflicts", "方案会碰到保护资产或超出施工范围。请调整设计后再批准。");
        }

        requireCheckpointWorker();
        worker.halt();
        prepare(check.steps());
        BuildPlan next = candidate.plan();
        UUID predecessor = current.plan.id();
        SiteSnapshot snapshot = current.snapshot;
        var scaffolds = new LinkedHashMap<>(current.scaffolds);
        var previousSupply = current.supply;
        var previous = current;
        List<WorkStep> steps = check.steps();
        if (next.terrain() != null) {
            var amended = new ArrayList<>(previous.steps.stream().filter(WorkStep::clearance).toList());
            amended.addAll(steps);
            steps = amended;
        }
        snapshot.track(next);
        snapshot.track(check.steps());
        archiveCurrent(next.id());
        current = new ConstructionJob(next, worker.id(), predecessor, check.report(), snapshot, steps);
        if (next.terrain() != null) {
            current.autonomous = previous.autonomous;
            current.cursor = steps.size() - check.steps().size();
            for (var row : previous.records) {
                var receipt = row.getAsJsonObject();
                if ("clear".equals(receipt.get("operation").getAsString())) {
                    current.records.add(receipt.deepCopy());
                    if ("removed".equals(receipt.get("status").getAsString())) current.removed++;
                }
            }
        }
        current.supply = site.survival() ? new SupplyCheckpoint(owner) : null;
        if (current.supply != null && previousSupply != null) {
            current.supply.temporary.putAll(previousSupply.temporary);
            current.supply.heatedFurnace = previousSupply.heatedFurnace;
            current.supply.heat = previousSupply.heat;
        }
        updateSupplyArea();
        current.scaffolds.putAll(scaffolds);
        worker.useScaffolds(current.scaffolds, next.byPosition().keySet(), next.terrain() != null);
        checkSupply();
        startPerformance(approvalStarted);
        registered.put(id, candidate.withDisposition("approved"));
        candidate = null;
        schedule();
        persist();
        return status(false);
    }

    JsonObject reject(UUID id) {
        requireWritable();
        requireCandidate(id);
        registered.put(id, candidate.withDisposition("rejected"));
        candidate = null;
        current.reason = "change_rejected";
        persist();
        return status(false);
    }

    private ChangeReview review() {
        return review(candidate.plan());
    }

    private ChangeReview review(BuildPlan next) {
        site.requireLoaded(level);
        ChangeReview check = ChangeReview.inspect(level, site, current.plan, next, current.snapshot, step -> {
            if (next.terrain() != null && step.remove() && !current.snapshot.owns(step.pos())) {
                throw Wire.conflict("terrain_clearance_changed", "现场有未批准的新增障碍。");
            }
            requireSafeMutation(step, level.getBlockState(step.pos()), Set.of());
        });
        if (current.supply != null) {
            check.report().add("supply", supplySummary(current.supply.owner, check.steps(), 0));
        }
        if (next.terrain() != null) {
            addTerrainReport(check.report(), next, current.clearancePositions);
            if (next.terrain().landings().stream().anyMatch(pos -> !TerrainDesign.plannedStandable(
                    level, next, current.clearancePositions, pos))) {
                throw Wire.conflict("terrain_landing_unusable", "改图后批准的站位失去支撑或净空。");
            }
        }
        return check;
    }

    private void addTerrainReport(JsonObject report, BuildPlan plan, Set<BlockPos> cleared) {
        report.add("terrain", plan.terrain().facts(level, site, plan, cleared));
        JsonObject approved = new JsonObject();
        for (String key : List.of("changes", "terrain", "covered_grass", "preparation")) {
            if (report.has(key)) approved.add(key, report.get(key));
        }
        report.addProperty("terrain_sha256", Wire.sha256(approved));
    }

    boolean warehouseApproved() {
        return !warehouses.isEmpty();
    }

    boolean siteLoaded() {
        try {
            site.requireLoaded(level);
            return true;
        } catch (Wire.Problem problem) {
            return false;
        }
    }

    void revokeWarehouse(ServerPlayer player) {
        requireWritable();
        warehouses.remove(player.getUUID());
        if (ownsSupply(player.getUUID()) && site.area().contains(BlockPos.containing(
                worker.position() == null ? Vec3.atCenterOf(site.spawn()) : worker.position()))) {
            updateSupplyArea();
        }
        if (ownsSupply(player.getUUID()) && current.state == State.RUNNING
                && current.cursor < current.steps.size() && !worker.hasSupply(current.steps.get(current.cursor))) {
            pause("awaiting_materials");
        }
        persist();
    }

    void authorizeWarehouse(ServerPlayer player, BlockPos center) {
        requireWritable();
        if (!site.survival()) {
            return;
        }
        if (center.distSqr(player.blockPosition()) > 16 * 16) {
            throw Wire.invalid("请在要共享的容器附近告诉我材料已备好。");
        }
        UUID owner = player.getUUID();
        warehouses.put(owner, new Warehouse.Source(owner, center.immutable()));
        if (ownsSupply(owner)) {
            updateSupplyArea();
        }
        if (ownsSupply(owner) && current.state == State.PAUSED && candidate == null
                && Set.of("awaiting_materials", "warehouse_unreachable", "scaffold_material").contains(current.reason)) {
            requireCheckpointWorker();
            if (current.planning.started()) {
                current.planning.resetAttempts();
                current.planning.invalidate("materials_changed");
                if (current.planning.action() == null) {
                    requestBrain("materials_changed");
                }
            } else {
                worker.halt();
                checkSupply();
                schedule();
            }
        }
        persist();
    }

    private void updateSupplyArea() {
        Warehouse.Source source = current.supply == null ? null : warehouses.get(current.supply.owner);
        worker.useSupplyArea(source == null ? null : source.pos());
        worker.useWorkshop(current == null ? null : current.supply);
    }

    private void requireSupplyOwner(UUID owner) {
        if (site.survival() && (owner == null || site.architect() == null || !site.architect().players().contains(owner))) {
            throw new Wire.Problem(403, "warehouse_player_approval", "需要由授权玩家在游戏中批准生存施工。");
        }
    }

    private boolean ownsSupply(UUID owner) {
        return current != null && current.supply != null && current.supply.owner.equals(owner);
    }

    private void requireCurrentWarehouse(UUID owner) {
        requireSupplyOwner(owner);
        if (site.survival() && !ownsSupply(owner)) {
            throw Wire.conflict("warehouse_job_scope", "当前任务属于另一位玩家。");
        }
    }

    JsonObject moddedMaterials() {
        return BuildPlan.moddedMaterials(palette);
    }

    JsonObject materials(UUID owner) {
        if (!site.survival()) {
            JsonObject report = new JsonObject();
            report.addProperty("mode", "preset");
            return report;
        }
        if (!ownsSupply(owner)) {
            return supplySummary(owner, List.of(), 0);
        }
        List<WorkStep> remaining = current.steps.subList(current.cursor, current.steps.size());
        if (current.needsScaffoldDirt) {
            remaining = new ArrayList<>(remaining);
            remaining.addAll(scaffoldDirt());
        }
        JsonObject report = supplySummary(owner, remaining, 0);
        if (Set.of("warehouse_unreachable", "workstation_unreachable", "workshop_inventory")
                .stream().anyMatch(reason -> reason.equals(current.reason))) {
            report.addProperty("reason", current.reason);
        }
        return report;
    }

    private JsonObject supplySummary(UUID owner, List<WorkStep> steps, int cursor) {
        Warehouse.Source source = owner == null ? null : warehouses.get(owner);
        boolean sameWorker = current == null || ownsSupply(owner);
        if (ownsSupply(owner)) {
            worker.attach(current.npc);
        }
        var stations = steps.isEmpty() ? Set.<CraftingAssistant.Station>of()
                : worker.stations(source == null ? null : source.pos());
        var workshop = sameWorker && current != null ? current.supply : null;
        int heat = workshop != null && workshop.heatedFurnace != null && level.hasChunkAt(workshop.heatedFurnace)
                && level.getBlockState(workshop.heatedFurnace).is(Blocks.FURNACE) ? workshop.heat : 0;
        JsonObject summary = Warehouse.summary(steps, cursor, sameWorker ? worker.inventory() : null,
                Warehouse.scan(level, site, source), worker.recipes(), stations, heat);
        summary.addProperty("mode", "survival");
        summary.addProperty("warehouse_approved", source != null);
        return summary;
    }

    private void checkSupply() {
        if (current.supply == null || current.cancelling) {
            current.state = State.RUNNING;
            current.reason = null;
            return;
        }
        JsonObject stock = materials(current.supply.owner);
        boolean missing = Warehouse.missing(stock);
        boolean canClear = current.preparing() && current.steps.subList(current.cursor, current.steps.size())
                .stream().takeWhile(WorkStep::clearance).allMatch(step -> {
                    Item need = Warehouse.item(step);
                    JsonObject row = stock.getAsJsonObject("requirements").getAsJsonObject(Warehouse.id(need));
                    return Warehouse.optionalTool(step) || need == net.minecraft.world.item.Items.AIR
                            || row != null && row.get("missing").getAsInt() == 0;
                });
        if (missing && !canClear) {
            pause("awaiting_materials");
            current.supply.phase = "supply_check";
            if (current.scaffolds.isEmpty()) {
                var stocks = Warehouse.scan(level, site, warehouses.get(current.supply.owner));
                if (!stocks.isEmpty()) {
                    pendingStockVisit = stocks.get(0).pos();
                    stockVisitUntil = level.getGameTime() + 160;
                }
            }
        } else {
            pendingStockVisit = null;
            current.state = State.RUNNING;
            current.reason = null;
            current.supply.phase = missing ? "preparing" : "ready";
        }
    }

    private List<WorkStep> scaffoldDirt() {
        var target = new BuildPlan.Target(BlockPos.ZERO, site.spawn(), Blocks.DIRT.defaultBlockState(),
                net.minecraft.world.item.Items.DIRT);
        int height = current.plan.worldBounds().max().getY() - current.plan.worldBounds().min().getY();
        return java.util.Collections.nCopies(Math.max(1, Math.min(48, height)), new WorkStep(target, AIR, false));
    }

    private void resupply(List<WorkStep> steps, int cursor) {
        current.stepStarted = -1;
        SupplyCheckpoint supply = current.supply;
        if (!supply.phase.equals("to_warehouse") && !supply.phase.equals("processing")) {
            worker.mentionSupply();
        }
        JsonObject transfer = worker.resupply(() -> Warehouse.scan(level, site, warehouses.get(supply.owner)),
                steps, cursor, current.supplyChoice);
        if (transfer != null) {
            transfer.addProperty("tick", level.getGameTime());
            if (transfer.has("crafted")) {
                supply.recipes.add(transfer);
                supply.phase = "processing";
                current.performance.event("recipe_batch");
            } else {
                supply.trips.add(transfer);
                supply.phase = "to_site";
                current.performance.event("warehouse_transfer");
            }
            schedule();
        } else {
            supply.phase = worker.activity().equals("processing_materials") ? "processing" : "to_warehouse";
            current.nextTick = level.getGameTime() + 4;
        }
        checkpoint();
    }

    private void requireCandidate(UUID id) {
        if (candidate == null || !candidate.plan().id().equals(id)) {
            throw new Wire.Problem(404, "change_not_found", "The ID is not the pending change");
        }
    }

    private void requireCurrent(UUID id) {
        if (current == null || !current.plan.id().equals(id)) {
            throw new Wire.Problem(404, "job_not_found", "The job ID is not the current job");
        }
    }

    private void requireUnused(UUID id) {
        if (registered.containsKey(id)) {
            throw Wire.conflict("plan_already_used", "This plan ID was already registered");
        }
    }

    private void requireImmutableVersion(BuildPlan plan) {
        for (Registration entry : registered.values()) {
            BuildPlan known = entry.plan();
            if (known.buildingId().equals(plan.buildingId()) && known.version() == plan.version()
                    && !known.modelHash().equals(plan.modelHash())) {
                throw Wire.conflict("immutable_version", "This building version already has a different model hash");
            }
        }
    }

    private void prepare(List<WorkStep> steps) {
        List<BuildPlan.Target> supplied = steps.stream().filter(step -> !step.remove() && step.before() != step.after())
                .map(WorkStep::target).toList();
        worker.prepare(supplied, steps.stream().anyMatch(WorkStep::remove));
    }

    private void startPerformance(long approvalStarted) {
        if (BuildPerformance.ENABLED) {
            worker.profile(current.performance);
            current.performance.begin(level.getGameTime(), worker.position());
        }
        if (current.preparing() && current.clearanceStarted == 0) {
            current.clearanceStarted = BuildPerformance.clock();
        }
        current.performance.span(BuildPerformance.Span.APPROVAL, approvalStarted);
    }

    void recordApprovalWait(long issued) {
        if (current != null) {
            current.performance.span(BuildPerformance.Span.APPROVAL_WAIT, issued);
        }
    }

    void endPerformanceTick(long serverTickStarted) {
        if (!BuildPerformance.ENABLED || current == null || !current.performance.measuring()) {
            return;
        }
        current.performance.span(BuildPerformance.Span.SERVER_TICK, serverTickStarted);
        current.performance.sample(level.getGameTime(), activity(), worker.position());
        if (!current.state.active()) {
            current.performance.stop();
        }
    }

    String activity() {
        if (current == null) {
            return "idle";
        }
        if (current.planning.waiting()) {
            return "planning_checkpoint";
        }
        if (current.planning.action() != null && current.planning.action().kind().equals("move_to")) {
            return "planning_move";
        }
        if (current.state == State.PAUSED) {
            return "paused:" + current.reason;
        }
        if (current.state != State.RUNNING) {
            return current.state.wire();
        }
        if (level.getGameTime() < current.nextTick) {
            return "scheduled_wait";
        }
        if (current.supply != null && current.supply.phase.equals("to_warehouse")) {
            return "to_warehouse";
        }
        String workerActivity = worker.activity();
        if (current.supply != null && current.supply.phase.equals("to_site") && workerActivity.equals("navigating")) {
            return "warehouse_return";
        }
        return workerActivity;
    }

    private void schedule() {
        current.nextTick = level.getGameTime() + ACTION_INTERVAL_TICKS;
    }

    private void scheduleAfter(WorkStep previous) {
        WorkStep next = current.steps.get(current.cursor);
        boolean adjacent = (current.plan.optimizedCottage() || current.plan.voxel()) && !previous.remove() && !next.remove()
                && next.before().isAir() && previous.pos().distSqr(next.pos()) <= 2;
        current.nextTick = level.getGameTime() + (adjacent ? NEARBY_PLACEMENT_INTERVAL_TICKS : ACTION_INTERVAL_TICKS);
    }

    JsonObject control(String operation, UUID id) {
        return control(operation, id, null);
    }

    JsonObject control(String operation, UUID id, UUID owner) {
        requireWritable();
        requireCurrent(id);
        if (operation.equals("resume") && current.autonomous) {
            requireAutonomy();
        }
        switch (operation) {
            case "pause" -> {
                if (current.state == State.RUNNING || current.state == State.PAUSED
                        && (current.planning.waiting() || current.planning.action() != null)) {
                    pause("paused_by_request");
                    persist();
                } else {
                    requireState(State.PAUSED);
                }
            }
            case "resume" -> {
                if (site.survival()) {
                    requireCurrentWarehouse(owner);
                }
                if (current.state == State.RUNNING && current.planning.waiting()) {
                    stopBrain("resumed_by_request");
                    schedule();
                    persist();
                } else if (current.state != State.RUNNING) {
                    if (!current.state.active() && current.state != State.FAILED) {
                        requireState(State.PAUSED);
                    }
                    if (candidate != null) {
                        throw Wire.conflict("change_pending", "请先处理待审阅的建筑方案。");
                    }
                    long recoveryStarted = BuildPerformance.clock();
                    site.requireLoaded(level);
                    requireCheckpointWorker();
                    if (current.planning.action() != null) {
                        worker.halt();
                    }
                    stopBrain("resumed_by_request");
                    checkSupply();
                    current.verification = null;
                    current.deferred = 0;
                    startPerformance(0);
                    current.performance.span(BuildPerformance.Span.RECOVERY, recoveryStarted);
                    schedule();
                    persist();
                }
            }
            case "cancel" -> {
                if (current.state != State.CANCELLED) {
                    if (!current.state.active()) {
                        throw Wire.conflict("job_finished", "Only a running or paused job can be cancelled");
                    }
                    if (candidate != null) {
                        registered.put(candidate.plan().id(), candidate.withDisposition("rejected"));
                        candidate = null;
                    }
                    worker.halt();
                    stopBrain("cancelled_by_request");
                    if (current.scaffolds.isEmpty() && (current.supply == null || current.supply.temporary.isEmpty())) {
                        finish(State.CANCELLED, "cancelled_by_request");
                    } else {
                        current.cancelling = true;
                        current.state = State.RUNNING;
                        current.reason = null;
                        schedule();
                    }
                    persist();
                }
            }
            default -> throw new IllegalArgumentException(operation);
        }
        ForgeBridge.LOGGER.info("construction_control {} {} tick={}", id, operation, level.getGameTime());
        return status(false);
    }

    private void requireCheckpointWorker() {
        if (!worker.attach(current.npc)) {
            throw Wire.conflict("checkpoint_npc_unavailable", "原施工 NPC 尚未加载。请回到施工区。");
        }
    }

    private void requireState(State state) {
        if (current.state != state) {
            throw Wire.conflict("invalid_job_state", "Job state is " + current.state.wire());
        }
    }

    private void pause(String reason) {
        pendingStockVisit = null;
        worker.halt();
        current.state = State.PAUSED;
        current.reason = reason;
        current.stepStarted = -1;
        current.approach = null;
        current.supplyChoice = null;
        current.performance.event(reason);
        boolean actionFailed = current.planning.action() != null;
        stopBrain(reason);
        if (current.planning.started() && (recoverableFailure() || scaffoldCleanupFailed()
                || reason.equals("worksite_replanned") || actionFailed && !Set.of("paused_by_request", "change_pending",
                        "visual_checkpoint", "autonomy_revoked", "journal_unavailable", "server_stopping").contains(reason))) {
            current.planning.request("action_failed");
        }
        if (current.supply != null) {
            current.supply.phase = "paused";
        }
    }

    void externalEdit(ServerLevel editedLevel, Entity entity, BlockPos pos) {
        if (editedLevel == level && current != null && !worker.isActor(entity)) {
            if (site.area().contains(pos)) {
                current.planning.invalidate("planning_world_changed");
                current.planning.resetAttempts();
                if (current.planning.started() && current.state == State.PAUSED && candidate == null
                        && current.planning.action() == null && (recoverableFailure() || scaffoldCleanupFailed()
                                || PLANNING_BLOCKED.equals(current.reason))) {
                    current.planning.request("site_changed");
                }
            }
            // Once a player takes over a temporary block, it is no longer ours to remove.
            current.scaffolds.remove(pos);
            if (current.supply != null) {
                current.supply.temporary.remove(pos);
                if (pos.equals(current.supply.heatedFurnace)) {
                    current.supply.heatedFurnace = null;
                    current.supply.heat = 0;
                }
            }
        }
    }

    void tick() {
        worker.tickLife((current == null || current.state != State.RUNNING
                && current.planning.action() == null && !current.planning.waiting()) && pendingStockVisit == null);
        if (journalBlocked || current == null) {
            return;
        }
        if (current.planning.expire(current)) {
            stopPlanningAtCheckpoint();
            persistAfterFailure();
        }
        if (current.state == State.PAUSED && pendingStockVisit != null
                && (level.getGameTime() >= stockVisitUntil || worker.inspectStock(pendingStockVisit))) {
            worker.halt();
            pendingStockVisit = null;
        }
        if (current.planning.waiting() || current.state != State.RUNNING && current.planning.action() == null
                || level.getGameTime() < current.nextTick) {
            return;
        }
        long tickStarted = BuildPerformance.clock();
        try {
            if (current.autonomous) {
                requireAutonomy();
            }
            site.requireLoaded(level);
            requireCheckpointWorker();
            if (!executeBrainAction()) {
                executeNext();
            }
        } catch (Wire.Problem problem) {
            if (!journalBlocked) {
                ConstructionPlanning.Action action = current.planning.action();
                if (action != null && action.kind().equals("move_to")) {
                    finishBrainAction(problem.code, movementEvidence());
                    return;
                }
                if (Set.of(SCAFFOLD_CLEANUP_UNREACHABLE, SCAFFOLD_CLEANUP_BLOCKED, "scaffold_cleanup_denied").contains(problem.code)) {
                    var cleanup = worker.scaffoldCleanup();
                    if (cleanup != null) {
                        current.lastFailure = scaffoldFailure(cleanup, problem.code);
                        current.lastFailure.addProperty("tick", level.getGameTime());
                        current.lastFailure.add("route", worker.routeEvidence(cleanup.pos()));
                    }
                } else if (current.cursor < current.steps.size()) {
                    WorkStep step = current.steps.get(current.cursor);
                    rememberFailure(step, problem.code, worker.routeEvidence(step.pos()));
                }
                pause(problem.code);
                if (current.supply != null && Set.of("awaiting_materials", "warehouse_unreachable").contains(problem.code)) {
                    current.supply.phase = "supply_check";
                }
                if (!Set.of("awaiting_materials", "warehouse_unreachable").contains(problem.code)) {
                    ForgeBridge.LOGGER.info("construction_paused {} {}", current.plan.id(), problem.code);
                }
                persistAfterFailure();
            }
        } catch (RuntimeException error) {
            ForgeBridge.LOGGER.error("Construction worker failed for job {}", current.plan.id(), error);
            pause("worker_error");
            persistAfterFailure();
        } finally {
            current.performance.span(BuildPerformance.Span.CONSTRUCTION_TICK, tickStarted);
        }
    }

    private void persistAfterFailure() {
        try {
            persist();
        } catch (Wire.Problem ignored) {
            // Keep the last durable checkpoint.
        }
    }

    private void executeNext() {
        if (SCAFFOLD_DESCENT.equals(current.approach)) {
            if (worker.cleanScaffolds(true)) {
                current.lastFailure = null;
                current.approach = null;
                schedule();
            }
            checkpoint();
            return;
        }
        if (current.cancelling || current.cursor == current.steps.size()) {
            if (!worker.cleanScaffolds() || !worker.cleanWorkshop() || !worker.cleanDoors()) {
                checkpoint();
                return;
            }
            if (!current.cancelling && current.plan.sourceProject() != null && !alignProjectJoins()) {
                return;
            }
            if (!current.cancelling && current.plan.lighting()) {
                long now = level.getGameTime();
                if (current.lightingSettleUntil == 0) {
                    current.lightingSettleUntil = now + 10;
                }
                if (now < current.lightingSettleUntil) {
                    return;
                }
            }
            finish(current.cancelling ? State.CANCELLED : State.COMPLETED,
                    current.cancelling ? "cancelled_by_request" : null);
            persist();
            return;
        }
        if (current.supplyChoice != null) {
            List<WorkStep> steps = current.needsScaffoldDirt ? scaffoldDirt() : current.steps;
            int cursor = current.needsScaffoldDirt ? 0 : current.cursor;
            if (supplyNeeded()) {
                resupply(steps, cursor);
            } else {
                current.supplyChoice = null;
                checkSupply();
                schedule();
                persist();
            }
            return;
        }
        if (current.needsScaffoldDirt && current.supply != null) {
            var dirt = scaffoldDirt();
            if (!worker.gatherScaffoldMaterial(dirt.size())) {
                checkpoint();
                return;
            }
            if (Warehouse.carried(worker.inventory()).getOrDefault(net.minecraft.world.item.Items.DIRT, 0) < dirt.size()) {
                resupply(dirt, 0);
                return;
            }
            current.needsScaffoldDirt = false;
        }
        preferReachablePlacement();
        WorkStep step = current.steps.get(current.cursor);
        if (!level.hasChunkAt(step.pos())) {
            throw Wire.conflict("site_not_loaded", "请走近下一段工地。");
        }
        if (current.scaffolds.containsKey(step.pos())) {
            if (!worker.cleanScaffolds()) {
                checkpoint();
                return;
            }
        }
        if (current.supply != null && current.supply.phase.equals("preparing") && !step.clearance()) {
            checkSupply();
            if (current.state != State.RUNNING) {
                persist();
                return;
            }
        }
        BlockState actual = level.getBlockState(step.pos());
        var following = current.plan.at(step.pos());
        ConstructionWorker.Result result;
        if ((step.remove() ? matches(actual, step.after()) : step.target().matches(actual) || installedDoorMatches(step, actual))
                || step.clearance() && following != null && following.matches(actual)) {
            result = new ConstructionWorker.Result("already_present", null, null);
            worker.halt();
        } else {
            if (!step.remove()) {
                if (step.target().upper() && actual.isAir()) {
                    deferStep(step, "project_pair_pending");
                    return;
                }
                if (!actual.isAir()) {
                    admitObstacle(step);
                    return;
                }
                if (step.target().paired() && !level.getBlockState(step.pos().above()).isAir()) {
                    admitObstacle(new WorkStep(current.plan.at(step.pos().above()), AIR, false));
                    return;
                }
            }
            WorkStep live = step.remove() && actual != step.before()
                    ? WorkStep.clear(current.plan, step.pos(), actual) : step;
            if (step.clearance() && current.plan.terrain() != null && actual.getBlock() != step.before().getBlock()) {
                throw Wire.conflict("terrain_clearance_changed", "待清理方块已变，请检查现场后再继续。");
            }
            requireSafeMutation(live, actual, Set.of());
            if (live != step) {
                replaceNext(live);
                step = live;
            }
            if (current.supply != null && !worker.hasSupply(step)) {
                resupply(current.steps, current.cursor);
                return;
            }
            if (current.stepStarted < 0) {
                current.stepStarted = level.getGameTime();
            }
            result = worker.act(step, current.approach);
        }
        if (result == null) {
            if (level.getGameTime() - current.stepStarted >= PLACEMENT_LIMIT_TICKS) {
                rememberFailure(step, "placement_stalled", worker.routeEvidence(step.pos()));
                pause("placement_stalled");
                persist();
                return;
            }
            checkpoint();
            return;
        }
        if (result.status().equals("deferred")) {
            rememberFailure(step, result.reason(), result.evidence());
            deferStep(step, result.reason());
            return;
        }
        JsonObject record = step.json(result.status());
        record.addProperty("step", current.cursor);
        record.addProperty("tick", level.getGameTime());
        record.add("before", Wire.state(actual));
        record.add("actual", Wire.state(level.getBlockState(step.pos())));
        if (result.reason() != null) {
            record.addProperty("reason", result.reason());
        }
        if (result.evidence() != null) {
            record.add("interaction_evidence", result.evidence());
        }
        if (BuildPerformance.ENABLED) {
            record.addProperty("profile_ms", current.performance.offsetMs());
            current.performance.action(result.status());
        }
        current.records.add(record);
        if (result.status().equals("failed")) {
            rememberFailure(step, result.reason(), worker.routeEvidence(step.pos()));
            if (current.supply != null && result.reason().equals("scaffold_material")) {
                current.needsScaffoldDirt = true;
                worker.halt();
                schedule();
            } else {
                pause(result.reason());
            }
            persist();
            return;
        }
        if (result.status().equals("placed") || result.status().equals("removed")) {
            current.snapshot.written(step);
            if (current.supply != null && !current.supply.phase.equals("preparing")) {
                current.supply.phase = "working";
            }
            if (step.remove()) {
                current.removed++;
            } else {
                current.placed++;
            }
        }
        current.cursor++;
        current.deferred = 0;
        current.stepStarted = -1;
        current.approach = null;
        if (step.clearance() && !current.preparing()) {
            current.performance.span(BuildPerformance.Span.CLEARANCE, current.clearanceStarted);
            current.clearanceStarted = 0;
        }
        if (current.cursor < current.steps.size()) {
            scheduleAfter(step);
        } else {
            schedule();
        }
        checkpoint();
    }

    private boolean installedDoorMatches(WorkStep step, BlockState actual) {
        return step.target().replica() && actual.getBlock() instanceof DoorBlock
                && current.snapshot.owns(step.target().upper() ? step.pos().below() : step.pos())
                && ProjectBlocks.alignDoorOpen(actual, step.after()) == step.after();
    }

    private void preferReachablePlacement() {
        var action = current.planning.action();
        if (current.plan.terrain() == null || action == null || !action.kind().equals("execute_group")
                || current.stepStarted >= 0 || current.deferred != 0 || current.approach != null) {
            return;
        }
        WorkStep first = current.steps.get(current.cursor);
        if (first.remove() || first.target().paired() || first.target().upper() || first.target().light() || worker.canPlaceHere(first)) {
            return;
        }
        int end = Math.min(action.end(), current.cursor + 96);
        for (int i = current.cursor + 1; i < end; i++) {
            WorkStep step = current.steps.get(i);
            if (step.remove() || step.pos().getY() != first.pos().getY() || step.target().light()) {
                break;
            }
            if (step.after() != first.after() || step.target().paired() || step.target().upper() || current.scaffolds.containsKey(step.pos())
                    || !site.canWorkAt(level, step.pos()) || !level.getBlockState(step.pos()).isAir()
                    || !BlockPos.containing(worker.position()).closerThan(step.pos(), 5) || !worker.canPlaceHere(step)) {
                continue;
            }
            // Preserve the model's group and layer. Finish visible placements from the current footing.
            List<WorkStep> queue = new ArrayList<>(current.steps);
            queue.add(current.cursor, queue.remove(i));
            current.steps = List.copyOf(queue);
            current.performance.event("reachable_placement_prioritized");
            return;
        }
    }

    static boolean matches(BlockState actual, BlockState wanted) {
        if (wanted.isAir()) {
            return actual.isAir();
        }
        var shape = net.minecraft.world.level.block.state.properties.BlockStateProperties.STAIRS_SHAPE;
        return actual == wanted || actual.is(wanted.getBlock()) && actual.hasProperty(shape)
                && wanted.hasProperty(shape) && actual.setValue(shape, wanted.getValue(shape)) == wanted;
    }

    private void replaceNext(WorkStep step) {
        List<WorkStep> queue = new ArrayList<>(current.steps);
        queue.set(current.cursor, step);
        current.steps = List.copyOf(queue);
        current.snapshot.track(List.of(step));
    }

    private void deferStep(WorkStep step, String reason) {
        current.planning.invalidate("worker_reordering");
        current.performance.event("deferred_step");
        List<WorkStep> early = new ArrayList<>(current.steps.subList(0, current.cursor));
        List<WorkStep> delayed = new ArrayList<>();
        List<WorkStep> above = new ArrayList<>();
        // Finish the lower placement layer before its roof, then install its light fixtures.
        ConstructionPlanning.Action action = current.planning.action();
        int end = action == null ? current.steps.size() : action.end();
        for (WorkStep pending : current.steps.subList(current.cursor, end)) {
            if (pending.pos().equals(step.pos())) {
                delayed.add(pending);
            } else if (!step.remove() && !pending.remove()
                    && (pending.target().light() && !step.target().light()
                    || pending.target().light() == step.target().light() && pending.pos().getY() > step.pos().getY())) {
                above.add(pending);
            } else {
                early.add(pending);
            }
        }
        early.addAll(delayed);
        early.addAll(above);
        current.deferred++;
        int band = early.size() - current.cursor - above.size();
        if (current.deferred >= 4 * band || current.stepStarted >= 0
                && level.getGameTime() - current.stepStarted >= PLACEMENT_LIMIT_TICKS) {
            pause(reason);
            persist();
            return;
        }
        early.addAll(current.steps.subList(end, current.steps.size()));
        current.steps = List.copyOf(early);
        current.approach = null;
        current.nextTick = level.getGameTime() + (current.deferred % band == 0 ? 30 : ACTION_INTERVAL_TICKS);
        checkpoint();
    }

    private void rememberFailure(WorkStep step, String reason, JsonObject evidence) {
        current.lastFailure = briefStep(step);
        current.lastFailure.addProperty("reason", reason);
        current.lastFailure.addProperty("tick", level.getGameTime());
        if (evidence != null && evidence.size() > 0) {
            current.lastFailure.add("route", evidence);
        }
    }

    private boolean reopenAccess(WorkStep step) {
        if (step.remove() || current.plan.localized()) {
            return false;
        }
        List<WorkStep> removals = new ArrayList<>();
        List<WorkStep> replacements = new ArrayList<>();
        for (BlockPos pos = step.pos().above(); site.canWorkAt(level, pos); pos = pos.above()) {
            BuildPlan.Target cover = current.plan.at(pos);
            BlockState state = level.getBlockState(pos);
            if (state.isAir()) {
                break;
            }
            if (cover == null || !current.snapshot.owns(pos) || !matches(state, cover.state())) {
                return false;
            }
            WorkStep remove = new WorkStep(cover, state, true);
            try {
                requireSafeMutation(remove, state, Set.of());
            } catch (Wire.Problem risk) {
                return false;
            }
            removals.add(remove);
            replacements.add(new WorkStep(cover, AIR, false));
        }
        if (removals.isEmpty()) {
            return false;
        }
        java.util.Collections.reverse(removals);
        List<WorkStep> queue = new ArrayList<>(current.steps.subList(0, current.cursor));
        queue.addAll(removals);
        queue.add(step);
        queue.addAll(replacements);
        for (WorkStep pending : current.steps.subList(current.cursor, current.steps.size())) {
            if (pending != step) {
                queue.add(pending);
            }
        }
        current.steps = List.copyOf(queue);
        current.deferred = 0;
        worker.halt();
        schedule();
        checkpoint();
        return true;
    }

    private void admitObstacle(WorkStep pending) {
        if (current.plan.terrain() != null) {
            throw Wire.conflict("terrain_clearance_changed", "出现了未批准的障碍，请先检查现场。");
        }
        Clearance check = new Clearance(level, site);
        Clearance.Finding finding = check.inspect(pending.pos());
        if (finding.risk() != null || finding.cells().isEmpty()) {
            throw Wire.conflict(finding.risk() == null ? "unsafe_interaction" : finding.risk(),
                    "这个位置涉及保护资产，请调整方案或在现场查看。");
        }
        List<WorkStep> inserted = clearanceSteps(current.plan, check.states(finding), "route_first");
        List<WorkStep> queue = new ArrayList<>(current.steps);
        queue.addAll(current.cursor, inserted);
        current.steps = List.copyOf(queue);
        current.snapshot.track(inserted);
        current.clearancePositions.addAll(finding.cells());
        current.planning.invalidate("worksite_replanned");
        current.clearanceReplans++;
        worker.halt();
        if (current.planning.action() != null) {
            pause("worksite_replanned");
            persist();
            return;
        }
        schedule();
        checkpoint();
    }

    private void requirePlacementSite(WorkStep step) {
        BlockPos target = step.pos();
        if (!site.canWorkAt(level, target)) {
            throw Wire.conflict("outside_approved_area", "目标超出施工区。");
        }
        if (site.worldCopy() && !site.area().contains(target.below())
                && BuildTerrain.coversGrass(level, target, step.after(), level.getBlockState(target.below()))) {
            throw Wire.conflict("terrain_effect_outside_scope", "这里需要把地基也纳入施工区。");
        }
    }

    private void requireSafeMutation(WorkStep step, BlockState actual, Set<BlockPos> group) {
        if (!step.remove()) {
            requirePlacementSite(step);
        }
        var clearance = new Clearance(level, site);
        Set<BlockPos> together = group.isEmpty() ? Set.copyOf(step.affected()) : group;
        String risk = clearance.mutationRisk(step.pos(), actual, step.remove(), together);
        BlockPos secondary = step.secondary();
        if (risk == null && step.remove() && secondary != null && level.getBlockState(secondary).is(actual.getBlock())) {
            risk = clearance.mutationRisk(secondary, level.getBlockState(secondary), true, together);
        }
        if (risk != null) {
            throw Wire.conflict(risk, "这个位置涉及保护资产、危险地形或施工区边界。");
        }
    }

    private void checkpoint() {
        if (level.getGameTime() >= nextCheckpoint) {
            persist();
        }
    }

    private void finish(State state, String reason) {
        current.verification = state == State.COMPLETED ? verify() : null;
        worker.halt();
        current.state = state;
        current.reason = reason;
        stopBrain(reason == null ? state.wire() : reason);
        if (state == State.COMPLETED && !current.verification.get("passed").getAsBoolean()) {
            current.state = State.FAILED;
            current.reason = "world_verification_failed";
        }
        if (current.state == State.COMPLETED) {
            if (current.plan.lighting()) {
                completedLighting.put(current.plan.buildingId(), current.plan);
            } else {
                completedLighting.remove(current.plan.buildingId());
            }
            worker.celebrate(current.plan.worldBounds());
        }
        if (current.supply != null) {
            current.supply.phase = current.state.wire();
        }
        ForgeBridge.LOGGER.info("construction_finish {} state={} reason={}", current.plan.id(), current.state.wire(), current.reason);
    }

    private boolean alignProjectJoins() {
        var targets = current.plan.targets();
        int end = Math.min(targets.size(), current.replicaJoinCursor + 128);
        for (; current.replicaJoinCursor < end; current.replicaJoinCursor++) {
            var target = targets.get(current.replicaJoinCursor);
            BlockPos pos = target.world();
            if (!current.snapshot.owns(pos) || !site.canWorkAt(level, pos)) {
                continue;
            }
            BlockState actual = level.getBlockState(pos);
            if (target.paired() && actual != target.state() && actual.getBlock() instanceof DoorBlock door
                    && ProjectBlocks.alignDoorOpen(actual, target.state()) == target.state()) {
                BlockPos above = pos.above();
                var top = current.plan.at(above);
                if (top != null && site.canWorkAt(level, above)
                        && ProjectBlocks.alignDoorOpen(level.getBlockState(above), top.state()) == top.state()
                        && level.isUnobstructed(target.state(), pos, CollisionContext.empty())
                        && level.isUnobstructed(top.state(), above, CollisionContext.empty())) {
                    door.setOpen(null, level, actual, pos, target.state().getValue(DoorBlock.OPEN));
                }
                continue;
            }
            BlockState aligned = ProjectBlocks.alignJoins(actual, target.state());
            if (aligned != actual && level.isUnobstructed(aligned, pos, CollisionContext.empty())) {
                level.setBlock(pos, aligned, net.minecraft.world.level.block.Block.UPDATE_CLIENTS
                        | net.minecraft.world.level.block.Block.UPDATE_KNOWN_SHAPE);
            }
        }
        return end == targets.size();
    }

    private JsonObject verify() {
        JsonArray states = new JsonArray();
        JsonArray mismatches = new JsonArray();
        int matched = 0;
        Set<BlockPos> inspected = current.plan.localized() && current.predecessor != null
                ? current.steps.stream().map(WorkStep::pos).collect(java.util.stream.Collectors.toSet()) : null;
        TerrainDesign terrain = current.plan.terrain();
        int foundationMatched = 0;
        for (BuildPlan.Target target : current.plan.targets()) {
            if (inspected != null && !inspected.contains(target.world()) && !level.hasChunkAt(target.world())) {
                continue;
            }
            if (!level.hasChunkAt(target.world())) {
                throw Wire.conflict("site_not_loaded", "请加载建筑所在区块，以便验收。");
            }
            BlockState actual = level.getBlockState(target.world());
            JsonObject row = difference(target.world(), target.state(), actual);
            states.add(row);
            if (current.plan.sourceProject() != null || terrain != null ? actual == target.state() : matches(actual, target.state())) {
                matched++;
                if (terrain != null && terrain.foundation().contains(target.world())) foundationMatched++;
            } else if (inspected == null || inspected.contains(target.world())) {
                mismatches.add(row);
            }
        }
        JsonObject data = new JsonObject();
        data.addProperty("passed", mismatches.isEmpty());
        data.addProperty("matched", matched);
        data.add("target_mismatches", mismatches);
        data.add("target_states", states);
        if (terrain != null) {
            JsonArray cuts = new JsonArray();
            boolean clean = true;
            for (BlockPos pos : current.clearancePositions.stream().sorted(BuildPlan.BUILD_ORDER).toList()) {
                BlockState actual = level.getBlockState(pos);
                BlockState expected = current.plan.at(pos) == null ? AIR : current.plan.at(pos).state();
                cuts.add(difference(pos, expected, actual));
                clean &= matches(actual, expected);
            }
            var access = new JsonArray();
            boolean connected = true;
            for (BlockPos feet : terrain.landings()) {
                JsonObject landing = new JsonObject();
                landing.add("local", Wire.position(current.plan.localPosition(feet)));
                boolean reachable = worker.canNavigateTo(feet);
                landing.addProperty("reachable", reachable);
                access.add(landing);
                connected &= reachable;
            }
            data.addProperty("foundation_matched", foundationMatched);
            data.addProperty("foundation_total", terrain.foundation().size());
            data.add("cleared_states", cuts);
            data.add("access", access);
            data.addProperty("passed", mismatches.isEmpty() && clean && connected && current.scaffolds.isEmpty());
        }
        if (current.plan.lighting()) {
            JsonArray dark = new JsonArray();
            int checked = 0;
            for (BuildPlan.Target target : current.plan.targets()) {
                BlockPos feet = target.world().above();
                if (target.light() || !spawnSurface(feet, current.plan)) {
                    continue;
                }
                checked++;
                if (level.getBrightness(LightLayer.BLOCK, feet) == 0) {
                    dark.add(Wire.position(feet));
                }
            }
            JsonObject lighting = new JsonObject();
            lighting.addProperty("surfaces_checked", checked);
            lighting.add("dark_cells", dark);
            data.add("lighting", lighting);
        }
        return data;
    }

    private boolean spawnSurface(BlockPos feet, BuildPlan plan) {
        if (!site.area().contains(feet)) {
            return false;
        }
        BlockPos floor = feet.below();
        BuildPlan.Target support = plan.at(floor);
        if (support == null || !matches(level.getBlockState(floor), support.state())) {
            return false;
        }
        return support.state().isFaceSturdy(level, floor, Direction.UP)
                && level.getBlockState(feet).getCollisionShape(level, feet).isEmpty()
                && level.getBlockState(feet.above()).getCollisionShape(level, feet.above()).isEmpty();
    }

    void protectNaturalSpawns(MobSpawnEvent.PositionCheck event) {
        if (event.getLevel() != level || event.getSpawnType() != MobSpawnType.NATURAL
                || event.getEntity().getType().getCategory() != MobCategory.MONSTER) {
            return;
        }
        BlockPos feet = BlockPos.containing(event.getX(), event.getY(), event.getZ());
        for (BuildPlan plan : completedLighting.values()) {
            if (plan.at(feet.below()) != null && spawnSurface(feet, plan)) {
                event.setResult(net.minecraftforge.eventbus.api.Event.Result.DENY);
                return;
            }
        }
    }

    private static JsonObject difference(BlockPos pos, BlockState expected, BlockState actual) {
        JsonObject difference = new JsonObject();
        difference.add("world", Wire.position(pos));
        difference.add("expected", Wire.state(expected));
        difference.add("actual", Wire.state(actual));
        return difference;
    }

    ConstructionWorker beginSurvey() {
        requireWritable();
        if (!site.worldCopy() || current != null && current.state.active()) {
            throw Wire.conflict("survey_construction_active", "请在无运行中施工的获准世界副本内观察。");
        }
        site.requireLoaded(level);
        UUID selected = current == null ? surveyNpc : current.npc;
        if (selected != null && !worker.attach(selected)) {
            throw Wire.conflict("observation_npc_unavailable", "原 NPC 尚未加载或已经离开授权区。");
        }
        worker.startSurvey();
        if (current == null && surveyNpc == null) {
            surveyNpc = worker.id();
            persist();
        }
        return worker;
    }

    FakePlayerEntity observationTarget() {
        UUID npc = current == null ? surveyNpc : current.npc;
        if (npc == null || !(level.getEntity(npc) instanceof FakePlayerEntity target)
                || !target.isAlive() || !target.getTags().contains(ForgeBridge.MOD_ID)
                || !site.area().contains(target.blockPosition()) || !level.hasChunkAt(target.blockPosition())) {
            throw Wire.conflict("observation_npc_unavailable", "没有可观察的已加载施工 NPC。");
        }
        return target;
    }

    BuildSite.Box observationArea() {
        return site.area();
    }

    UUID currentJobId() {
        return current == null ? null : current.plan.id();
    }

    boolean terrainJob() {
        return current != null && current.plan.terrain() != null;
    }

    boolean observationCheckpoint(UUID id) {
        return id != null && id.equals(currentJobId()) && current.state != State.RUNNING
                && current.planning.action() == null;
    }

    record CheckpointView(BlockPos feet, BlockPos focus) {}

    CheckpointView checkpointView(CheckpointView previous) {
        var npc = observationTarget();
        site.requireLoaded(level);
        var built = current.plan.targets().stream()
                .filter(target -> level.getBlockState(target.world()) == target.state()).toList();
        var windows = built.stream().filter(target -> target.state().is(Blocks.GLASS)
                || target.state().is(Blocks.BLUE_STAINED_GLASS)
                || target.state().is(Blocks.LIGHT_BLUE_STAINED_GLASS)).toList();
        var subjects = windows.isEmpty() ? built : windows;
        BlockPos feet = npc.blockPosition();
        for (BlockPos point : BlockPos.withinManhattan(feet, 4, 1, 4)) {
            if (!feet.closerThan(point, 4.1) || !site.area().contains(point)
                    || !site.area().contains(point.above()) || !BuildTerrain.canStand(level, point)) {
                continue;
            }
            // The installed pane should have a direct line of sight and room in the frame.
            Vec3 eye = point.equals(feet) ? npc.getEyePosition() : Vec3.atBottomCenterOf(point).add(0, npc.getEyeHeight(), 0);
            for (BuildPlan.Target target : subjects) {
                var view = new CheckpointView(point.immutable(), target.world());
                Vec3 center = Vec3.atCenterOf(view.focus());
                double distance = eye.distanceToSqr(center);
                if (view.equals(previous) || distance < 2.8 || distance > 42) {
                    continue;
                }
                var hit = level.clip(new ClipContext(eye, center, ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE, npc));
                if (hit.getType() == HitResult.Type.BLOCK && hit.getBlockPos().equals(view.focus())) {
                    return view;
                }
            }
        }
        return null;
    }

    ConstructionWorker checkpointWorker() {
        requireWritable();
        if (!observationCheckpoint(currentJobId()) || !worker.attach(current.npc)) {
            throw Wire.conflict("visual_checkpoint_changed", "施工观察已失效，原 NPC 无法安全复查。");
        }
        worker.startSurvey();
        return worker;
    }

    Boolean verifiedFinishedSite(UUID id) {
        if (!id.equals(currentJobId()) || current.state != State.COMPLETED) {
            return null;
        }
        site.requireLoaded(level);
        return verify().get("passed").getAsBoolean();
    }

    String visualEvent(UUID job, boolean checkpoint, boolean failure, boolean finalReview) {
        if (!job.equals(currentJobId()) || journalBlocked) {
            return null;
        }
        if (!checkpoint && current.state == State.RUNNING && current.cursor * 2 >= current.steps.size()
                && current.cursor < current.steps.size()) {
            pause("visual_checkpoint");
            persist();
            return "checkpoint";
        }
        if (!failure && (current.state == State.FAILED || current.state == State.PAUSED
                && current.reason != null && !Set.of("visual_checkpoint", "paused_by_request", "change_pending",
                        "change_rejected", "server_stopping", "awaiting_materials", "warehouse_unreachable").contains(current.reason))) {
            return "failure";
        }
        return !finalReview && current.state == State.COMPLETED ? "final" : null;
    }

    boolean offerPlanning(UUID job) {
        return !journalBlocked && candidate == null && job != null && job.equals(currentJobId())
                && !current.cancelling
                && (!current.autonomous || autonomousAvailable())
                && (current.planning.waiting() || current.state == State.RUNNING && current.cursor < current.steps.size()
                    && current.supplyChoice == null && !SCAFFOLD_DESCENT.equals(current.approach)
                    || current.state == State.PAUSED && (scaffoldCleanupFailed() || current.cursor < current.steps.size()
                        && (recoverableFailure() || PLANNING_BLOCKED.equals(current.reason)
                            || REOPENED_CHECKPOINT.stream().anyMatch(reason -> reason.equals(current.reason)))));
    }

    boolean brainControlling() {
        return current != null && (current.planning.waiting() || current.planning.action() != null);
    }

    private boolean atBrainCheckpoint() {
        return current.state == State.PAUSED || current.state == State.RUNNING && current.stepStarted < 0
                && current.approach == null && !current.needsScaffoldDirt && !worker.status().get("moving").getAsBoolean()
                && (current.supply == null || !Set.of("processing", "to_warehouse").contains(current.supply.phase));
    }

    void requestBrain(String event) {
        if (pendingStockVisit != null) {
            pendingStockVisit = null;
            worker.halt();
        }
        current.planning.invalidate(event);
        current.planning.request(event);
        persist();
    }

    private void stopBrain(String reason) {
        if (current.planning.action() != null) {
            current.planning.resolved(current, reason, null);
        }
        current.planning.stop();
        current.planning.invalidate(reason);
    }

    private void stopPlanningAtCheckpoint() {
        current.planning.stop();
        if (current.state == State.RUNNING) {
            pause(PLANNING_BLOCKED);
        }
    }

    private boolean recoverableFailure() {
        return current.reason != null
                && (PLACEMENT_FAILURES.contains(current.reason) || SUPPLY_FAILURES.contains(current.reason));
    }

    private boolean scaffoldCleanupFailed() {
        return current.state == State.PAUSED && current.reason != null && !current.scaffolds.isEmpty()
                && Set.of(SCAFFOLD_CLEANUP_UNREACHABLE, SCAFFOLD_CLEANUP_BLOCKED,
                          "scaffold_cleanup_denied", "protected_facility").contains(current.reason);
    }

    private JsonObject scaffoldFailure(ConstructionWorker.ScaffoldCleanup cleanup, String reason) {
        JsonObject failure = new JsonObject();
        failure.addProperty("operation", "remove_scaffold");
        failure.addProperty("reason", reason);
        if (cleanup != null) {
            failure.add("local", Wire.position(current.plan.localPosition(cleanup.pos())));
            failure.add("block", Wire.state(current.scaffolds.get(cleanup.pos())).get("id"));
            var blocked = cleanup.blocked();
            if (blocked != null) {
                failure.addProperty("blocker_reason", blocked.code());
                failure.add("blocker_local", Wire.position(current.plan.localPosition(blocked.position())));
                if (level.hasChunkAt(blocked.position())) {
                    failure.add("blocker", Wire.state(level.getBlockState(blocked.position())).get("id"));
                }
            }
        }
        return failure;
    }

    JsonObject executionState() {
        JsonObject result = observationJob();
        result.addProperty("activity", activity());
        result.addProperty("awaiting_approval", candidate != null);
        if (current != null) {
            if (current.reason != null) {
                result.addProperty("reason", current.reason);
            }
            if (current.planning.hasRecord()) {
                result.add("planning", current.planning.status());
            }
            if (!current.clearancePositions.isEmpty()) {
                result.add("preparation", current.preparationStatus());
            }
        }
        return result;
    }

    JsonObject taskContext() {
        JsonObject task = observationJob();
        if (current == null) {
            return task;
        }
        task.addProperty("activity", activity());
        task.addProperty("placed", current.placed);
        task.addProperty("target_count", current.plan.targets().size());
        task.addProperty("remaining", current.steps.size() - current.cursor);
        task.addProperty("temporary_blocks", current.scaffolds.size());
        task.addProperty("revision_approved", current.approvedReview != null);
        JsonObject blueprint = new JsonObject();
        var box = current.plan.worldBounds();
        var a = current.plan.localPosition(box.min());
        var b = current.plan.localPosition(box.max());
        blueprint.add("min_local", Wire.position(new BlockPos(Math.min(a.getX(), b.getX()), a.getY(), Math.min(a.getZ(), b.getZ()))));
        blueprint.add("max_local", Wire.position(new BlockPos(Math.max(a.getX(), b.getX()), b.getY(), Math.max(a.getZ(), b.getZ()))));
        if (current.plan.terrain() != null) {
            blueprint.addProperty("foundation_blocks", current.plan.terrain().foundation().size());
            blueprint.addProperty("structure_blocks", current.plan.targets().size() - current.plan.terrain().foundation().size());
        }
        task.add("blueprint", blueprint);
        JsonObject outcome = current.planning.outcome();
        if (outcome != null) {
            task.add("last_action", outcome);
        }
        if (current.reason != null) {
            task.addProperty("reason", current.reason);
        }
        if (current.plan.sourceProject() != null) {
            task.addProperty("source_project", current.plan.sourceProject());
        }
        if (current.supply != null) {
            task.addProperty("supply_phase", current.supply.phase);
        }
        if (current.strategy != null) {
            task.add("strategy", current.strategy.deepCopy());
        }
        if (!current.clearancePositions.isEmpty()) {
            JsonObject preparation = current.preparationStatus();
            preparation.remove("next");
            task.add("preparation", preparation);
        }
        JsonObject npc = worker.status();
        JsonObject detail = new JsonObject();
        detail.add("present", npc.get("present"));
        if (npc.get("present").getAsBoolean()) {
            detail.add("local", Wire.position(current.plan.localPosition(BlockPos.containing(worker.position()))));
            detail.add("moving", npc.get("moving"));
        }
        task.add("npc", detail);
        if (current.cursor < current.steps.size()) {
            task.add("next_step", briefStep(current.steps.get(current.cursor)));
        }
        if (scaffoldCleanupFailed()) {
            task.add("last_failure", scaffoldFailure(worker.scaffoldCleanup(), current.reason));
        } else if (current.lastFailure != null) {
            task.add("last_failure", current.lastFailure.deepCopy());
        }
        JsonArray recent = new JsonArray();
        for (int i = current.records.size() - 1; i >= 0 && recent.size() < 3; i--) {
            JsonObject row = current.records.get(i).getAsJsonObject();
            if (!Set.of("placed", "removed", "already_present").contains(row.get("status").getAsString())) {
                continue;
            }
            JsonObject step = new JsonObject();
            step.add("local", row.get("local"));
            step.add("status", row.get("status"));
            step.add("block", row.getAsJsonObject("expected").get("id"));
            recent.add(step);
        }
        task.add("recent_steps", recent);
        return task;
    }

    private static JsonObject briefStep(WorkStep step) {
        JsonObject data = new JsonObject();
        data.add("local", Wire.position(step.target().local()));
        data.addProperty("operation", step.clearance() ? "clear" : step.remove() ? "remove" : "place");
        data.add("block", Wire.state(step.remove() ? step.before() : step.after()).get("id"));
        data.addProperty("item", Warehouse.id(Warehouse.item(step)));
        return data;
    }

    private JsonObject inspectTask(UUID owner, String topic, String group, boolean brain) {
        if (!Set.of("status", "failure", "supply", "world", "blueprint").contains(topic)) {
            throw Wire.invalid("Unknown task topic");
        }
        JsonObject response = new JsonObject();
        response.addProperty("result", "completed");
        response.add("task", taskContext());
        if (topic.equals("supply")) {
            JsonObject stock = materials(owner);
            for (String field : List.of("requirements", "recipe_inputs", "npc_items", "warehouse_items")) {
                if (stock.get(field) instanceof JsonObject rows) {
                    JsonObject brief = new JsonObject();
                    if (field.equals("requirements") && current.cursor < current.steps.size()) {
                        String item = Warehouse.id(Warehouse.item(current.steps.get(current.cursor)));
                        if (rows.has(item)) {
                            brief.add(item, rows.get(item));
                        }
                    }
                    rows.entrySet().stream().limit(16).forEach(entry -> brief.add(entry.getKey(), entry.getValue()));
                    stock.add(field, brief);
                }
            }
            stock.add("tools", worker.status().get("tools"));
            response.add("supply", stock);
        } else if (topic.equals("blueprint")) {
            Map<Integer, Integer> layers = new java.util.TreeMap<>();
            current.plan.targets().forEach(target -> layers.merge(target.local().getY(), 1, Integer::sum));
            JsonObject counts = new JsonObject();
            layers.forEach((y, count) -> counts.addProperty(y.toString(), count));
            response.add("target_counts_by_local_y", counts);
        } else if (topic.equals("world") && (scaffoldCleanupFailed() || current.cursor < current.steps.size())) {
            WorkStep focus = group != null ? current.planning.group(group).get(0)
                    : current.cursor < current.steps.size() ? current.steps.get(current.cursor) : null;
            var cleanup = group == null && scaffoldCleanupFailed() ? worker.scaffoldCleanup() : null;
            BlockPos target = cleanup != null ? cleanup.pos() : focus == null ? null : focus.pos();
            if (target == null) {
                return response;
            }
            response.add("focus_local", Wire.position(current.plan.localPosition(target)));
            JsonArray sample = new JsonArray();
            for (BlockPos pos : BlockPos.betweenClosed(target.offset(-1, -1, -1), target.offset(1, 1, 1))) {
                if (site.area().contains(pos) && level.hasChunkAt(pos)) {
                    JsonObject cell = new JsonObject();
                    cell.add("local", Wire.position(current.plan.localPosition(pos)));
                    cell.add("actual", Wire.state(level.getBlockState(pos)).get("id"));
                    BuildPlan.Target desired = current.plan.at(pos);
                    if (desired != null) {
                        cell.add("planned", Wire.state(desired.state()).get("id"));
                    }
                    cell.addProperty("standable", BuildTerrain.canStand(level, pos));
                    sample.add(cell);
                }
            }
            response.add("cells", sample);
            if (brain && cleanup == null && focus != null && target.equals(focus.pos())) {
                JsonArray stances = new JsonArray();
                for (var candidate : worker.workStances(focus)) {
                    current.planning.offerStance(candidate.feet());
                    JsonObject stance = new JsonObject();
                    stance.add("local", Wire.position(current.plan.localPosition(candidate.feet())));
                    stance.addProperty("can_reach_target", candidate.canWork());
                    stances.add(stance);
                }
                response.add("stances", stances);
            }
        }
        return response;
    }

    JsonObject planning(String operation, ServerPlayer player, JsonObject data, NpcObservations observations) {
        UUID owner = player.getUUID();
        if (operation.equals("claim")) {
            boolean brain = data.has("brain") && !data.has("request_id") && data.get("brain").getAsBoolean();
            if (!offerPlanning(currentJobId()) || current.planning.action() != null) {
                return new JsonObject();
            }
            requireCurrentWarehouse(owner);
            if (brain) {
                if (!current.planning.waiting() && (!current.planning.started() || current.planning.hasHint())
                        && atBrainCheckpoint() && siteLoaded()) {
                    current.planning.request(current.planning.hasHint() ? "player_hint" : "resume_checkpoint");
                }
                if (!current.planning.waiting() || !siteLoaded() || !worker.attach(current.npc)) {
                    return new JsonObject();
                }
                if (pendingStockVisit != null) {
                    pendingStockVisit = null;
                    worker.halt();
                }
            }
            Set<String> choices = new LinkedHashSet<>();
            boolean cleanupOnly = scaffoldCleanupFailed();
            if (current.state == State.PAUSED) {
                if (PLACEMENT_FAILURES.contains(current.reason)) {
                    choices.addAll(List.of("upper_stance", "scaffold", "open_access"));
                }
                if (cleanupOnly && !"scaffold_cleanup_denied".equals(current.reason)) {
                    var cleanup = worker.scaffoldCleanup();
                    if (cleanup == null || cleanup.blocked() == null) {
                        choices.add(SCAFFOLD_DESCENT);
                    }
                }
                if (current.supply != null && warehouses.containsKey(owner) && SUPPLY_FAILURES.contains(current.reason)) {
                    choices.addAll(List.of("collect", "craft"));
                }
            }
            JsonObject offer = current.planning.claim(current, owner, level.getGameTime(), data.has("request_id"),
                    choices, this::readyForPriority, brain, cleanupOnly);
            if (offer.size() > 0) {
                offer.addProperty("job_id", current.plan.id().toString());
                offer.addProperty("building_version", current.plan.version());
                offer.addProperty("player_id", owner.toString());
                if (site.worldCopy()) {
                    offer.addProperty("site_authorization", site.scope().toString());
                }
                offer.addProperty("can_observe", observations != null && site.worldCopy());
                if (brain) {
                    offer.addProperty("can_move", current.cursor < current.steps.size());
                    offer.addProperty("can_prepare", current.supply != null && warehouses.containsKey(owner)
                            && current.cursor < current.steps.size() && supplyNeeded());
                }
                offer.add("task", taskContext());
                persist();
            }
            return offer;
        }
        if (data.has("job_id")) {
            requireCurrent(BuildPlan.id(data, "job_id"));
            if (current.plan.version() != Wire.integer(data.get("building_version"), "building_version")) {
                throw Wire.conflict("stale_planning", "施工版本已变化。");
            }
        }
        requireCurrentWarehouse(owner);
        if (operation.equals("inspect")) {
            String topic = Wire.string(data.get("topic"), "topic");
            boolean brain = data.has("lease") && current.planning.brainLease();
            if (data.has("lease")) {
                current.planning.require(current, owner, BuildPlan.id(data, "lease"), brain);
            }
            String group = data.has("group") ? Wire.string(data.get("group"), "group") : null;
            if (group != null && (!brain || !topic.equals("world"))) {
                throw Wire.conflict("invalid_planning_group", "请在当前施工检查点查询提供的工作组。");
            }
            return inspectTask(owner, topic, group, brain);
        }
        requireWritable();
        UUID lease = BuildPlan.id(data, "lease");
        current.planning.require(current, owner, lease, !operation.equals("finish"));
        if (operation.equals("finish")) {
            String reply = planningText(data);
            boolean notify = data.has("notify") && data.get("notify").getAsBoolean();
            String event = current.planning.status().get("event").getAsString();
            boolean brain = current.planning.brainLease();
            current.planning.finish(current, reply, level.getGameTime());
            if (brain) {
                stopPlanningAtCheckpoint();
            }
            persist();
            if (notify && !reply.isEmpty() && (brain || event.startsWith("failure:") || current.planning.status()
                    .get("state").getAsString().equals("applied"))) {
                ArchitectService.tell(player, reply);
            }
            return current.planning.status();
        }
        if (!offerPlanning(currentJobId())) {
            throw Wire.conflict("stale_planning", "施工已暂停或变更，策略已失效。");
        }
        if (operation.equals("capture")) {
            if (observations == null || !site.worldCopy() || !current.planning.canCapture()) {
                throw Wire.conflict("observation_unavailable", "当前没有可用的新 NPC 相机画面。");
            }
            UUID id = observations.request(player, this, true);
            current.planning.capture(id);
            JsonObject response = new JsonObject();
            response.addProperty("observation_id", id.toString());
            return response;
        }
        if (operation.equals("thinking")) {
            UUID id = data.has("observation_id") ? BuildPlan.id(data, "observation_id") : null;
            if (id != null && (observations == null || !current.planning.ownsCapture(id)
                    || !observations.pairedPlanning(id, this))) {
                throw Wire.conflict("world_observation_stale", "规划图像和当前任务未成功配对。");
            }
            current.planning.thinking(current, level.getGameTime(), id);
            return current.planning.status();
        }
        if (operation.equals("act") && current.planning.brainLease()) {
            return startBrainAction(data, owner);
        }
        if (operation.equals("apply") && !current.planning.brainLease()) {
            return applyStrategy(data, owner);
        }
        throw Wire.invalid("Unknown planning operation");
    }

    private static String planningText(JsonObject data) {
        String reason = Wire.string(data.get("reason"), "reason").strip();
        if (reason.length() > 240 || reason.codePoints().anyMatch(Character::isISOControl)) {
            throw Wire.invalid("Invalid planning reason");
        }
        return reason;
    }

    private boolean readyForPriority(WorkStep step) {
        BlockPos pos = step.pos();
        if (!site.canWorkAt(level, pos) || !level.hasChunkAt(pos) || !level.getBlockState(pos).isAir()
                || !level.getBlockState(pos.below()).isFaceSturdy(level, pos.below(), Direction.UP)
                || !step.after().canSurvive(level, pos)) {
            return false;
        }
        try {
            requireSafeMutation(step, AIR, Set.of());
            return true;
        } catch (Wire.Problem blocked) {
            return false;
        }
    }

    private boolean supplyNeeded() {
        return current.needsScaffoldDirt
                ? Warehouse.carried(worker.inventory()).getOrDefault(net.minecraft.world.item.Items.DIRT, 0) < scaffoldDirt().size()
                : !worker.hasSupply(current.steps.get(current.cursor));
    }

    private JsonObject applyStrategy(JsonObject data, UUID owner) {
        String strategy = Wire.string(data.get("strategy"), "strategy");
        String reason = planningText(data);
        current.planning.requireStrategy(strategy);
        if (current.autonomous) {
            requireAutonomy();
        }
        site.requireLoaded(level);
        boolean paused = current.state == State.PAUSED;
        boolean supplyWork = strategy.equals("collect") || strategy.equals("craft");
        boolean scaffoldWork = SCAFFOLD_DESCENT.equals(strategy);
        String group = null;
        if (paused) {
            requireCheckpointWorker();
            if (supplyWork) {
                if (!supplyNeeded()) {
                    throw Wire.conflict("supply_already_ready", "下一步所需物品已经在 NPC 背包里。");
                }
            } else if (!scaffoldWork && current.supply != null && Warehouse.missing(materials(owner))) {
                throw Wire.conflict("awaiting_materials", "先解决授权库存中的实际缺口。");
            }
        }
        switch (strategy) {
            case "prioritize" -> {
                group = Wire.string(data.get("group"), "group");
                current.steps = current.planning.prioritize(current, group, this::readyForPriority, false);
            }
            case "upper_stance", "scaffold", SCAFFOLD_DESCENT -> current.approach = strategy;
            case "open_access" -> {
                if (!reopenAccess(current.steps.get(current.cursor))) {
                    throw Wire.conflict("planning_access_unavailable", "目标上方没有可安全临时移开的自有覆盖物。");
                }
            }
            case "collect", "craft" -> current.supplyChoice = strategy;
            default -> throw Wire.invalid("Unknown work strategy");
        }
        if (paused) {
            pendingStockVisit = null;
            worker.halt();
            current.state = State.RUNNING;
            current.reason = null;
            current.deferred = 0;
            current.stepStarted = -1;
            current.verification = null;
            if (current.supply != null) {
                current.supply.phase = supplyWork ? "ready" : "working";
            }
            startPerformance(0);
            schedule();
        }
        current.planning.applied(current, strategy, group, reason, level.getGameTime());
        current.strategy = current.planning.status();
        current.strategy.remove("lease");
        current.strategy.remove("observation_id");
        persist();
        JsonObject result = new JsonObject();
        result.addProperty("result", "running");
        result.addProperty("strategy", strategy);
        result.add("task", taskContext());
        return result;
    }

    private JsonObject startBrainAction(JsonObject data, UUID owner) {
        var planning = current.planning;
        String action = Wire.string(data.get("action"), "action");
        String strategy = data.has("strategy") ? Wire.string(data.get("strategy"), "strategy") : null;
        String reason = planningText(data);
        if (action.equals("choose_work")) {
            planning.requireStrategy(strategy);
            action = switch (strategy) {
                case "prioritize" -> "reorder_work";
                case "collect", "craft" -> "prepare_materials";
                default -> "choose_work";
            };
        }
        if (current.autonomous) {
            requireAutonomy();
        }
        site.requireLoaded(level);
        requireCheckpointWorker();
        int first = current.cursor;
        int end = first;
        BlockPos stance = null;
        JsonObject details = new JsonObject();
        String signature;
        switch (action) {
            case "move_to" -> {
                BlockPos local = Wire.position(data.get("stance"), "stance");
                stance = current.plan.worldPosition(local);
                planning.requireStance(stance);
                signature = action + ":" + stance.asLong();
                planning.requireAttempt(current, signature);
                worker.startSurvey();
                worker.moveForSurvey(stance);
                details.add("stance", Wire.position(local));
                details.add("npc_before", Wire.position(current.plan.localPosition(BlockPos.containing(worker.position()))));
            }
            case "execute_group", "reorder_work" -> {
                String group = Wire.string(data.get("group"), "group");
                List<WorkStep> work = planning.group(group);
                boolean execute = action.equals("execute_group");
                if (!execute && group.equals("next")) {
                    throw Wire.conflict("planning_order_unchanged", "这组已经排在队列前面。");
                }
                signature = action + ":" + work.stream().map(WorkStep::pos)
                        .collect(java.util.stream.Collectors.toSet()).hashCode();
                if (execute) {
                    signature += ":" + BlockPos.containing(worker.position()).asLong();
                }
                planning.requireAttempt(current, signature);
                if (!group.equals("next")) {
                    current.steps = planning.prioritize(current, group, this::readyForPriority, execute);
                }
                details.addProperty("group", group);
                details.addProperty("steps", work.size());
                details.add("first_local", Wire.position(work.get(0).target().local()));
                details.add("last_local", Wire.position(work.get(work.size() - 1).target().local()));
                end = first + work.size();
                if (execute) {
                    resumeBrainWork();
                }
            }
            case "prepare_materials" -> {
                if (strategy == null || !Set.of("collect", "craft").contains(strategy) || current.supply == null
                        || !warehouses.containsKey(owner) || first == current.steps.size()) {
                    throw Wire.conflict("planning_supply_unavailable", "需要授权仓库和明确的下一步物料需求。");
                }
                if (!supplyNeeded()) {
                    throw Wire.conflict("supply_already_ready", "下一步所需物品已经在 NPC 背包里。");
                }
                signature = action + ":" + strategy;
                planning.requireAttempt(current, signature);
                current.supplyChoice = strategy;
            }
            case "choose_work" -> {
                signature = action + ":" + strategy;
                planning.requireAttempt(current, signature);
                if (!SCAFFOLD_DESCENT.equals(strategy)) {
                    end = first + 1;
                    if (strategy.equals("open_access")) {
                        int oldSize = current.steps.size();
                        if (!reopenAccess(current.steps.get(first))) {
                            throw Wire.conflict("planning_access_unavailable", "目标上方没有可安全临时移开的自有覆盖物。");
                        }
                        end += current.steps.size() - oldSize;
                    } else {
                        current.approach = strategy;
                    }
                    resumeBrainWork();
                } else {
                    var cleanup = worker.scaffoldCleanup();
                    if (cleanup != null) {
                        details.add("scaffold_local", Wire.position(current.plan.localPosition(cleanup.pos())));
                    }
                }
            }
            default -> throw Wire.invalid("Unknown task action");
        }
        long now = level.getGameTime();
        planning.start(current, new ConstructionPlanning.Action(action, strategy, first, end, stance, now),
                signature, details, reason, now);
        if (action.equals("reorder_work")) {
            planning.resolved(current, null, null);
        }
        schedule();
        persist();
        JsonObject response = new JsonObject();
        JsonObject outcome = planning.outcome();
        response.addProperty("result", outcome.get("status").getAsString());
        response.add("execution", outcome);
        response.add("task", taskContext());
        return response;
    }

    private void resumeBrainWork() {
        if (current.state == State.PAUSED) {
            current.state = State.RUNNING;
            current.reason = null;
            current.deferred = 0;
            current.stepStarted = -1;
            current.verification = null;
            if (current.supply != null) {
                current.supply.phase = "working";
            }
            startPerformance(0);
        }
    }

    private boolean executeBrainAction() {
        var planning = current.planning;
        var action = planning.action();
        if (action == null) {
            return false;
        }
        planning.running();
        if (action.kind().equals("move_to")) {
            if (worker.surveyArrived()) {
                String code = action.stance().equals(BlockPos.containing(worker.position())) ? null : "planning_stance_changed";
                finishBrainAction(code, movementEvidence());
            }
            return true;
        }
        if (action.kind().equals("prepare_materials")) {
            if (!supplyNeeded()) {
                JsonObject evidence = new JsonObject();
                WorkStep step = current.needsScaffoldDirt ? scaffoldDirt().get(0) : current.steps.get(current.cursor);
                evidence.addProperty("item", Warehouse.id(Warehouse.item(step)));
                evidence.addProperty("carried", Warehouse.carried(worker.inventory()).getOrDefault(Warehouse.item(step), 0));
                finishBrainAction(null, evidence);
            } else if (level.getGameTime() - action.started() >= PLACEMENT_LIMIT_TICKS) {
                throw Wire.conflict("material_preparation_timeout", "NPC 未能在检查点前备齐下一步物料。");
            } else {
                List<WorkStep> steps = current.needsScaffoldDirt ? scaffoldDirt() : current.steps;
                resupply(steps, current.needsScaffoldDirt ? 0 : current.cursor);
            }
            return true;
        }
        if (SCAFFOLD_DESCENT.equals(action.strategy())) {
            if (worker.cleanScaffolds(true)) {
                current.lastFailure = null;
                resumeBrainWork();
                JsonObject evidence = new JsonObject();
                evidence.addProperty("temporary_blocks", current.scaffolds.size());
                finishBrainAction(null, evidence);
            } else if (level.getGameTime() - action.started() >= PLACEMENT_LIMIT_TICKS) {
                throw Wire.conflict(SCAFFOLD_CLEANUP_UNREACHABLE, "NPC 未能在限定时间内清理脚手架。");
            } else {
                checkpoint();
            }
            return true;
        }
        if (current.cursor < action.end()) {
            return false;
        }
        JsonObject verification = verifyBrainGroup(current.steps.subList(action.first(), action.end()));
        boolean passed = verification.get("checked").getAsInt() == verification.get("matched").getAsInt();
        finishBrainAction(passed ? null : "planning_group_world_mismatch", verification);
        return true;
    }

    private JsonObject movementEvidence() {
        JsonObject evidence = new JsonObject();
        if (worker.position() != null) {
            evidence.add("npc_local", Wire.position(current.plan.localPosition(BlockPos.containing(worker.position()))));
        }
        return evidence;
    }

    private JsonObject verifyBrainGroup(List<WorkStep> steps) {
        Map<BlockPos, WorkStep> finalSteps = new LinkedHashMap<>();
        steps.forEach(step -> step.affected().forEach(pos -> finalSteps.put(pos, step)));
        JsonArray sample = new JsonArray();
        JsonArray conflicts = new JsonArray();
        int matched = 0;
        for (var entry : finalSteps.entrySet()) {
            BlockPos pos = entry.getKey();
            if (!level.hasChunkAt(pos)) {
                throw Wire.conflict("site_not_loaded", "请加载工作组所在区块，以便核对施工结果。");
            }
            WorkStep step = entry.getValue();
            var approved = current.plan.at(pos);
            BlockState actual = level.getBlockState(pos);
            boolean pass = step.remove() ? actual.isAir() || step.clearance() && approved != null && approved.matches(actual)
                    : approved != null && (approved.matches(actual) || installedDoorMatches(
                            pos.equals(step.pos()) ? step : new WorkStep(approved, AIR, false), actual));
            if (pass) {
                matched++;
            }
            JsonArray rows = pass ? sample : conflicts;
            if (rows.size() < 3) {
                JsonObject row = new JsonObject();
                row.add("local", Wire.position(current.plan.localPosition(pos)));
                row.add("actual", Wire.state(actual));
                rows.add(row);
            }
        }
        JsonObject result = new JsonObject();
        result.addProperty("checked", finalSteps.size());
        result.addProperty("matched", matched);
        result.add("sample", sample);
        result.add("mismatches", conflicts);
        return result;
    }

    private void finishBrainAction(String code, JsonObject evidence) {
        worker.halt();
        current.supplyChoice = null;
        current.planning.resolved(current, code, evidence);
        if (code == null && current.cursor == current.steps.size() && current.state == State.RUNNING) {
            current.planning.stop(); // The existing cleanup and final world verification still run.
        }
        schedule();
        persist();
    }

    JsonObject planningFrame(BlockPos sampleOrigin) {
        JsonObject frame = new JsonObject();
        frame.add("origin_from_scene", Wire.position(current.plan.origin().subtract(sampleOrigin)));
        frame.add("clockwise_degrees", current.plan.versionInfo().get("rotation"));
        return frame;
    }

    void planningHint(String message, boolean brain) {
        current.planning.hint(message);
        if (brain && current.planning.action() == null && atBrainCheckpoint()) {
            requestBrain("player_hint");
        }
    }

    void invalidatePlanning(String reason) {
        if (current != null) {
            current.planning.invalidate(reason);
        }
    }


    JsonObject observationJob() {
        JsonObject info = new JsonObject();
        if (current == null) {
            info.addProperty("state", "survey");
            info.addProperty("building_version", 0);
            info.addProperty("processed", 0);
            info.addProperty("total", 0);
            return info;
        }
        info.addProperty("job_id", current.plan.id().toString());
        info.addProperty("building_version", current.plan.version());
        info.addProperty("state", current.state.wire());
        info.addProperty("processed", current.cursor);
        info.addProperty("total", current.steps.size());
        return info;
    }

    JsonObject jobStatus() {
        return current == null ? null : current.status(false);
    }

    JsonObject status(boolean detailed) {
        JsonObject response = new JsonObject();
        response.addProperty("protocol", 2);
        response.addProperty("world", site.world());
        response.addProperty("dimension", level.dimension().location().toString());
        response.addProperty("game_tick", level.getGameTime());
        response.add("approved_area", site.area().json());
        response.addProperty("journal_available", !journalBlocked);
        response.addProperty("change_pending", candidate != null);
        response.addProperty("supply_mode", site.survival() ? "survival" : "preset");
        response.addProperty("activity", activity());
        if (detailed) {
            response.add("modded_materials", moddedMaterials());
        }
        if (current != null) {
            response.add("job", current.status(detailed));
        }
        if (detailed && site.survival()) {
            response.add("materials", materials(current != null && current.supply != null ? current.supply.owner
                    : warehouses.keySet().stream().findFirst().orElse(null)));
        }
        response.add("npc", worker.status());
        return response;
    }

    JsonObject versions() {
        JsonObject response = new JsonObject();
        JsonArray versions = new JsonArray();
        for (Registration entry : registered.values()) {
            JsonObject version = entry.plan().versionInfo();
            version.addProperty("disposition", entry.disposition());
            JsonElement archived = history.get(entry.plan().id().toString());
            if (archived != null) {
                version.add("archived_job", archived.deepCopy());
            }
            versions.add(version);
        }
        response.add("versions", versions);
        if (current != null) {
            response.addProperty("current_job_id", current.plan.id().toString());
        }
        return response;
    }

    private void archiveCurrent(UUID successor) {
        if (current != null) {
            JsonObject archived = current.status(true);
            if (successor != null) {
                archived.addProperty("superseded_by", successor.toString());
            }
            history.add(current.plan.id().toString(), archived);
        }
    }

    private void requireWritable() {
        if (journalBlocked) {
            throw new Wire.Problem(503, "journal_unavailable", "Construction is halted until the journal can be recovered");
        }
    }

    private void persist() {
        long checkpointStarted = BuildPerformance.clock();
        requireWritable();
        JsonObject state = new JsonObject();
        JsonArray plans = new JsonArray();
        for (Registration entry : registered.values()) {
            JsonObject row = new JsonObject();
            row.add("plan", entry.payload());
            row.addProperty("disposition", entry.disposition());
            plans.add(row);
        }
        state.add("plans", plans);
        state.add("history", history);
        if (surveyNpc != null) {
            state.addProperty("survey_npc", surveyNpc.toString());
        }
        JsonArray stores = new JsonArray();
        warehouses.values().forEach(source -> stores.add(source.json()));
        state.add("warehouses", stores);
        if (autonomyRevoked) {
            state.addProperty("autonomy_revoked", true);
        }
        if (current != null) {
            state.add("current", current.checkpoint());
        }
        if (candidate != null) {
            state.addProperty("candidate", candidate.plan().id().toString());
        }
        try {
            journal.write(state);
            nextCheckpoint = level.getGameTime() + 40;
        } catch (IOException error) {
            journalBlocked = true;
            if (current != null && current.state.active()) {
                pause("journal_unavailable");
            } else {
                worker.halt();
            }
            ForgeBridge.LOGGER.error("Could not commit the construction journal", error);
            throw new Wire.Problem(503, "journal_unavailable", "The construction journal could not be committed");
        } finally {
            if (current != null) {
                current.performance.span(BuildPerformance.Span.CHECKPOINT, checkpointStarted);
            }
        }
    }

    private void restore(JsonObject data) {
        if (data == null) {
            return;
        }
        for (JsonElement element : Wire.array(data.get("plans"), "plans")) {
            JsonObject entry = Wire.object(element, "registration");
            JsonObject payload = Wire.object(entry.get("plan"), "registered plan");
            BuildPlan plan = BuildPlan.read(payload, site, palette, true);
            String disposition = Wire.string(entry.get("disposition"), "disposition");
            if (!Set.of("approved", "rejected", "proposed").contains(disposition) || registered.containsKey(plan.id())) {
                throw Wire.invalid("Invalid journal plan registration");
            }
            requireImmutableVersion(plan);
            registered.put(plan.id(), new Registration(plan, payload, disposition));
        }
        history = Wire.object(data.get("history"), "journal history");
        for (Registration registration : registered.values()) {
            BuildPlan known = registration.plan();
            JsonElement archived = history.get(known.id().toString());
            if (known.lighting() && archived instanceof JsonObject job && job.has("state")
                    && job.get("state").getAsString().equals("completed") && !job.has("superseded_by")) {
                completedLighting.put(known.buildingId(), known);
            }
        }
        autonomyRevoked = data.has("autonomy_revoked") && data.get("autonomy_revoked").getAsBoolean();
        if (data.has("survey_npc") && site.worldCopy()) {
            surveyNpc = BuildPlan.id(data, "survey_npc");
        }
        if (data.has("warehouse")) {
            Warehouse.Source legacy = Warehouse.Source.read(Wire.object(data.get("warehouse"), "warehouse"), site);
            warehouses.put(legacy.owner(), legacy);
        }
        if (data.has("warehouses")) {
            for (JsonElement element : data.getAsJsonArray("warehouses")) {
                Warehouse.Source source = Warehouse.Source.read(Wire.object(element, "supply area"), site);
                warehouses.put(source.owner(), source);
            }
        }
        if (data.has("current")) {
            JsonObject checkpoint = Wire.object(data.get("current"), "current job");
            Registration entry = registered.get(BuildPlan.id(checkpoint, "job_id"));
            if (entry == null || !entry.disposition().equals("approved")) {
                throw Wire.invalid("The current job is missing its approved version");
            }
            current = ConstructionJob.restore(checkpoint, entry.plan(), palette, site);
            if (current.plan.lighting() && current.state == State.COMPLETED) {
                completedLighting.put(current.plan.buildingId(), current.plan);
            }
            worker.useScaffolds(current.scaffolds, current.plan.byPosition().keySet(), current.plan.terrain() != null);
            updateSupplyArea();
        }
        if (data.has("candidate")) {
            candidate = registered.get(BuildPlan.id(data, "candidate"));
            if (current == null || candidate == null || !candidate.disposition().equals("proposed")
                    || !current.plan.samePlacement(candidate.plan()) || current.plan.version() >= candidate.plan().version()
                    || current.state != State.PAUSED) {
                throw Wire.invalid("The journal candidate is not tied to the current paused version");
            }
        }
    }

    void close() {
        if (current != null && current.state == State.RUNNING) {
            pause("server_stopping");
            persistAfterFailure();
        }
        worker.close();
    }
}
