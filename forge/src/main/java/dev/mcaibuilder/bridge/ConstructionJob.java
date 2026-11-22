package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.world.level.block.Block;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;

final class ConstructionJob {
    static final int MAX_CLEARANCE_REPLANS = 8;

    enum State {
        RUNNING, PAUSED, CANCELLED, COMPLETED, FAILED;

        boolean active() {
            return this == RUNNING || this == PAUSED;
        }

        String wire() {
            return name().toLowerCase(Locale.ROOT);
        }
    }

    final BuildPlan plan;
    final UUID npc;
    final UUID predecessor;
    final JsonObject approvedReview;
    final SiteSnapshot snapshot;
    List<WorkStep> steps;
    final ConstructionPlanning planning = new ConstructionPlanning();
    final JsonArray records = new JsonArray();
    final BuildPerformance performance = new BuildPerformance();
    State state = State.RUNNING;
    long nextTick;
    int cursor;
    int placed;
    int removed;
    int retries;
    int intent = -1;
    String reason;
    JsonObject verification;
    JsonArray conflicts = new JsonArray();
    JsonObject cargo;
    boolean reopened;
    boolean autonomous;
    long clearanceStarted;
    boolean groundVerified;
    final Set<BlockPos> clearancePositions = new HashSet<>();
    final int initialClearanceSteps;
    int clearanceReplans;
    SupplyCheckpoint supply;

    ConstructionJob(BuildPlan plan, UUID npc, UUID predecessor, JsonObject approvedReview,
                    SiteSnapshot snapshot, List<WorkStep> steps, JsonObject cargo) {
        this.plan = plan;
        this.npc = npc;
        this.predecessor = predecessor;
        this.approvedReview = approvedReview;
        this.snapshot = snapshot;
        this.steps = List.copyOf(steps);
        this.initialClearanceSteps = (int) steps.stream().takeWhile(WorkStep::clearance).count();
        steps.stream().filter(WorkStep::clearance).forEach(step -> clearancePositions.addAll(step.affected()));
        this.cargo = cargo;
    }

    boolean preparing() {
        return cursor < steps.size() && steps.get(cursor).clearance();
    }

    JsonObject preparationStatus() {
        JsonObject data = new JsonObject();
        data.addProperty("total", steps.stream().filter(WorkStep::clearance).mapToInt(step -> step.affected().size()).sum());
        int cleared = steps.subList(0, cursor).stream().filter(WorkStep::clearance)
                .mapToInt(step -> step.affected().size()).sum();
        data.addProperty("cleared", cleared);
        data.addProperty("replans", clearanceReplans);
        data.addProperty("phase", preparing() ? "clear" : state == State.COMPLETED ? "verify" : "build");
        if (preparing()) {
            WorkStep step = steps.get(cursor);
            data.add("next", Wire.position(step.pos()));
            data.addProperty("kind", Clearance.kind(step.before()));
            data.addProperty("tool", Warehouse.id(Warehouse.item(step)));
        }
        return data;
    }

    JsonObject status(boolean detailed) {
        JsonObject job = plan.versionInfo();
        job.remove("plan_id");
        job.addProperty("job_id", plan.id().toString());
        job.addProperty("state", state.wire());
        job.addProperty("total", steps.size());
        job.addProperty("target_count", plan.targets().size());
        job.addProperty("processed", cursor);
        job.addProperty("placed", placed);
        job.addProperty("removed", removed);
        job.addProperty("skipped", cursor - placed - removed);
        job.addProperty("reopened", reopened);
        job.addProperty("in_flight", intent >= 0);
        if (autonomous) {
            job.addProperty("autonomous", true);
            job.add("preparation", preparationStatus());
        }
        if (planning.hasRecord()) {
            job.add("planning", planning.status());
        }
        job.addProperty("checkpoint_npc", npc.toString());
        if (predecessor != null) {
            job.addProperty("previous_job_id", predecessor.toString());
        }
        if (reason != null) {
            job.addProperty("reason", reason);
        }
        if (supply != null) {
            job.add("supply", supply.status(detailed));
        }
        if (detailed) {
            if (BuildPerformance.ENABLED) {
                job.add("performance", performance.status());
            }
            JsonArray actions = records.deepCopy();
            for (int i = cursor; i < steps.size(); i++) {
                if (state == State.FAILED && i == cursor && !records.isEmpty()
                        && records.get(records.size() - 1).getAsJsonObject().get("status").getAsString().equals("failed")) {
                    continue;
                }
                actions.add(steps.get(i).json(state.active() ? "pending" : "skipped"));
            }
            job.add("actions", actions);
            job.add("conflicts", conflicts.deepCopy());
            if (verification != null) {
                job.add("verification", verification.deepCopy());
            }
            if (approvedReview != null) {
                job.add("approved_change", approvedReview.deepCopy());
            }
        }
        return job;
    }

