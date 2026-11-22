package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.mojang.authlib.GameProfile;
import dev.duzo.players.entities.FakePlayerEntity;
import dev.duzo.players.entities.possession.PossessionReconciler;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.network.chat.Component;
import net.minecraft.network.protocol.game.ServerboundPlayerActionPacket;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.animal.Animal;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.level.pathfinder.BlockPathTypes;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.SimpleContainer;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.tags.BlockTags;
import net.minecraft.util.Mth;
import net.minecraft.world.item.context.BlockPlaceContext;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.BlockStateProperties;
import net.minecraft.world.level.pathfinder.Path;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;
import net.minecraftforge.common.ForgeHooks;
import net.minecraftforge.common.util.FakePlayer;
import net.minecraftforge.eventbus.api.Event;
import net.minecraftforge.registries.ForgeRegistries;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.function.Predicate;
import java.util.function.Supplier;

final class FakePlayersWorker implements ConstructionWorker {
    private static final double REACH = 4.5;
    private BuildPerformance performance = new BuildPerformance();
    private static final int MOVE_LIMIT_TICKS = 300;
    private static final int STANCE_RADIUS = 4;
    private static final int NUDGE_AFTER_TICKS = 8;
    private static final int DEFER_AFTER_TICKS = 24;
    private static final BlockState TEMPORARY_BLOCK = Blocks.DIRT.defaultBlockState();
    private BlockPos obstruction;
    private long obstructionSince;
    private boolean nudged;
    private static final Direction[] FACES = {Direction.UP, Direction.NORTH, Direction.SOUTH,
            Direction.WEST, Direction.EAST, Direction.DOWN};

    private static final int SURVEY_LIMIT_TICKS = 110;
    private static final int SURVEY_RADIUS = 8;

    private final ServerLevel level;
    private final BuildSite site;
    private final Set<Block> anchors;
    private final Set<BlockPos> triedStances = new HashSet<>();
    private FakePlayerEntity npc;
    private FakePlayer actor;
    private BlockPos destination;
    private long deadline;
    private Vec3 lastPosition;
    private double walked;
    private BlockPos mining;
    private long miningSince;
    private Map<BlockPos, BlockState> scaffolds = new LinkedHashMap<>();
    private Set<BlockPos> designCells = Set.of();
    private final Set<BlockPos> skippedStock = new HashSet<>();
    private BlockPos supplyCenter;
    private BlockPos stockTarget;
    private BlockPos actionTarget;
    private ScaffoldRoute scaffoldRoute;
    private BlockPos jumpBlock;
    private long jumpStarted;
    private boolean recoveringScaffold;
    private boolean soughtScaffold;
    private int scaffoldDirtCollected;
    private BlockPos dirtTarget;
    private final Set<BlockPos> unreachableSoil = new HashSet<>();
    private CraftingAssistant crafting;
    private SupplyCheckpoint workshop;
    private final Map<CraftingAssistant.Station, List<BlockPos>> workshopNearby = new java.util.EnumMap<>(
            CraftingAssistant.Station.class);
    private final Set<BlockPos> skippedStations = new HashSet<>();
    private BlockPos stationScanCenter;
    private long stationScanUntil;
    private BlockPos stationTarget;
    private CraftingAssistant.Plan fabrication;
    private Map<Item, Integer> fabricationPickups;
    private int batchCursor;
    private long cookingUntil;
    private long greetingAfter;
    private long speechAfter;
    private boolean greeted;
    private Vec3 ceremonyLook;
    private long ceremonyMoveUntil;
    private long ceremonyLookUntil;

    private record ScaffoldRoute(BlockPos base, int workY) {}
    private record Descent(BlockPos edge, BlockPos landing, long deadline) {}
    private Descent descent;
    private final Set<BlockPos> triedDescents = new HashSet<>();
    private BlockPos surveyDestination;
    private BlockPos surveyStart;
    private long surveyDeadline;
    private boolean surveyPathStarted;

    FakePlayersWorker(ServerLevel level, BuildSite site, Set<Block> palette) {
        this.level = level;
        this.site = site;
        anchors = new HashSet<>(palette);
        anchors.addAll(site.worldCopy() ? BuildTerrain.NATURAL_SUPPORTS
                : Set.of(Blocks.GRASS_BLOCK, Blocks.DIRT, Blocks.BEDROCK));
    }

    @Override
    public void prepare(List<BuildPlan.Target> targets, boolean removals) {
        ceremonyLook = null;
        if (npc == null || !npc.isAlive()) {
            spawn();
        }
        if (site.survival()) {
            if (!available()) {
                throw Wire.conflict("npc_unavailable", "The survival worker and its inventory must be available");
            }
        } else {
            preparePreset(targets, removals);
        }
        halt();
        walked = 0;
        lastPosition = npc.position();
    }

    private void preparePreset(List<BuildPlan.Target> targets, boolean removals) {
        Map<Item, Integer> totals = new LinkedHashMap<>();
        for (BuildPlan.Target target : targets) {
            totals.merge(target.item(), 1, Integer::sum);
        }
        List<ItemStack> supplied = new ArrayList<>();
        for (Map.Entry<Item, Integer> total : totals.entrySet()) {
            int stackLimit = new ItemStack(total.getKey()).getMaxStackSize();
            for (int left = total.getValue(); left > 0; left -= stackLimit) {
                supplied.add(new ItemStack(total.getKey(), Math.min(left, stackLimit)));
            }
        }
        for (Item tool : List.of(Items.IRON_PICKAXE, Items.IRON_AXE)) {
            int existing = itemSlot(tool);
            if (existing >= 0) {
                supplied.add(npc.getInventory().getItem(existing).copy());
            } else if (removals) {
                supplied.add(new ItemStack(tool));
            }
        }
        if (npc.isVacant() || npc.isUnreconciled() || supplied.size() > npc.getInventory().getContainerSize()) {
            throw Wire.conflict("npc_unavailable", "The worker cannot accept the preset materials");
        }
        npc.getInventory().clearContent();
        for (int slot = 0; slot < supplied.size(); slot++) {
            npc.getInventory().setItem(slot, supplied.get(slot));
        }
    }

    @Override
    public void startSurvey() {
        ceremonyLook = null;
        boolean spawned = npc == null;
        if (spawned) {
            if (!surveyFooting(site.spawn())) {
                throw Wire.conflict("survey_unsafe_footing", "观察出生点没有安全站位。");
            }
            spawn();
        }
        if (!available() || Math.abs(npc.getY() - npc.blockPosition().getY()) > 0.15
                || !surveyFooting(npc.blockPosition())) {
            throw Wire.conflict("survey_unsafe_footing", "NPC 当前没有安全站位。");
        }
        halt();
    }

    @Override
    public void moveForSurvey(BlockPos goal) {
        if (!available() || !npc.blockPosition().closerThan(goal, SURVEY_RADIUS + 0.01)
                || !surveyFooting(goal)) {
            throw Wire.conflict("survey_unsafe_footing", "观察目标失载、过远或没有安全站位。");
        }
        BlockPos start = npc.blockPosition();
        if (start.equals(goal)) {
            throw Wire.conflict("survey_same_stance", "NPC 已站在该位置。");
        }
        surveyStart = start;
        surveyDestination = goal;
        surveyDeadline = level.getGameTime() + SURVEY_LIMIT_TICKS;
        surveyPathStarted = false;
        // A frozen FakePlayer can stand on the floor with its onGround flag unset.
        // Tick its ordinary physics before asking ground navigation for a route.
        npc.setNoAi(false);
    }

    @Override
    public boolean surveyArrived() {
        if (!available() || surveyDestination == null || !surveyFooting(surveyDestination)
                || !site.area().contains(npc.blockPosition().above()) || !level.hasChunkAt(npc.blockPosition())) {
            throw Wire.conflict("survey_route_changed", "观察移动已停止，站位或路线失效。");
        }
        if (npc.onGround() && npc.blockPosition().equals(surveyDestination)
                && surveyFooting(npc.blockPosition())) {
            halt();
            return true;
        }
        if (level.getGameTime() >= surveyDeadline) {
            throw Wire.conflict("survey_navigation_timeout", "观察移动超时，NPC 已停止。");
        }
        if (npc.isInWaterOrBubble() || npc.isInLava() || npc.fallDistance > 1.0f
                || !npc.blockPosition().closerThan(surveyStart, SURVEY_RADIUS + 1.5)) {
            throw Wire.conflict("survey_route_changed", "观察行走超出安全范围。");
        }
        if (!surveyPathStarted) {
            if (npc.onGround()) {
                if (!npc.blockPosition().equals(surveyStart) || !surveyFooting(surveyStart)) {
                    throw Wire.conflict("survey_route_changed", "观察起点已改变。");
                }
                Path path = npc.getNavigation().createPath(surveyDestination, 0);
                if (path == null || !path.canReach() || !surveyPath(path) || !npc.getNavigation().moveTo(path, 1.0)) {
                    throw Wire.conflict("survey_unreachable", "NPC 无法沿安全路径到达站位。");
                }
                surveyPathStarted = true;
            }
            return false;
        }
        Path path = npc.getNavigation().getPath();
        if (path == null || !path.getTarget().equals(surveyDestination) || !surveyPath(path)
                || npc.getNavigation().isDone()) {
            throw Wire.conflict("survey_route_changed", "NPC 无法继续沿安全范围内的观察路线移动。");
        }
        return false;
    }

