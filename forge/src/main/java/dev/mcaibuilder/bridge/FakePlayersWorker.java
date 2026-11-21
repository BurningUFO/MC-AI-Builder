package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.mojang.authlib.GameProfile;
import dev.duzo.players.entities.FakePlayerEntity;
import dev.duzo.players.entities.ai.JobHelpers;
import dev.duzo.players.entities.possession.PossessionReconciler;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.SimpleContainer;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.context.BlockPlaceContext;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.pathfinder.Path;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;
import net.minecraftforge.common.util.FakePlayer;
import net.minecraftforge.registries.ForgeRegistries;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

final class FakePlayersWorker implements ConstructionWorker {
    private static final double REACH = 4.5;
    private static final int MOVE_LIMIT_TICKS = 300;
    private static final int STANCE_RADIUS = 4;
    private static final Direction[] FACES = {Direction.UP, Direction.NORTH, Direction.SOUTH,
            Direction.WEST, Direction.EAST, Direction.DOWN};

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

    FakePlayersWorker(ServerLevel level, BuildSite site, Set<Block> palette) {
        this.level = level;
        this.site = site;
        anchors = new HashSet<>(palette);
        anchors.addAll(Set.of(Blocks.GRASS_BLOCK, Blocks.DIRT, Blocks.BEDROCK));
    }

    @Override
    public void prepare(List<BuildPlan.Target> targets) {
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
        if (npc == null || !npc.isAlive()) {
            spawn();
        }
        if (npc.isVacant() || npc.isUnreconciled() || supplied.size() > npc.getInventory().getContainerSize()) {
            throw Wire.conflict("npc_unavailable", "The worker cannot accept the preset materials");
        }
        npc.getInventory().clearContent();
        for (int slot = 0; slot < supplied.size(); slot++) {
            npc.getInventory().setItem(slot, supplied.get(slot));
        }
        halt();
        walked = 0;
        lastPosition = npc.position();
    }

    private void spawn() {
        BlockPos feet = site.spawn();
        if (!JobHelpers.canStandAt(level, feet)) {
            throw Wire.conflict("spawn_blocked", "The approved NPC spawn has no walkable floor");
        }
        FakePlayerEntity worker = new FakePlayerEntity(level);
        worker.moveTo(feet.getX() + 0.5, feet.getY(), feet.getZ() + 0.5, 0, 0);
        worker.setCustomName(Component.literal("MC Builder"));
        worker.setCustomNameVisible(true);
        worker.setPersistenceRequired();
        worker.setCanPickUpLoot(false);
        worker.setJobPaused(true);
        worker.removeAllGoals(goal -> true);
        worker.addTag(ForgeBridge.MOD_ID);
        PossessionReconciler.reconcile(worker, level);
        if (worker.isUnreconciled() || worker.isVacant()) {
            throw Wire.conflict("npc_unavailable", "Fake Players could not initialize the worker inventory");
        }
        if (!level.noCollision(worker) || !level.addFreshEntity(worker)) {
            throw Wire.conflict("spawn_blocked", "Could not spawn players:fake_player at the approved position");
        }
        npc = worker;
        actor = new FakePlayer(level, new GameProfile(npc.getUUID(), "MCBuilder"));
        actor.gameMode.changeGameModeForPlayer(GameType.SURVIVAL);
    }

    @Override
    public Result act(BuildPlan.Target target) {
        if (npc == null || !npc.isAlive() || npc.isVacant() || npc.isUnreconciled()
                || npc.isPassenger() || !site.area().contains(npc.blockPosition())) {
            return failure("npc_unavailable");
        }
        Vec3 current = npc.position();
        walked += current.distanceTo(lastPosition);
        lastPosition = current;
        int slot = itemSlot(target.item());
        if (slot < 0) {
            return failure("out_of_material");
        }
        ItemStack stack = npc.getInventory().getItem(slot);
        npc.setDisplayItem(stack);
        Aim aim = chooseAim(target, stack, current);
        if (aim != null && !new AABB(target.world()).intersects(npc.getBoundingBox())) {
            stopNavigation();
            return useItem(target, slot, aim);
        }

        long now = level.getGameTime();
        if (deadline == 0) {
            deadline = now + MOVE_LIMIT_TICKS;
        }
        if (now >= deadline) {
            return failure("navigation_timeout");
        }
        if (destination != null && !npc.getNavigation().isDone()) {
            return null;
        }
        for (BlockPos candidate : stances(target.world(), current)) {
            if (!triedStances.add(candidate) || !JobHelpers.canStandAt(level, candidate)
                    || chooseAim(target, stack, Vec3.atBottomCenterOf(candidate)) == null) {
                continue;
            }
            Path path = npc.getNavigation().createPath(candidate, 0);
            if (path != null && path.canReach() && insideSite(path) && npc.getNavigation().moveTo(path, 1.15)) {
                destination = candidate;
                return null;
            }
        }
        return failure("no_reachable_placement_stance");
    }

