package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.level.BlockGetter;
import net.minecraft.world.level.EmptyBlockGetter;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.BushBlock;
import net.minecraft.world.level.block.DoublePlantBlock;
import net.minecraft.world.level.block.FallingBlock;
import net.minecraft.world.level.block.LeavesBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.BlockStateProperties;
import net.minecraft.world.level.block.state.properties.DoubleBlockHalf;
import net.minecraft.world.level.storage.loot.BuiltInLootTables;
import net.minecraftforge.common.IPlantable;

import java.util.ArrayDeque;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.function.Function;
import java.util.function.Predicate;

/** Map native mining and immediate block dependencies to work inside the signed site. */
final class Clearance {
    static final int MAX_CELLS = 512;

    record Finding(String risk, Set<BlockPos> cells) {
        static Finding blocked(String reason) {
            return new Finding(reason, Set.of());
        }
    }

    private final ServerLevel level;
    private final BuildSite site;
    private final Predicate<BlockPos> edited;
    private final Map<BlockPos, Finding> cache = new HashMap<>();

    Clearance(ServerLevel level, BuildSite site) {
        this(level, site, pos -> false);
    }

    Clearance(ServerLevel level, BuildSite site, Predicate<BlockPos> edited) {
        this.level = level;
        this.site = site;
        this.edited = edited;
    }

    static boolean managed(BlockState state) {
        return state.hasBlockEntity() || state.isSignalSource() || state.is(BlockTags.CROPS)
                || state.getLightEmission() > 0;
    }

    private static TerrainTraits physical(BlockState state, BlockGetter world, BlockPos pos) {
        Block block = state.getBlock();
        return new TerrainTraits(block instanceof BushBlock || block instanceof IPlantable, state.canBeReplaced(),
                state.getCollisionShape(world, pos).isEmpty(), state.getDestroySpeed(world, pos),
                managed(state) || WorldObservation.hazard(state), !state.getFluidState().isEmpty(), state.is(BlockTags.DIRT),
                block instanceof FallingBlock && state.is(BlockTags.MINEABLE_WITH_SHOVEL),
                state.is(BlockTags.BASE_STONE_OVERWORLD) || state.is(BlockTags.BASE_STONE_NETHER));
    }

    private static String kind(BlockState state, BlockGetter world, BlockPos pos) {
        TerrainTraits traits = physical(state, world, pos);
        if (state.isAir() || traits.ownedFunction() || traits.liquid()
                || !Float.isFinite(traits.hardness()) || traits.hardness() < 0) {
            return "unknown";
        }
        if (state.is(BlockTags.LOGS)) {
            return "logs";
        }
        if (state.is(BlockTags.LEAVES)) {
            return "leaves";
        }
        String surface = traits.removalKind();
        if (!surface.equals("unknown")) {
            return surface;
        }
        if (state.is(BlockTags.MINEABLE_WITH_SHOVEL)) {
            return "soil";
        }
        if (state.is(BlockTags.MINEABLE_WITH_AXE)) {
            return "wood";
        }
        return state.is(BlockTags.MINEABLE_WITH_PICKAXE) || state.requiresCorrectToolForDrops() ? "masonry" : "hand";
    }

    static String kind(BlockState state) {
        try {
            return kind(state, EmptyBlockGetter.INSTANCE, BlockPos.ZERO);
        } catch (RuntimeException unavailableWithoutNeighbors) {
            return "unknown";
        }
    }

    JsonObject describe(BlockPos pos) {
        BlockState state = level.getBlockState(pos);
        TerrainTraits traits = physical(state, level, pos);
        JsonObject data = new JsonObject();
        data.addProperty("id", Wire.state(state).get("id").getAsString());
        data.addProperty("replaceable", traits.replaceable());
        data.addProperty("collision_free", traits.collisionFree());
        data.addProperty("hardness", Float.isFinite(traits.hardness()) ? Math.max(-1, Math.min(traits.hardness(), 1000)) : -1);
        data.addProperty("has_loot_table", !state.getBlock().getLootTable().equals(BuiltInLootTables.EMPTY));
        data.addProperty("requires_tool_for_drops", state.requiresCorrectToolForDrops());
        data.addProperty("form", kind(state, level, pos));
        data.addProperty("managed", traits.ownedFunction());
        data.addProperty("block_entity", level.getBlockEntity(pos) != null || state.hasBlockEntity());
        data.addProperty("liquid", traits.liquid());
        return data;
    }