    @Override
    public void lookForSurvey(BlockPos point) {
        if (!available() || !site.area().contains(point) || !level.hasChunkAt(point) || !surveyFooting(npc.blockPosition())
                || !npc.blockPosition().closerThan(point, 13)) {
            throw Wire.conflict("survey_target_outside_scope", "观察方向超出已加载、获准的近距离范围。");
        }
        Vec3 delta = Vec3.atCenterOf(point).subtract(npc.getEyePosition());
        float yaw = (float) (Mth.atan2(delta.z, delta.x) * Mth.RAD_TO_DEG) - 90;
        float pitch = (float) (-Mth.atan2(delta.y, delta.horizontalDistance()) * Mth.RAD_TO_DEG);
        npc.setYRot(yaw);
        npc.setYHeadRot(yaw);
        npc.setYBodyRot(yaw);
        npc.setXRot(pitch);
    }

    private boolean surveyPath(Path path) {
        if (path.getNodeCount() > 13) {
            return false;
        }
        BlockPos previous = surveyStart;
        for (int i = 0; i < path.getNodeCount(); i++) {
            BlockPos node = path.getNode(i).asBlockPos();
            if (!surveyFooting(node) || !surveyStart.closerThan(node, SURVEY_RADIUS + 1)
                    || Math.abs(previous.getY() - node.getY()) > 1) {
                return false;
            }
            // Vanilla can skip to a diagonal node. Check both corner columns as well.
            if (previous.getX() != node.getX() && previous.getZ() != node.getZ()
                    && (!surveyFooting(new BlockPos(previous.getX(), node.getY(), node.getZ()))
                    || !surveyFooting(new BlockPos(node.getX(), node.getY(), previous.getZ())))) {
                return false;
            }
            previous = node;
        }
        return true;
    }

    private boolean surveyFooting(BlockPos feet) {
        if (!site.area().contains(feet) || !site.area().contains(feet.above())
                || !level.isInWorldBounds(feet.above()) || !level.hasChunkAt(feet)
                || !BuildTerrain.canStand(level, feet)) {
            return false;
        }
        for (BlockPos cell : List.of(feet.below(), feet, feet.above())) {
            if (WorldObservation.hazard(level.getBlockState(cell))) {
                return false;
            }
        }
        AABB bounds = npc == null ? new AABB(feet).expandTowards(0, 0.8, 0)
                : npc.getBoundingBox().move(Vec3.atBottomCenterOf(feet).subtract(npc.position()));
        return level.noCollision(npc, bounds);
    }

    private void spawn() {
        BlockPos feet = site.spawn();
        if (!canStand(feet)) {
            throw Wire.conflict("spawn_blocked", "The approved NPC spawn has no walkable floor");
        }
        FakePlayerEntity worker = new FakePlayerEntity(level);
        worker.moveTo(feet.getX() + 0.5, feet.getY(), feet.getZ() + 0.5, 0, 0);
        worker.setCustomName(Component.literal("MC Builder"));
        worker.setCustomNameVisible(true);
        worker.setPersistenceRequired();
        worker.setJobPaused(true);
        worker.addTag(ForgeBridge.MOD_ID);
        PossessionReconciler.reconcile(worker, level);
        if (worker.isUnreconciled() || worker.isVacant()) {
            throw Wire.conflict("npc_unavailable", "Fake Players could not initialize the worker inventory");
        }
        if (!level.noCollision(worker) || !level.addFreshEntity(worker)) {
            throw Wire.conflict("spawn_blocked", "Could not spawn players:fake_player at the approved position");
        }
        bind(worker);
    }

    private void bind(FakePlayerEntity worker) {
        npc = worker;
        npc.getNavigation().stop();
        npc.setJobPaused(true);
        npc.setNoAi(false);
        npc.setCanPickUpLoot(true);
        // Construction owns movement. Keep floating and looking while paths and collisions remain native.
        npc.removeAllGoals(goal -> !(goal instanceof net.minecraft.world.entity.ai.goal.FloatGoal
                || goal instanceof net.minecraft.world.entity.ai.goal.LookAtPlayerGoal));
        for (BlockPathTypes hazard : List.of(BlockPathTypes.LAVA, BlockPathTypes.DAMAGE_FIRE,
                BlockPathTypes.DANGER_FIRE, BlockPathTypes.POWDER_SNOW)) {
            npc.setPathfindingMalus(hazard, -1.0F);
        }
        actor = new FakePlayer(level, new GameProfile(npc.getUUID(), "MCBuilder"));
        actor.gameMode.changeGameModeForPlayer(GameType.SURVIVAL);
        lastPosition = npc.position();
    }

    @Override
    public boolean attach(UUID id) {
        if (npc != null && id.equals(npc.getUUID()) && available()) {
            return true;
        }
        if (!(level.getEntity(id) instanceof FakePlayerEntity worker)
                || !worker.getTags().contains(ForgeBridge.MOD_ID) || !worker.isAlive()
                || worker.isVacant() || !canTravelAt(worker.blockPosition())) {
            return false;
        }
        PossessionReconciler.reconcile(worker, level);
        if (worker.isUnreconciled()) {
            return false;
        }
        bind(worker);
        return available();
    }

    @Override
    public UUID id() {
        return npc == null ? null : npc.getUUID();
    }

    @Override
    public boolean isActor(Entity entity) {
        return entity != null && entity == actor;
    }

    private boolean available() {
        return npc != null && npc.isAlive() && !npc.isVacant() && !npc.isUnreconciled()
                && !npc.isPassenger() && canTravelAt(npc.blockPosition());
    }

    @Override
    public void profile(BuildPerformance performance) {
        this.performance = performance;
    }

    @Override
    public String activity() {
        if (fabrication != null && fabricationPickups.isEmpty()) {
            return "processing_materials";
        }
        if (destination != null) {
            return "navigating";
        }
        if (obstruction != null) {
            return "waiting_for_space";
        }
        return recoveringScaffold ? "cleaning_scaffolds" : scaffoldRoute != null ? "scaffolding"
                : mining != null ? "mining" : "working";
    }

    @Override
    public Vec3 position() {
        return available() ? npc.position() : null;
    }

    @Override
    public Result act(WorkStep step) {
        if (tickDescent()) {
            return null;
        }
        if (!available()) {
            return failure("npc_unavailable");
        }
        if (!step.pos().equals(actionTarget)) {
            haltAction();
            actionTarget = step.pos();
        }
        if (recoveringScaffold) {
            if (!cleanScaffolds()) {
                return null;
            }
            recoveringScaffold = false;
        }
        if (jumpBlock != null) {
            return finishScaffoldJump();
        }
        Vec3 current = trackPosition();
        Item item = Warehouse.item(step);
        int slot = item == Items.AIR ? -1 : supplySlot(step);
        if (item != Items.AIR && slot < 0 && !Warehouse.optionalTool(step)) {
            return failure(step.remove() ? "out_of_tool" : "out_of_material");
        }
        ItemStack stack = slot < 0 ? ItemStack.EMPTY : npc.getInventory().getItem(slot);
        npc.setDisplayItem(stack);
        Aim aim = chooseAim(step, stack, current);
        if (aim != null && (!new AABB(step.pos()).intersects(npc.getBoundingBox()) || step.remove())) {
            if (!step.remove() && !level.isUnobstructed(step.after(), step.pos(),
                    net.minecraft.world.phys.shapes.CollisionContext.of(actor))) {
                return avoidObstruction(step, stack, current, aim);
            }
            obstruction = null;
            stopNavigation();
            return step.remove() ? mine(step, slot, aim) : useItem(step.target(), slot, aim);
        }
        if (scaffoldRoute != null) {
            return climbScaffold();
        }
        stopMining();
        BlockPos feet = npc.blockPosition();
        String unreachable = step.remove() ? "no_reachable_break_stance" : "no_reachable_placement_stance";
        if (scaffolds.containsKey(feet.below()) && npc.onGround()) {
            ScaffoldRoute higher = scaffoldRouteAt(step, stack, feet, Math.min(step.pos().getY() + 1, site.area().max().getY() - 1));
            if (higher != null) {
                scaffoldRoute = higher;
                deadline = level.getGameTime() + MOVE_LIMIT_TICKS;
                return climbScaffold();
            }
            // Step across when the finished structure offers a reachable platform at this height.
            if (navigate(step.pos(), current, stance -> stance.y >= feet.getY() - 1
                    && chooseAim(step, stack, stance) != null, unreachable) == null) {
                return null;
            }
            recoveringScaffold = true;
            triedStances.clear();
            deadline = 0;
            return null;
        }
        if (!soughtScaffold && npc.onGround() && step.pos().getY() >= feet.getY() + 4) {
            soughtScaffold = true;
            if (findScaffold(step, stack)) {
                return climbScaffold();
            }
        }
        String trouble = navigate(step.pos(), current, stance -> chooseAim(step, stack, stance) != null, unreachable);
        if (trouble == null) {
            return null;
        }
        if (npc.onGround() && findScaffold(step, stack)) {
            return climbScaffold();
        }
        if (startDescent(step.pos())) {
            return null;
        }
        if (!scaffolds.isEmpty()) {
            triedStances.clear();
            deadline = 0;
            recoveringScaffold = true;
            return null;
        }
        return deferred(trouble);
    }

