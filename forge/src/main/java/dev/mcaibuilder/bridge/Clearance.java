package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.tags.BlockTags;
import net.minecraft.tags.TagKey;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.BasePressurePlateBlock;
import net.minecraft.world.level.block.ButtonBlock;
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

/** Protect Minecraft assets and gather terrain that can be cleared inside the worksite. */
final class Clearance {
    static final int MAX_CELLS = 16_384;
    static final Set<TagKey<Block>> PROTECTED_TAGS = Set.of(BlockTags.BEDS, BlockTags.CROPS, BlockTags.RAILS,
            BlockTags.create(ResourceLocation.tryParse("forge:chests")),
            BlockTags.create(ResourceLocation.tryParse("forge:barrels")),
            BlockTags.create(ResourceLocation.tryParse("forge:storage_blocks/redstone")));
    private static final Set<Block> PROTECTED_BLOCKS = Set.of(Blocks.NETHER_PORTAL, Blocks.END_PORTAL,
            Blocks.END_PORTAL_FRAME, Blocks.END_GATEWAY, Blocks.REDSTONE_WIRE, Blocks.REDSTONE_BLOCK,
            Blocks.REDSTONE_TORCH, Blocks.REDSTONE_WALL_TORCH, Blocks.REPEATER, Blocks.COMPARATOR,
            Blocks.REDSTONE_LAMP, Blocks.OBSERVER, Blocks.PISTON, Blocks.STICKY_PISTON, Blocks.PISTON_HEAD,
            Blocks.DAYLIGHT_DETECTOR, Blocks.TARGET, Blocks.LEVER, Blocks.TRIPWIRE, Blocks.TRIPWIRE_HOOK,
            Blocks.SCULK_SENSOR, Blocks.FARMLAND, Blocks.BEDROCK);

    record Finding(String risk, Set<BlockPos> cells) {
        static Finding blocked(String reason) {
            return new Finding(reason, Set.of());
        }
    }

    private final ServerLevel level;
    private final BuildSite site;
    private final Map<BlockPos, Finding> cache = new HashMap<>();

    Clearance(ServerLevel level, BuildSite site) {
        this.level = level;
        this.site = site;
    }

    static boolean managed(BlockState state) {
        return state.hasBlockEntity() || state.isSignalSource() || PROTECTED_BLOCKS.contains(state.getBlock())
                || state.getBlock() instanceof BasePressurePlateBlock || state.getBlock() instanceof ButtonBlock
                || PROTECTED_TAGS.stream().anyMatch(state::is);
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
        BlockState first = level.getBlockState(pos);
        if (first.isAir()) {
            return new Finding(null, Set.of());
        }
        if (pairedPlant(first)) {
            BlockPos lower = upperHalf(first) ? pos.below() : pos;
            BlockState base = level.getBlockState(lower);
            if (pairedPlant(base) && !upperHalf(base)) {
                group.add(lower);
                if (level.getBlockState(lower.above()).is(base.getBlock())) {
                    group.add(lower.above());
                }
            } else {
                group.add(pos);
            }
        } else {
            group.add(pos);
            if (first.is(BlockTags.LOGS) && !collectTrunk(pos, group)) {
                return Finding.blocked("outside_effect");
            }
            for (BlockPos cell = pos.above(); level.getBlockState(cell).getBlock() instanceof FallingBlock;
                 cell = cell.above()) {
                if (!site.canWorkAt(level, cell)) {
                    return Finding.blocked("outside_effect");
                }
                group.add(cell);
            }
        }
        for (BlockPos cell : group) {
            String risk = mutationRisk(cell, level.getBlockState(cell), true, group);
            if (risk != null) {
                return Finding.blocked(risk);
            }
        }
        if (!collectDependentLeaves(group)) {
            return Finding.blocked("outside_effect");
        }
        return new Finding(null, Set.copyOf(group));
    }

    private boolean collectTrunk(BlockPos origin, Set<BlockPos> group) {
        var pending = new ArrayDeque<BlockPos>();
        pending.add(origin);
        while (!pending.isEmpty()) {
            BlockPos log = pending.removeFirst();
            for (Direction side : Direction.values()) {
                BlockPos next = log.relative(side);
                if (!level.hasChunkAt(next) || !level.getBlockState(next).is(BlockTags.LOGS) || group.contains(next)) {
                    continue;
                }
                // Grounded adjoining timber is a separate column. Branches stay within the authorized site.
                if (side.getAxis().isHorizontal() && level.getBlockState(next.below()).isFaceSturdy(level, next.below(), Direction.UP)
                        && !level.getBlockState(next.below()).is(BlockTags.LOGS)) {
                    continue;
                }
                if (group.size() >= MAX_CELLS || protectedReason(next, level.getBlockState(next)) != null) {
                    return false;
                }
                group.add(next);
                pending.add(next);
            }
        }
        return true;
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
        if (!site.canWorkAt(level, pos)) {
            return "outside_worksite";
        }
        if (managed(state) || level.getBlockEntity(pos) != null) {
            return "protected_facility";
        }
        if (!state.getFluidState().isEmpty() || WorldObservation.hazard(state)) {
            return "hazardous_terrain";
        }
        return state.getDestroySpeed(level, pos) < 0 ? "protected_facility" : null;
    }

    String mutationRisk(BlockPos pos, BlockState state, boolean breaking, Set<BlockPos> removedTogether) {
        String risk = protectedReason(pos, state);
        if (risk != null || !breaking) {
            return risk;
        }
        for (Direction direction : Direction.values()) {
            BlockPos neighbor = pos.relative(direction);
            if (!level.hasChunkAt(neighbor)) {
                return "site_not_loaded";
            }
            BlockState adjacent = level.getBlockState(neighbor);
            if (adjacent.isAir() || removedTogether.contains(neighbor)) {
                continue;
            }
            if (!adjacent.getFluidState().isEmpty() || WorldObservation.hazard(adjacent)) {
                return "hazardous_terrain";
            }
            boolean attached = !Block.isShapeFullBlock(adjacent.getCollisionShape(level, neighbor));
            boolean supported = direction == Direction.UP || attached && direction.getAxis().isHorizontal();
            if (adjacent.is(Blocks.NETHER_PORTAL) || adjacent.is(Blocks.END_PORTAL)
                    || supported && (managed(adjacent) || level.getBlockEntity(neighbor) != null
                    || site.worksite().protectedAt(neighbor))) {
                return "protected_facility";
            }
            if (direction == Direction.UP && adjacent.getBlock() instanceof FallingBlock
                    || supported && !site.area().contains(neighbor) && attached) {
                return "outside_effect";
            }
        }
        return null;
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
