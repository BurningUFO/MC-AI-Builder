package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import dev.duzo.players.entities.FakePlayerEntity;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.network.chat.Component;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.Rotation;
import net.minecraft.world.level.block.StairBlock;
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

import static dev.mcaibuilder.bridge.BuildTerrain.NATURAL_SUPPORTS;

import dev.mcaibuilder.bridge.ConstructionJob.State;

final class ConstructionService {
    private static final int ACTION_INTERVAL_TICKS = 6;
    private static final int NEARBY_PLACEMENT_INTERVAL_TICKS = 2;
    private static final Set<String> RETRYABLE = Set.of("navigation_timeout",
            "no_reachable_placement_stance", "no_reachable_break_stance", "placement_obstructed");
    private static final BlockState AIR = Blocks.AIR.defaultBlockState();

    private record Registration(BuildPlan plan, JsonObject payload, String disposition) {
        Registration withDisposition(String next) {
            return new Registration(plan, payload, next);
        }
    }

    private final ServerLevel level;
    private final BuildSite site;
    private final Set<Block> palette;
    private final Set<Block> borderBlocks;
    private final ConstructionWorker worker;
    private final JobJournal journal;
    private final Map<UUID, Registration> registered = new LinkedHashMap<>();
    private JsonObject history = new JsonObject();
    private ConstructionJob current;
    private UUID surveyNpc;
    private Registration candidate;
    private Warehouse.Source warehouse;
    private boolean journalBlocked;
    private boolean autonomyRevoked;