    private static boolean dependentLeaf(BlockState state) {
        return state.hasProperty(LeavesBlock.PERSISTENT) && !state.getValue(LeavesBlock.PERSISTENT)
                && state.hasProperty(LeavesBlock.DISTANCE) && state.getValue(LeavesBlock.DISTANCE) < 7;
    }

    static boolean upperHalf(BlockState state) {
        return state.getBlock() instanceof DoublePlantBlock && state.hasProperty(BlockStateProperties.DOUBLE_BLOCK_HALF)
                && state.getValue(BlockStateProperties.DOUBLE_BLOCK_HALF) == DoubleBlockHalf.UPPER;
    }

    static boolean pairedPlant(BlockState state) {
        return state.getBlock() instanceof DoublePlantBlock && state.hasProperty(BlockStateProperties.DOUBLE_BLOCK_HALF)
                && kind(state).equals("vegetation");
    }

    Finding inspect(BlockPos pos) {
        return cache.computeIfAbsent(pos.immutable(), this::inspectColumn);
    }

    private Finding inspectColumn(BlockPos pos) {
        Set<BlockPos> group = new HashSet<>();
        for (BlockPos cell = pos; ; cell = cell.above()) {
            BlockState state = level.getBlockState(cell);
            if (state.isAir()) {
                break;
            }
            String risk = protectedReason(cell, state);
            if (risk != null || kind(state).equals("unknown") || group.size() >= MAX_CELLS) {
                return Finding.blocked(risk == null ? "unsafe_interaction" : risk);
            }
            group.add(cell);
            if (pairedPlant(state)) {
                BlockPos lower = upperHalf(state) ? cell.below() : cell;
                BlockState base = level.getBlockState(lower);
                if (!pairedPlant(base) || upperHalf(base) || base.setValue(BlockStateProperties.DOUBLE_BLOCK_HALF,
                        DoubleBlockHalf.UPPER) != level.getBlockState(lower.above())) {
                    return Finding.blocked("structure_support");
                }
                group.add(lower);
                group.add(lower.above());
                break;
            }
            if (!canSupport(state, cell)) {
                break;
            }
        }
        if (!collectDependentLeaves(group)) {
            return Finding.blocked("outside_effect");
        }
        for (BlockPos cell : group) {
            String risk = impact(cell, level.getBlockState(cell), group, false);
            if (risk != null) {
                return Finding.blocked(risk);
            }
        }
        return new Finding(null, Set.copyOf(group));
    }

    private boolean canSupport(BlockState state, BlockPos cell) {
        return !state.is(BlockTags.LEAVES) && !state.getBlockSupportShape(level, cell).isEmpty();
    }

    private boolean collectDependentLeaves(Set<BlockPos> group) {
        var next = new ArrayDeque<BlockPos>();
        for (BlockPos pos : group) {
            BlockState state = level.getBlockState(pos);
            if (dependentLeaf(state) || state.is(BlockTags.LOGS)) {
                next.add(pos);
            }
        }
        while (!next.isEmpty()) {
            BlockPos from = next.removeFirst();
            for (Direction side : Direction.values()) {
                BlockPos leaf = from.relative(side);
                if (!level.hasChunkAt(leaf)) {
                    return false;
                }
                BlockState state = level.getBlockState(leaf);
                if (!dependentLeaf(state) || group.contains(leaf)
                        || retainsLeafSupport(leaf, state.getValue(LeavesBlock.DISTANCE), group)) {
                    continue;
                }
                if (group.size() >= MAX_CELLS || protectedReason(leaf, state) != null) {
                    return false;
                }
                group.add(leaf);
                next.addLast(leaf);
            }
        }
        return true;
    }

    private boolean retainsLeafSupport(BlockPos leaf, int distance, Set<BlockPos> removed) {
        for (Direction side : Direction.values()) {
            BlockPos neighbor = leaf.relative(side);
            if (!level.hasChunkAt(neighbor) || removed.contains(neighbor)) {
                continue;
            }
            BlockState state = level.getBlockState(neighbor);
            if (state.is(BlockTags.LOGS) || distance > 1 && state.hasProperty(LeavesBlock.DISTANCE)
                    && state.getValue(LeavesBlock.DISTANCE) < distance
                    && retainsLeafSupport(neighbor, state.getValue(LeavesBlock.DISTANCE), removed)) {
                return true;
            }
        }
        return false;
    }

