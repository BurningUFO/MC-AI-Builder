package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.tags.FluidTags;
import net.minecraft.world.level.BlockGetter;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.chunk.ChunkStatus;
import net.minecraft.world.level.material.FluidState;
import net.minecraftforge.registries.ForgeRegistries;

import java.nio.ByteBuffer;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;

/** A bounded block-state window shared by the integrated server and the actual render frame. */
final class WorldObservation {
    private static final int RADIUS = 4;
    private static final int WIDTH = RADIUS * 2 + 1;
    private static final int BOTTOM = -2;
    private static final int TOP = 4;
    private static final int OUTSIDE = -1;
    private static final int UNLOADED = -2;
    private static final int MAX_EXACT_CELLS = 36;
    private static final List<BlockPos> PROBES = List.of(new BlockPos(0, -1, 0), new BlockPos(0, 1, -3),
            new BlockPos(0, 1, 3), new BlockPos(-3, 1, 0), new BlockPos(3, 1, 0));

    record Window(BlockPos origin, BuildSite.Box scope) {
        Sample read(Level level) {
            int[] states = new int[WIDTH * WIDTH * (TOP - BOTTOM + 1)];
            for (int z = -RADIUS; z <= RADIUS; z++) {
                for (int x = -RADIUS; x <= RADIUS; x++) {
                    BlockPos column = origin.offset(x, 0, z);
                    var chunk = level.getChunkSource().getChunk(column.getX() >> 4, column.getZ() >> 4,
                            ChunkStatus.FULL, false);
                    for (int y = BOTTOM; y <= TOP; y++) {
                        BlockPos absolute = column.offset(0, y, 0);
                        int state = OUTSIDE;
                        if (scope.contains(absolute) && level.isInWorldBounds(absolute)) {
                            state = chunk == null ? UNLOADED : Block.getId(chunk.getBlockState(absolute));
                        }
                        states[index(x, y, z)] = state;
                    }
                }
            }
            return new Sample(states, level.getHeight(), level.getMinBuildHeight());
        }
    }

    record Sample(int[] states, int height, int minBuildHeight) implements BlockGetter {
        private int id(BlockPos local) {
            int x = local.getX(), y = local.getY(), z = local.getZ();
            return Math.abs(x) > RADIUS || Math.abs(z) > RADIUS || y < BOTTOM || y > TOP
                    ? OUTSIDE : states[index(x, y, z)];
        }

        long unloadedCells() {
            return Arrays.stream(states).filter(state -> state == UNLOADED).count();
        }

