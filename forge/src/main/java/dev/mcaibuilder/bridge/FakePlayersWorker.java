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
import net.minecraft.network.protocol.game.ServerboundPlayerActionPacket;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.SimpleContainer;
import net.minecraft.world.inventory.ChestMenu;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.item.context.BlockPlaceContext;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.entity.ChestBlockEntity;
import net.minecraft.world.level.block.state.BlockState;
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
import java.util.function.Consumer;
import java.util.function.Predicate;

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
    private BlockPos mining;
    private long miningSince;

    FakePlayersWorker(ServerLevel level, BuildSite site, Set<Block> palette) {
        this.level = level;
        this.site = site;
        anchors = new HashSet<>(palette);
        anchors.addAll(Set.of(Blocks.GRASS_BLOCK, Blocks.DIRT, Blocks.BEDROCK));
    }

    @Override
    public void prepare(List<BuildPlan.Target> targets, boolean removals) {
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
        bind(worker);
    }

    private void bind(FakePlayerEntity worker) {
        npc = worker;
        npc.getNavigation().stop();
        npc.setJobPaused(true);
        npc.removeAllGoals(goal -> true);
        npc.setCanPickUpLoot(false);
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
                || worker.isVacant() || !site.area().contains(worker.blockPosition())) {
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
                && !npc.isPassenger() && site.area().contains(npc.blockPosition());
    }

    @Override
    public Result act(WorkStep step, Runnable beforeInteraction) {
        if (!available()) {
            return failure("npc_unavailable");
        }
        Vec3 current = trackPosition();
        Item item = Warehouse.item(step);
        int slot = supplySlot(item);
        if (slot < 0) {
            return failure(step.remove() ? "out_of_tool" : "out_of_material");
        }
        ItemStack stack = npc.getInventory().getItem(slot);
        npc.setDisplayItem(stack);
        Aim aim = chooseAim(step, stack, current);
        if (aim != null && !new AABB(step.pos()).intersects(npc.getBoundingBox())) {
            stopNavigation();
            return step.remove() ? mine(step, slot, aim, beforeInteraction)
                    : useItem(step.target(), slot, aim, beforeInteraction);
        }
        stopMining();

        String trouble = navigate(step.pos(), current, feet -> chooseAim(step, stack, feet) != null,
                step.remove() ? "no_reachable_break_stance" : "no_reachable_placement_stance");
        return trouble == null ? null : failure(trouble);
    }

    @Override
    public SimpleContainer inventory() {
        return available() ? npc.getInventory() : null;
    }

    @Override
    public boolean hasSupply(WorkStep step) {
        return available() && supplySlot(Warehouse.item(step)) >= 0;
    }

    private int supplySlot(Item item) {
        return site.survival() ? Warehouse.find(npc.getInventory(), item) : itemSlot(item);
    }

    @Override
    public JsonObject resupply(Warehouse.Source source, String stockHash, List<WorkStep> steps, int cursor,
                               Consumer<JsonObject> beforeTransfer) {
        if (!available()) {
            throw Wire.conflict("npc_unavailable", "NPC 无法搬运物品。");
        }
        ChestBlockEntity chest = Warehouse.open(level, source);
        // Check the next action before a trip, so unavailable stock is reported at once.
        Warehouse.Transfer transfer = Warehouse.planTransfer(chest, npc.getInventory(), steps, cursor);
        Vec3 current = trackPosition();
        Aim aim = chestAim(source.pos(), current);
        if (aim == null) {
            String trouble = navigate(source.pos(), current, feet -> chestAim(source.pos(), feet) != null,
                    "warehouse_unreachable");
            if (trouble != null) {
                throw Wire.conflict("warehouse_unreachable", "NPC 无法到达供料箱。");
            }
            return null;
        }
        haltAction();
        if (!level.mayInteract(actor, source.pos()) || !chest.stillValid(actor)) {
            throw Wire.conflict("warehouse_interaction_denied", "供料箱拒绝交互。");
        }
        lookAndSwing(aim);
        WarehouseEvidence interaction = WarehouseEvidence.begin(actor, source.pos());
        try {
            InteractionResult result = actor.gameMode.useItemOn(
                    actor, level, ItemStack.EMPTY, InteractionHand.MAIN_HAND, aim.hit());
            if (!result.consumesAction() || interaction.denied || interaction.rightClicks != 1 || interaction.menus != 1
                    || !(actor.containerMenu instanceof ChestMenu menu) || menu.getContainer() != chest) {
                throw Wire.conflict("warehouse_interaction_denied", "供料箱未开启，未取出任何物品。");
            }
            if (Warehouse.open(level, source) != chest || !stockHash.equals(Warehouse.digest(chest))) {
                throw Wire.conflict("warehouse_changed", "交互期间箱内物品已变化。");
            }
            transfer.evidence.add("warehouse_position", Wire.position(source.pos()));
            transfer.evidence.add("npc_position", Wire.position(npc.blockPosition()));
            transfer.evidence.addProperty("reach", aim.distance());
            transfer.evidence.addProperty("right_click_events", interaction.rightClicks);
            transfer.evidence.addProperty("container_open_events", interaction.menus);
            beforeTransfer.accept(transfer.evidence);
            transfer.apply(chest, npc.getInventory());
            return transfer.evidence;
        } finally {
            WarehouseEvidence.end();
            if (actor.containerMenu != actor.inventoryMenu) {
                actor.closeContainer();
            }
        }
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
        for (BlockPos candidate : stances(pos, current)) {
            if (!triedStances.add(candidate) || !JobHelpers.canStandAt(level, candidate)
                    || !canAim.test(Vec3.atBottomCenterOf(candidate))) {
                continue;
            }
            Path path = npc.getNavigation().createPath(candidate, 0);
            if (path != null && path.canReach() && insideSite(path) && npc.getNavigation().moveTo(path, 1.15)) {
                destination = candidate;
                return null;
            }
        }
        return unreachable;
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

    private Aim chooseAim(WorkStep step, ItemStack stack, Vec3 feet) {
        actor.setPos(feet.x, feet.y, feet.z);
        actor.setOnGround(npc.onGround());
        Vec3 eye = actor.getEyePosition();
        if (step.remove()) {
            return breakAim(step.pos(), eye);
        }
        BuildPlan.Target target = step.target();
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
                Aim aim = face(hit, eye);
                BlockPlaceContext context = new BlockPlaceContext(actor, InteractionHand.MAIN_HAND, stack, hit);
                if (context.canPlace() && context.getClickedPos().equals(target.world())
                        && target.state().getBlock().getStateForPlacement(context) == target.state()
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

    private Result mine(WorkStep step, int slot, Aim aim, Runnable beforeInteraction) {
        BlockPos pos = step.pos();
        if (!level.mayInteract(actor, pos) || level.getBlockState(pos) != step.before()) {
            return failure("interaction_forbidden");
        }
        SimpleContainer inventory = npc.getInventory();
        ItemStack stack = inventory.getItem(slot);
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

            beforeInteraction.run();
            lookAndSwing(aim);
            int damage = stack.getDamageValue();
            int maxDamage = stack.getMaxDamage();
            String tool = ForgeRegistries.ITEMS.getKey(stack.getItem()).toString();
            RemovalEvidence attempt = RemovalEvidence.begin(actor, step);
            boolean destroyed;
            try {
                var click = ForgeHooks.onLeftClickBlock(actor, pos, aim.hit().getDirection(),
                        ServerboundPlayerActionPacket.Action.START_DESTROY_BLOCK);
                destroyed = !click.isCanceled() && click.getResult() != Event.Result.DENY
                        && click.getUseItem() != Event.Result.DENY && actor.gameMode.destroyBlock(pos);
            } finally {
                RemovalEvidence.end();
                inventory.setItem(slot, actor.getMainHandItem());
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
            evidence.add("drops", attempt.drops);
            String reason = null;
            if (!destroyed || attempt.rejected) {
                reason = "break_rejected";
            } else if (level.getBlockState(pos) != step.after()) {
                reason = "unexpected_block_state";
            } else if (attempt.leftClicks != 1 || attempt.breakEvents != 1 || after - damage != 1) {
                reason = "unverified_break_interaction";
            }
            haltAction();
            return new Result(reason == null ? "removed" : "failed", reason, evidence);
        } finally {
            actor.setItemInHand(InteractionHand.MAIN_HAND, ItemStack.EMPTY);
        }
    }

    private Result useItem(BuildPlan.Target target, int slot, Aim aim, Runnable beforeInteraction) {
        BlockPos clicked = aim.hit().getBlockPos();
        if (!level.mayInteract(actor, clicked) || !level.mayInteract(actor, target.world())
                || !level.getBlockState(target.world()).isAir()) {
            return failure("interaction_forbidden");
        }
        beforeInteraction.run();
        lookAndSwing(aim);
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
        if (site.survival()) {
            evidence.add("npc_position", Wire.position(npc.blockPosition()));
        }
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