    private String protectedReason(BlockPos pos, BlockState state) {
        if (site.worksite().protectedAt(pos)) {
            return "protected_area";
        }
        if (!site.canBuildAt(pos) || !level.hasChunkAt(pos)) {
            return "outside_worksite";
        }
        if (managed(state) || level.getBlockEntity(pos) != null) {
            return "protected_facility";
        }
        if (!state.getFluidState().isEmpty() || WorldObservation.hazard(state)) {
            return "hazardous_terrain";
        }
        return edited.test(pos) ? "player_structure" : null;
    }

    private String impact(BlockPos pos, BlockState state, Set<BlockPos> prepared, boolean execution) {
        String target = protectedReason(pos, state);
        if (target != null) {
            return target;
        }
        boolean support = canSupport(state, pos);
        for (Direction direction : Direction.values()) {
            BlockPos neighbor = pos.relative(direction);
            if (!level.hasChunkAt(neighbor)) {
                return "site_not_loaded";
            }
            BlockState nearby = level.getBlockState(neighbor);
            if (nearby.isAir()) {
                continue;
            }
            if (!nearby.getFluidState().isEmpty() || WorldObservation.hazard(nearby)) {
                return "hazardous_terrain";
            }
            boolean inPlan = prepared.contains(neighbor);
            if (direction == Direction.UP && nearby.getBlock() instanceof FallingBlock && (!inPlan || execution)) {
                return "structure_support";
            }
            if (support && direction == Direction.UP && (!inPlan || execution)) {
                return "structure_support";
            }
            if (support && direction.getAxis().isHorizontal() && !inPlan
                    && (managed(nearby) || edited.test(neighbor) || !site.canBuildAt(neighbor))
                    && !Block.isShapeFullBlock(nearby.getCollisionShape(level, neighbor))) {
                return "outside_effect";
            }
        }
        return null;
    }

    void requireLive(WorkStep step, Set<BlockPos> prepared, Function<BlockPos, BlockState> expected) {
        for (Direction direction : Direction.values()) {
            BlockPos neighbor = step.pos().relative(direction);
            if (prepared.contains(neighbor) && level.getBlockState(neighbor) != expected.apply(neighbor)) {
                throw Wire.conflict("worksite_preparation_changed", "相邻的待清理位置已变化，请核对现场。");
            }
            BlockState adjacent = level.getBlockState(neighbor);
            if ((step.before().is(BlockTags.LOGS) || step.before().hasProperty(LeavesBlock.DISTANCE))
                    && dependentLeaf(adjacent)
                    && !retainsLeafSupport(neighbor, adjacent.getValue(LeavesBlock.DISTANCE), Set.of(step.pos()))) {
                throw Wire.conflict("worksite_preparation_changed", "当前挖掘会影响新的附属树叶，请重新观察。");
            }
        }
        String risk = impact(step.pos(), step.before(), prepared, true);
        if (risk == null && step.secondary() != null) {
            risk = impact(step.secondary(), level.getBlockState(step.secondary()), prepared, true);
        }
        if (risk != null) {
            throw Wire.conflict(risk, "清障会影响保护对象或未经授权的位置，请核对现场。");
        }
    }

    static boolean limited(Map<BlockPos, BlockState> cells) {
        return cells.size() <= MAX_CELLS;
    }

    static JsonObject counts(Map<BlockPos, BlockState> cells) {
        Map<String, Integer> totals = new java.util.TreeMap<>();
        cells.values().forEach(state -> totals.merge(kind(state), 1, Integer::sum));
        JsonObject result = new JsonObject();
        totals.forEach(result::addProperty);
        return result;
    }

    Map<BlockPos, BlockState> states(Finding finding) {
        Map<BlockPos, BlockState> states = new LinkedHashMap<>();
        finding.cells().stream().sorted(BuildPlan.BUILD_ORDER).forEach(pos -> states.put(pos, level.getBlockState(pos)));
        return states;
    }

    static Comparator<WorkStep> order(String strategy) {
        boolean canopyFirst = strategy.equals("tree_first");
        // Native fall/support updates need the upper cell removed first. Leaf distance is also a dependency.
        return Comparator.<WorkStep>comparingInt(step -> switch (kind(step.before())) {
                    case "leaves" -> canopyFirst ? 0 : 1;
                    case "vegetation" -> canopyFirst ? 1 : 0;
                    default -> 2;
                })
                .thenComparingInt(step -> dependentLeaf(step.before()) ? -step.before().getValue(LeavesBlock.DISTANCE) : 0)
                .thenComparingInt(step -> -step.pos().getY())
                .thenComparing(step -> step.pos(), BuildPlan.BUILD_ORDER);
    }
}