    JsonObject checkpoint() {
        JsonObject data = status(false);
        data.add("snapshot", snapshot.json());
        data.add("cargo", cargo);
        data.add("records", records);
        data.add("conflicts", conflicts);
        data.addProperty("intent", intent);
        data.addProperty("retries", retries);
        if (clearanceReplans > 0) {
            data.addProperty("clearance_replans", clearanceReplans);
        }
        data.add("verification", verification);
        data.add("approved_change", approvedReview);
        if (supply != null) {
            data.add("supply", supply.checkpoint());
        }
        JsonArray queue = new JsonArray();
        steps.forEach(step -> queue.add(step.json("pending")));
        data.add("queue", queue);
        return data;
    }

    static ConstructionJob restore(JsonObject data, BuildPlan plan, Set<Block> palette, BuildSite site) {
        SiteSnapshot snapshot = SiteSnapshot.read(Wire.object(data.get("snapshot"), "snapshot"), site);
        List<WorkStep> queue = new ArrayList<>();
        for (JsonElement row : Wire.array(data.get("queue"), "queue")) {
            WorkStep step = WorkStep.read(Wire.object(row, "step"), plan, palette, site);
            if (!snapshot.footprint().containsAll(step.affected())) {
                throw Wire.invalid("Journal step is outside the recorded footprint");
            }
            queue.add(step);
        }
        UUID predecessor = data.has("previous_job_id") ? BuildPlan.id(data, "previous_job_id") : null;
        JsonObject approval = optionalObject(data, "approved_change");
        ConstructionJob job = new ConstructionJob(plan, BuildPlan.id(data, "checkpoint_npc"), predecessor,
                approval, snapshot, queue, Wire.object(data.get("cargo"), "checkpoint cargo"));
        job.cursor = Wire.integer(data.get("processed"), "processed");
        job.placed = Wire.integer(data.get("placed"), "placed");
        job.removed = Wire.integer(data.get("removed"), "removed");
        job.intent = Wire.integer(data.get("intent"), "intent");
        job.retries = Wire.integer(data.get("retries"), "retries");
        if (job.cursor < 0 || job.cursor > queue.size() || job.intent < -1
                || job.intent >= queue.size() || (job.intent != -1 && job.intent != job.cursor)) {
            throw Wire.invalid("Invalid construction cursor or intent");
        }
        job.records.addAll(Wire.array(data.get("records"), "records"));
        job.state = State.valueOf(Wire.string(data.get("state"), "state").toUpperCase(Locale.ROOT));
        job.reason = data.has("reason") ? Wire.string(data.get("reason"), "reason") : null;
        job.conflicts = Wire.array(data.get("conflicts"), "conflicts");
        job.verification = optionalObject(data, "verification");
        job.reopened = true;
        job.autonomous = data.has("autonomous") && data.get("autonomous").getAsBoolean();
        job.clearanceReplans = data.has("clearance_replans") ? Wire.integer(data.get("clearance_replans"), "clearance_replans") : 0;
        if (job.clearanceReplans < 0 || job.clearanceReplans > MAX_CLEARANCE_REPLANS
                || job.clearanceReplans > 0 && !job.autonomous
                || job.autonomous && (!site.worldCopy() || site.worksite().mode().equals("manual"))
                || !job.clearancePositions.isEmpty() && !job.autonomous
                || job.clearancePositions.size() > Clearance.MAX_CELLS) {
            throw Wire.invalid("Saved clearance is outside its worksite authorization or phase");
        }
        JsonObject planning = optionalObject(data, "planning");
        if (planning != null) {
            job.planning.restore(planning);
        }
        JsonObject supply = optionalObject(data, "supply");
        if (site.survival() != (supply != null)) {
            throw Wire.invalid("Cannot switch the supply mode of a saved construction job");
        }
        if (supply != null) {
            job.supply = SupplyCheckpoint.read(supply, site);
        }
        if (job.state == State.RUNNING) {
            job.state = State.PAUSED;
            job.reason = "restart_verification";
        }
        if (job.supply != null) {
            job.supply.phase = job.state.wire();
        }
        return job;
    }

    private static JsonObject optionalObject(JsonObject value, String key) {
        JsonElement entry = value.get(key);
        return entry == null || entry.isJsonNull() ? null : Wire.object(entry, key);
    }
}