    private Result avoidObstruction(WorkStep step, ItemStack stack, Vec3 current, Aim aim) {
        long now = level.getGameTime();
        if (!step.pos().equals(obstruction)) {
            haltAction();
            obstruction = step.pos();
            obstructionSince = now;
            nudged = false;
            triedStances.add(npc.blockPosition());
            navigate(step.pos(), current, feet -> chooseAim(step, stack, feet) != null, "no_alternate_stance");
        }
        if (now - obstructionSince >= NUDGE_AFTER_TICKS && !nudged) {
            lookAndSwing(aim);
            for (Animal animal : level.getEntitiesOfClass(Animal.class, new AABB(step.pos()).inflate(0.15), Entity::isAlive)) {
                for (Direction direction : Direction.Plane.HORIZONTAL) {
                    BlockPos escape = animal.blockPosition().relative(direction);
                    if (!escape.equals(step.pos()) && site.area().contains(escape) && BuildTerrain.canStand(level, escape)) {
                        animal.push(0.22 * direction.getStepX(), 0.0, 0.22 * direction.getStepZ());
                        break;
                    }
                }
            }
            nudged = true;
        }
        return now - obstructionSince >= DEFER_AFTER_TICKS ? deferred("waiting_for_space") : null;
    }

    @Override
    public void useScaffolds(Map<BlockPos, BlockState> blocks, Set<BlockPos> targets) {
        scaffolds = blocks;
        designCells = targets;
    }

    @Override
    public void useSupplyArea(BlockPos center) {
        if (!java.util.Objects.equals(supplyCenter, center)) {
            skippedStock.clear();
        }
        supplyCenter = center;
    }

    @Override
    public boolean inspectStock(BlockPos pos) {
        if (!available() || !level.hasChunkAt(pos) || level.getBlockEntity(pos) == null) {
            return true;
        }
        Vec3 current = trackPosition();
        Aim aim = chestAim(pos, current);
        if (aim != null) {
            lookAndSwing(aim);
            haltAction();
            return true;
        }
        return navigate(pos, current, feet -> chestAim(pos, feet) != null, "warehouse_unreachable") != null;
    }

    private boolean canTravelAt(BlockPos pos) {
        return site.area().contains(pos) || supplyCenter != null && supplyCenter.distSqr(pos) <= 16 * 16;
    }

    private boolean findScaffold(WorkStep step, ItemStack stack) {
        BlockPos target = step.pos();
        int feetY = npc.blockPosition().getY();
        if (target.getY() < feetY + 2) {
            return false;
        }
        int highest = Math.min(target.getY() + 1, site.area().max().getY() - 1);
        int lowest = Math.max(Math.max(feetY - 6, site.area().min().getY()), target.getY() - 128);
        List<ScaffoldRoute> routes = new ArrayList<>();
        BlockPos center = new BlockPos(target.getX(), 0, target.getZ());
        for (BlockPos horizontal : BlockPos.withinManhattan(center, STANCE_RADIUS, 0, STANCE_RADIUS)) {
            if (horizontal.distSqr(center) > 18) {
                continue;
            }
            for (int y = highest - 1; y >= lowest; y--) {
                BlockPos base = new BlockPos(horizontal.getX(), y, horizontal.getZ());
                if (!site.area().contains(base) || !canStand(base)) {
                    continue;
                }
                ScaffoldRoute route = scaffoldRouteAt(step, stack, base, highest);
                if (route != null) {
                    routes.add(route);
                }
            }
        }
        int dirt = count(Items.DIRT);
        Set<BlockPos> suppliedBases = new HashSet<>();
        for (ScaffoldRoute route : routes) {
            int shortage = route.workY() - route.base().getY() - dirt;
            if (!site.survival() || shortage <= 0 || scaffoldSoil(route.base(), BuildTerrain.standPoint(level, route.base()),
                    shortage).size() >= shortage) {
                suppliedBases.add(route.base());
            }
        }
        routes.sort(Comparator.comparing((ScaffoldRoute route) -> !suppliedBases.contains(route.base()))
                .thenComparingDouble(route -> route.base().distSqr(npc.blockPosition())));
        for (ScaffoldRoute route : routes) {
            if (route.base().equals(npc.blockPosition())) {
                stopNavigation();
                scaffoldRoute = route;
                sayOccasionally("屋顶这里够不着，我搭几个垫脚块，上去就好。");
                deadline = level.getGameTime() + MOVE_LIMIT_TICKS;
                return true;
            }
            Path path = npc.getNavigation().createPath(route.base(), 0);
            if (path != null && path.canReach() && insideSite(path) && npc.getNavigation().moveTo(path, 1.15)) {
                destination = route.base();
                scaffoldRoute = route;
                sayOccasionally("屋顶这里够不着，我搭几个垫脚块，上去就好。");
                deadline = level.getGameTime() + MOVE_LIMIT_TICKS;
                return true;
            }
        }
        return false;
    }

    private ScaffoldRoute scaffoldRouteAt(WorkStep step, ItemStack stack, BlockPos base, int highest) {
        for (int workY = base.getY() + 1; workY <= highest; workY++) {
            BlockPos feet = new BlockPos(base.getX(), workY, base.getZ());
            if (!scaffoldCell(feet.below()) || !freeColumn(feet)) {
                break;
            }
            if (chooseAim(step, stack, Vec3.atBottomCenterOf(feet)) != null) {
                return new ScaffoldRoute(base, workY);
            }
        }
        return null;
    }

    private boolean freeColumn(BlockPos feet) {
        return site.area().contains(feet.above()) && level.hasChunkAt(feet)
                && level.getBlockState(feet).isAir() && level.getBlockState(feet.above()).isAir();
    }

    private boolean scaffoldCell(BlockPos pos) {
        return site.canWorkAt(level, pos) && !designCells.contains(pos) && level.getBlockState(pos).isAir()
                && (site.area().contains(pos.below()) || !level.getBlockState(pos.below()).is(Blocks.GRASS_BLOCK));
    }

    private Result climbScaffold() {
        if (deadline > 0 && level.getGameTime() >= deadline) {
            return deferred("scaffold_route_blocked");
        }
        BlockPos feet = npc.blockPosition();
        BlockPos base = scaffoldRoute.base();
        if (feet.getX() != base.getX() || feet.getZ() != base.getZ() || feet.getY() < base.getY()) {
            Vec3 approach = BuildTerrain.standPoint(level, base);
            if (npc.position().distanceToSqr(approach) <= 1.5) {
                npc.getMoveControl().setWantedPosition(approach.x, approach.y, approach.z, 0.85);
                return null;
            }
            return npc.getNavigation().isDone() ? deferred("scaffold_route_blocked") : null;
        }
        if (!npc.onGround()) {
            return null;
        }
        if (feet.getY() >= scaffoldRoute.workY()) {
            scaffoldRoute = null;
            return deferred("scaffold_aim_changed");
        }
        if (!scaffoldCell(feet) || !freeColumn(feet.above())) {
            return deferred("scaffold_route_blocked");
        }
        int rise = scaffoldRoute.workY() - feet.getY();
        if (count(Items.DIRT) < rise) {
            if (!site.survival()) {
                npc.getInventory().addItem(new ItemStack(Items.DIRT, Math.min(64, Math.max(32, rise))));
                if (supplySlot(Items.DIRT) < 0) {
                    return failure("scaffold_material");
                }
            } else if (gatherScaffoldDirt(feet)) {
                return null;
            } else {
                return failure("scaffold_material");
            }
        }
        Vec3 center = Vec3.atBottomCenterOf(feet);
        if (center.subtract(npc.position()).horizontalDistanceSqr() > 0.035) {
            npc.getMoveControl().setWantedPosition(center.x, center.y, center.z, 0.9);
            return null;
        }
        stopNavigation();
        npc.setDeltaMovement(0, npc.getDeltaMovement().y, 0);
        jumpBlock = feet.immutable();
        jumpStarted = level.getGameTime();
        npc.getJumpControl().jump();
        return null;
    }

    private List<BlockPos> scaffoldSoil(BlockPos feet, Vec3 stance, int limit) {
        var guard = new Clearance(level, site);
        BlockPos footing = feet.below();
        List<BlockPos> usable = new ArrayList<>();
        for (BlockPos dirt : BlockPos.withinManhattan(footing, 3, 1, 3)) {
            if (dirt.getX() == footing.getX() && dirt.getZ() == footing.getZ()) {
                continue;
            }
            if (scaffoldSoilAt(dirt, guard) && chestAim(dirt, stance) != null) {
                usable.add(dirt.immutable());
                if (usable.size() >= limit) {
                    break;
                }
            }
        }
        return usable;
    }

    private boolean gatherScaffoldDirt(BlockPos feet) {
        for (BlockPos dirt : scaffoldSoil(feet, npc.position(), 16)) {
            BlockState actual = level.getBlockState(dirt);
            Aim hit = chestAim(dirt, npc.position());
            var target = new BuildPlan.Target(dirt, dirt, actual, Items.DIRT);
            ScaffoldRoute route = scaffoldRoute;
            long scaffoldDeadline = deadline;
            int before = count(Items.DIRT);
            Result result = mine(new WorkStep(target, actual, true, true), -1, hit);
            scaffoldRoute = route;
            deadline = scaffoldDeadline;
            if (result == null) {
                return true;
            }
            scaffoldDirtCollected += Math.max(0, count(Items.DIRT) - before);
            if (result.status().equals("removed")) {
                return true;
            }
        }
        return false;
    }

