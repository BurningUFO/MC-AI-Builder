package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;

import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

final class SiteSnapshot {
    private static final BlockState AIR = Blocks.AIR.defaultBlockState();
    private final Map<BlockPos, BlockState> baseline = new HashMap<>();
    private final Map<BlockPos, BlockState> written = new HashMap<>();
    private final Map<BlockPos, BlockState> observed = new HashMap<>();
    private final Set<BlockPos> footprint = new HashSet<>();
    private final Set<BlockPos> playerEdits = new HashSet<>();

    static SiteSnapshot capture(ServerLevel level, BuildSite site, BuildPlan plan) {
        SiteSnapshot snapshot = new SiteSnapshot();
        BuildSite.Box zone = site.area().halo();
        for (BlockPos pos : BlockPos.betweenClosed(zone.min(), zone.max())) {
            BlockState state = level.getBlockState(pos);
            if (state != AIR) {
                snapshot.baseline.put(pos.immutable(), state);
            }
        }
        snapshot.track(plan);
        return snapshot;
    }

    void track(BuildPlan plan) {
        plan.targets().forEach(target -> footprint.add(target.world()));
    }

    void trackClearance(Iterable<WorkStep> queue) {
        for (WorkStep step : queue) {
            if (step.clearance()) {
                footprint.addAll(step.affected());
            }
        }
    }

    Set<BlockPos> footprint() {
        return Set.copyOf(footprint);
    }

    BlockState initial(BlockPos pos) {
        return baseline.getOrDefault(pos, AIR);
    }

    BlockState expected(BlockPos pos) {
        return written.getOrDefault(pos, observed.getOrDefault(pos, initial(pos)));
    }

    boolean owns(BlockPos pos) {
        return written.containsKey(pos) && !playerEdits.contains(pos);
    }

    boolean modifiedByPlayer(BlockPos pos) {
        return playerEdits.contains(pos);
    }

    boolean recordExternalEdit(BlockPos pos) {
        return playerEdits.add(pos.immutable());
    }

    void observeClearance(Map<BlockPos, BlockState> cells) {
        cells.forEach((pos, state) -> {
            footprint.add(pos);
            written.remove(pos);
            observed.put(pos, state);
        });
    }

    void written(WorkStep step) {
        step.affected().forEach(pos -> {
            observed.remove(pos);
            written.put(pos, step.after());
        });
    }

    JsonObject json() {
        JsonObject value = new JsonObject();
        value.add("baseline", states(baseline));
        value.add("written", states(written));
        if (!observed.isEmpty()) {
            value.add("observed", states(observed));
        }
        value.add("footprint", positions(footprint));
        value.add("player_edits", positions(playerEdits));
        return value;
    }

    static SiteSnapshot read(JsonObject value, BuildSite site) {
        SiteSnapshot snapshot = new SiteSnapshot();
        readStates(snapshot.baseline, value.get("baseline"), site.area().halo());
        readStates(snapshot.written, value.get("written"), site.area());
        if (value.has("observed")) {
            readStates(snapshot.observed, value.get("observed"), site.area());
        }
        readPositions(snapshot.footprint, value.get("footprint"), site.area());
        readPositions(snapshot.playerEdits, value.get("player_edits"), site.area());
        if (!snapshot.footprint.containsAll(snapshot.written.keySet())
                || !snapshot.footprint.containsAll(snapshot.observed.keySet())
                || !java.util.Collections.disjoint(snapshot.written.keySet(), snapshot.observed.keySet())) {
            throw Wire.invalid("Journal writes outside the recorded footprint");
        }
        return snapshot;
    }

    private static JsonArray states(Map<BlockPos, BlockState> source) {
        JsonArray rows = new JsonArray();
        for (Map.Entry<BlockPos, BlockState> entry : source.entrySet()) {
            JsonObject row = new JsonObject();
            row.add("pos", Wire.position(entry.getKey()));
            row.add("state", Wire.state(entry.getValue()));
            rows.add(row);
        }
        return rows;
    }

    private static JsonArray positions(Set<BlockPos> positions) {
        JsonArray rows = new JsonArray();
        positions.stream().sorted(BuildPlan.BUILD_ORDER).forEach(pos -> rows.add(Wire.position(pos)));
        return rows;
    }

    private static void readStates(Map<BlockPos, BlockState> target, JsonElement value, BuildSite.Box box) {
        for (JsonElement element : Wire.array(value, "snapshot states")) {
            JsonObject row = Wire.object(element, "snapshot state");
            BlockPos pos = Wire.position(row.get("pos"), "snapshot.pos");
            if (!box.contains(pos) || target.put(pos, Wire.readState(Wire.object(row.get("state"), "state"))) != null) {
                throw Wire.invalid("Invalid coordinate in the journal snapshot");
            }
        }
    }

    private static void readPositions(Set<BlockPos> target, JsonElement value, BuildSite.Box box) {
        for (JsonElement row : Wire.array(value, "snapshot positions")) {
            BlockPos pos = Wire.position(row, "snapshot position");
            if (!box.contains(pos) || !target.add(pos)) {
                throw Wire.invalid("Invalid coordinate in the journal footprint");
            }
        }
    }
}
