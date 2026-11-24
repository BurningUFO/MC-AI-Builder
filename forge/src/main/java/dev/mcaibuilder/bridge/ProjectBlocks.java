package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.DoublePlantBlock;
import net.minecraft.world.level.block.DoorBlock;
import net.minecraft.world.level.block.FlowerPotBlock;
import net.minecraft.world.level.block.FenceBlock;
import net.minecraft.world.level.block.WallBlock;
import net.minecraft.world.level.block.IronBarsBlock;
import net.minecraft.world.level.block.TrapDoorBlock;
import net.minecraft.world.level.block.FenceGateBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.BlockStateProperties;
import net.minecraft.world.level.block.state.properties.DoubleBlockHalf;
import net.minecraft.world.level.block.state.properties.SlabType;
import net.minecraftforge.registries.ForgeRegistries;

import java.util.Set;

final class ProjectBlocks {
    private static final Set<Block> EMPTY_FIXTURES = Set.of(Blocks.BARREL, Blocks.CRAFTING_TABLE,
            Blocks.FURNACE, Blocks.ENCHANTING_TABLE, Blocks.LECTERN);
    private static final Set<String> JOIN_PROPERTIES = Set.of("north", "east", "south", "west", "up", "shape",
            "table_leg1", "table_leg2", "table_leg3", "table_leg4");

    private ProjectBlocks() {}

    static String name(String value) {
        if (!value.matches("[\\p{L}\\p{N}_][\\p{L}\\p{N}_. -]{0,95}")
                || value.endsWith(".") || value.endsWith(" ")) {
            throw Wire.invalid("Invalid project name");
        }
        return value;
    }

    static BlockState read(JsonObject action) {
        var id = ResourceLocation.tryParse(Wire.string(action.get("id"), "action.id"));
        if (id == null || !ForgeRegistries.BLOCKS.containsKey(id)) {
            throw Wire.conflict("project_block_unavailable", "这个工程需要当前实例没有的方块 " + action.get("id").getAsString()
                    + "。切换到包含原材料的 BMC4 实例后可以复现。");
        }
        BlockState state = Wire.readState(action);
        if (!allowed(state)) {
            ForgeBridge.LOGGER.info("project_state_unsupported {} managed={} entity={} signal={} fluid={} item={} type={}",
                    id, Clearance.managed(state), state.hasBlockEntity(), state.isSignalSource(), state.getFluidState(),
                    ForgeRegistries.ITEMS.getKey(primaryItem(state)), state.getBlock().getClass().getSimpleName());
            throw Wire.conflict("project_block_unsupported", "这个工程包含 Builder 无法原样放置的方块状态 "
                    + id + "。请检查工程的功能方块和含水状态。");
        }
        return state;
    }

    static boolean allowed(BlockState state) {
        Block block = state.getBlock();
        if (state.isAir() || !state.getFluidState().isEmpty() || WorldObservation.hazard(state)
                || Clearance.managed(state) && !emptyFixture(block) && !structural(block)) {
            return false;
        }
        return placementItem(state, primaryItem(state), true) && (pottedItem(state) == Items.AIR || pottedItem(state) instanceof BlockItem);
    }

    private static boolean structural(Block block) {
        return block instanceof FenceBlock || block instanceof WallBlock || block instanceof IronBarsBlock
                || block instanceof DoorBlock || block instanceof TrapDoorBlock || block instanceof FenceGateBlock;
    }

    private static boolean emptyFixture(Block block) {
        if (EMPTY_FIXTURES.contains(block)) {
            return true;
        }
        var id = ForgeRegistries.BLOCKS.getKey(block);
        return id != null && (id.getNamespace().equals("handcrafted") && id.getPath().endsWith("_chair")
                || id.getNamespace().equals("twigs") && id.getPath().endsWith("_table"));
    }

    static boolean placementItem(BlockState state, Item item, boolean replica) {
        return item instanceof BlockItem placing && (placing.getBlock() == primaryState(state).getBlock()
                || state.is(Blocks.WALL_TORCH) || replica && convertedStructure(placing.getBlock(), state.getBlock()));
    }