    private Result finishScaffoldJump() {
        BlockPos pos = jumpBlock;
        if (level.getGameTime() - jumpStarted > 20 || !scaffoldCell(pos)) {
            return deferred("scaffold_jump_blocked");
        }
        if (npc.getBoundingBox().minY < pos.getY() + 1.0) {
            return null;
        }
        int slot = supplySlot(Items.DIRT);
        if (slot < 0) {
            return failure("scaffold_material");
        }
        var target = new BuildPlan.Target(pos, pos, TEMPORARY_BLOCK, Items.DIRT);
        var aim = chooseAim(new WorkStep(target, Blocks.AIR.defaultBlockState(), false),
                npc.getInventory().getItem(slot), npc.position());
        if (aim == null) {
            return null;
        }
        ScaffoldRoute route = scaffoldRoute;
        long scaffoldDeadline = deadline;
        useItem(target, slot, aim);
        if (!level.getBlockState(pos).is(Blocks.DIRT)) {
            return deferred("scaffold_jump_blocked");
        }
        scaffolds.put(pos, TEMPORARY_BLOCK);
        scaffoldRoute = route;
        deadline = scaffoldDeadline;
        jumpBlock = null;
        performance.event("scaffold_placed");
        sayOccasionally("这边有点高，我先垫几格，做完再收走。");
        return null;
    }

    @Override
    public boolean cleanScaffolds() {
        if (tickDescent()) {
            return false;
        }
        scaffolds.entrySet().removeIf(entry -> level.hasChunkAt(entry.getKey())
                && level.getBlockState(entry.getKey()) != entry.getValue());
        if (scaffolds.isEmpty()) {
            return true;
        }
        BlockPos underfoot = npc.blockPosition().below();
        BlockPos pos = scaffolds.containsKey(underfoot) ? underfoot : scaffolds.keySet().stream()
                .max(Comparator.comparingInt(BlockPos::getY)).orElseThrow();
        if (!npc.onGround()) {
            return false;
        }
        if (scaffolds.containsKey(pos.above())) {
            pos = scaffolds.keySet().stream().max(Comparator.comparingInt(BlockPos::getY)).orElseThrow();
        }
        BlockState state = scaffolds.get(pos);
        String risk = new Clearance(level, site).mutationRisk(pos, state, true, Set.of());
        if (risk != null) {
            throw Wire.conflict(risk, "临时施工位置旁出现了保护设施，需要留出清理空间。");
        }
        Vec3 current = trackPosition();
        Aim aim = chestAim(pos, current);
        if (aim == null) {
            BlockPos target = pos;
            if (navigate(pos, current, feet -> chestAim(target, feet) != null, "scaffold_cleanup_unreachable") != null
                    && !startDescent(target)) {
                throw Wire.conflict("scaffold_cleanup_unreachable", "我需要一条能到达脚手架顶端的通道来拆除临时方块。");
            }
            return false;
        }
        stopNavigation();
        var target = new BuildPlan.Target(pos, pos, state, Items.DIRT);
        Result result = mine(new WorkStep(target, state, true), -1, aim);
        if (result != null && level.getBlockState(pos).isAir()) {
            scaffolds.remove(pos);
            performance.event("scaffold_removed");
        } else if (result != null) {
            throw Wire.conflict("scaffold_cleanup_denied", "这里的临时方块暂时无法拆除。");
        }
        return scaffolds.isEmpty();
    }

    @Override
    public boolean gatherScaffoldMaterial(int amount) {
        if (count(Items.DIRT) >= amount) {
            dirtTarget = null;
            unreachableSoil.clear();
            haltAction();
            return true;
        }
        if (!scaffolds.isEmpty() && !cleanScaffolds()) {
            return false;
        }
        var guard = new Clearance(level, site);
        if (dirtTarget == null || !scaffoldSoilAt(dirtTarget, guard)) {
            haltAction();
            dirtTarget = null;
            BlockPos center = npc.blockPosition();
            for (BlockPos pos : BlockPos.withinManhattan(center.below(), 9, 10, 9)) {
                if (scaffoldSoilAt(pos, guard) && !unreachableSoil.contains(pos)) {
                    dirtTarget = pos.immutable();
                    break;
                }
            }
            if (dirtTarget == null) {
                return true;
            }
            sayOccasionally("我再去找点土，把高处剩下的几块搭完。");
        }
        BlockPos pos = dirtTarget;
        Aim aim = chestAim(pos, trackPosition());
        if (aim == null || pos.equals(npc.blockPosition().below())) {
            if (navigate(pos, npc.position(), feet -> !pos.equals(BlockPos.containing(feet).below())
                    && chestAim(pos, feet) != null, "scaffold_soil_unreachable") != null) {
                unreachableSoil.add(pos);
                dirtTarget = null;
            }
            return false;
        }
        stopNavigation();
        int before = count(Items.DIRT);
        var state = level.getBlockState(pos);
        var target = new BuildPlan.Target(pos, pos, state, Items.DIRT);
        var result = mine(new WorkStep(target, state, true, true), -1, aim);
        if (result != null) {
            scaffoldDirtCollected += Math.max(0, count(Items.DIRT) - before);
            unreachableSoil.add(pos);
            dirtTarget = null;
        }
        return false;
    }

    private boolean scaffoldSoilAt(BlockPos pos, Clearance guard) {
        if (!site.canWorkAt(level, pos) || designCells.contains(pos) || scaffolds.containsKey(pos)) {
            return false;
        }
        var actual = level.getBlockState(pos);
        return (actual.is(Blocks.DIRT) || actual.is(Blocks.GRASS_BLOCK))
                && level.getBlockState(pos.above()).isAir() && level.getBlockState(pos.below()).isSolid()
                && guard.mutationRisk(pos, actual, true, Set.of()) == null;
    }

    @Override
    public SimpleContainer inventory() {
        return available() ? npc.getInventory() : null;
    }

    @Override
    public boolean hasSupply(WorkStep step) {
        Item item = Warehouse.item(step);
        return available() && (item == Items.AIR || Warehouse.optionalTool(step) || supplySlot(step) >= 0);
    }

    private int supplySlot(WorkStep step) {
        int exact = supplySlot(Warehouse.item(step));
        Item alternative = site.survival() && exact < 0 ? Warehouse.substitute(step) : null;
        return alternative == null ? exact : supplySlot(alternative);
    }

    private int supplySlot(Item item) {
        return site.survival() ? Warehouse.find(npc.getInventory(), item) : itemSlot(item);
    }

    @Override
    public JsonObject resupply(Supplier<List<Warehouse.Stock>> availableStock, List<WorkStep> steps, int cursor) {
        if (!available()) {
            throw Wire.conflict("npc_unavailable", "NPC 暂时无法搬运。");
        }
        if (tickDescent()) {
            return null;
        }
        if (!scaffolds.isEmpty() && !cleanScaffolds()) {
            return null;
        }
        Item item = Warehouse.item(steps.get(cursor));
        List<Warehouse.Stock> stocks = null;
        if (fabrication == null) {
            stocks = availableStock.get();
            Item substitute = Warehouse.substitute(steps.get(cursor));
            var direct = rankedStock(stocks, Set.of(item));
            if (!direct.isEmpty()) {
                return fetchStock(direct, Warehouse.needs(steps, cursor));
            }
            if (substitute != null) {
                direct = rankedStock(stocks, Set.of(substitute));
                if (!direct.isEmpty()) {
                    return fetchStock(direct, Map.of(substitute, 1));
                }
            }
            var carried = Warehouse.carried(npc.getInventory());
            fabrication = recipes().next(item, Warehouse.needs(steps, cursor), Warehouse.substitutes(steps, cursor),
                    carried, Warehouse.stocked(stocks), stations(supplyCenter), furnaceHeat());
            if (fabrication == null) {
                throw Wire.conflict("awaiting_materials", "附近箱子里的下一批材料用完了。");
            }
            fabricationPickups = new LinkedHashMap<>();
            fabrication.fetch().forEach((needed, count) ->
                    fabricationPickups.put(needed, carried.getOrDefault(needed, 0) + count));
        }
        var carried = Warehouse.carried(npc.getInventory());
        fabricationPickups.entrySet().removeIf(entry -> carried.getOrDefault(entry.getKey(), 0) >= entry.getValue());
        if (!fabricationPickups.isEmpty()) {
            var candidates = rankedStock(stocks == null ? availableStock.get() : stocks, fabricationPickups.keySet());
            if (candidates.isEmpty()) {
                resetFabrication();
                throw Wire.conflict("awaiting_materials", "做下一批方块的原料刚刚用完了。补齐以后我再继续。");
            }
            return fetchStock(candidates, fabricationPickups);
        }
        return fabricateBatch();
    }

    private List<Warehouse.Stock> rankedStock(List<Warehouse.Stock> stocks, Set<Item> items) {
        return stocks.stream().filter(stock -> items.stream().anyMatch(stock::has))
                .sorted(Comparator.comparingDouble(stock -> stock.pos().distSqr(npc.blockPosition()))).toList();
    }

