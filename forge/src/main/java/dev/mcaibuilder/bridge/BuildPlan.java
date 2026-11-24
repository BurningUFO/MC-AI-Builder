package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.DoorBlock;
import net.minecraft.world.level.block.RotatedPillarBlock;
import net.minecraft.world.level.block.Rotation;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.BlockStateProperties;
import net.minecraft.world.level.block.state.properties.SlabType;
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
                 List<Target> targets, Map<BlockPos, Target> byPosition, BuildSite.Box worldBounds, boolean lighting,
                 boolean localized, String sourceProject, TerrainDesign terrain) {
    static final int MAX_ACTIONS = 4096;
    static final List<String> WOOD_FAMILIES = List.of("oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove", "cherry");
    static final boolean LEGACY_EXECUTION = "1".equals(System.getenv("MC_AI_BUILDER_LEGACY_EXECUTOR"));
    private static final String QUARK_STAIR = "quark:red_shingles_stairs";
    static final Comparator<BlockPos> BUILD_ORDER = Comparator.<BlockPos>comparingInt(BlockPos::getY)
            .thenComparingInt(BlockPos::getZ).thenComparingInt(BlockPos::getX);

    // The two directional roof stairs can click a solid tile on the same layer.
    static final Comparator<Target> PLACEMENT_ORDER = Comparator.<Target>comparingInt(target -> target.world().getY())
            .thenComparing(Target::topSlab)
            .thenComparing(target -> QUARK_STAIR.equals(ForgeRegistries.BLOCKS.getKey(target.state().getBlock()).toString()))
            .thenComparing(Target::world, BUILD_ORDER);

    record Target(BlockPos local, BlockPos world, BlockState state, Item item, boolean replica) {
        Target(BlockPos local, BlockPos world, BlockState state, Item item) {
            this(local, world, state, item, false);
        }

        boolean upper() {
            return replica && ProjectBlocks.upper(state);
        }

        boolean paired() {
            return replica && ProjectBlocks.doubleHigh(state) && !upper();
        }

        int units() {
            return replica ? ProjectBlocks.units(state) : 1;
        }

        Item potPlant() {
            return replica ? ProjectBlocks.pottedItem(state) : Items.AIR;
        }

        boolean matches(BlockState actual) {
            return replica ? ProjectBlocks.alignJoins(actual, state) == state : ConstructionService.matches(actual, state);
        }

        boolean light() {
            return state.is(Blocks.TORCH) || state.is(Blocks.WALL_TORCH) || state.is(Blocks.LANTERN)
                    || state.is(Blocks.SOUL_LANTERN);
        }

        boolean topSlab() {
            return state.hasProperty(BlockStateProperties.SLAB_TYPE)
                    && state.getValue(BlockStateProperties.SLAB_TYPE) == net.minecraft.world.level.block.state.properties.SlabType.TOP;
        }

        BlockPos attachment() {
            if (state.is(Blocks.WALL_TORCH)) {
                return world.relative(state.getValue(BlockStateProperties.HORIZONTAL_FACING).getOpposite());
            }
            return state.hasProperty(BlockStateProperties.HANGING) && state.getValue(BlockStateProperties.HANGING)
                    ? world.above() : world.below();
        }
    }

    Target at(BlockPos world) {
        return byPosition.get(world);
    }

    BlockPos worldPosition(BlockPos local) {
        return origin.offset(local.rotate(rotation));
    }

    BlockPos localPosition(BlockPos world) {
        return world.subtract(origin).rotate(inverseRotation());
    }

    Rotation inverseRotation() {
        return switch (rotation) {
            case NONE -> Rotation.NONE;
            case CLOCKWISE_90 -> Rotation.COUNTERCLOCKWISE_90;
            case CLOCKWISE_180 -> Rotation.CLOCKWISE_180;
            case COUNTERCLOCKWISE_90 -> Rotation.CLOCKWISE_90;
        };
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
        if (lighting) {
            value.addProperty("lighting", true);
        }
        if (localized) {
            value.addProperty("localized", true);
        }
        if (sourceProject != null) {
            value.addProperty("source_project", sourceProject);
        }
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
                .thenComparing(Target::topSlab)
                .thenComparingInt(target -> distance.getOrDefault(target.world(), Integer.MAX_VALUE))
                .thenComparing(LEGACY_EXECUTION ? PLACEMENT_ORDER : adjacentRows);
    }

    Comparator<WorkStep> revisionPlacementOrder() {
        if (sourceProject == null && !buildingId.startsWith("cottage_") && !voxel() && !lighting) {
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
                Blocks.OAK_SLAB, Blocks.STONE_BRICK_SLAB, Blocks.COBBLESTONE_STAIRS, Blocks.COBBLESTONE_SLAB,
                Blocks.STONE_STAIRS, Blocks.STONE_SLAB, Blocks.TORCH, Blocks.WALL_TORCH, Blocks.LANTERN));
        for (String wood : WOOD_FAMILIES) {
            for (String piece : List.of("log", "stairs", "slab", "door")) {
                blocks.add(ForgeRegistries.BLOCKS.getValue(ResourceLocation.tryParse("minecraft:" + wood + "_" + piece)));
            }
        }
        Set<String> namespaces = Set.of("chipped", "handcrafted", "quark", "supplementaries", "twigs",
                "decorative_blocks", "biomesoplenty");
        ForgeRegistries.BLOCKS.getEntries().stream()
                .filter(entry -> namespaces.contains(entry.getKey().location().getNamespace()))
                .filter(entry -> designerMaterial(entry.getKey().location().getPath()))
                .filter(entry -> !Clearance.managed(entry.getValue().defaultBlockState())
                        && ProjectBlocks.allowed(entry.getValue().defaultBlockState()))
                .map(Map.Entry::getValue).forEach(blocks::add);
        return Set.copyOf(blocks);
    }

    private static boolean designerMaterial(String path) {
        return path.matches("(?:stacked_(?:oak|spruce|birch|dark_oak)_planks"
                + "|(?:red|blue|light_blue)_shingles(?:_stairs|_slab)?"
                + "|(?:polished_(?:shale|limestone|tuff)|(?:shale|limestone|calcite|polished_tuff)_bricks)(?:_stairs|_slab)?"
                + "|(?:stripped_)?(?:jacaranda|magic|mahogany|redwood|fir)_(?:planks|stairs|slab|log|wood)"
                + "|(?:dark_oak|spruce|birch|oak)_(?:beam|chair|table|bookshelf)|daub(?:_cross_brace|_frame)?)");
    }

    static JsonObject moddedMaterials(Set<Block> palette) {
        JsonObject materials = new JsonObject();
        palette.stream().sorted(Comparator.comparing(block -> ForgeRegistries.BLOCKS.getKey(block).toString()))
                .filter(block -> !ForgeRegistries.BLOCKS.getKey(block).getNamespace().equals("minecraft")).forEach(block -> {
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
        String project = data.has("source_project") ? ProjectBlocks.name(Wire.string(data.get("source_project"), "source_project")) : null;
        boolean terrainDesign = data.has("terrain");
        if (terrainDesign && (project != null || !buildingId.startsWith("voxel_"))) {
            throw Wire.invalid("Terrain changes belong to an original voxel design");
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
            BlockState state = (project == null ? readState(action, palette) : ProjectBlocks.read(action)).rotate(rotation);
            // A horizontal timber can start on a pier, and a top slab can start on its floor.
            // Use the existing native BlockItem state tag and inventory accounting for those placements.
            boolean replica = project != null || terrainDesign && (state.getBlock() instanceof DoorBlock
                    || state.getBlock() instanceof RotatedPillarBlock
                    || state.hasProperty(BlockStateProperties.SLAB_TYPE) && state.getValue(BlockStateProperties.SLAB_TYPE) == SlabType.TOP);
            if (ProjectBlocks.doubleHigh(state) && !replica) {
                throw Wire.invalid("Paired blocks require a paired placement plan");
            }
            Item item = ProjectBlocks.primaryItem(state);
            ResourceLocation itemId = ForgeRegistries.ITEMS.getKey(item);
            JsonElement declaredItem = action.get("item_id");
            boolean resolveItem = project != null || !ForgeRegistries.BLOCKS.getKey(state.getBlock()).getNamespace().equals("minecraft");
            if (!ProjectBlocks.placementItem(state, item, replica)
                    || itemId == null || (!(resolveItem && (declaredItem == null || declaredItem.isJsonNull()))
                    && !itemId.toString().equals(Wire.string(declaredItem, "action.item_id")))) {
                throw Wire.invalid("Incorrect or unsupported placement item at " + local.toShortString());
            }
            Target target = new Target(local, world, state, item, replica);
            targets.add(target);
            byPosition.put(world, target);
            JsonObject block = new JsonObject();
            block.add("local", Wire.position(local));
            block.add("id", action.get("id"));
            block.add("properties", action.get("properties"));
            model.put(local, block);
        }
        if (project != null || terrainDesign) {
            for (Target target : targets) {
                if (target.upper() || target.paired()) {
                    Target other = byPosition.get(target.upper() ? target.world().below() : target.world().above());
                    if (other == null || other.state() != ProjectBlocks.otherHalf(target.state())) {
                        throw Wire.conflict("project_invalid_halves", "工程中的门或双高植物需要上下两半 " + target.local().toShortString());
                    }
                }
            }
        }
        if (!declared.equals(bounds(model.keySet()))) {
            throw Wire.invalid("Declared bounds differ from the target coordinates");
        }
        Comparator<Target> order = project != null || buildingId.startsWith("cottage_") || buildingId.startsWith("voxel_")
                ? supportedLayers(targets, byPosition) : PLACEMENT_ORDER;
        targets.sort(Comparator.comparing(Target::light).thenComparing(order));
        JsonArray blocks = new JsonArray();
        model.values().forEach(blocks::add);
        String modelHash = Wire.sha256(blocks);
        if (data.has("model_sha256") && !modelHash.equals(Wire.string(data.get("model_sha256"), "model_sha256"))) {
            throw Wire.invalid("Model content hash does not match the supplied blocks");
        }
        return new BuildPlan(id, buildingId, version, origin, rotation, modelHash, List.copyOf(targets),

                Map.copyOf(byPosition), bounds(byPosition.keySet()),
                data.has("lighting") && data.get("lighting").getAsBoolean(),
                data.has("localized") && data.get("localized").getAsBoolean(), project,
                terrainDesign ? TerrainDesign.read(Wire.object(data.get("terrain"), "terrain"), site, origin, rotation, byPosition) : null);
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
        return state;
    }

}
