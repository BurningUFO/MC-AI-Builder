package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.function.Consumer;

record ChangeReview(JsonObject report, List<WorkStep> steps, int conflicts) {
    private static final BlockState AIR = Blocks.AIR.defaultBlockState();

    static ChangeReview inspect(ServerLevel level, BuildSite site, BuildPlan old, BuildPlan next,
                                SiteSnapshot snapshot, Consumer<WorkStep> checkSafety) {
        Set<BlockPos> positions = new TreeSet<>(BuildPlan.BUILD_ORDER);
        positions.addAll(snapshot.footprint());
        positions.addAll(next.byPosition().keySet());
        Map<String, Integer> counts = new LinkedHashMap<>();
        for (String key : List.of("keep", "add", "remove", "replace", "conflict")) {
            counts.put(key, 0);
        }
        JsonArray changes = new JsonArray();
        List<WorkStep> removals = new ArrayList<>();
        List<WorkStep> placements = new ArrayList<>();
        for (BlockPos pos : positions) {
            BuildPlan.Target wanted = next.at(pos);
            BlockState actual = level.getBlockState(pos);
            BlockState desired = goal(wanted);
            BlockState expected = snapshot.expected(pos);
            String reason = null;
            String category;
            if (snapshot.modifiedByPlayer(pos)) {
                reason = "player_modified";
            } else if (actual != expected) {
                reason = "checkpoint_state_mismatch";
            } else if (!actual.isAir() && actual != desired && !snapshot.owns(pos)) {
                reason = "preexisting_block";
            } else if (site.worldCopy() && actual != desired && !site.area().contains(pos.below())
                    && BuildTerrain.coversGrass(level, pos, desired, level.getBlockState(pos.below()))) {
                reason = "terrain_effect_outside_scope";
            }
            WorkStep removal = null;
            WorkStep placement = null;
            if (reason == null && actual != desired) {
                if (!actual.isAir()) {
                    var previous = new BuildPlan.Target(next.localPosition(pos), pos, actual, actual.getBlock().asItem());
                    removal = new WorkStep(previous, actual, true);
                }
                if (wanted != null) {
                    placement = new WorkStep(wanted, AIR, false);
                }
                try {
                    if (removal != null) {
                        checkSafety.accept(removal);
                    }
                    if (placement != null) {
                        checkSafety.accept(placement);
                    }
                } catch (Wire.Problem problem) {
                    reason = problem.code;
                }
            }
            if (reason != null) {
                category = "conflict";
            } else if (actual == desired || (wanted == null && actual.isAir())) {
                category = "keep";
            } else if (actual.isAir()) {
                category = "add";
                placements.add(placement);
            } else {
                category = wanted == null ? "remove" : "replace";
                removals.add(removal);
                if (placement != null) {
                    placements.add(placement);
                }
            }
            counts.merge(category, 1, Integer::sum);
            JsonObject row = new JsonObject();
            row.addProperty("category", category);
            row.add("local", Wire.position(next.localPosition(pos)));
            row.add("world", Wire.position(pos));
            row.add("old", Wire.state(goal(old.at(pos))));
            row.add("actual", Wire.state(actual));
            row.add("desired", Wire.state(desired));
            row.add("checkpoint", Wire.state(expected));
            row.addProperty("npc_owned", snapshot.owns(pos));
            if (reason != null) {
                row.addProperty("reason", reason);
            }
            changes.add(row);
        }

        removals.sort(Comparator.comparing(WorkStep::pos, BuildPlan.BUILD_ORDER.reversed()));
        placements.sort(next.revisionPlacementOrder());
        removals.addAll(placements);
        JsonObject totals = new JsonObject();
        counts.forEach(totals::addProperty);
        JsonObject report = new JsonObject();
        report.addProperty("change_id", next.id().toString());
        report.addProperty("base_job_id", old.id().toString());
        report.add("old_version", old.versionInfo());
        report.add("new_version", next.versionInfo());
        report.add("approved_area", site.area().json());
        if (site.worldCopy()) {
            report.addProperty("authorization", site.scope().toString());
            report.addProperty("dimension", level.dimension().location().toString());
            report.add("covered_grass", BuildTerrain.coveredGrass(level, site, next, snapshot::initial));
        }
        report.add("counts", totals);
        report.add("changes", changes);
        report.addProperty("review_sha256", Wire.sha256(report));
        return new ChangeReview(report, List.copyOf(removals), counts.get("conflict"));
    }

    private static BlockState goal(BuildPlan.Target target) {
        return target == null ? AIR : target.state();
    }
}