    private JsonObject fetchStock(List<Warehouse.Stock> candidates, Map<Item, Integer> wanted) {
        Warehouse.Stock stock = candidates.stream().filter(candidate -> candidate.pos().equals(stockTarget)).findFirst()
                .orElseGet(() -> candidates.stream().filter(candidate -> !skippedStock.contains(candidate.pos())).findFirst().orElse(null));
        if (stock == null) {
            skippedStock.clear();
            throw Wire.conflict("warehouse_unreachable", "我暂时走不到下一批材料所在的容器。");
        }
        if (!stock.pos().equals(stockTarget)) {
            haltAction();
            stockTarget = stock.pos();
        }
        Vec3 current = trackPosition();
        Aim aim = chestAim(stock.pos(), current);
        if (aim == null) {
            String trouble = navigate(stock.pos(), current, feet -> chestAim(stock.pos(), feet) != null,
                    "warehouse_unreachable");
            if (trouble != null && !startDescent(stock.pos())) {
                skipStock(stock);
            }
            return null;
        }
        var interaction = ForgeHooks.onRightClickBlock(actor, InteractionHand.MAIN_HAND, stock.pos(), aim.hit());
        if (!level.mayInteract(actor, stock.pos()) || interaction.isCanceled()
                || interaction.getUseBlock() == Event.Result.DENY) {
            skipStock(stock);
            return null;
        }
        lookAndSwing(aim);
        JsonObject transfer = Warehouse.transfer(stock, npc.getInventory(), wanted);
        if (transfer.getAsJsonObject("items").entrySet().isEmpty()) {
            throw Wire.conflict("warehouse_inventory_full", "我的背包暂时装不下这批材料。");
        }
        transfer.add("npc_position", Wire.position(npc.blockPosition()));
        transfer.addProperty("reach", aim.distance());
        haltAction();
        skippedStock.clear();
        stockTarget = null;
        return transfer;
    }

    @Override
    public CraftingAssistant recipes() {
        if (crafting == null || !crafting.current()) {
            crafting = new CraftingAssistant(level);
        }
        return crafting;
    }

    @Override
    public Set<CraftingAssistant.Station> stations(BlockPos center) {
        if (level.getGameTime() >= stationScanUntil || !java.util.Objects.equals(stationScanCenter, center)) {
            stationScanCenter = center;
            stationScanUntil = level.getGameTime() + 80;
            workshopNearby.clear();
            scanStations(site.spawn());
            if (center != null && !center.equals(site.spawn())) {
                scanStations(center);
            }
        }
        var found = java.util.EnumSet.noneOf(CraftingAssistant.Station.class);
        for (var station : List.of(CraftingAssistant.Station.TABLE, CraftingAssistant.Station.FURNACE)) {
            if (!stationChoices(station).isEmpty()) {
                found.add(station);
            }
        }
        return found;
    }

    private void scanStations(BlockPos center) {
        var corner = center.offset(-16, -5, -16);
        var opposite = center.offset(16, 5, 16);
        for (BlockPos pos : BlockPos.betweenClosed(corner, opposite)) {
            if (pos.distSqr(center) > 16 * 16 || !level.hasChunkAt(pos) || !level.isInWorldBounds(pos)) {
                continue;
            }
            BlockState state = level.getBlockState(pos);
            if (state.is(Blocks.CRAFTING_TABLE)) {
                workshopNearby.computeIfAbsent(CraftingAssistant.Station.TABLE, ignored -> new ArrayList<>()).add(pos.immutable());
            } else if (state.is(Blocks.FURNACE)) {
                workshopNearby.computeIfAbsent(CraftingAssistant.Station.FURNACE, ignored -> new ArrayList<>()).add(pos.immutable());
            }
        }
    }

    private List<BlockPos> stationChoices(CraftingAssistant.Station station) {
        var candidates = new java.util.LinkedHashSet<>(workshopNearby.getOrDefault(station, List.of()));
        if (workshop != null) {
            workshop.temporary.forEach((pos, type) -> {
                if (type == station) {
                    candidates.add(pos);
                }
            });
        }
        var origin = available() ? npc.blockPosition() : site.spawn();
        return candidates.stream().filter(pos -> canTravelAt(pos) && !site.worksite().protectedAt(pos)
                        && !skippedStations.contains(pos) && level.hasChunkAt(pos)
                        && level.getWorldBorder().isWithinBounds(pos)
                        && level.getBlockState(pos).is(station.block())
                        && (actor == null || level.mayInteract(actor, pos)))
                .sorted(Comparator.comparingDouble(origin::distSqr)).toList();
    }

    @Override
    public void useWorkshop(SupplyCheckpoint supply) {
        if (workshop != supply) {
            workshop = supply;
            skippedStations.clear();
            resetFabrication();
        }
    }

    private int furnaceHeat() {
        return workshop.heatedFurnace != null
                && stationChoices(CraftingAssistant.Station.FURNACE).contains(workshop.heatedFurnace) ? workshop.heat : 0;
    }

    private JsonObject fabricateBatch() {
        if (!crafting.current()) {
            resetFabrication();
            return null;
        }
        var batch = fabrication.batches().get(batchCursor);
        var station = batch.formula().station();
        BlockPos pos = null;
        if (station != CraftingAssistant.Station.HAND) {
            if (station == CraftingAssistant.Station.FURNACE && furnaceHeat() != batch.heatBefore()) {
                resetFabrication();
                return null;
            }
            List<BlockPos> known = stationChoices(station);
            if (known.isEmpty()) {
                if (supplySlot(station.block().asItem()) < 0) {
                    resetFabrication();
                    return null;
                }
                placeWorkstation(station);
                return null;
            }
            pos = stationTarget != null && known.contains(stationTarget) ? stationTarget
                    : workshop.heatedFurnace != null && known.contains(workshop.heatedFurnace)
                    ? workshop.heatedFurnace : known.get(0);
            if (!pos.equals(stationTarget)) {
                haltAction();
                stationTarget = pos;
                cookingUntil = 0;
            }
            Aim aim = chestAim(pos, trackPosition());
            if (aim == null) {
                BlockPos next = pos;
                if (navigate(pos, npc.position(), point -> chestAim(next, point) != null, "workstation_unreachable") != null) {
                    skippedStations.add(pos);
                    resetFabrication();
                }
                return null;
            }
            stopNavigation();
            if (cookingUntil == 0) {
                var interaction = ForgeHooks.onRightClickBlock(actor, InteractionHand.MAIN_HAND, pos, aim.hit());
                if (interaction.isCanceled() || interaction.getUseBlock() == Event.Result.DENY) {
                    skippedStations.add(pos);
                    resetFabrication();
                    return null;
                }
                lookAndSwing(aim);
                if (station == CraftingAssistant.Station.FURNACE) {
                    cookingUntil = level.getGameTime() + (long) batch.formula().cookTicks() * batch.times();
                }
            }
            if (level.getGameTime() < cookingUntil) {
                return null;
            }
        } else {
            npc.swing(InteractionHand.MAIN_HAND);
        }
        ItemStack result = batch.output(level);
        if (!Warehouse.fabricate(npc.getInventory(), batch.inputs(), result)) {
            resetFabrication();
            throw Wire.conflict("workshop_inventory", "这批原料或我的背包空间不够，等我重新盘点。");
        }
        if (station == CraftingAssistant.Station.FURNACE) {
            workshop.heat = batch.heatAfter();
            workshop.heatedFurnace = pos;
        }
        var receipt = new JsonObject();
        receipt.addProperty("item", Warehouse.id(result.getItem()));
        receipt.addProperty("count", result.getCount());
        receipt.add("ingredients", Warehouse.jsonTotals(batch.inputs()));
        receipt.addProperty("station", station.name().toLowerCase(java.util.Locale.ROOT));
        if (pos != null) {
            receipt.add("position", Wire.position(pos));
        }
        receipt.addProperty("recipe", batch.formula().recipe().getId().toString());
        receipt.addProperty("crafted", true);
        cookingUntil = 0;
        batchCursor++;
        if (batchCursor == fabrication.batches().size()) {
            resetFabrication();
        }
        return receipt;
    }

    private void placeWorkstation(CraftingAssistant.Station type) {
        Item item = type.block().asItem();
        int slot = supplySlot(item);
        ItemStack stack = npc.getInventory().getItem(slot);
        BlockPos center = npc.blockPosition();
        var choices = new ArrayList<BlockPos>();
        for (BlockPos pos : BlockPos.betweenClosed(center.offset(-3, -2, -3), center.offset(3, 2, 3))) {
            if (site.canWorkAt(level, pos) && !designCells.contains(pos) && !scaffolds.containsKey(pos)
                    && level.getBlockState(pos).isAir() && level.getBlockState(pos.below()).isFaceSturdy(level, pos.below(), Direction.UP)
                    && level.isUnobstructed(type.block().defaultBlockState(), pos,
                    net.minecraft.world.phys.shapes.CollisionContext.of(actor))) {
                choices.add(pos.immutable());
            }
        }
        choices.sort(Comparator.comparingDouble(center::distSqr));
        for (BlockPos pos : choices) {
            var state = type.block().defaultBlockState();
            for (Direction facing : Direction.Plane.HORIZONTAL) {
                BlockState placed = state.hasProperty(BlockStateProperties.HORIZONTAL_FACING)
                        ? state.setValue(BlockStateProperties.HORIZONTAL_FACING, facing) : state;
                var target = new BuildPlan.Target(pos, pos, placed, item);
                Aim aim = chooseAim(new WorkStep(target, Blocks.AIR.defaultBlockState(), false), stack, npc.position());
                if (aim != null && useItem(target, slot, aim).status().equals("placed")) {
                    workshop.temporary.put(pos, type);
                    performance.event("workstation_placed");
                    return;
                }
            }
        }
        resetFabrication();
        throw Wire.conflict("workstation_unreachable", "箱子里有原料，我需要附近一处能站稳并放下工作台或熔炉的空地。");
    }

