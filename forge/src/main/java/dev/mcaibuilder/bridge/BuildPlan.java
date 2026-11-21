package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.Item;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.Rotation;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.BlockStateProperties;
import net.minecraft.world.level.block.state.properties.Property;
import net.minecraft.world.level.block.state.properties.SlabType;
import net.minecraftforge.registries.ForgeRegistries;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

record BuildPlan(UUID id, String buildingId, int version, List<Target> targets, BuildSite.Box worldBounds) {
    private static final int MAX_ACTIONS = 512;
    private static final Comparator<BlockPos> BUILD_ORDER = Comparator.<BlockPos>comparingInt(BlockPos::getY)
            .thenComparingInt(BlockPos::getZ).thenComparingInt(BlockPos::getX);

    record Target(BlockPos local, BlockPos world, BlockState state, Item item) {}

    static Set<Block> palette() {
        // Resolve vanilla blocks after the server has finished registry initialization.
        return Set.of(Blocks.STONE, Blocks.COBBLESTONE, Blocks.STONE_BRICKS, Blocks.OAK_PLANKS,
                Blocks.SPRUCE_PLANKS, Blocks.GLASS, Blocks.OAK_STAIRS, Blocks.STONE_BRICK_STAIRS,
                Blocks.OAK_SLAB, Blocks.STONE_BRICK_SLAB);
    }

    static BuildPlan read(JsonObject data, BuildSite site, Set<Block> palette) {
        if (Wire.integer(data.get("schema_version"), "schema_version") != 1
                || !Wire.string(data.get("minecraft_version"), "minecraft_version").equals("1.20.1")
                || Wire.integer(data.get("data_version"), "data_version") != 3465) {
            throw Wire.invalid("Expected plan schema 1 and Minecraft 1.20.1 data (3465)");
        }
        UUID id = id(data, "plan_id");
        String buildingId = Wire.string(data.get("building_id"), "building_id");
        int version = Wire.integer(data.get("building_version"), "building_version");
        if (!buildingId.matches("[a-z0-9][a-z0-9_.-]{0,63}") || version < 1) {
            throw Wire.invalid("Invalid building ID or version");
        }
        BlockPos origin = Wire.position(data.get("origin"), "origin");
        Rotation rotation = switch (Wire.integer(data.get("rotation"), "rotation")) {
            case 0 -> Rotation.NONE;
            case 90 -> Rotation.CLOCKWISE_90;
            case 180 -> Rotation.CLOCKWISE_180;
            case 270 -> Rotation.COUNTERCLOCKWISE_90;
            default -> throw Wire.invalid("Rotation must be 0, 90, 180 or 270 degrees clockwise");
        };
        BuildSite.Box declared = BuildSite.Box.read(data.get("local_bounds"), "local_bounds");
        JsonArray actions = Wire.array(data.get("actions"), "actions");
        if (actions.isEmpty() || actions.size() > MAX_ACTIONS) {
            throw Wire.invalid("Expected 1–" + MAX_ACTIONS + " actions");
        }

        List<Target> targets = new ArrayList<>(actions.size());
        Set<BlockPos> localPositions = new HashSet<>();
        List<BlockPos> worldPositions = new ArrayList<>();
        for (JsonElement element : actions) {
            JsonObject action = Wire.object(element, "action");
            BlockPos local = Wire.position(action.get("local"), "action.local");
            BlockPos world = origin.offset(local.rotate(rotation));
            if (!site.area().contains(world) || world.equals(site.spawn()) || world.equals(site.spawn().above())) {
                throw Wire.invalid("Target is outside the approved buildable area: " + world.toShortString());
            }
            if (!localPositions.add(local)) {
                throw Wire.invalid("Duplicate target: " + local.toShortString());
            }
            if (action.has("block_entity") && !action.get("block_entity").isJsonNull()) {
                throw Wire.invalid("M1 cannot place block entity data at " + local.toShortString());
            }
            BlockState state = readState(action, palette).rotate(rotation);
            ResourceLocation itemId = ResourceLocation.tryParse(Wire.string(action.get("item_id"), "action.item_id"));
            Item item = state.getBlock().asItem();
            if (!(item instanceof BlockItem) || !ForgeRegistries.ITEMS.getKey(item).equals(itemId)) {
                throw Wire.invalid("Incorrect placement item at " + local.toShortString());
            }
            targets.add(new Target(local, world, state, item));
            worldPositions.add(world);
        }
        if (!declared.equals(bounds(localPositions))) {
            throw Wire.invalid("Declared bounds differ from the target coordinates");
        }
        targets.sort(Comparator.comparing(Target::world, BUILD_ORDER));
        return new BuildPlan(id, buildingId, version, List.copyOf(targets), bounds(worldPositions));
    }

    static UUID id(JsonObject data, String key) {
        String value = Wire.string(data.get(key), key);
        try {
            UUID id = UUID.fromString(value);
            if (id.toString().equals(value)) {
                return id;
            }
        } catch (IllegalArgumentException ignored) {
            // Report malformed keys in the same way.
        }
        throw Wire.invalid(key + " must be a lowercase UUID");
    }

    private static BuildSite.Box bounds(Iterable<BlockPos> positions) {
        BlockPos min = new BlockPos(Integer.MAX_VALUE, Integer.MAX_VALUE, Integer.MAX_VALUE);
        BlockPos max = new BlockPos(Integer.MIN_VALUE, Integer.MIN_VALUE, Integer.MIN_VALUE);
        for (BlockPos pos : positions) {
            min = new BlockPos(Math.min(min.getX(), pos.getX()), Math.min(min.getY(), pos.getY()), Math.min(min.getZ(), pos.getZ()));
            max = new BlockPos(Math.max(max.getX(), pos.getX()), Math.max(max.getY(), pos.getY()), Math.max(max.getZ(), pos.getZ()));
        }
        return new BuildSite.Box(min, max);
    }

    private static BlockState readState(JsonObject action, Set<Block> palette) {
        String id = Wire.string(action.get("id"), "action.id");
        ResourceLocation name = ResourceLocation.tryParse(id);
        Block block = name == null ? null : ForgeRegistries.BLOCKS.getValue(name);
        if (block == null || !palette.contains(block) || !name.toString().equals(id)) {
            throw Wire.invalid("Unsupported M1 block: " + id);
        }
        BlockState state = block.defaultBlockState();
        for (Map.Entry<String, JsonElement> entry : Wire.object(action.get("properties"), "action.properties").entrySet()) {
            Property<?> property = block.getStateDefinition().getProperty(entry.getKey());
            if (property == null) {
                throw Wire.invalid("Unknown state property for " + id + ": " + entry.getKey());
            }
            state = withValue(state, property, Wire.string(entry.getValue(), entry.getKey()));
        }
        if (state.hasProperty(BlockStateProperties.WATERLOGGED) && state.getValue(BlockStateProperties.WATERLOGGED)
                || state.hasProperty(BlockStateProperties.SLAB_TYPE) && state.getValue(BlockStateProperties.SLAB_TYPE) == SlabType.DOUBLE) {
            throw Wire.invalid("M1 requires dry stairs and single dry slabs");
        }
        return state;
    }

    private static <T extends Comparable<T>> BlockState withValue(BlockState state, Property<T> property, String value) {
        T parsed = property.getValue(value).orElseThrow(() -> Wire.invalid("Invalid " + property.getName() + ": " + value));
        return state.setValue(property, parsed);
    }
}
