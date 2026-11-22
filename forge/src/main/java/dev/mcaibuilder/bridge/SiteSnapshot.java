package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;

import java.util.HashSet;
import java.util.Set;

/** Records the positions a job touched so later revisions can retire its blocks. */
final class SiteSnapshot {
    private final Set<BlockPos> footprint = new HashSet<>();
    private final Set<BlockPos> built = new HashSet<>();

    void track(BuildPlan plan) {
        footprint.addAll(plan.byPosition().keySet());
    }

    void track(Iterable<WorkStep> steps) {
        for (WorkStep step : steps) {
            footprint.addAll(step.affected());
        }
    }

    Set<BlockPos> footprint() {
        return Set.copyOf(footprint);
    }

    boolean owns(BlockPos pos) {
        return built.contains(pos);
    }

    void written(WorkStep step) {
        if (step.remove()) {
            built.removeAll(step.affected());
        } else {
            built.add(step.pos());
        }
    }

    JsonObject json() {
        JsonObject data = new JsonObject();
        data.add("footprint", positions(footprint));
        data.add("built", positions(built));
        return data;
    }

    static SiteSnapshot read(JsonObject value, BuildSite site) {
        SiteSnapshot snapshot = new SiteSnapshot();
        Wire.array(value.get("footprint"), "footprint").forEach(row ->
                readPosition(snapshot.footprint, row, site));
        if (value.has("built")) {
            Wire.array(value.get("built"), "built").forEach(row -> readPosition(snapshot.built, row, site));
        } else if (value.has("written")) {
            Wire.array(value.get("written"), "written").forEach(element -> {
                JsonObject row = element.getAsJsonObject();
                if (!Wire.readState(row.getAsJsonObject("state")).isAir()) {
                    readPosition(snapshot.built, row.get("pos"), site);
                }
            });
        }
        return snapshot;
    }

    private static void readPosition(Set<BlockPos> positions, com.google.gson.JsonElement value, BuildSite site) {
        BlockPos pos = Wire.position(value, "work position");
        if (!site.area().contains(pos) || !positions.add(pos)) {
            throw Wire.invalid("Invalid coordinate in the construction journal");
        }
    }

    private static JsonArray positions(Set<BlockPos> positions) {
        JsonArray rows = new JsonArray();
        positions.stream().sorted(BuildPlan.BUILD_ORDER).forEach(pos -> rows.add(Wire.position(pos)));
        return rows;
    }
}