    @Override
    public boolean cleanWorkshop() {
        if (tickDescent()) {
            return false;
        }
        if (workshop == null || workshop.temporary.isEmpty()) {
            return true;
        }
        var entry = workshop.temporary.entrySet().stream()
                .max(Comparator.comparingInt(value -> value.getValue().ordinal())).orElseThrow();
        BlockPos pos = entry.getKey();
        if (!level.hasChunkAt(pos)) {
            throw Wire.conflict("site_not_loaded", "请回到施工区，我还要收起临时工位。");
        }
        BlockState state = level.getBlockState(pos);
        var blockEntity = level.getBlockEntity(pos);
        if (!state.is(entry.getValue().block()) || !site.canWorkAt(level, pos)
                || blockEntity instanceof net.minecraft.world.Container contents && !contents.isEmpty()
                || state.hasProperty(BlockStateProperties.LIT) && state.getValue(BlockStateProperties.LIT)
                || new Clearance(level, site).mutationRisk(pos, Blocks.AIR.defaultBlockState(), true, Set.of()) != null) {
            workshop.temporary.remove(pos);
            return false;
        }
        if (!pos.equals(stationTarget)) {
            haltAction();
            stationTarget = pos;
        }
        Aim aim = chestAim(pos, trackPosition());
        if (aim == null) {
            if (navigate(pos, npc.position(), feet -> chestAim(pos, feet) != null, "workstation_unreachable") != null
                    && !startDescent(pos)) {
                throw Wire.conflict("workstation_unreachable", "等附近能通行时，我会回来收起这个临时工位。");
            }
            return false;
        }
        stopNavigation();
        int slot = -1;
        if (entry.getValue() == CraftingAssistant.Station.FURNACE) {
            slot = supplySlot(Items.IRON_PICKAXE);
            if (slot < 0) {
                slot = supplySlot(Items.STONE_PICKAXE);
            }
        }
        var target = new BuildPlan.Target(pos, pos, state, state.getBlock().asItem());
        if (entry.getValue() == CraftingAssistant.Station.FURNACE && slot < 0) {
            var source = supplyCenter == null ? null : new Warehouse.Source(workshop.owner, supplyCenter);
            var receipt = resupply(() -> Warehouse.scan(level, site, source), List.of(new WorkStep(target, state, true)), 0);
            if (receipt != null) {
                receipt.addProperty("tick", level.getGameTime());
                if (receipt.has("crafted")) {
                    workshop.recipes.add(receipt);
                } else {
                    workshop.trips.add(receipt);
                }
            }
            return false;
        }
        Result result = mine(new WorkStep(target, state, true), slot, aim);
        if (result != null && level.getBlockState(pos).isAir()) {
            workshop.temporary.remove(pos);
            stationTarget = null;
            performance.event("workstation_removed");
        } else if (result != null) {
            throw Wire.conflict("workstation_unreachable", "临时工位暂时无法收起。");
        }
        return workshop.temporary.isEmpty();
    }

    private void resetFabrication() {
        fabrication = null;
        fabricationPickups = null;
        batchCursor = 0;
        cookingUntil = 0;
        stationTarget = null;
        haltAction();
    }

    @Override
    public void mentionSupply() {
        sayOccasionally("我去附近的箱子搬这一批材料，等会儿回来。");
    }

    private void sayOccasionally(String text) {
        long now = level.getGameTime();
        if (now >= speechAfter) {
            speechAfter = now + 20 * 45;
            say(text);
        }
    }

    private void say(String text) {
        level.getServer().getPlayerList().broadcastSystemMessage(Component.literal("[Builder] " + text), false);
    }

    @Override
    public void celebrate(BuildSite.Box bounds) {
        if (!available()) {
            return;
        }
        var min = bounds.min();
        var max = bounds.max();
        ceremonyLook = new Vec3((min.getX() + max.getX() + 1) * 0.5, max.getY() + 0.8,
                (min.getZ() + max.getZ() + 1) * 0.5);
        ceremonyMoveUntil = level.getGameTime() + 35;
        ceremonyLookUntil = 0;
        Vec3 away = npc.position().subtract(ceremonyLook).multiply(1, 0, 1);
        if (away.horizontalDistanceSqr() < 0.04) {
            away = npc.getLookAngle().multiply(-1, 0, -1);
        }
        BlockPos step = BlockPos.containing(npc.position().add(away.normalize().scale(2)));
        for (int y : new int[]{0, -1, 1}) {
            BlockPos goal = step.offset(0, y, 0);
            if (!canStand(goal)) {
                continue;
            }
            Path path = npc.getNavigation().createPath(goal, 0);
            if (path != null && path.canReach() && insideSite(path)) {
                npc.getNavigation().moveTo(path, 0.8);
                return;
            }
        }
        ceremonyMoveUntil = level.getGameTime();
    }

    @Override
    public void tickLife(boolean idle) {
        if (!available()) {
            return;
        }
        long now = level.getGameTime();
        if (ceremonyLook != null) {
            if (!idle) {
                ceremonyLook = null;
                return;
            }
            if (ceremonyLookUntil == 0) {
                if (now < ceremonyMoveUntil && !npc.getNavigation().isDone()) {
                    return;
                }
                stopNavigation();
                ceremonyLookUntil = now + 40;
            }
            npc.getLookControl().setLookAt(ceremonyLook.x, ceremonyLook.y, ceremonyLook.z, 35, 35);
            if (now >= ceremonyLookUntil) {
                npc.swing(InteractionHand.MAIN_HAND);
                say("完工啦！我刚退后看过了。快来看看我们一起建的作品吧。");
                ceremonyLook = null;
            }
            return;
        }
        if (!idle || surveyDestination != null || !npc.getNavigation().isDone()) {
            return;
        }
        var player = level.getNearestPlayer(npc.getX(), npc.getY(), npc.getZ(), 4,
                entity -> entity.isAlive() && !entity.isSpectator() && !isActor(entity));
        if (player == null) {
            greeted = false;
        } else {
            npc.getLookControl().setLookAt(player.getX(), player.getEyeY(), player.getZ(), 25, 25);
            if (!greeted && now >= greetingAfter) {
                npc.swing(InteractionHand.MAIN_HAND);
                greeted = true;
                greetingAfter = now + 180;
            }
        }
    }

    private void skipStock(Warehouse.Stock stock) {
        skippedStock.add(stock.pos());
        stockTarget = null;
        haltAction();
    }

    private Aim chestAim(BlockPos pos, Vec3 feet) {
        actor.setPos(feet.x, feet.y, feet.z);
        actor.setOnGround(npc.onGround());
        return breakAim(pos, actor.getEyePosition());
    }

    private Vec3 trackPosition() {
        Vec3 current = npc.position();
        walked += current.distanceTo(lastPosition);
        lastPosition = current;
        return current;
    }

    private String navigate(BlockPos pos, Vec3 current, Predicate<Vec3> canAim, String unreachable) {
        long now = level.getGameTime();
        if (deadline == 0) {
            deadline = now + MOVE_LIMIT_TICKS;
        }
        if (now >= deadline) {
            return "navigation_timeout";
        }
        if (destination != null && !npc.getNavigation().isDone()) {
            return null;
        }
        // Vanilla ground navigation cannot create a path until the worker finishes a step or fall.
        if (!npc.onGround()) {
            return null;
        }
        int walkable = 0;
        int visible = 0;
        int reaches = 0;
        long searchStarted = BuildPerformance.clock();
        List<BlockPos> options = stances(pos, current);
        performance.span(BuildPerformance.Span.STANCE_SEARCH, searchStarted);
        for (BlockPos candidate : options) {
            long stanceStarted = BuildPerformance.clock();
            try {
                if (!triedStances.add(candidate) || !canStand(candidate)) {
                    continue;
                }
                walkable++;
                if (!canAim.test(BuildTerrain.standPoint(level, candidate))) {
                    continue;
                }
                visible++;
            } finally {
                performance.span(BuildPerformance.Span.STANCE_SEARCH, stanceStarted);
            }
            long pathStarted = BuildPerformance.clock();
            Path path = npc.getNavigation().createPath(candidate, 0);
            performance.span(BuildPerformance.Span.PATH_SEARCH, pathStarted);
            if (path != null && path.canReach()) {
                reaches++;
                if (insideSite(path) && npc.getNavigation().moveTo(path, 1.15)) {
                    destination = candidate;
                    return null;
                }
            }
        }
        if (site.worldCopy()) {
            ForgeBridge.LOGGER.info("site_navigation_unreachable {} walkable={} visible={} reachable_paths={}",
                    unreachable, walkable, visible, reaches);
        }
        return unreachable;
    }