    private static boolean convertedStructure(Block actual, Block expected) {
        var id = ForgeRegistries.BLOCKS.getKey(actual);
        return id != null && structural(expected) && actual.asItem() == expected.asItem()
                && Set.of("diagonalfences", "diagonalwalls", "diagonalwindows").contains(id.getNamespace());
    }

    static boolean nativePlacement(BlockState actual, BuildPlan.Target target) {
        return target.matches(actual) || target.replica() && actual.getFluidState().isEmpty()
                && convertedStructure(actual.getBlock(), target.state().getBlock());
    }

    static BlockState restoreSourceBlock(BlockState actual, BuildPlan.Target target) {
        return target.replica() && actual.getBlock() != target.state().getBlock()
                && nativePlacement(actual, target) ? target.state() : actual;
    }

    static Item primaryItem(BlockState state) {
        return state.is(Blocks.WALL_TORCH) ? Items.TORCH : primaryState(state).getBlock().asItem();
    }

    static BlockState primaryState(BlockState state) {
        if (pottedItem(state) != Items.AIR) {
            return Blocks.FLOWER_POT.defaultBlockState();
        }
        return state;
    }

    static Item pottedItem(BlockState state) {
        if (state.getBlock() instanceof FlowerPotBlock pot) {
            return pot.getContent().asItem();
        }
        return Items.AIR;
    }

    /** A hand-opened door still represents both installed parts of the same block. */
    static BlockState alignDoorOpen(BlockState actual, BlockState desired) {
        if (actual.is(desired.getBlock()) && actual.getBlock() instanceof DoorBlock door && door.type().canOpenByHand()
                && !actual.getValue(DoorBlock.POWERED) && !desired.getValue(DoorBlock.POWERED)) {
            return actual.setValue(DoorBlock.OPEN, desired.getValue(DoorBlock.OPEN));
        }
        return actual;
    }

    static int units(BlockState state) {
        if (state.hasProperty(BlockStateProperties.FLOWER_AMOUNT)) {
            return state.getValue(BlockStateProperties.FLOWER_AMOUNT);
        }
        return state.hasProperty(BlockStateProperties.SLAB_TYPE)
                && state.getValue(BlockStateProperties.SLAB_TYPE) == SlabType.DOUBLE ? 2 : 1;
    }

    static boolean doubleHigh(BlockState state) {
        return state.getBlock() instanceof DoorBlock || state.getBlock() instanceof DoublePlantBlock;
    }

    static boolean upper(BlockState state) {
        return doubleHigh(state) && state.getValue(BlockStateProperties.DOUBLE_BLOCK_HALF) == DoubleBlockHalf.UPPER;
    }

    static BlockState otherHalf(BlockState state) {
        return state.setValue(BlockStateProperties.DOUBLE_BLOCK_HALF,
                upper(state) ? DoubleBlockHalf.LOWER : DoubleBlockHalf.UPPER);
    }

    static ItemStack placementStack(ItemStack actual, BlockState desired) {
        ItemStack stack = actual.copyWithCount(1);
        CompoundTag tags = new CompoundTag();
        Wire.state(desired).getAsJsonObject("properties").entrySet().forEach(entry ->
                tags.putString(entry.getKey(), entry.getValue().getAsString()));
        if (!tags.isEmpty()) {
            stack.addTagElement("BlockStateTag", tags);
        }
        return stack;
    }

    /** Neighbor updates can derive these properties as the rest of the project is assembled. */
    static BlockState alignJoins(BlockState actual, BlockState desired) {
        if (actual == desired || actual.getBlock() != desired.getBlock()
                || Clearance.managed(actual) && !structural(actual.getBlock())) {
            return actual;
        }
        BlockState aligned = actual;
        for (var property : actual.getProperties()) {
            if (JOIN_PROPERTIES.contains(property.getName())) {
                aligned = withValue(aligned, desired, property);
            }
        }
        return aligned;
    }

    private static <T extends Comparable<T>> BlockState withValue(BlockState actual, BlockState desired,
            net.minecraft.world.level.block.state.properties.Property<T> property) {
        return actual.setValue(property, desired.getValue(property));
    }
}