    ConstructionService(ServerLevel level, BuildSite site, Set<Block> palette, ConstructionWorker worker) throws IOException {
        this.level = level;
        this.site = site;
        this.palette = palette;
        this.worker = worker;
        borderBlocks = new HashSet<>(Set.of(Blocks.DIRT, Blocks.GRASS_BLOCK, Blocks.BEDROCK, Blocks.STONE,
                Blocks.COBBLESTONE, Blocks.STONE_BRICKS, Blocks.OAK_PLANKS, Blocks.SPRUCE_PLANKS,
                Blocks.DARK_OAK_PLANKS, Blocks.GLASS, Blocks.LIGHT_BLUE_STAINED_GLASS, Blocks.BLUE_STAINED_GLASS));
        if (site.worldCopy()) {
            borderBlocks.addAll(NATURAL_SUPPORTS);
        }
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
        Warehouse.Source source = requireWarehouse(owner);
        UUID id = BuildPlan.id(payload, "plan_id");
        if (current != null && current.plan.id().equals(id) && registered.get(id).payload().equals(payload)) {
            return status(false);
        }
        InitialBuild initial = validateInitial(payload, false, surveyed, autonomous);
        BuildPlan plan = initial.plan();
        List<WorkStep> steps = initial.steps();
        SupplyCheckpoint supply = prepareSupply(source, plan);
        SiteSnapshot snapshot = SiteSnapshot.capture(level, site, plan);
        snapshot.trackClearance(initial.clearance());
        if (current != null) {
            if (site.survival()) {
                if (!current.supply.checked || current.supply.intent != null) {
                    checkSupplyRecovery();
                }
                requireCheckpointWorker();
            } else {
                worker.attach(current.npc);
            }
        }
        if (current == null && surveyNpc != null && !worker.attach(surveyNpc)) {
            throw Wire.conflict("observation_npc_unavailable", "获准的原 NPC 尚未加载。");
        }
        prepare(steps);
        archiveCurrent(null);
        registered.put(id, new Registration(plan, payload.deepCopy(), "approved"));
        current = new ConstructionJob(plan, worker.id(), null, null, snapshot, steps, cargo());
        current.autonomous = autonomous;
        current.supply = supply;
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
            if (plan.buildingId().startsWith("cottage_") && target.local().getY() == 0) {
                for (int y = 1; y <= 3; y++) {
                    cells.add(target.world().above(y));
                }
            }
        }
        return cells;
    }

    private Map<BlockPos, BlockState> clearance(BuildPlan plan, Map<BlockPos, String> obstacles) {
        var inspect = new Clearance(level, site);
        Map<BlockPos, BlockState> cells = new LinkedHashMap<>();
        for (BlockPos pos : worksiteEnvelope(plan)) {
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
        Map<BlockPos, BlockState> preparation = autonomous ? clearance(plan, obstacles) : Map.of();
        if (!reportObstacles && !obstacles.isEmpty()) {
            throw Wire.conflict(obstacles.values().iterator().next(), "现场存在须由玩家决定的障碍，请重新勘察。");
        }
        String strategy = autonomous && payload.has("worksite_order")
                ? Wire.string(payload.get("worksite_order"), "worksite_order") : "route_first";
        if (!strategy.equals("route_first") && !strategy.equals("tree_first")) {
            throw Wire.invalid("Unknown worksite clearance order");
        }
        List<WorkStep> removal = preparation.entrySet().stream()
                .filter(entry -> !Clearance.upperHalf(entry.getValue()))
                .map(entry -> WorkStep.clear(plan, entry.getKey(), entry.getValue()))
                .sorted(Clearance.order(strategy)).toList();
        Set<BlockPos> supported = new HashSet<>();
        for (BuildPlan.Target target : plan.targets()) {
            BlockPos pos = target.world();
            BlockState actual = preparation.containsKey(pos) ? AIR : level.getBlockState(pos);
            WorkStep step = new WorkStep(target, actual, false);
            if (actual != target.state() && !obstacles.containsKey(pos)) {
                try {
                    if (!actual.isAir()) {
                        throw Wire.conflict("occupied_target", "Existing block at " + pos.toShortString());
                    }
                    requireSafeMutation(step, false);
                    requirePlannedSupport(target, surveyed, supported, preparation.keySet());
                } catch (Wire.Problem obstacle) {
                    if (!reportObstacles || !site.worldCopy()) {
                        throw obstacle;
                    }
                    obstacles.put(pos, obstacle.code);
                }
            }
            if (!obstacles.containsKey(pos)) {
                supported.add(pos);
            }
            placements.add(step);
        }
        return new InitialBuild(plan, placements, removal, obstacles, strategy);
    }

    private void requirePlannedSupport(BuildPlan.Target target, boolean surveyed,
                                       Set<BlockPos> supported, Set<BlockPos> prepared) {
        BlockPos pos = target.world();
        BlockPos below = pos.below();
        boolean firm = !prepared.contains(below) && surveyFoundation(pos);
        if (surveyed && target.local().getY() <= 0 && !firm && !supported.contains(below)
                || site.worldCopy() && !hasSupport(pos, supported, prepared)) {
            throw Wire.conflict("missing_terrain_support", "No approved support at " + pos.toShortString());
        }
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
        return (NATURAL_SUPPORTS.contains(state.getBlock()) || palette.contains(state.getBlock()))
                && !WorldObservation.hazard(state) && state.getFluidState().isEmpty()
                && state.isFaceSturdy(level, floor, Direction.UP);
    }

    private boolean hasSupport(BlockPos target, Set<BlockPos> supported, Set<BlockPos> prepared) {
        for (Direction direction : Direction.values()) {
            BlockPos neighbor = target.relative(direction);
            BlockState state = level.getBlockState(neighbor);
            if (supported.contains(neighbor) || (!prepared.contains(neighbor) && state.getFluidState().isEmpty()
                    && (NATURAL_SUPPORTS.contains(state.getBlock()) || palette.contains(state.getBlock())))) {
                return true;
            }
        }
        return false;
    }

    JsonObject inspectInitial(JsonObject payload, UUID owner, boolean surveyed) {
        return inspectInitial(payload, owner, surveyed, false);
    }

    JsonObject inspectInitial(JsonObject payload, UUID owner, boolean surveyed, boolean autonomous) {
        Warehouse.Source source = requireWarehouse(owner);
        InitialBuild initial = validateInitial(payload, true, surveyed, autonomous);
        requireWarehouseClear(source, initial.plan());
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
        if (autonomous) {
            JsonObject preparation = new JsonObject();
            preparation.addProperty("authorization", site.scope().toString());
            preparation.addProperty("mode", site.worksite().mode());
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
        addSupplyReview(report, source, initial.steps(), new JsonArray());
        report.addProperty("review_sha256", Wire.sha256(report));
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
        requireWarehouseClear(current.supply == null ? null : current.supply.source, next);
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
        if (current != null && current.preparing()) {
            throw Wire.conflict("worksite_preparation_pending", "请完成或取消已批准的现场清理，再修改建筑。");
        }
        requireWritable();
        requireCurrent(baseId);
        if (!current.state.active() && current.state != State.COMPLETED) {
            throw Wire.conflict("job_finished", "Only the current running, paused, or completed placement can be changed");
        }
        requireNoIntent();
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

    JsonObject approve(UUID id, String reviewedHash) {
        return approve(id, reviewedHash, null);
    }

    JsonObject approve(UUID id, String reviewedHash, UUID owner) {
        long approvalStarted = BuildPerformance.clock();
        requireWritable();
        if (site.survival()) {
            requireCurrentWarehouse(owner);
        }
        if (candidate == null && current != null && current.plan.id().equals(id) && current.approvedReview != null
                && current.approvedReview.get("review_sha256").getAsString().equals(reviewedHash)) {
            return status(false);
        }
        requireCandidate(id);
        requireNoIntent();
        ChangeReview check = review();
        if (!check.report().get("review_sha256").getAsString().equals(reviewedHash)) {
            throw Wire.conflict("review_changed", "Read the current change report and review it again");
        }
        if (check.conflicts() != 0) {
            throw Wire.conflict("change_conflicts", "The reviewed change contains conflicts");
        }
        checkSupplyRecovery();
        requireCheckpointWorker();
        for (WorkStep step : check.steps()) {
            requireSafeMutation(step, true);
        }

        worker.halt();
        prepare(check.steps());
        BuildPlan next = candidate.plan();
        SupplyCheckpoint supply = prepareSupply(current.supply == null ? null : current.supply.source, next);
        UUID predecessor = current.plan.id();
        SiteSnapshot snapshot = current.snapshot;
        snapshot.track(next);
        archiveCurrent(next.id());
        current = new ConstructionJob(next, worker.id(), predecessor, check.report(), snapshot, check.steps(), cargo());
        current.supply = supply;
        startPerformance(approvalStarted);
        registered.put(id, candidate.withDisposition("approved"));
        candidate = null;
        schedule();
        if (current.steps.isEmpty()) {
            finish(State.COMPLETED, null);
        }
        persist();
        ForgeBridge.LOGGER.info("m2_approved {} supersedes {} review={}", id, predecessor, reviewedHash);
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
                step -> requireSafeMutation(step, true));
        if (current.supply != null) {
            if (site.worldCopy()) {
                requireCheckpointWorker();
            }
            JsonObject report = check.report();
            report.remove("review_sha256");
            addSupplyReview(report, current.supply.source, check.steps(), current.records);
            report.addProperty("review_sha256", Wire.sha256(report));
        }
        return check;
    }

    boolean warehouseApproved() {
        return warehouse != null;
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
        if (current != null && current.state.active()) {
            throw Wire.conflict("warehouse_job_active", "请先结束施工，再撤销供料箱。");
        }
        requireWarehouse(player.getUUID());
        warehouse = null;
        persist();
    }

    void authorizeWarehouse(ServerPlayer player, BlockPos pos) {
        requireWritable();
        if (!site.survival()) {
            throw Wire.conflict("survival_supply_disabled", "当前实例使用隔离测试材料。");
        }
        if (current != null && current.state.active()) {
            throw Wire.conflict("warehouse_job_active", "施工期间只能由玩家补充原供料箱。");
        }
        site.requireLoaded(level);
        if (current != null && (!current.supply.checked || current.supply.intent != null)) {
            checkSupplyRecovery();
        }
        if (current != null && !current.supply.source.owner().equals(player.getUUID())
                && (!worker.attach(current.npc) || worker.inventory() == null || !worker.inventory().isEmpty())) {
            throw Wire.conflict("warehouse_previous_cargo", "NPC 仍有上一位玩家的库存。");
        }
        warehouse = Warehouse.authorize(level, site, player, pos);
        persist();
        ForgeBridge.LOGGER.info("warehouse_authorized {} at {}", warehouse.id(), pos.toShortString());
    }

    private Warehouse.Source requireWarehouse(UUID owner) {
        if (!site.survival()) {
            return null;
        }
        if (owner == null || (warehouse != null && !warehouse.owner().equals(owner))) {
            throw new Wire.Problem(403, "warehouse_player_approval", "需要由供料箱的授权玩家在游戏中批准。");
        }
        if (warehouse == null) {
            throw Wire.conflict("warehouse_not_approved", "请先用 /architect supply x y z 批准一个原版单箱。");
        }
        return warehouse;
    }

    private void requireCurrentWarehouse(UUID owner) {
        Warehouse.Source source = requireWarehouse(owner);
        if (source != null && (current == null || current.supply == null || !source.equals(current.supply.source))) {
            throw Wire.conflict("warehouse_job_scope", "当前任务不属于这个供料授权。");
        }
    }

    private void requireWarehouseClear(Warehouse.Source source, BuildPlan plan) {
        if (source != null && (plan.at(source.pos()) != null || plan.at(source.pos().above()) != null)) {
            throw Wire.conflict("warehouse_occupied", "设计需要为供料箱和箱盖留出空间。");
        }
    }

    private SupplyCheckpoint prepareSupply(Warehouse.Source source, BuildPlan plan) {
        if (source == null) {
            return null;
        }
        requireWarehouseClear(source, plan);
        return new SupplyCheckpoint(source, Warehouse.digest(Warehouse.open(level, source)));
    }

    private void addSupplyReview(JsonObject report, Warehouse.Source source, List<WorkStep> steps, JsonArray records) {
        if (source != null) {
            var chest = Warehouse.open(level, source);
            if (current != null) {
                worker.attach(current.npc);
            }
            JsonObject supply = Warehouse.summary(level, steps, 0, worker.inventory(), chest, records);
            supply.addProperty("mode", "survival");
            supply.addProperty("source_id", source.id().toString());
            supply.add("position", Wire.position(source.pos()));
            supply.addProperty("warehouse_sha256", Warehouse.digest(chest));
            report.add("supply", supply);
        }
    }

    JsonObject moddedMaterials() {
        return BuildPlan.moddedMaterials(palette);
    }

    JsonObject materials(UUID owner) {
        JsonObject result = new JsonObject();
        result.addProperty("mode", site.survival() ? "survival" : "preset");
        if (!site.survival() || warehouse == null || !warehouse.owner().equals(owner)) {
            result.addProperty("warehouse_approved", false);
            return result;
        }
        ConstructionJob job = current != null && current.supply != null && warehouse.equals(current.supply.source)
                ? current : null;
        if (job != null) {
            worker.attach(job.npc);
        }
        String problem = null;
        net.minecraft.world.Container chest = null;
        try {
            chest = Warehouse.open(level, warehouse);
        } catch (Wire.Problem error) {
            problem = error.code;
        }
        result = Warehouse.summary(level, job == null ? List.of() : job.steps, job == null ? 0 : job.cursor,
                job == null ? null : worker.inventory(), chest, job == null ? new JsonArray() : job.records);
        result.addProperty("mode", "survival");
        result.addProperty("warehouse_approved", true);
        result.add("position", Wire.position(warehouse.pos()));
        if (problem != null) {
            result.addProperty("reason", problem);
        }
        return result;
    }

    private void checkLiveSupply() {
        if (worker.inventory() == null || !current.cargo.equals(cargo())) {
            throw Wire.conflict("npc_inventory_changed", "NPC 背包与上次检查点不一致。");
        }
        if (!current.supply.stockHash.equals(Warehouse.digest(Warehouse.open(level, current.supply.source)))) {
            throw Wire.conflict("warehouse_changed", "供料箱内容已变化。请核对后确认继续。");
        }
    }

    private void resupply() {
        SupplyCheckpoint supply = current.supply;
        supply.phase = "to_warehouse";
        JsonObject transfer = worker.resupply(supply.source, supply.stockHash, current.steps, current.cursor,
                current.plan.optimizedCottage(), intent -> {
            if (supply.intent != null || current.intent >= 0) {
                throw Wire.conflict("warehouse_checkpoint_mismatch", "上一次交互尚未核对。");
            }
            intent.addProperty("tick", level.getGameTime());
            intent.addProperty("step", current.cursor);
            supply.intent = intent.deepCopy();
            persist();
        });
        if (transfer != null) {
            current.performance.event("warehouse_transfer");
            finishTransfer(transfer, false);
            schedule();
            persist();
        }
    }

    private void finishTransfer(JsonObject transfer, boolean reconciled) {
        SupplyCheckpoint supply = current.supply;
        transfer.addProperty("reconciled_from_checkpoint", reconciled);
        supply.stockHash = transfer.get("warehouse_after_sha256").getAsString();
        supply.intent = null;
        supply.phase = "to_site";
        supply.trips.add(transfer);
        current.cargo = cargo();
        ForgeBridge.LOGGER.info("warehouse_transfer {} {}", current.plan.id(), transfer);
    }

    private void checkSupplyRecovery() {
        SupplyCheckpoint supply = current.supply;
        if (supply == null) {
            return;
        }
        if (!worker.attach(current.npc) || worker.inventory() == null) {
            throw Wire.conflict("checkpoint_npc_unavailable", "NPC 及其库存尚未加载。");
        }
        String chestHash = Warehouse.digest(Warehouse.open(level, supply.source));
        String cargoHash = Warehouse.cargoDigest(worker.inventory());
        if (supply.intent != null) {
            JsonObject intent = supply.intent;
            if (matchesTransfer(intent, "before", chestHash, cargoHash)) {
                supply.intent = null;
                persist();
            } else if (matchesTransfer(intent, "after", chestHash, cargoHash)) {
                finishTransfer(intent, true);
                persist();
            } else {
                throw Wire.conflict("warehouse_checkpoint_mismatch", "供料箱和 NPC 背包未处于同一搬运检查点。");
            }
        }
        if (!supply.checked && (!supply.stockHash.equals(chestHash) || !current.cargo.equals(cargo()))) {
            throw Wire.conflict("warehouse_checkpoint_mismatch", "重开后的供料箱或背包与保存记录不一致。");
        }
        supply.checked = true;
    }

    private static boolean matchesTransfer(JsonObject intent, String phase, String chest, String npc) {
        return chest.equals(intent.get("warehouse_" + phase + "_sha256").getAsString())
                && npc.equals(intent.get("npc_" + phase + "_sha256").getAsString());
    }

    private void notifySupplyPause(Wire.Problem problem) {
        if (current.supply == null) {
            return;
        }
        ServerPlayer player = level.getServer().getPlayerList().getPlayer(current.supply.source.owner());
        if (player != null) {
            JsonObject report = materials(player.getUUID());
            player.sendSystemMessage(Component.literal("[Architect] 施工暂停。" + problem.getMessage() + " "
                    + Warehouse.shortages(report) + "。核对后可输入 /architect resume " + current.plan.id() + "。"));
        }
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

    private void requireNoIntent() {
        if (current.intent >= 0) {
            throw Wire.conflict("interrupted_interaction", "The journal has an unconfirmed interaction; inspect the world checkpoint");
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
        if (current.preparing() && current.clearanceStarted == 0) {
            current.clearanceStarted = BuildPerformance.clock();
        }
            current.performance.span(BuildPerformance.Span.APPROVAL, approvalStarted);
        }
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
        boolean adjacent = current.plan.optimizedCottage() && !previous.remove() && !next.remove()
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
                    boolean priorNavigationFailure = site.worldCopy() && current.state == State.FAILED
                            && current.reason != null && RETRYABLE.contains(current.reason);
                    if (!priorNavigationFailure) {
                        requireState(State.PAUSED);
                    }
                    requireNoIntent();
                    if (candidate != null) {
                        throw Wire.conflict("change_pending", "Approve or reject the pending change first");
                    }
                    long recoveryStarted = BuildPerformance.clock();
                    site.requireLoaded(level);
                    current.conflicts = checkpointConflicts();
                    if (!current.conflicts.isEmpty()) {
                        pause("world_conflict");
                        persist();
                        throw Wire.conflict("world_conflict", "The saved construction positions no longer match the world");
                    }
                    checkSupplyRecovery();
                    requireCheckpointWorker();
                    if (current.supply != null) {
                        current.supply.stockHash = Warehouse.digest(Warehouse.open(level, current.supply.source));
                        current.supply.phase = "ready";
                    }
                    current.state = State.RUNNING;
                    current.reason = null;
                    current.verification = null;
                    current.retries = 0;
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
                    finish(State.CANCELLED, "cancelled_by_request");
                    persist();
                }
            }
            default -> throw new IllegalArgumentException(operation);
        }
        ForgeBridge.LOGGER.info("construction_control {} {} tick={}", id, operation, level.getGameTime());
        return status(false);
    }

    private void requireCheckpointWorker() {
        if (!worker.attach(current.npc) || !current.cargo.equals(cargo())) {
            throw Wire.conflict("checkpoint_npc_unavailable", "The recorded worker or inventory could not be verified");
        }
    }

    private void requireState(State state) {
        if (current.state != state) {
            throw Wire.conflict("invalid_job_state", "Job state is " + current.state.wire());
        }
    }

    private void pause(String reason) {
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
        if (editedLevel != level || entity == null || worker.isActor(entity) || !site.area().contains(pos)) {
            return;
        }
        if (current == null || !current.snapshot.recordExternalEdit(pos)) {
            return;
        }
        if (current.state == State.RUNNING && current.snapshot.footprint().contains(pos)) {
            pause("world_conflict");
        }
        try {
            persist();
        } catch (Wire.Problem ignored) {
            // The journal failure halts the worker. The player's interaction can still finish.
        }
    }

    void tick() {
        if (current != null) {
            current.planning.expire(current);
        }
        if (journalBlocked || current == null || current.state != State.RUNNING || level.getGameTime() < current.nextTick) {
            return;
        }
        long tickStarted = BuildPerformance.clock();
        try {
            if (current.autonomous) {
                requireAutonomy();
            }
            site.requireLoaded(level);
            if (current.supply != null) {
                long inventoryStarted = BuildPerformance.clock();
                try {
                    checkLiveSupply();
                } finally {
                    current.performance.span(BuildPerformance.Span.INVENTORY, inventoryStarted);
                }
            }
            executeNext();
        } catch (Wire.Problem problem) {
            if (!journalBlocked) {
                pause(problem.code);
                persistAfterFailure();
                notifySupplyPause(problem);
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
        if (current.cursor == current.steps.size()) {
            finish(State.COMPLETED, null);
            persist();
            return;
        }
        WorkStep step = current.steps.get(current.cursor);
        if (current.autonomous && !step.clearance() && !current.groundVerified
                && current.cursor == current.initialClearanceSteps) {
            verifyPreparedGround();
        }
        BlockState actual = level.getBlockState(step.pos());
        if (actual != step.before() && admitNewObstacle(step, actual)) {
            return;
        }
        if (actual != step.before() || actual != current.snapshot.expected(step.pos())
                || current.snapshot.modifiedByPlayer(step.pos())
                || step.remove() && !step.clearance() && !current.snapshot.owns(step.pos())) {
            current.conflicts = checkpointConflicts();
            pause("world_conflict");
            persist();
            if (current.autonomous) {
                notifySupplyPause(Wire.conflict("world_conflict", "现场出现无法自动处理的改动，请确认是否保留或调整方案。"));
            }
            return;
        }

        ConstructionWorker.Result result;
        if (actual == step.after()) {
            result = new ConstructionWorker.Result("already_present", null, null);
            worker.halt();
        } else {
            if (step.clearance()) {
                new Clearance(level, site, current.snapshot::modifiedByPlayer)
                        .requireLive(step, current.clearancePositions, current.snapshot::expected);
            } else {
                requireSafeMutation(step, current.predecessor != null);
            }
            if (current.supply != null && !worker.hasSupply(step)) {
                resupply();
                return;
            }
            result = worker.act(step, this::beginInteraction);
        }
        if (result == null) {
            return;
        }

        boolean navigationFailure = result.status().equals("failed") && current.intent < 0
                && result.reason() != null && RETRYABLE.contains(result.reason());
        boolean retry = navigationFailure && (site.worldCopy() || current.predecessor != null)
                && current.retries == 0;
        JsonObject record = step.json(retry ? "retrying" : result.status());
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
            current.performance.action(retry ? "retrying:" + result.reason() : result.status());
        }
        current.records.add(record);
        ForgeBridge.LOGGER.info("construction_action {} {} {}", current.plan.id(), current.cursor, record);
        if (retry) {
            current.retries++;
            schedule();
        } else if (site.worldCopy() && navigationFailure) {
            pause(result.reason());
            notifySupplyPause(Wire.conflict(result.reason(), switch (result.reason()) {
                case "placement_obstructed" -> "有实体持续占用放置空间。请让出目标位置后确认继续。";
                case "no_reachable_break_stance" -> "找不到区域内可达的拆除站位。";
                case "no_reachable_placement_stance" -> "找不到区域内可达的放置站位。";
                default -> "NPC 导航超时。请检查通道和地面高差。";
            }));
        } else if (result.status().equals("failed")) {
            if (actual == level.getBlockState(step.pos()) && current.cargo.equals(cargo())) {
                current.intent = -1;
            }
            finish(State.FAILED, result.reason());
        } else {
            if (result.status().equals("placed") || result.status().equals("removed")) {
                current.snapshot.written(step);
                current.cargo = cargo();
                if (current.supply != null) {
                    current.supply.phase = "working";
                }
                if (step.remove()) {
                    current.removed++;
                } else {
                    current.placed++;
                }
            }
            current.cursor++;
            if (step.clearance() && !current.preparing()) {
                current.performance.span(BuildPerformance.Span.CLEARANCE, current.clearanceStarted);
                current.clearanceStarted = 0;
            }
            current.intent = -1;
            current.retries = 0;
            if (current.cursor == current.steps.size()) {
                finish(State.COMPLETED, null);
            } else {
                scheduleAfter(step);
            }
        }
        persist();
    }

    private boolean admitNewObstacle(WorkStep pending, BlockState actual) {
        if (!current.autonomous || current.intent >= 0 || pending.remove() || !pending.before().isAir()
                || current.clearanceReplans >= ConstructionJob.MAX_CLEARANCE_REPLANS
                || Clearance.kind(actual).equals("unknown")) {
            return false;
        }
        Clearance check = new Clearance(level, site, current.snapshot::modifiedByPlayer);
        Clearance.Finding finding = check.inspect(pending.pos());
        if (finding.risk() != null || finding.cells().isEmpty() || finding.cells().stream().anyMatch(pos ->
                !current.snapshot.expected(pos).isAir() || current.snapshot.modifiedByPlayer(pos))) {
            return false;
        }
        Map<BlockPos, BlockState> additions = check.states(finding);
        Set<BlockPos> touched = new HashSet<>(current.clearancePositions);
        touched.addAll(additions.keySet());
        if (touched.size() > Clearance.MAX_CELLS) {
            return false;
        }
        for (BlockPos pos : additions.keySet()) {
            BuildPlan.Target above = current.plan.at(pos.above());
            if (above != null && level.getBlockState(pos.above()) == above.state()) {
                return false;
            }
        }
        List<WorkStep> inserted = additions.entrySet().stream().filter(e -> !Clearance.upperHalf(e.getValue()))
                .map(e -> WorkStep.clear(current.plan, e.getKey(), e.getValue()))
                .sorted(Clearance.order("route_first")).toList();
        worker.halt();
        current.planning.invalidate("worksite_replanned");
        current.snapshot.observeClearance(additions);
        current.clearancePositions.addAll(additions.keySet());
        List<WorkStep> queue = new ArrayList<>(current.steps);
        queue.addAll(current.cursor, inserted);
        current.steps = List.copyOf(queue);
        current.clearanceReplans++;
        current.retries = 0;
        current.clearanceStarted = BuildPerformance.clock();
        current.performance.event("worksite_replanned");
        ForgeBridge.LOGGER.info("worksite_replanned {} cells={} at={}", current.plan.id(), additions.size(), current.cursor);
        schedule();
        persist();
        return true;
    }

    private void verifyPreparedGround() {
        for (BlockPos pos : current.clearancePositions) {
            if (!level.getBlockState(pos).isAir() || current.snapshot.modifiedByPlayer(pos)) {
                throw Wire.conflict("worksite_preparation_changed", "已清理位置发生变化，请重新核对地基。");
            }
        }
        Set<BlockPos> supported = new HashSet<>();
        for (BuildPlan.Target target : current.plan.targets()) {
            requirePlannedSupport(target, true, supported, Set.of());
            supported.add(target.world());
        }
        current.groundVerified = true;
    }

    private void beginInteraction() {
        requireNoIntent();
        current.intent = current.cursor;
        persist();
    }

    private void requireSafeMutation(WorkStep step, boolean revision) {
        BlockPos target = step.pos();
        if (!site.canBuildAt(target)) {
            throw Wire.conflict("outside_approved_area", "Target outside the approved area");
        }
        if (site.worldCopy() && !step.remove() && !site.area().contains(target.below())
                && BuildTerrain.coversGrass(level, target, step.after(), level.getBlockState(target.below()))) {
            throw Wire.conflict("terrain_effect_outside_scope", "The block would cover grass outside the approved area");
        }
        for (Direction direction : Direction.values()) {
            BlockPos neighbor = target.relative(direction);
            if (site.area().contains(neighbor) && !revision) {
                continue;
            }
            BlockState state = level.getBlockState(neighbor);
            // Vanilla grass depends on its vertical support and, for tall grass, the other half.
            boolean sideGrass = site.worldCopy() && site.area().contains(neighbor)
                    && direction.getAxis().isHorizontal() && (state.is(Blocks.GRASS) || state.is(Blocks.TALL_GRASS))
                    && state.canSurvive(level, neighbor);
            if (state.isAir() || borderBlocks.contains(state.getBlock()) || sideGrass) {
                continue;
            }
            boolean stairsMayChange = direction.getAxis().isHorizontal() && state.getBlock() instanceof StairBlock
                    && (step.before().getBlock() instanceof StairBlock || step.after().getBlock() instanceof StairBlock);
            if (!site.area().contains(neighbor) || !palette.contains(state.getBlock())
                    || !state.getFluidState().isEmpty() || stairsMayChange) {
                throw Wire.conflict("unsafe_site_boundary", "Sensitive neighbor at " + neighbor.toShortString());
            }
        }
    }

    private JsonArray checkpointConflicts() {
        JsonArray conflicts = new JsonArray();
        current.snapshot.footprint().stream().sorted(BuildPlan.BUILD_ORDER).forEach(pos -> {
            BlockState actual = level.getBlockState(pos);
            BlockState expected = current.snapshot.expected(pos);
            boolean player = current.snapshot.modifiedByPlayer(pos);
            if (actual != expected || player) {
                JsonObject row = new JsonObject();
                row.add("world", Wire.position(pos));
                row.add("expected", Wire.state(expected));
                row.add("actual", Wire.state(actual));
                row.addProperty("reason", player ? "player_modified" : "checkpoint_state_mismatch");
                conflicts.add(row);
            }
        });
        return conflicts;
    }

    private void finish(State state, String reason) {
        worker.halt();
        current.state = state;
        current.reason = reason;
        current.planning.invalidate(reason == null ? state.wire() : reason);
        long verificationStarted = BuildPerformance.clock();
        try {
            site.requireLoaded(level);
            current.verification = verify();
            if (state == State.COMPLETED && !current.verification.get("passed").getAsBoolean()) {
                current.state = State.FAILED;
                current.reason = "world_verification_failed";
            }
        } catch (Wire.Problem problem) {
            if (state == State.COMPLETED) {
                current.state = State.FAILED;
                current.reason = problem.code;
            }
            current.verification = new JsonObject();
            current.verification.addProperty("passed", false);
            current.verification.addProperty("error", problem.code);
        }
        if (current.supply != null) {
            current.supply.phase = current.state.wire();
        }
        current.performance.span(BuildPerformance.Span.WORLD_VERIFY, verificationStarted);
        ForgeBridge.LOGGER.info("construction_finish {} state={} reason={} verification={}",
                current.plan.id(), current.state.wire(), current.reason, current.verification);
    }

    private JsonObject verify() {
        Set<BlockPos> footprint = current.snapshot.footprint();
        JsonArray targetStates = new JsonArray();
        JsonArray retiredStates = new JsonArray();
        JsonArray mismatches = new JsonArray();
        JsonArray retiredMismatches = new JsonArray();
        int matched = 0;
        for (BlockPos pos : footprint.stream().sorted(BuildPlan.BUILD_ORDER).toList()) {
            BuildPlan.Target target = current.plan.at(pos);
            BlockState actual = level.getBlockState(pos);
            if (target == null) {
                JsonObject readback = difference(pos, AIR, actual);
                retiredStates.add(readback);
                if (!actual.isAir()) {
                    retiredMismatches.add(readback);
                }
            } else {
                JsonObject readback = difference(pos, target.state(), actual);
                targetStates.add(readback);
                if (actual == target.state()) {
                    matched++;
                } else {
                    mismatches.add(readback);
                }
            }
        }

        JsonArray changes = new JsonArray();
        JsonArray natural = new JsonArray();
        int outside = 0;
        int outsideChecked = 0;
        int checked = 0;
        int changed = 0;
        BuildSite.Box zone = site.area().halo();
        for (BlockPos pos : BlockPos.betweenClosed(zone.min(), zone.max())) {
            if (footprint.contains(pos)) {
                continue;
            }
            checked++;
            boolean beyond = !site.area().contains(pos);
            if (beyond) {
                outsideChecked++;
            }
            BlockState actual = level.getBlockState(pos);
            BlockState before = current.snapshot.initial(pos);
            if (actual != before) {
                if (!beyond && site.worldCopy() && coveredGrassChange(pos, before, actual)) {
                    natural.add(difference(pos, before, actual));
                    continue;
                }
                if (beyond) {
                    outside++;
                }
                changed++;
                if (changes.size() < 32) {
                    changes.add(difference(pos, before, actual));
                }
            }
        }
        JsonObject data = new JsonObject();
        data.addProperty("passed", mismatches.isEmpty() && retiredMismatches.isEmpty() && changed == 0);
        data.addProperty("matched", matched);
        data.add("target_mismatches", mismatches);
        data.add("retired_mismatches", retiredMismatches);
        data.add("target_states", targetStates);
        data.add("retired_states", retiredStates);
        data.add("monitored_bounds", zone.json());
        data.addProperty("non_target_cells_checked", checked);
        data.addProperty("outside_approved_cells_checked", outsideChecked);
        data.addProperty("non_target_changes", changed);
        data.addProperty("outside_approved_changes", outside);
        data.add("first_non_target_changes", changes);
        if (site.worldCopy()) {
            data.add("expected_covered_grass_changes", natural);
        }
        return data;
    }

    private boolean coveredGrassChange(BlockPos floor, BlockState original, BlockState actual) {
        BlockPos above = floor.above();
        BuildPlan.Target cover = current.plan.at(above);
        return actual == Blocks.DIRT.defaultBlockState() && cover != null
                && !current.snapshot.modifiedByPlayer(floor) && current.snapshot.owns(above)
                && level.getBlockState(above) == cover.state()
                && BuildTerrain.coversGrass(level, above, cover.state(), original);
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
        return id != null && id.equals(currentJobId()) && current.state != State.RUNNING && current.intent < 0
                && (current.supply == null || current.supply.intent == null);
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
        if (!job.equals(currentJobId()) || journalBlocked || current.intent >= 0
                || current.supply != null && current.supply.intent != null) {
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
                        "change_rejected", "server_stopping").contains(current.reason))) {
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
            if (current.autonomous) {
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
                    JsonObject stock = Warehouse.summary(level, current.steps, offer.get("first_step").getAsInt(),
                            worker.inventory(), Warehouse.open(level, current.supply.source), new JsonArray());
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
        if (current.intent >= 0 || current.supply != null && current.supply.intent != null) {
            throw Wire.conflict("planning_interaction_in_flight", "当前交互仍在核账，保留原施工顺序。");
        }
        if (group != null) {
            site.requireLoaded(level);
            if (current.supply != null) {
                checkLiveSupply();
            }
        }
        current.planning.resolve(current, group, reason, level.getGameTime(), step -> {
            BlockState state = level.getBlockState(step.pos());
            if (state != step.before() || state != current.snapshot.expected(step.pos())
                    || current.snapshot.modifiedByPlayer(step.pos())) {
                throw Wire.conflict("planning_world_changed", "工作组内的方块已发生变化，保留原施工顺序。");
            }
            requireSafeMutation(step, current.predecessor != null);
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

    private JsonObject cargo() {
        JsonObject state = worker.status();
        JsonObject cargo = new JsonObject();
        cargo.add("materials", state.get("materials"));
        cargo.add("tools", state.get("tools"));
        if (site.survival() && worker.inventory() != null) {
            cargo.addProperty("inventory_sha256", Warehouse.cargoDigest(worker.inventory()));
        }
        return cargo;
    }

    private JsonObject rereadClearanceDrops() {
        int observed = 0, present = 0;
        for (JsonElement element : current.records) {
            JsonObject record = element.getAsJsonObject();
            if (!record.get("operation").getAsString().equals("clear") || !record.has("interaction_evidence")) {
                continue;
            }
            for (JsonElement entry : record.getAsJsonObject("interaction_evidence").getAsJsonArray("drops")) {
                JsonObject drop = entry.getAsJsonObject();
                int count = drop.get("count").getAsInt();
                observed += count;
                if (drop.has("entity_id") && level.getEntity(BuildPlan.id(drop, "entity_id"))
                        instanceof net.minecraft.world.entity.item.ItemEntity ground
                        && Warehouse.id(ground.getItem().getItem()).equals(drop.get("id").getAsString())) {
                    present += Math.min(count, ground.getItem().getCount());
                }
            }
        }
        JsonObject result = new JsonObject();
        result.addProperty("spawned_items", observed);
        result.addProperty("still_observed_in_world", present);
        result.addProperty("unlocated_without_inventory_credit", observed - present);
        result.addProperty("recovered_items", 0);
        return result;
    }

    private JsonObject recovery() {
        JsonObject details = new JsonObject();
        try {
            site.requireLoaded(level);
            boolean entity = worker.attach(current.npc);
            JsonArray conflicts = checkpointConflicts();
            details.addProperty("world_matches_checkpoint", conflicts.isEmpty());
            details.addProperty("npc_reused", entity);
            details.addProperty("inventory_matches_checkpoint", entity && current.cargo.equals(cargo()));
            details.add("conflicts", conflicts);
            details.addProperty("interaction_confirmed", current.intent < 0);
            if (current.autonomous) {
                details.add("clearance_drops", rereadClearanceDrops());
            }
            if (current.supply != null) {
                boolean matched = current.supply.stockHash.equals(
                        Warehouse.digest(Warehouse.open(level, current.supply.source)));
                details.addProperty("warehouse_matches_checkpoint", matched);
                details.addProperty("warehouse_transfer_confirmed", current.supply.intent == null);
                if (entity && matched && current.supply.intent == null && current.cargo.equals(cargo())) {
                    current.supply.checked = true;
                }
            }
        } catch (Wire.Problem problem) {
            details.addProperty("waiting", problem.code);
        }
        return details;
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
            if (current.reopened && current.state != State.RUNNING) {
                response.add("recovery", recovery());
            }
            response.add("job", current.status(detailed));
        }
        if (detailed && site.survival()) {
            response.add("materials", materials(warehouse == null ? null : warehouse.owner()));
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
        if (warehouse != null) {
            state.add("warehouse", warehouse.json());
        }
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
        autonomyRevoked = data.has("autonomy_revoked") && data.get("autonomy_revoked").getAsBoolean();
        if (data.has("survey_npc") && site.worldCopy()) {
            surveyNpc = BuildPlan.id(data, "survey_npc");
        }
        if (data.has("warehouse")) {
            warehouse = Warehouse.Source.read(Wire.object(data.get("warehouse"), "warehouse"), site);
        }
        if (data.has("current")) {
            JsonObject checkpoint = Wire.object(data.get("current"), "current job");
            Registration entry = registered.get(BuildPlan.id(checkpoint, "job_id"));
            if (entry == null || !entry.disposition().equals("approved")) {
                throw Wire.invalid("The current job is missing its approved version");
            }
            current = ConstructionJob.restore(checkpoint, entry.plan(), palette, site);
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