    private boolean startDescent(BlockPos target) {
        BlockPos here = npc.blockPosition();
        if (!npc.onGround() || here.getY() < target.getY() + 2) {
            return false;
        }
        int maxDrop = npc.getHealth() >= 8 ? 6 : 3;
        List<Descent> options = new ArrayList<>();
        for (BlockPos edge : BlockPos.withinManhattan(here, 6, 1, 6)) {
            if (!canTravelAt(edge) || !canStand(edge)) {
                continue;
            }
            for (Direction side : Direction.Plane.HORIZONTAL) {
                BlockPos air = edge.relative(side);
                if (triedDescents.contains(air) || !freeTravelColumn(air)) {
                    continue;
                }
                for (int drop = 1; drop <= maxDrop; drop++) {
                    BlockPos landing = air.below(drop);
                    if (!freeTravelColumn(landing)) {
                        break;
                    }
                    if (canStand(landing)) {
                        if (drop >= 2 && landing.getY() < here.getY()) {
                            options.add(new Descent(edge.immutable(), landing, level.getGameTime() + 110));
                        }
                        break;
                    }
                }
            }
        }
        options.sort(Comparator.comparingDouble(option -> option.edge().distSqr(here)
                + 0.35 * option.landing().distSqr(target)));
        for (Descent option : options) {
            if (option.edge().equals(here)) {
                haltAction();
                descent = option;
                return true;
            }
            Path path = npc.getNavigation().createPath(option.edge(), 0);
            if (path != null && path.canReach() && insideSite(path)) {
                haltAction();
                if (npc.getNavigation().moveTo(path, 0.9)) {
                    descent = option;
                    return true;
                }
            }
        }
        return false;
    }

    private boolean freeTravelColumn(BlockPos feet) {
        return canTravelAt(feet) && canTravelAt(feet.above()) && level.hasChunkAt(feet)
                && level.isInWorldBounds(feet.above()) && level.getBlockState(feet).isAir()
                && level.getBlockState(feet.above()).isAir();
    }

    private boolean tickDescent() {
        if (descent == null) {
            return false;
        }
        BlockPos here = npc.blockPosition();
        if (npc.onGround() && here.getY() <= descent.landing().getY()
                || level.getGameTime() >= descent.deadline()) {
            if (here.getY() > descent.landing().getY()) {
                triedDescents.add(new BlockPos(descent.landing().getX(), descent.edge().getY(), descent.landing().getZ()));
            }
            descent = null;
            haltAction();
            return false;
        }
        if (!npc.getNavigation().isDone()) {
            return true;
        }
        if (here.getY() >= descent.edge().getY()) {
            BlockPos shaft = new BlockPos(descent.landing().getX(), descent.edge().getY(), descent.landing().getZ());
            boolean safe = canStand(descent.landing());
            for (BlockPos pos = shaft; safe && pos.getY() >= descent.landing().getY(); pos = pos.below()) {
                safe = freeTravelColumn(pos);
            }
            if (!safe || !here.equals(descent.edge()) && !here.equals(shaft)) {
                triedDescents.add(shaft);
                descent = null;
                haltAction();
                return false;
            }
        }
        Vec3 landing = BuildTerrain.standPoint(level, descent.landing());
        npc.getMoveControl().setWantedPosition(landing.x, landing.y, landing.z, 0.9);
        return true;
    }

    private boolean canStand(BlockPos position) {
        return BuildTerrain.canStand(level, position);
    }

    private List<BlockPos> stances(BlockPos target, Vec3 current) {
        List<BlockPos> candidates = new ArrayList<>();
        int minY = Math.max(level.getMinBuildHeight(), target.getY() - 4);
        int maxY = Math.min(level.getMaxBuildHeight() - 2, target.getY() + 1);
        BlockPos min = new BlockPos(target.getX() - STANCE_RADIUS, minY, target.getZ() - STANCE_RADIUS);
        BlockPos max = new BlockPos(target.getX() + STANCE_RADIUS, maxY, target.getZ() + STANCE_RADIUS);
        for (BlockPos pos : BlockPos.betweenClosed(min, max)) {
            if (canTravelAt(pos) && canTravelAt(pos.above()) && !pos.equals(target) && !pos.above().equals(target)) {
                candidates.add(pos.immutable());
            }
        }
        candidates.sort(Comparator.comparingDouble(pos -> Vec3.atBottomCenterOf(pos).distanceToSqr(current)));
        return candidates;
    }

    private boolean insideSite(Path path) {
        for (int i = 0; i < path.getNodeCount(); i++) {
            if (!canTravelAt(path.getNode(i).asBlockPos())) {
                return false;
            }
        }
        return true;
    }

    private Aim chooseAim(WorkStep step, ItemStack stack, Vec3 feet) {
        actor.setPos(feet.x, feet.y, feet.z);
        actor.setOnGround(npc.onGround());
        Vec3 eye = actor.getEyePosition();
        if (step.remove()) {
            return step.clearance() && step.pos().equals(BlockPos.containing(feet).below())
                    ? null : breakAim(step.pos(), eye);
        }
        BuildPlan.Target target = step.target();
        for (Direction face : FACES) {
            BlockPos clicked = target.world().relative(face.getOpposite());
            if (target.light() && !clicked.equals(target.attachment())) {
                continue;
            }
            BlockState support = level.getBlockState(clicked);
            if (!level.hasChunkAt(clicked) || Clearance.managed(support) || !support.getFluidState().isEmpty()
                    || support.isAir() || !anchors.contains(support.getBlock())
                    && support.getCollisionShape(level, clicked).isEmpty()) {
                continue;
            }
            double[] heights = face.getAxis().isHorizontal() ? new double[]{0, -0.25, 0.25} : new double[]{0};
            for (double height : heights) {
                Vec3 facePoint = Vec3.atCenterOf(clicked)
                        .add(face.getStepX() * 0.499, face.getStepY() * 0.499 + height, face.getStepZ() * 0.499);
                if (eye.distanceToSqr(facePoint) > REACH * REACH) {
                    continue;
                }
                BlockHitResult hit = level.clip(new ClipContext(eye, facePoint, ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE, actor));
                if (hit.getType() != HitResult.Type.BLOCK || !hit.getBlockPos().equals(clicked) || hit.getDirection() != face) {
                    continue;
                }
                Aim aim = face(hit, eye);
                if (target.state().getBlock() instanceof net.minecraft.world.level.block.StairBlock) {
                    float facing = target.state().getValue(BlockStateProperties.HORIZONTAL_FACING).toYRot();
                    float angle = Mth.wrapDegrees(aim.yaw() - facing);
                    // A player can click a block off the center of the crosshair while keeping the desired
                    // cardinal heading. Keep the hit ray and reach checks, and only turn within this field.
                    if (Math.abs(angle) < 72) {
                        float yaw = facing + Mth.clamp(angle, -43, 43);
                        actor.setYRot(yaw);
                        aim = new Aim(hit, yaw, aim.pitch(), aim.distance());
                    }
                }
                BlockPlaceContext context = new BlockPlaceContext(actor, InteractionHand.MAIN_HAND, stack, hit);
                BlockState placed = target.state().getBlock().getStateForPlacement(context);
                if (context.canPlace() && context.getClickedPos().equals(target.world())
                        && placed != null && ConstructionService.matches(placed, target.state())
                        && target.state().canSurvive(level, target.world())) {
                    return aim;
                }
            }
        }
        return null;
    }

    private Aim face(BlockHitResult hit, Vec3 eye) {
        Vec3 delta = hit.getLocation().subtract(eye);
        float yaw = (float) Math.toDegrees(Math.atan2(delta.z, delta.x)) - 90;
        float pitch = (float) -Math.toDegrees(Math.atan2(delta.y, delta.horizontalDistance()));
        actor.setYRot(yaw);
        actor.setXRot(pitch);
        return new Aim(hit, yaw, pitch, delta.length());
    }

    private Aim breakAim(BlockPos pos, Vec3 eye) {
        for (double height : new double[]{0, -0.28, 0.28}) {
            Vec3 endpoint = Vec3.atCenterOf(pos).add(0, height, 0);
            BlockHitResult hit = level.clip(new ClipContext(eye, endpoint, ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE, actor));
            if (hit.getType() == HitResult.Type.BLOCK && hit.getBlockPos().equals(pos)
                    && eye.distanceToSqr(hit.getLocation()) <= REACH * REACH) {
                return face(hit, eye);
            }
        }
        return null;
    }

    private void lookAndSwing(Aim aim) {
        npc.setYRot(aim.yaw());
        npc.setYHeadRot(aim.yaw());
        npc.setYBodyRot(aim.yaw());
        npc.setXRot(aim.pitch());
        npc.swing(InteractionHand.MAIN_HAND);
    }

    static Item toolFor(BlockState state) {
        return state.is(BlockTags.MINEABLE_WITH_AXE) ? Items.IRON_AXE : Items.IRON_PICKAXE;
    }