        String fingerprint() {
            ByteBuffer bytes = ByteBuffer.allocate(states.length * Integer.BYTES);
            for (int state : states) {
                bytes.putInt(state);
            }
            try {
                return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes.array()));
            } catch (NoSuchAlgorithmException impossible) {
                throw new IllegalStateException(impossible);
            }
        }

        JsonObject describe(BuildSite.Box allowed, BlockPos origin) {
            JsonObject local = new JsonObject();
            local.add("window_min", Wire.position(new BlockPos(-RADIUS, BOTTOM, -RADIUS)));
            local.add("window_max", Wire.position(new BlockPos(RADIUS, TOP, RADIUS)));
            JsonObject bounds = new JsonObject();
            bounds.add("min", Wire.position(allowed.min().subtract(origin)));
            bounds.add("max", Wire.position(allowed.max().subtract(origin)));
            local.add("approved_bounds", bounds);
            int outside = 0;
            int unloaded = 0;
            for (int state : states) {
                outside += state == OUTSIDE ? 1 : 0;
                unloaded += state == UNLOADED ? 1 : 0;
            }
            local.addProperty("unknown_outside_scope", outside);
            local.addProperty("unknown_unloaded", unloaded);
            local.add("terrain", terrain(allowed, origin));
            local.add("cells", exactCells());
            return local;
        }

        private JsonObject terrain(BuildSite.Box allowed, BlockPos origin) {
            Map<BlockPos, BlockPos> footing = new HashMap<>();
            JsonArray grid = new JsonArray();
            for (int z = -RADIUS; z <= RADIUS; z++) {
                JsonArray row = new JsonArray();
                for (int x = -RADIUS; x <= RADIUS; x++) {
                    BlockPos column = new BlockPos(x, 0, z);
                    BlockPos ground = null;
                    for (int y : new int[]{0, -1, 1, 2}) {
                        BlockPos feet = column.above(y);
                        if (allowed.contains(origin.offset(feet)) && canStand(feet)) {
                            ground = feet;
                            break;
                        }
                    }
                    if (ground != null) {
                        footing.put(column, ground);
                        row.add(ground.getY());
                    } else if (!allowed.contains(origin.offset(column)) || id(column) < 0
                            || id(column.below()) < 0 || id(column.above()) < 0) {
                        row.add(JsonNull.INSTANCE);
                    } else {
                        row.add("blocked");
                    }
                }
                grid.add(row);
            }

            var queue = new ArrayDeque<BlockPos>();
            var connected = new LinkedHashSet<BlockPos>();
            if (BlockPos.ZERO.equals(footing.get(BlockPos.ZERO))) {
                queue.add(BlockPos.ZERO);
                connected.add(BlockPos.ZERO);
            }
            while (!queue.isEmpty()) {
                BlockPos position = queue.remove();
                for (Direction direction : Direction.Plane.HORIZONTAL) {
                    BlockPos column = new BlockPos(position.getX(), 0, position.getZ()).relative(direction);
                    BlockPos next = footing.get(column);
                    if (next != null && Math.abs(next.getY() - position.getY()) <= 1 && connected.add(next)) {
                        queue.add(next);
                    }
                }
            }
            JsonArray connectedCells = new JsonArray();
            connected.forEach(pos -> connectedCells.add(Wire.position(pos)));
            int min = connected.stream().mapToInt(BlockPos::getY).min().orElse(0);
            int max = connected.stream().mapToInt(BlockPos::getY).max().orElse(0);
            JsonObject result = new JsonObject();
            result.add("footing_y_rows_z_then_x", grid);
            result.add("connected_footing", connectedCells);
            result.addProperty("connected_relief", max - min);
            return result;
        }

        private boolean canStand(BlockPos feet) {
            BlockPos floor = feet.below();
            BlockPos head = feet.above();
            return id(floor) >= 0 && id(feet) >= 0 && id(head) >= 0
                    && !hazard(getBlockState(floor)) && getFluidState(floor).isEmpty()
                    && getBlockState(floor).isFaceSturdy(this, floor, Direction.UP)
                    && clear(feet) && clear(head);
        }

        private boolean clear(BlockPos pos) {
            BlockState state = getBlockState(pos);
            return !hazard(state) && state.getFluidState().isEmpty() && state.getCollisionShape(this, pos).isEmpty();
        }

        private JsonArray exactCells() {
            var selected = new LinkedHashSet<BlockPos>();
            PROBES.stream().filter(pos -> id(pos) >= 0).forEach(selected::add);
            var candidates = new ArrayList<BlockPos>();
            for (int z = -RADIUS; z <= RADIUS; z++) {
                for (int x = -RADIUS; x <= RADIUS; x++) {
                    for (int y = -1; y <= TOP; y++) {
                        BlockPos pos = new BlockPos(x, y, z);
                        if (id(pos) >= 0 && !getBlockState(pos).isAir()) {
                            candidates.add(pos);
                        }
                    }
                }
            }
            candidates.sort(Comparator.comparingInt((BlockPos pos) -> {
                BlockState state = getBlockState(pos);
                return hazard(state) || !state.getFluidState().isEmpty() ? 0 : pos.getY() >= 0 ? 1 : 2;
            }).thenComparingDouble(pos -> pos.distSqr(new BlockPos(0, 1, 0))));
            var counts = new HashMap<Integer, Integer>();
            for (BlockPos pos : candidates) {
                if (selected.size() == MAX_EXACT_CELLS) {
                    break;
                }
                int state = id(pos);
                if (counts.getOrDefault(state, 0) < 6 && selected.add(pos)) {
                    counts.merge(state, 1, Integer::sum);
                }
            }
            JsonArray cells = new JsonArray();
            for (BlockPos pos : selected) {
                BlockState state = getBlockState(pos);
                JsonObject cell = Wire.state(state);
                cell.add("offset", Wire.position(pos));
                cell.addProperty("solid", !state.getCollisionShape(this, pos).isEmpty());
                if (!state.getFluidState().isEmpty()) {
                    cell.addProperty("fluid", ForgeRegistries.FLUIDS.getKey(state.getFluidState().getType()).toString());
                }
                if (hazard(state)) {
                    cell.addProperty("hazard", true);
                }
                cells.add(cell);
            }
            return cells;
        }

        @Override
        public BlockEntity getBlockEntity(BlockPos pos) {
            return null;
        }

        @Override
        public BlockState getBlockState(BlockPos pos) {
            int id = id(pos);
            return id < 0 ? Blocks.BARRIER.defaultBlockState() : Block.stateById(id);
        }

        @Override
        public FluidState getFluidState(BlockPos pos) {
            return getBlockState(pos).getFluidState();
        }

        @Override
        public int getHeight() {
            return height;
        }

        @Override
        public int getMinBuildHeight() {
            return minBuildHeight;
        }
    }

    private static boolean hazard(BlockState state) {
        return state.getFluidState().is(FluidTags.LAVA) || state.is(Blocks.FIRE) || state.is(Blocks.SOUL_FIRE)
                || state.is(Blocks.MAGMA_BLOCK) || state.is(Blocks.CACTUS) || state.is(Blocks.POWDER_SNOW);
    }

    private static int index(int x, int y, int z) {
        return ((y - BOTTOM) * WIDTH + z + RADIUS) * WIDTH + x + RADIUS;
    }

    private WorldObservation() {}
}
