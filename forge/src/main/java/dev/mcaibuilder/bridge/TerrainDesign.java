package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.level.block.Rotation;
import net.minecraft.world.level.block.state.BlockState;

import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

/** Actual loaded columns around a signed mark, and the explicit changes in a terrain design. */
record TerrainDesign(BuildSite.Box footprint, Set<BlockPos> foundation, Set<BlockPos> excavation,
                     List<BlockPos> landings) {
    private static final int RADIUS = 18;
    private static final int BELOW = 12;
    private static final int ABOVE = 24;

    static TerrainDesign read(JsonObject data, BuildSite site, BlockPos origin, Rotation rotation,
                              Map<BlockPos, BuildPlan.Target> targets) {
        if (!site.worldCopy()) {
            throw Wire.conflict("survey_requires_copy", "地形设计需要获准的世界副本。");
        }
        BuildSite.Box local = BuildSite.Box.read(data.get("footprint"), "terrain footprint");
        if (local.min().getY() != local.max().getY() || local.volume() > 1024) {
            throw Wire.invalid("Expected one horizontal footprint of at most 1024 columns");
        }
        BlockPos a = origin.offset(local.min().rotate(rotation));
        BlockPos b = origin.offset(local.max().rotate(rotation));
        BuildSite.Box footprint = new BuildSite.Box(new BlockPos(Math.min(a.getX(), b.getX()), a.getY(), Math.min(a.getZ(), b.getZ())),
                new BlockPos(Math.max(a.getX(), b.getX()), b.getY(), Math.max(a.getZ(), b.getZ())));
        BuildSite.Box surveyed = surveyBox(site);
        if (!surveyed.contains(footprint.min()) || !surveyed.contains(footprint.max())
                || targets.keySet().stream().anyMatch(pos -> !surveyed.contains(pos))) {
            throw Wire.conflict("terrain_outside_survey", "占地、基础及屋顶都需要位于已勘察范围内。");
        }
        Set<BlockPos> foundation = Set.copyOf(positions(data, "foundation", BuildPlan.MAX_ACTIONS, origin, rotation));
        if (!targets.keySet().containsAll(foundation)) {
            throw Wire.invalid("Permanent foundation cells must be part of the blueprint");
        }
        Set<BlockPos> excavation = Set.copyOf(positions(data, "excavation", 4096, origin, rotation));
        if (excavation.stream().anyMatch(pos -> !surveyed.contains(pos) || !site.canBuildAt(pos))) {
            throw Wire.conflict("terrain_outside_survey", "开挖位置必须在已勘察且获准施工的范围内。");
        }
        List<BlockPos> landings = positions(data, "landings", 8, origin, rotation);
        if (landings.stream().anyMatch(pos -> !surveyed.contains(pos) || !site.canBuildAt(pos))) {
            throw Wire.conflict("terrain_outside_survey", "入口和楼层站位必须在已勘察范围内。");
        }
        return new TerrainDesign(footprint, foundation, excavation, landings);
    }

    private static List<BlockPos> positions(JsonObject data, String field, int limit, BlockPos origin, Rotation rotation) {
        JsonArray rows = Wire.array(data.get(field), "terrain." + field);
        if (rows.size() > limit) {
            throw Wire.invalid("Too many terrain " + field + " positions");
        }
        Set<BlockPos> unique = new HashSet<>();
        var result = new java.util.ArrayList<BlockPos>();
        for (var row : rows) {
            BlockPos pos = origin.offset(Wire.position(row, "terrain." + field).rotate(rotation));
            if (!unique.add(pos)) {
                throw Wire.invalid("Duplicate terrain " + field + " position");
            }
            result.add(pos);
        }
        return List.copyOf(result);
    }

    static BuildSite.Box surveyBox(BuildSite site) {
        BlockPos mark = site.origin();
        BlockPos low = site.area().min();
        BlockPos high = site.area().max();
        return new BuildSite.Box(new BlockPos(Math.max(low.getX(), mark.getX() - RADIUS),
                Math.max(low.getY(), mark.getY() - BELOW), Math.max(low.getZ(), mark.getZ() - RADIUS)),
                new BlockPos(Math.min(high.getX(), mark.getX() + RADIUS),
                        Math.min(high.getY(), mark.getY() + ABOVE), Math.min(high.getZ(), mark.getZ() + RADIUS)));
    }

    private static boolean ground(ServerLevel level, BlockPos pos, BlockState state) {
        return (BuildTerrain.NATURAL_SUPPORTS.contains(state.getBlock()) || state.is(BlockTags.DIRT)
                || state.is(BlockTags.BASE_STONE_OVERWORLD) || state.is(BlockTags.SAND)
                || state.is(net.minecraft.world.level.block.Blocks.GRAVEL))
                && state.getFluidState().isEmpty() && state.isFaceSturdy(level, pos, Direction.UP);
    }

    private static Integer surface(ServerLevel level, int x, int z, int low, int high) {
        if (!level.hasChunkAt(new BlockPos(x, low, z))) {
            return null;
        }
        for (int y = high; y >= low; y--) {
            BlockPos pos = new BlockPos(x, y, z);
            if (ground(level, pos, level.getBlockState(pos))) {
                return y;
            }
        }
        return null;
    }

    private static String feature(BuildSite site, ServerLevel level, BlockPos pos, BlockState state) {
        if (site.worksite().protectedAt(pos)) return "protected";
        if (state.isAir()) return "";
        if (!state.getFluidState().isEmpty() || WorldObservation.hazard(state)) return "hazard";
        if (Clearance.managed(state) || state.hasBlockEntity() || level.getBlockEntity(pos) != null) return "facility";
        return Clearance.kind(state);
    }

    static JsonObject survey(ServerLevel level, BuildSite site) {
        BuildSite.Box box = surveyBox(site);
        BlockPos mark = site.origin();
        var heights = new JsonArray();
        var floors = new JsonArray();
        var supports = new JsonArray();
        var standable = new JsonArray();
        var features = new JsonArray();
        Map<String, Integer> counts = new TreeMap<>();
        int min = Integer.MAX_VALUE;
        int max = Integer.MIN_VALUE;
        int unknown = 0;
        for (int z = box.min().getZ(); z <= box.max().getZ(); z++) {
            var heightsRow = new JsonArray();
            var floorsRow = new JsonArray();
            var supportRow = new JsonArray();
            var standRow = new StringBuilder();
            for (int x = box.min().getX(); x <= box.max().getX(); x++) {
                Integer y = surface(level, x, z, box.min().getY(), box.max().getY());
                if (y == null) {
                    unknown++;
                    heightsRow.add(JsonNull.INSTANCE);
                    floorsRow.add(JsonNull.INSTANCE);
                    supportRow.add(0);
                    standRow.append('?');
                    continue;
                }
                min = Math.min(min, y);
                max = Math.max(max, y);
                BlockPos base = new BlockPos(x, y, z);
                heightsRow.add(y - mark.getY());
                floorsRow.add(Wire.state(level.getBlockState(base)).get("id"));
                int depth = 1;
                while (depth < 5 && y - depth >= box.min().getY()
                        && ground(level, base.below(depth), level.getBlockState(base.below(depth)))) {
                    depth++;
                }
                supportRow.add(depth);
                standRow.append(BuildTerrain.canStand(level, base.above()) ? '+' : '-');
                for (int scan = Math.max(box.min().getY(), y - 3); scan <= box.max().getY(); scan++) {
                    BlockPos pos = new BlockPos(x, scan, z);
                    BlockState state = level.getBlockState(pos);
                    String kind = ground(level, pos, state) && !site.worksite().protectedAt(pos) ? "" : feature(site, level, pos, state);
                    if (kind.isEmpty()) continue;
                    int from = scan;
                    while (scan < box.max().getY() && level.getBlockState(pos.above(scan - from + 1)) == state
                            && feature(site, level, pos.above(scan - from + 1), state).equals(kind)) {
                        scan++;
                    }
                    counts.merge(kind, scan - from + 1, Integer::sum);
                    var row = new JsonArray();
                    row.add(x - mark.getX());
                    row.add(z - mark.getZ());
                    row.add(from - mark.getY());
                    row.add(scan - mark.getY());
                    row.add(kind);
                    row.add(Wire.state(state).get("id"));
                    features.add(row);
                }
            }
            heights.add(heightsRow);
            floors.add(floorsRow);
            supports.add(supportRow);
            standable.add(standRow.toString());
        }
        JsonObject data = new JsonObject();
        data.add("min", Wire.position(box.min().subtract(mark)));
        data.add("max", Wire.position(box.max().subtract(mark)));
        data.add("surface_y", heights);
        data.add("surface_block", floors);
        data.add("solid_depth_capped_at_5", supports);
        data.add("standable", standable);
        data.add("features", features);
        JsonObject totals = new JsonObject();
        counts.forEach(totals::addProperty);
        data.add("feature_counts", totals);
        data.add("npc_spawn", Wire.position(site.spawn().subtract(mark)));
        data.addProperty("unknown_columns", unknown);
        if (max >= min) {
            data.addProperty("min_surface_y", min - mark.getY());
            data.addProperty("max_surface_y", max - mark.getY());
            data.addProperty("relief", max - min);
        }
        data.addProperty("worksite_mode", site.worksite().mode());
        return data;
    }

    JsonObject facts(ServerLevel level, BuildSite site, BuildPlan plan, Set<BlockPos> cleared) {
        BuildSite.Box box = surveyBox(site);
        JsonArray heights = new JsonArray();
        int low = Integer.MAX_VALUE;
        int high = Integer.MIN_VALUE;
        int unknown = 0;
        for (int z = footprint.min().getZ(); z <= footprint.max().getZ(); z++) {
            JsonArray row = new JsonArray();
            for (int x = footprint.min().getX(); x <= footprint.max().getX(); x++) {
                Integer y = surface(level, x, z, box.min().getY(), box.max().getY());
                if (y == null) {
                    row.add(JsonNull.INSTANCE);
                    unknown++;
                } else {
                    row.add(y);
                    low = Math.min(low, y);
                    high = Math.max(high, y);
                }
            }
            heights.add(row);
        }
        JsonObject result = new JsonObject();
        result.add("footprint", footprint.json());
        result.add("original_surface_y", heights);
        result.addProperty("unknown_columns", unknown);
        if (high >= low) {
            result.addProperty("min_surface_y", low);
            result.addProperty("max_surface_y", high);
            result.addProperty("relief", high - low);
        }
        result.addProperty("foundation_blocks", foundation.size());
        result.addProperty("structure_blocks", plan.targets().size() - foundation.size());
        JsonArray contacts = new JsonArray();
        foundation.stream().sorted(BuildPlan.BUILD_ORDER).forEach(pos -> {
            BlockPos below = pos.below();
            BlockState state = level.getBlockState(below);
            if (plan.at(below) == null && !cleared.contains(below) && ground(level, below, state)) {
                JsonObject row = new JsonObject();
                row.add("foundation", Wire.position(plan.localPosition(pos)));
                row.add("ground", Wire.state(state));
                contacts.add(row);
            }
        });
        result.add("foundation_contacts", contacts);
        JsonArray access = new JsonArray();
        for (BlockPos feet : landings) {
            JsonObject row = new JsonObject();
            row.add("local", Wire.position(plan.localPosition(feet)));
            Integer y = surface(level, feet.getX(), feet.getZ(), box.min().getY(), box.max().getY());
            if (y != null) row.addProperty("feet_above_original_surface", feet.getY() - y - 1);
            row.addProperty("initially_standable", BuildTerrain.canStand(level, feet));
            row.addProperty("planned_standable", plannedStandable(level, plan, cleared, feet));
            row.add("planned_support", Wire.state(plannedState(level, plan, cleared, feet.below())));
            access.add(row);
        }
        result.add("landings", access);
        return result;
    }

    static boolean plannedStandable(ServerLevel level, BuildPlan plan, Set<BlockPos> cleared, BlockPos feet) {
        return BuildTerrain.canStand(level, feet, pos -> plannedState(level, plan, cleared, pos));
    }

    private static BlockState plannedState(ServerLevel level, BuildPlan plan, Set<BlockPos> cleared, BlockPos pos) {
        var target = plan.at(pos);
        if (target != null) return target.state();
        return cleared.contains(pos) ? net.minecraft.world.level.block.Blocks.AIR.defaultBlockState() : level.getBlockState(pos);
    }
}