    private Result mine(WorkStep step, int slot, Aim aim) {
        BlockPos pos = step.pos();
        if (!level.mayInteract(actor, pos) || level.getBlockState(pos) != step.before()) {
            return failure("interaction_forbidden");
        }
        SimpleContainer inventory = npc.getInventory();
        ItemStack stack = slot < 0 ? ItemStack.EMPTY : inventory.getItem(slot);
        actor.setItemInHand(InteractionHand.MAIN_HAND, stack);
        try {
            if (site.survival() && step.before().requiresCorrectToolForDrops()
                    && !stack.isCorrectToolForDrops(step.before())) {
                return failure("incompatible_tool");
            }
            float progress = step.before().getDestroyProgress(actor, level, pos);
            if (!(progress > 0) || progress * MOVE_LIMIT_TICKS < 1) {
                return failure("cannot_mine_with_tool");
            }
            long now = level.getGameTime();
            if (!pos.equals(mining)) {
                mining = pos;
                miningSince = now;
                lookAndSwing(aim);
            }
            float amount = progress * (now - miningSince + 1);
            if (amount < 1) {
                level.destroyBlockProgress(npc.getId(), pos, (int) (amount * 10));
                return null;
            }

            lookAndSwing(aim);
            int damage = stack.getDamageValue();
            int maxDamage = stack.getMaxDamage();
            String tool = ForgeRegistries.ITEMS.getKey(stack.getItem()).toString();
            RemovalEvidence attempt = RemovalEvidence.begin(actor, step);
            boolean destroyed;
            long breakStarted = BuildPerformance.clock();
            try {
                var click = ForgeHooks.onLeftClickBlock(actor, pos, aim.hit().getDirection(),
                        ServerboundPlayerActionPacket.Action.START_DESTROY_BLOCK);
                destroyed = !click.isCanceled() && click.getResult() != Event.Result.DENY
                        && click.getUseItem() != Event.Result.DENY && actor.gameMode.destroyBlock(pos);
            } finally {
                performance.span(BuildPerformance.Span.BREAK, breakStarted);
                RemovalEvidence.end();
                if (slot >= 0) {
                    inventory.setItem(slot, actor.getMainHandItem());
                }
            }
            ItemStack remaining = actor.getMainHandItem();
            int after = remaining.isEmpty() ? maxDamage : remaining.getDamageValue();
            JsonObject evidence = new JsonObject();
            evidence.addProperty("method", "ServerPlayerGameMode.destroyBlock");
            evidence.addProperty("left_click_events", attempt.leftClicks);
            evidence.addProperty("break_events", attempt.breakEvents);
            evidence.addProperty("tool_id", tool);
            evidence.addProperty("tool_damage_before", damage);
            evidence.addProperty("tool_damage_after", after);
            evidence.addProperty("tool_count_after", remaining.getCount());
            evidence.addProperty("reach", aim.distance());
            evidence.addProperty("mining_ticks", now - miningSince + 1);
            evidence.addProperty("face", aim.hit().getDirection().getName());
            int recovered = 0;
            for (ItemEntity drop : attempt.drops) {
                if (drop.isAlive() && site.area().contains(drop.blockPosition())) {
                    ItemStack stackOnGround = drop.getItem();
                    int beforePickup = stackOnGround.getCount();
                    ItemStack extra = inventory.addItem(stackOnGround);
                    int collected = beforePickup - extra.getCount();
                    if (collected > 0) {
                        npc.take(drop, collected);
                        recovered += collected;
                        if (extra.isEmpty()) {
                            drop.discard();
                        } else {
                            drop.setItem(extra);
                        }
                    }
                }
            }
            evidence.addProperty("recovered_items", recovered);
            String reason = !destroyed || attempt.rejected ? "break_rejected"
                    : !level.getBlockState(pos).isAir() ? "unexpected_block_state" : null;
            haltAction();
            return new Result(reason == null ? "removed" : "failed", reason, evidence);
        } finally {
            actor.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
        }
    }

    private Result useItem(BuildPlan.Target target, int slot, Aim aim) {
        BlockPos clicked = aim.hit().getBlockPos();
        if (!level.mayInteract(actor, clicked) || !level.mayInteract(actor, target.world())
                || !level.getBlockState(target.world()).isAir()) {
            return failure("interaction_forbidden");
        }
        lookAndSwing(aim);
        SimpleContainer inventory = npc.getInventory();
        ItemStack stack = inventory.getItem(slot);
        int before = count(target.item());
        actor.setItemInHand(InteractionHand.MAIN_HAND, stack);
        PlacementEvidence attempt = PlacementEvidence.begin(actor, target);
        InteractionResult result;
        long placementStarted = BuildPerformance.clock();
        try {
            result = actor.gameMode.useItemOn(actor, level, stack, InteractionHand.MAIN_HAND, aim.hit());
        } finally {
            performance.span(BuildPerformance.Span.PLACE, placementStarted);
            PlacementEvidence.end();
            inventory.setItem(slot, actor.getMainHandItem());
            actor.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
        }
        int after = count(target.item());
        JsonObject evidence = new JsonObject();
        evidence.addProperty("method", "ServerPlayerGameMode.useItemOn");
        evidence.addProperty("right_click_events", attempt.rightClicks);
        evidence.addProperty("place_events", attempt.placeEvents);
        evidence.addProperty("interaction", result.name());
        evidence.addProperty("item_before", before);
        evidence.addProperty("item_after", after);
        evidence.addProperty("reach", aim.distance());
        evidence.add("clicked", Wire.position(clicked));
        if (site.survival()) {
            evidence.add("npc_position", Wire.position(npc.blockPosition()));
        }
        evidence.addProperty("face", aim.hit().getDirection().getName());
        String reason = attempt.rejected || !result.consumesAction() ? "placement_rejected"
                : !ConstructionService.matches(level.getBlockState(target.world()), target.state())
                        ? "unexpected_block_state" : null;
        haltAction();
        return new Result(reason == null ? "placed" : "failed", reason, evidence);
    }

    private int count(Item item) {
        return npc.getInventory().countItem(item);
    }

    private int itemSlot(Item item) {
        SimpleContainer inventory = npc.getInventory();
        for (int slot = 0; slot < inventory.getContainerSize(); slot++) {
            if (inventory.getItem(slot).is(item)) {
                return slot;
            }
        }
        return -1;
    }

    private Result failure(String reason) {
        halt();
        return new Result("failed", reason, null);
    }

    private Result deferred(String reason) {
        haltAction();
        return new Result("deferred", reason, null);
    }

    private void stopNavigation() {
        if (npc != null) {
            npc.getNavigation().stop();
            npc.getMoveControl().strafe(0, 0);
        }
        destination = null;
    }

    private void stopMining() {
        if (mining != null && npc != null) {
            level.destroyBlockProgress(npc.getId(), mining, -1);
            mining = null;
        }
    }

    private void haltAction() {
        stopMining();
        stopNavigation();
        triedStances.clear();
        deadline = 0;
        scaffoldRoute = null;
        soughtScaffold = false;
        jumpBlock = null;
        obstruction = null;
    }

    @Override
    public void halt() {
        resetFabrication();
        descent = null;
        triedDescents.clear();
        dirtTarget = null;
        unreachableSoil.clear();
        surveyDestination = null;
        stockTarget = null;
        skippedStock.clear();
        actionTarget = null;
        recoveringScaffold = false;
        if (npc != null) {
            npc.setDisplayItem(ItemStack.EMPTY);

        }
    }

    @Override
    public JsonObject status() {
        JsonObject data = new JsonObject();
        data.addProperty("backend", "fake_players_item_interaction");
        data.addProperty("present", npc != null && npc.isAlive());
        if (npc != null) {
            if (site.worldCopy() && npc.getRemovalReason() != null) {
                data.addProperty("removal_reason", npc.getRemovalReason().name());
            }
            data.addProperty("entity_type", ForgeRegistries.ENTITY_TYPES.getKey(npc.getType()).toString());
            data.addProperty("npc_id", npc.getUUID().toString());
            data.addProperty("walked_blocks", walked);
            data.addProperty("moving", destination != null);
            data.addProperty("scaffold_dirt_collected", scaffoldDirtCollected);
            if (scaffoldRoute != null) {
                data.add("scaffold_base", Wire.position(scaffoldRoute.base()));
                data.addProperty("scaffold_work_y", scaffoldRoute.workY());
            }
            JsonArray position = new JsonArray();
            position.add(npc.getX());
            position.add(npc.getY());
            position.add(npc.getZ());
            data.add("position", position);
            JsonObject materials = new JsonObject();
            JsonObject tools = new JsonObject();
            SimpleContainer inventory = npc.getInventory();
            for (int i = 0; i < inventory.getContainerSize(); i++) {
                ItemStack stack = inventory.getItem(i);
                if (!stack.isEmpty()) {
                    String key = ForgeRegistries.ITEMS.getKey(stack.getItem()).toString();
                    if (stack.isDamageableItem()) {
                        tools.addProperty(key, stack.getDamageValue());
                    } else {
                        int stored = materials.has(key) ? materials.get(key).getAsInt() : 0;
                        materials.addProperty(key, stored + stack.getCount());
                    }
                }
            }
            data.add("materials", materials);
            data.add("tools", tools);
        }
        return data;
    }

    @Override
    public void close() {
        halt();
    }

    private record Aim(BlockHitResult hit, float yaw, float pitch, double distance) {}
}
