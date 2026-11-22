package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.Item;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.Rotation;
import net.minecraft.world.level.block.StairBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.BlockStateProperties;
import net.minecraft.world.level.block.state.properties.SlabType;
import net.minecraft.world.level.block.state.properties.Half;
import net.minecraft.world.level.block.state.properties.StairsShape;
import net.minecraftforge.registries.ForgeRegistries;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.UUID;

record BuildPlan(UUID id, String buildingId, int version, BlockPos origin, Rotation rotation, String modelHash,
                 List<Target> targets, Map<BlockPos, Target> byPosition, BuildSite.Box worldBounds) {
    private static final int MAX_ACTIONS = 4096;
    static final boolean LEGACY_EXECUTION = "1".equals(System.getenv("MC_AI_BUILDER_LEGACY_EXECUTOR"));
    private static final String QUARK_STAIR = "quark:red_shingles_stairs";
    private static final Set<String> MODDED = Set.of(
            "chipped:stacked_spruce_planks", "quark:red_shingles", QUARK_STAIR);
    static final Comparator<BlockPos> BUILD_ORDER = Comparator.<BlockPos>comparingInt(BlockPos::getY)
            .thenComparingInt(BlockPos::getZ).thenComparingInt(BlockPos::getX);

    // The two directional roof stairs can click a solid tile on the same layer.
    static final Comparator<Target> PLACEMENT_ORDER = Comparator.<Target>comparingInt(target -> target.world().getY())
            .thenComparing(target -> QUARK_STAIR.equals(ForgeRegistries.BLOCKS.getKey(target.state().getBlock()).toString()))
            .thenComparing(Target::world, BUILD_ORDER);

    record Target(BlockPos local, BlockPos world, BlockState state, Item item) {}

    Target at(BlockPos world) {
        return byPosition.get(world);
    }

    BlockPos worldPosition(BlockPos local) {
        return origin.offset(local.rotate(rotation));
    }

    BlockPos localPosition(BlockPos world) {
        Rotation inverse = switch (rotation) {
            case NONE -> Rotation.NONE;
            case CLOCKWISE_90 -> Rotation.COUNTERCLOCKWISE_90;
            case CLOCKWISE_180 -> Rotation.CLOCKWISE_180;
            case COUNTERCLOCKWISE_90 -> Rotation.CLOCKWISE_90;
        };
        return world.subtract(origin).rotate(inverse);
    }

    boolean samePlacement(BuildPlan other) {
        return buildingId.equals(other.buildingId) && origin.equals(other.origin) && rotation == other.rotation;
    }

    JsonObject versionInfo() {
        JsonObject value = new JsonObject();
        value.addProperty("plan_id", id.toString());
        value.addProperty("building_id", buildingId);
        value.addProperty("building_version", version);
        value.addProperty("model_sha256", modelHash);
        value.add("origin", Wire.position(origin));
        value.addProperty("rotation", rotation.ordinal() * 90);
        value.add("world_bounds", worldBounds.json());
        return value;
    }

    boolean voxel() {
        return buildingId.startsWith("voxel_");
    }

    boolean optimizedCottage() {
        return buildingId.startsWith("cottage_") && !LEGACY_EXECUTION;
    }

    private static Comparator<Target> supportedLayers(List<Target> targets, Map<BlockPos, Target> cells) {
        var distance = new HashMap<BlockPos, Integer>();
        var frontier = new ArrayDeque<BlockPos>();
        for (Target target : targets) {
            if (target.local().getY() <= 0 || cells.containsKey(target.world().below())) {
                distance.put(target.world(), 0);
                frontier.add(target.world());
            }
        }
        while (!frontier.isEmpty()) {
            BlockPos support = frontier.removeFirst();
            for (Direction direction : Direction.Plane.HORIZONTAL) {
                BlockPos next = support.relative(direction);
                if (cells.containsKey(next) && distance.putIfAbsent(next, distance.get(support) + 1) == null) {
                    frontier.addLast(next);
                }
            }
        }
        Comparator<Target> adjacentRows = Comparator.<Target>comparingInt(target -> target.local().getZ())
                .thenComparingInt(target -> (target.local().getZ() & 1) == 0 ? target.local().getX() : -target.local().getX());
        return Comparator.<Target>comparingInt(target -> target.world().getY())
                .thenComparingInt(target -> distance.getOrDefault(target.world(), Integer.MAX_VALUE))
                .thenComparing(LEGACY_EXECUTION ? PLACEMENT_ORDER : adjacentRows);
    }

    Comparator<WorkStep> revisionPlacementOrder() {
        if (!buildingId.startsWith("cottage_") && !voxel()) {
            return Comparator.comparing(WorkStep::target, PLACEMENT_ORDER);
        }
        var order = new HashMap<BlockPos, Integer>();
        for (int i = 0; i < targets.size(); i++) {
            order.put(targets.get(i).world(), i);
        }
        return Comparator.comparingInt(step -> order.get(step.pos()));
    }

    static Set<Block> palette() {
        // Resolve vanilla blocks after the server has finished registry initialization.
        Set<Block> blocks = new HashSet<>(Set.of(Blocks.STONE, Blocks.COBBLESTONE, Blocks.STONE_BRICKS, Blocks.OAK_PLANKS,
                Blocks.SPRUCE_PLANKS, Blocks.DARK_OAK_PLANKS,
                Blocks.DIRT, Blocks.SANDSTONE, Blocks.SMOOTH_SANDSTONE, Blocks.DEEPSLATE_BRICKS, Blocks.BRICKS,
                Blocks.MOSSY_COBBLESTONE, Blocks.ANDESITE, Blocks.POLISHED_ANDESITE, Blocks.TERRACOTTA,
                Blocks.WHITE_TERRACOTTA, Blocks.RED_TERRACOTTA, Blocks.MUD_BRICKS, Blocks.BIRCH_PLANKS,
                Blocks.JUNGLE_PLANKS, Blocks.ACACIA_PLANKS, Blocks.MANGROVE_PLANKS, Blocks.CHERRY_PLANKS,
                Blocks.OAK_LOG, Blocks.SPRUCE_LOG, Blocks.DARK_OAK_LOG, Blocks.STRIPPED_OAK_LOG,
                Blocks.STRIPPED_SPRUCE_LOG, Blocks.STRIPPED_DARK_OAK_LOG, Blocks.SPRUCE_STAIRS, Blocks.DARK_OAK_STAIRS,
                Blocks.BRICK_STAIRS, Blocks.SPRUCE_SLAB, Blocks.DARK_OAK_SLAB, Blocks.BRICK_SLAB,
                Blocks.WHITE_STAINED_GLASS, Blocks.GREEN_STAINED_GLASS, Blocks.BROWN_STAINED_GLASS, Blocks.GLASS, Blocks.LIGHT_BLUE_STAINED_GLASS, Blocks.BLUE_STAINED_GLASS, Blocks.OAK_STAIRS, Blocks.STONE_BRICK_STAIRS,
                Blocks.OAK_SLAB, Blocks.STONE_BRICK_SLAB));
        for (String id : MODDED) {
            ResourceLocation key = ResourceLocation.tryParse(id);
            Block block = ForgeRegistries.BLOCKS.getValue(key);
            if (block != null && key.equals(ForgeRegistries.BLOCKS.getKey(block))
                    && !block.defaultBlockState().hasBlockEntity() && block.asItem() instanceof BlockItem item
                    && item.getBlock() == block
                    && (id.endsWith("_stairs") ? block instanceof StairBlock
                    : block.defaultBlockState().getProperties().isEmpty())) {
                blocks.add(block);
            }
        }
        return Set.copyOf(blocks);
    }

    static JsonObject moddedMaterials(Set<Block> palette) {
        JsonObject materials = new JsonObject();
        palette.stream().sorted(Comparator.comparing(block -> ForgeRegistries.BLOCKS.getKey(block).toString()))
                .filter(block -> MODDED.contains(ForgeRegistries.BLOCKS.getKey(block).toString())).forEach(block -> {
                    JsonObject entry = Wire.state(block.defaultBlockState());
                    entry.addProperty("item_id", Warehouse.id(block.asItem()));
                    materials.add(ForgeRegistries.BLOCKS.getKey(block).toString(), entry);
                });
        return materials;
    }

    static BuildPlan read(JsonObject data, BuildSite site, Set<Block> palette) {
        return read(data, site, palette, false);
    }

    static BuildPlan read(JsonObject data, BuildSite site, Set<Block> palette, boolean surveyed) {
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
        if (site.worldCopy() && !site.canBuildAt(origin)) {
            throw Wire.conflict("placement_scope", "拟建原点超出了施工区授权。");
        }
        BuildSite.Box declared = BuildSite.Box.read(data.get("local_bounds"), "local_bounds");
        JsonArray actions = Wire.array(data.get("actions"), "actions");
        if (actions.isEmpty() || actions.size() > MAX_ACTIONS) {
            throw Wire.invalid("Expected 1–" + MAX_ACTIONS + " actions");
        }

        List<Target> targets = new ArrayList<>(actions.size());
        Map<BlockPos, JsonObject> model = new TreeMap<>(BUILD_ORDER);
        Map<BlockPos, Target> byPosition = new LinkedHashMap<>();
        for (JsonElement element : actions) {
            JsonObject action = Wire.object(element, "action");
            BlockPos local = Wire.position(action.get("local"), "action.local");
            BlockPos world = origin.offset(local.rotate(rotation));
            if (!site.canBuildAt(world)) {
                throw Wire.invalid("Target is outside the approved buildable area: " + world.toShortString());
            }
            if (model.containsKey(local)) {
                throw Wire.invalid("Duplicate target: " + local.toShortString());
            }
            if (action.has("block_entity") && !action.get("block_entity").isJsonNull()) {
                throw Wire.invalid("M1 cannot place block entity data at " + local.toShortString());
            }
            BlockState state = readState(action, palette).rotate(rotation);
            Item item = state.getBlock().asItem();
            ResourceLocation itemId = ForgeRegistries.ITEMS.getKey(item);
            JsonElement declaredItem = action.get("item_id");
            boolean resolveModItem = MODDED.contains(action.get("id").getAsString())
                    && (declaredItem == null || declaredItem.isJsonNull());
            if (!(item instanceof BlockItem placing) || placing.getBlock() != state.getBlock() || itemId == null
                    || (!resolveModItem && !itemId.toString().equals(Wire.string(declaredItem, "action.item_id")))) {
                throw Wire.invalid("Incorrect or unsupported placement item at " + local.toShortString());
            }
            Target target = new Target(local, world, state, item);
            targets.add(target);
            byPosition.put(world, target);
            JsonObject block = new JsonObject();
            block.add("local", Wire.position(local));
            block.add("id", action.get("id"));
            block.add("properties", action.get("properties"));
            model.put(local, block);
        }
        if (!declared.equals(bounds(model.keySet()))) {
            throw Wire.invalid("Declared bounds differ from the target coordinates");
        }
        targets.sort((buildingId.startsWith("cottage_") || buildingId.startsWith("voxel_")) ? supportedLayers(targets, byPosition) : PLACEMENT_ORDER);
        JsonArray blocks = new JsonArray();
        model.values().forEach(blocks::add);
        String modelHash = Wire.sha256(blocks);
        if (data.has("model_sha256") && !modelHash.equals(Wire.string(data.get("model_sha256"), "model_sha256"))) {
            throw Wire.invalid("Model content hash does not match the supplied blocks");
        }
        return new BuildPlan(id, buildingId, version, origin, rotation, modelHash, List.copyOf(targets),
                Map.copyOf(byPosition), bounds(byPosition.keySet()));
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
        BlockState state = Wire.readState(action);
        if (!palette.contains(state.getBlock())) {
            throw Wire.invalid("Unsupported construction block: " + action.get("id").getAsString());
        }
        if (state.hasProperty(BlockStateProperties.WATERLOGGED) && state.getValue(BlockStateProperties.WATERLOGGED)
                || state.hasProperty(BlockStateProperties.SLAB_TYPE) && state.getValue(BlockStateProperties.SLAB_TYPE) == SlabType.DOUBLE) {
            throw Wire.invalid("Construction requires dry stairs and single dry slabs");
        }
        if (state.hasBlockEntity()) {
            throw Wire.invalid("Block entity placements are not supported");
        }
        if (MODDED.contains(action.get("id").getAsString())) {
            if (action.getAsJsonObject("properties").size() != state.getProperties().size()
                    || (state.getBlock() instanceof StairBlock
                    && (state.getValue(BlockStateProperties.HALF) != Half.BOTTOM
                    || state.getValue(BlockStateProperties.STAIRS_SHAPE) != StairsShape.STRAIGHT))) {
                throw Wire.invalid("Modded blocks require explicit states and straight, lower stairs");
            }
        }
        return state;
    }

}
