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
import net.minecraft.world.level.block.Rotation;
import net.minecraft.world.item.Item;
import net.minecraft.world.level.block.state.BlockState;

import java.io.IOException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

import dev.mcaibuilder.bridge.ConstructionJob.State;

final class ConstructionService {
    private static final int ACTION_INTERVAL_TICKS = 6;
    private static final int NEARBY_PLACEMENT_INTERVAL_TICKS = 2;
    private static final BlockState AIR = Blocks.AIR.defaultBlockState();

    private record Registration(BuildPlan plan, JsonObject payload, String disposition) {
        Registration withDisposition(String next) {
            return new Registration(plan, payload, next);
        }
    }

    private final ServerLevel level;
    private final BuildSite site;
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
        worker.useScaffolds(current.scaffolds, plan.byPosition().keySet());
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
        return site.worldCopy() && !autonomyRevoked && site.worksite().active(System.currentTimeMillis());
    }

    JsonObject worksiteStatus() {
        JsonObject value = site.worksite().json();
        value.addProperty("active", autonomousAvailable());
        value.addProperty("state", autonomyRevoked ? "revoked" : autonomousAvailable() ? "active"
                : site.worksite().mode().equals("manual") ? "manual" : "expired");
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
        Map<BlockPos, BlockState> preparation = clearance(plan, obstacles);
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
        snapshot.track(next);
        snapshot.track(check.steps());
        archiveCurrent(next.id());
        current = new ConstructionJob(next, worker.id(), predecessor, check.report(), snapshot, check.steps());
        current.supply = site.survival() ? new SupplyCheckpoint(owner) : null;
        if (current.supply != null && previousSupply != null) {
            current.supply.temporary.putAll(previousSupply.temporary);
            current.supply.heatedFurnace = previousSupply.heatedFurnace;
            current.supply.heat = previousSupply.heat;
        }
        updateSupplyArea();
        current.scaffolds.putAll(scaffolds);
        worker.useScaffolds(current.scaffolds, next.byPosition().keySet());
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
        site.requireLoaded(level);
        ChangeReview check = ChangeReview.inspect(level, site, current.plan, candidate.plan(), current.snapshot,
                step -> requireSafeMutation(step, level.getBlockState(step.pos()), Set.of()));
        if (current.supply != null) {
            check.report().add("supply", supplySummary(current.supply.owner, check.steps(), 0));
        }
        return check;
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
            worker.halt();
            checkSupply();
            schedule();
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
        SupplyCheckpoint supply = current.supply;
        if (!supply.phase.equals("to_warehouse") && !supply.phase.equals("processing")) {
            worker.mentionSupply();
        }
        JsonObject transfer = worker.resupply(() -> Warehouse.scan(level, site, warehouses.get(supply.owner)), steps, cursor);
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
                if (current.state != State.PAUSED) {
                    requireState(State.RUNNING);
                    pause("paused_by_request");
                    persist();
                }
            }
            case "resume" -> {
                if (site.survival()) {
                    requireCurrentWarehouse(owner);
                }
                if (current.state != State.RUNNING) {
                    if (!current.state.active() && current.state != State.FAILED) {
                        requireState(State.PAUSED);
                    }
                    if (candidate != null) {
                        throw Wire.conflict("change_pending", "请先处理待审阅的建筑方案。");
                    }
                    long recoveryStarted = BuildPerformance.clock();
                    site.requireLoaded(level);
                    requireCheckpointWorker();
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
        current.performance.event(reason);
        current.planning.invalidate(reason);
        if (current.supply != null) {
            current.supply.phase = "paused";
        }
    }

    void externalEdit(ServerLevel editedLevel, Entity entity, BlockPos pos) {
        if (editedLevel == level && current != null && !worker.isActor(entity)) {
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
        worker.tickLife((current == null || current.state != State.RUNNING) && pendingStockVisit == null);
        if (current != null) {
            current.planning.expire(current);
        }
        if (journalBlocked || current == null) {
            return;
        }
        if (current.state == State.PAUSED && pendingStockVisit != null
                && (level.getGameTime() >= stockVisitUntil || worker.inspectStock(pendingStockVisit))) {
            worker.halt();
            pendingStockVisit = null;
        }
        if (current.state != State.RUNNING || level.getGameTime() < current.nextTick) {
            return;
        }
        long tickStarted = BuildPerformance.clock();
        try {
            if (current.autonomous) {
                requireAutonomy();
            }
            site.requireLoaded(level);
            requireCheckpointWorker();
            executeNext();
        } catch (Wire.Problem problem) {
            if (!journalBlocked) {
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
        if (current.cancelling || current.cursor == current.steps.size()) {
            if (!worker.cleanScaffolds() || !worker.cleanWorkshop()) {
                checkpoint();
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
        if (matches(actual, step.after()) || step.clearance() && following != null && matches(actual, following.state())) {
            result = new ConstructionWorker.Result("already_present", null, null);
            worker.halt();
        } else {
            if (!step.remove() && !actual.isAir()) {
                admitObstacle(step);
                return;
            }
            WorkStep live = step.remove() && actual != step.before()
                    ? WorkStep.clear(current.plan, step.pos(), actual) : step;
            requireSafeMutation(live, actual, Set.of());
            if (live != step) {
                replaceNext(live);
                step = live;
            }
            if (current.supply != null && !worker.hasSupply(step)) {
                resupply(current.steps, current.cursor);
                return;
            }
            result = worker.act(step);
        }
        if (result == null) {
            checkpoint();
            return;
        }
        if (result.status().equals("deferred")) {
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
        for (WorkStep pending : current.steps.subList(current.cursor, current.steps.size())) {
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
        current.steps = List.copyOf(early);
        current.deferred++;
        int band = early.size() - current.cursor - above.size();
        if (current.deferred >= band && reason.equals("no_reachable_placement_stance")
                && reopenAccess(step)) {
            return;
        }
        if (current.deferred >= 4 * band && !"waiting_for_space".equals(reason)
                && !reason.startsWith("scaffold_")) {
            pause(reason);
            persist();
            return;
        }
        current.nextTick = level.getGameTime() + (current.deferred % band == 0 ? 30 : ACTION_INTERVAL_TICKS);
        checkpoint();
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
        if (risk == null && secondary != null && level.getBlockState(secondary).is(actual.getBlock())) {
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
        current.planning.invalidate(reason == null ? state.wire() : reason);
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

    private JsonObject verify() {
        JsonArray states = new JsonArray();
        JsonArray mismatches = new JsonArray();
        int matched = 0;
        Set<BlockPos> inspected = current.plan.localized() && current.predecessor != null
                ? current.steps.stream().map(WorkStep::pos).collect(java.util.stream.Collectors.toSet()) : null;
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
            if (matches(actual, target.state())) {
                matched++;
            } else if (inspected == null || inspected.contains(target.world())) {
                mismatches.add(row);
            }
        }
        JsonObject data = new JsonObject();
        data.addProperty("passed", mismatches.isEmpty());
        data.addProperty("matched", matched);
        data.add("target_mismatches", mismatches);
        data.add("target_states", states);
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

    boolean observationCheckpoint(UUID id) {
        return id != null && id.equals(currentJobId()) && current.state != State.RUNNING;
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
        return site.worldCopy() && !journalBlocked && candidate == null && job != null
                && job.equals(currentJobId()) && current.state == State.RUNNING && !current.preparing();
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

    JsonObject planning(String operation, ServerPlayer player, JsonObject data, NpcObservations observations) {
        UUID owner = player.getUUID();
        if (operation.equals("claim")) {
            if (!offerPlanning(currentJobId())) {
                return new JsonObject();
            }
            requireCurrentWarehouse(owner);
            JsonObject offer = current.planning.claim(current, owner, level.getGameTime(), this::safeRoofFeet);
            if (!offer.entrySet().isEmpty()) {
                if (current.supply != null) {
                    JsonObject stock = supplySummary(owner, current.steps, offer.get("first_step").getAsInt());
                    offer.add("roof_supply", stock.getAsJsonObject("requirements"));
                }
                persist();
            }
            return offer;
        }
        requireWritable();
        requireCurrent(BuildPlan.id(data, "job_id"));
        UUID lease = BuildPlan.id(data, "lease");
        if (!offerPlanning(currentJobId())) {
            throw Wire.conflict("stale_planning", "施工已暂停或变更，策略已失效。");
        }
        requireCurrentWarehouse(owner);
        current.planning.require(current, owner, lease);
        if (operation.equals("capture")) {
            if (observations == null) {
                throw Wire.conflict("observation_unavailable", "此世界没有本地 NPC 相机。");
            }
            UUID id = current.planning.capture(() -> observations.request(player, this, true));
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
            current.planning.thinking(current, level.getGameTime(), id == null ? null : id.toString());
            return current.planning.status();
        }
        if (!operation.equals("resolve")) {
            throw Wire.invalid("Unknown planning operation");
        }
        String group = data.has("group") ? Wire.string(data.get("group"), "group") : null;
        String reason = Wire.string(data.get("reason"), "reason").strip();
        if (reason.isEmpty() || reason.length() > 160 || reason.codePoints().anyMatch(Character::isISOControl)) {
            throw Wire.invalid("Invalid planning reason");
        }
        if (group != null) {
            site.requireLoaded(level);
        }
        current.planning.resolve(current, group, reason, level.getGameTime(), step -> {
            BlockState state = level.getBlockState(step.pos());
            if (!state.isAir() && !matches(state, step.after())) {
                throw Wire.conflict("planning_world_changed", "工作组位置需要先清理，继续由 NPC 自行调度。");
            }
            requireSafeMutation(step, state, Set.of());
        }, this::safeRoofFeet);
        persist();
        return current.planning.status();
    }

    private boolean safeRoofFeet(BlockPos feet) {
        return site.area().contains(feet) && site.area().contains(feet.above()) && level.hasChunkAt(feet)
                && current.plan.at(feet) == null && current.plan.at(feet.above()) == null
                && BuildTerrain.canStand(level, feet);
    }

    JsonObject planningFrame(BlockPos sampleOrigin) {
        JsonObject frame = new JsonObject();
        frame.add("origin_from_scene", Wire.position(current.plan.origin().subtract(sampleOrigin)));
        frame.add("clockwise_degrees", current.plan.versionInfo().get("rotation"));
        return frame;
    }

    boolean planningHint(String message) {
        return current.planning.hint(message);
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
            worker.useScaffolds(current.scaffolds, current.plan.byPosition().keySet());
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