    private List<BlockPos> stances(BlockPos target, Vec3 current) {
        List<BlockPos> candidates = new ArrayList<>();
        int minY = Math.max(site.area().min().getY(), target.getY() - 3);
        int maxY = Math.min(site.area().max().getY() - 1, target.getY() + 1);
        BlockPos min = new BlockPos(target.getX() - STANCE_RADIUS, minY, target.getZ() - STANCE_RADIUS);
        BlockPos max = new BlockPos(target.getX() + STANCE_RADIUS, maxY, target.getZ() + STANCE_RADIUS);
        for (BlockPos pos : BlockPos.betweenClosed(min, max)) {
            if (site.area().contains(pos) && !pos.equals(target) && !pos.above().equals(target)) {
                candidates.add(pos.immutable());
            }
        }
        candidates.sort(Comparator.comparingDouble(pos -> Vec3.atBottomCenterOf(pos).distanceToSqr(current)));
        return candidates;
    }

    private boolean insideSite(Path path) {
        for (int i = 0; i < path.getNodeCount(); i++) {
            if (!site.area().contains(path.getNode(i).asBlockPos())) {
                return false;
            }
        }
        return true;
    }

    private Aim chooseAim(BuildPlan.Target target, ItemStack stack, Vec3 feet) {
        actor.setPos(feet.x, feet.y, feet.z);
        Vec3 eye = actor.getEyePosition();
        for (Direction face : FACES) {
            BlockPos clicked = target.world().relative(face.getOpposite());
            if (!anchors.contains(level.getBlockState(clicked).getBlock())) {
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
                Vec3 delta = hit.getLocation().subtract(eye);
                float yaw = (float) Math.toDegrees(Math.atan2(delta.z, delta.x)) - 90;
                float pitch = (float) -Math.toDegrees(Math.atan2(delta.y, delta.horizontalDistance()));
                actor.setYRot(yaw);
                actor.setXRot(pitch);
                BlockPlaceContext context = new BlockPlaceContext(actor, InteractionHand.MAIN_HAND, stack, hit);
                if (context.canPlace() && context.getClickedPos().equals(target.world())
                        && target.state().getBlock().getStateForPlacement(context) == target.state()
                        && target.state().canSurvive(level, target.world())) {
                    return new Aim(hit, yaw, pitch, delta.length());
                }
            }
        }
        return null;
    }

    private Result useItem(BuildPlan.Target target, int slot, Aim aim) {
        BlockPos clicked = aim.hit().getBlockPos();
        if (!level.mayInteract(actor, clicked) || !level.mayInteract(actor, target.world())
                || !level.getBlockState(target.world()).isAir()) {
            return failure("interaction_forbidden");
        }
        npc.setYRot(aim.yaw());
        npc.setYHeadRot(aim.yaw());
        npc.setYBodyRot(aim.yaw());
        npc.setXRot(aim.pitch());
        npc.swing(InteractionHand.MAIN_HAND);
        SimpleContainer inventory = npc.getInventory();
        ItemStack stack = inventory.getItem(slot);
        int before = count(target.item());
        actor.setItemInHand(InteractionHand.MAIN_HAND, stack);
        PlacementEvidence attempt = PlacementEvidence.begin(actor, target);
        InteractionResult result;
        try {
            result = actor.gameMode.useItemOn(actor, level, stack, InteractionHand.MAIN_HAND, aim.hit());
        } finally {
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
        evidence.addProperty("face", aim.hit().getDirection().getName());
        String reason = null;
        if (attempt.rejected || !result.consumesAction()) {
            reason = "placement_rejected";
        } else if (level.getBlockState(target.world()) != target.state()) {
            reason = "unexpected_block_state";
        } else if (attempt.rightClicks != 1 || attempt.placeEvents != 1 || before - after != 1) {
            reason = "unverified_item_interaction";
        }
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

    private void stopNavigation() {
        if (npc != null) {
            npc.getNavigation().stop();
        }
        destination = null;
    }

    private void haltAction() {
        stopNavigation();
        triedStances.clear();
        deadline = 0;
    }

    @Override
    public void halt() {
        haltAction();
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
            data.addProperty("entity_type", ForgeRegistries.ENTITY_TYPES.getKey(npc.getType()).toString());
            data.addProperty("npc_id", npc.getUUID().toString());
            data.addProperty("walked_blocks", walked);
            data.addProperty("moving", destination != null);
            JsonArray position = new JsonArray();
            position.add(npc.getX());
            position.add(npc.getY());
            position.add(npc.getZ());
            data.add("position", position);
            JsonObject materials = new JsonObject();
            SimpleContainer inventory = npc.getInventory();
            for (int i = 0; i < inventory.getContainerSize(); i++) {
                ItemStack stack = inventory.getItem(i);
                if (!stack.isEmpty()) {
                    String key = ForgeRegistries.ITEMS.getKey(stack.getItem()).toString();
                    int stored = materials.has(key) ? materials.get(key).getAsInt() : 0;
                    materials.addProperty(key, stored + stack.getCount());
                }
            }
            data.add("materials", materials);
        }
        return data;
    }

    @Override
    public void close() {
        halt();
        if (npc != null && npc.isAlive()) {
            npc.discard();
        }
    }

    private record Aim(BlockHitResult hit, float yaw, float pitch, double distance) {}
}
