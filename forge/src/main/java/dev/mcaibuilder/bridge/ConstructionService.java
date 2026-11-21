package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.StairBlock;
import net.minecraft.world.level.block.state.BlockState;

import java.io.IOException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

import dev.mcaibuilder.bridge.ConstructionJob.State;

final class ConstructionService {
    private static final int ACTION_INTERVAL_TICKS = 6;
    private static final Set<String> RETRYABLE = Set.of("navigation_timeout",
            "no_reachable_placement_stance", "no_reachable_break_stance");
    private static final BlockState AIR = Blocks.AIR.defaultBlockState();

    private record Registration(BuildPlan plan, JsonObject payload, String disposition) {
        Registration withDisposition(String next) {
            return new Registration(plan, payload, next);
        }
    }

    private final ServerLevel level;
    private final BuildSite site;
    private final Set<Block> palette;
    private final Set<Block> borderBlocks;
    private final ConstructionWorker worker;
    private final JobJournal journal;
    private final Map<UUID, Registration> registered = new LinkedHashMap<>();
    private JsonObject history = new JsonObject();
    private ConstructionJob current;
    private Registration candidate;
    private boolean journalBlocked;

    ConstructionService(ServerLevel level, BuildSite site, Set<Block> palette, ConstructionWorker worker) throws IOException {
        this.level = level;
        this.site = site;
        this.palette = palette;
        this.worker = worker;
        borderBlocks = Set.of(Blocks.DIRT, Blocks.GRASS_BLOCK, Blocks.BEDROCK, Blocks.STONE,
                Blocks.COBBLESTONE, Blocks.STONE_BRICKS, Blocks.OAK_PLANKS, Blocks.SPRUCE_PLANKS, Blocks.GLASS, Blocks.LIGHT_BLUE_STAINED_GLASS);
        journal = new JobJournal(level, site);
        restore(journal.read());
    }

    JsonObject submit(JsonObject payload) {
        requireWritable();
        UUID id = BuildPlan.id(payload, "plan_id");
        if (current != null && current.plan.id().equals(id) && registered.get(id).payload().equals(payload)) {
            return status(false);
        }
        requireUnused(id);
        if (current != null && current.state.active()) {
            throw Wire.conflict("job_active", "Pause and propose a change, or finish the active job");
        }
        BuildPlan plan = BuildPlan.read(payload, site, palette);
        requireImmutableVersion(plan);
        int latest = registered.values().stream().filter(entry -> entry.disposition().equals("approved")
                        && plan.samePlacement(entry.plan())).mapToInt(entry -> entry.plan().version()).max().orElse(0);
        if (latest > 0) {
            throw Wire.conflict(plan.version() < latest ? "stale_version" : "change_required",
                    "Use version review for an existing placement");
        }

        site.requireLoaded(level);
        List<WorkStep> steps = new ArrayList<>();
        for (BuildPlan.Target target : plan.targets()) {
            BlockState actual = level.getBlockState(target.world());
            if (actual != target.state() && !actual.isAir()) {
                throw Wire.conflict("occupied_target", "Existing block at " + target.world().toShortString());
            }
            WorkStep step = new WorkStep(target, actual, false);
            if (actual != target.state()) {
                requireSafeMutation(step, false);
            }
            steps.add(step);
        }
        SiteSnapshot snapshot = SiteSnapshot.capture(level, site, plan);
        if (current != null) {
            worker.attach(current.npc);
        }
        prepare(steps);
        archiveCurrent(null);
        registered.put(id, new Registration(plan, payload.deepCopy(), "approved"));
        current = new ConstructionJob(plan, worker.id(), null, null, snapshot, steps, cargo());
        schedule();
        persist();
        ForgeBridge.LOGGER.info("m1_submit {} {} targets={}", id, plan.buildingId(), plan.targets().size());
        return status(false);
    }

    JsonObject propose(UUID baseId, JsonObject payload) {
        requireWritable();
        requireCurrent(baseId);
        if (candidate != null) {
            if (candidate.payload().equals(payload)) {
                return change();
            }
            throw Wire.conflict("change_pending", "Resolve the pending review first");
        }
        if (!current.state.active() && current.state != State.COMPLETED) {
            throw Wire.conflict("job_finished", "Only the current running, paused, or completed placement can be changed");
        }
        requireNoIntent();
        BuildPlan next = BuildPlan.read(payload, site, palette);
        requireUnused(next.id());
        if (!current.plan.samePlacement(next)) {
            throw Wire.invalid("A revision must keep the building ID, origin, and rotation");
        }
        if (next.version() <= current.plan.version()) {
            throw Wire.conflict("stale_version", "A candidate must advance the active building version");
        }
        requireImmutableVersion(next);
        site.requireLoaded(level);
        worker.halt();
        current.state = State.PAUSED;
        current.reason = "change_pending";
        candidate = new Registration(next, payload.deepCopy(), "proposed");
        registered.put(next.id(), candidate);
        persist();
        return change();
    }

    JsonObject change() {
        if (candidate == null) {
            JsonObject response = new JsonObject();
            response.addProperty("pending", false);
            return response;
        }
        JsonObject response = review().report();
        response.addProperty("pending", true);
        return response;
    }

    JsonObject approve(UUID id, String reviewedHash) {
        requireWritable();
        if (candidate == null && current != null && current.plan.id().equals(id) && current.approvedReview != null
                && current.approvedReview.get("review_sha256").getAsString().equals(reviewedHash)) {
            return status(false);
        }
        requireCandidate(id);
        requireNoIntent();
        ChangeReview check = review();
        if (!check.report().get("review_sha256").getAsString().equals(reviewedHash)) {
            throw Wire.conflict("review_changed", "Read the current change report and review it again");
        }
        if (check.conflicts() != 0) {
            throw Wire.conflict("change_conflicts", "The reviewed change contains conflicts");
        }
        if (!worker.attach(current.npc) || !current.cargo.equals(cargo())) {
            throw Wire.conflict("checkpoint_npc_unavailable", "The recorded worker or inventory could not be verified");
        }
        for (WorkStep step : check.steps()) {
            requireSafeMutation(step, true);
        }

        worker.halt();
        prepare(check.steps());
        BuildPlan next = candidate.plan();
        UUID predecessor = current.plan.id();
        SiteSnapshot snapshot = current.snapshot;
        snapshot.track(next);
        archiveCurrent(next.id());
        current = new ConstructionJob(next, worker.id(), predecessor, check.report(), snapshot, check.steps(), cargo());
        registered.put(id, candidate.withDisposition("approved"));
        candidate = null;
        schedule();
        if (current.steps.isEmpty()) {
            finish(State.COMPLETED, null);
        }
        persist();
        ForgeBridge.LOGGER.info("m2_approved {} supersedes {} review={}", id, predecessor, reviewedHash);
        return status(false);
    }

    JsonObject reject(UUID id) {
        requireWritable();
        requireCandidate(id);
        registered.put(id, candidate.withDisposition("rejected"));
        candidate = null;
        current.reason = "change_rejected";
        persist();
        return status(false);
    }

    private ChangeReview review() {
        site.requireLoaded(level);
        return ChangeReview.inspect(level, site, current.plan, candidate.plan(), current.snapshot);
    }

    private void requireCandidate(UUID id) {
        if (candidate == null || !candidate.plan().id().equals(id)) {
            throw new Wire.Problem(404, "change_not_found", "The ID is not the pending change");
        }
    }

    private void requireCurrent(UUID id) {
        if (current == null || !current.plan.id().equals(id)) {
            throw new Wire.Problem(404, "job_not_found", "The job ID is not the current job");
        }
    }

    private void requireUnused(UUID id) {
        if (registered.containsKey(id)) {
            throw Wire.conflict("plan_already_used", "This plan ID was already registered");
        }
    }

    private void requireImmutableVersion(BuildPlan plan) {
        for (Registration entry : registered.values()) {
            BuildPlan known = entry.plan();
            if (known.buildingId().equals(plan.buildingId()) && known.version() == plan.version()
                    && !known.modelHash().equals(plan.modelHash())) {
                throw Wire.conflict("immutable_version", "This building version already has a different model hash");
            }
        }
    }

    private void requireNoIntent() {
        if (current.intent >= 0) {
            throw Wire.conflict("interrupted_interaction", "The journal has an unconfirmed interaction; inspect the world checkpoint");
        }
    }

    private void prepare(List<WorkStep> steps) {
        List<BuildPlan.Target> supplied = steps.stream().filter(step -> !step.remove() && step.before() != step.after())
                .map(WorkStep::target).toList();
        worker.prepare(supplied, steps.stream().anyMatch(WorkStep::remove));
    }

    private void schedule() {
        current.nextTick = level.getGameTime() + ACTION_INTERVAL_TICKS;
    }

    JsonObject control(String operation, UUID id) {
        requireWritable();
        requireCurrent(id);
        switch (operation) {
            case "pause" -> {
                if (current.state != State.PAUSED) {
                    requireState(State.RUNNING);
                    pause("paused_by_request");
                    persist();
                }
            }
            case "resume" -> {
                if (current.state != State.RUNNING) {
                    requireState(State.PAUSED);
                    requireNoIntent();
                    if (candidate != null) {
                        throw Wire.conflict("change_pending", "Approve or reject the pending change first");
                    }
                    site.requireLoaded(level);
                    current.conflicts = checkpointConflicts();
                    if (!current.conflicts.isEmpty()) {
                        pause("world_conflict");
                        persist();
                        throw Wire.conflict("world_conflict", "The saved construction positions no longer match the world");
                    }
                    requireCheckpointWorker();
                    current.state = State.RUNNING;
                    current.reason = null;
                    schedule();
                    persist();
                }
            }
            case "cancel" -> {
                if (current.state != State.CANCELLED) {
                    if (!current.state.active()) {
                        throw Wire.conflict("job_finished", "Only a running or paused job can be cancelled");
                    }
                    if (candidate != null) {
                        registered.put(candidate.plan().id(), candidate.withDisposition("rejected"));
                        candidate = null;
                    }
                    finish(State.CANCELLED, "cancelled_by_request");
                    persist();
                }
            }
            default -> throw new IllegalArgumentException(operation);
        }
        ForgeBridge.LOGGER.info("construction_control {} {} tick={}", id, operation, level.getGameTime());
        return status(false);
    }

    private void requireCheckpointWorker() {
        if (!worker.attach(current.npc) || !current.cargo.equals(cargo())) {
            throw Wire.conflict("checkpoint_npc_unavailable", "The recorded worker or inventory could not be verified");
        }
    }

    private void requireState(State state) {
        if (current.state != state) {
            throw Wire.conflict("invalid_job_state", "Job state is " + current.state.wire());
        }
    }

    private void pause(String reason) {
        worker.halt();
        current.state = State.PAUSED;
        current.reason = reason;
    }

    void externalEdit(ServerLevel editedLevel, Entity entity, BlockPos pos) {
        if (current == null || editedLevel != level || entity == null || worker.isActor(entity)
                || !site.area().contains(pos) || !current.snapshot.recordExternalEdit(pos)) {
            return;
        }
        if (current.state == State.RUNNING && current.snapshot.footprint().contains(pos)) {
            pause("world_conflict");
        }
        try {
            persist();
        } catch (Wire.Problem ignored) {
            // The journal failure halts the worker. The player's interaction can still finish.
        }
    }

    void tick() {
        if (journalBlocked || current == null || current.state != State.RUNNING || level.getGameTime() < current.nextTick) {
            return;
        }
        try {
            site.requireLoaded(level);
            executeNext();
        } catch (Wire.Problem problem) {
            if (!journalBlocked) {
                pause(problem.code);
                persistAfterFailure();
            }
        } catch (RuntimeException error) {
            ForgeBridge.LOGGER.error("Construction worker failed for job {}", current.plan.id(), error);
            pause("worker_error");
            persistAfterFailure();
        }
    }

    private void persistAfterFailure() {
        try {
            persist();
        } catch (Wire.Problem ignored) {
            // Keep the last durable checkpoint.
        }
    }

    private void executeNext() {
        if (current.cursor == current.steps.size()) {
            finish(State.COMPLETED, null);
            persist();
            return;
        }
        WorkStep step = current.steps.get(current.cursor);
        BlockState actual = level.getBlockState(step.pos());
        if (actual != step.before() || actual != current.snapshot.expected(step.pos())
                || current.snapshot.modifiedByPlayer(step.pos()) || (step.remove() && !current.snapshot.owns(step.pos()))) {
            current.conflicts = checkpointConflicts();
            pause("world_conflict");
            persist();
            return;
        }

        ConstructionWorker.Result result;
        if (actual == step.after()) {
            result = new ConstructionWorker.Result("already_present", null, null);
            worker.halt();
        } else {
            requireSafeMutation(step, current.predecessor != null);
            result = worker.act(step, this::beginInteraction);
        }
        if (result == null) {
            return;
        }

        boolean retry = result.status().equals("failed") && current.predecessor != null && current.intent < 0
                && result.reason() != null && RETRYABLE.contains(result.reason()) && current.retries == 0;
        JsonObject record = step.json(retry ? "retrying" : result.status());
        record.addProperty("step", current.cursor);
        record.addProperty("tick", level.getGameTime());
        record.add("before", Wire.state(actual));
        record.add("actual", Wire.state(level.getBlockState(step.pos())));
        if (result.reason() != null) {
            record.addProperty("reason", result.reason());
        }
        if (result.evidence() != null) {
            record.add("interaction_evidence", result.evidence());
        }
        current.records.add(record);
        ForgeBridge.LOGGER.info("construction_action {} {} {}", current.plan.id(), current.cursor, record);
        if (retry) {
            current.retries++;
            schedule();
        } else if (result.status().equals("failed")) {
            if (actual == level.getBlockState(step.pos()) && current.cargo.equals(cargo())) {
                current.intent = -1;
            }
            finish(State.FAILED, result.reason());
        } else {
            if (result.status().equals("placed") || result.status().equals("removed")) {
                current.snapshot.written(step);
                current.cargo = cargo();
                if (step.remove()) {
                    current.removed++;
                } else {
                    current.placed++;
                }
            }
            current.cursor++;
            current.intent = -1;
            current.retries = 0;
            if (current.cursor == current.steps.size()) {
                finish(State.COMPLETED, null);
            } else {
                schedule();
            }
        }
        persist();
    }

    private void beginInteraction() {
        requireNoIntent();
        current.intent = current.cursor;
        persist();
    }

    private void requireSafeMutation(WorkStep step, boolean revision) {
        BlockPos target = step.pos();
        if (!site.canBuildAt(target)) {
            throw Wire.conflict("outside_approved_area", "Target outside the approved area");
        }
        for (Direction direction : Direction.values()) {
            BlockPos neighbor = target.relative(direction);
            if (site.area().contains(neighbor) && !revision) {
                continue;
            }
            BlockState state = level.getBlockState(neighbor);
            if (state.isAir() || borderBlocks.contains(state.getBlock())) {
                continue;
            }
            boolean stairsMayChange = direction.getAxis().isHorizontal() && state.getBlock() instanceof StairBlock
                    && (step.before().getBlock() instanceof StairBlock || step.after().getBlock() instanceof StairBlock);
            if (!site.area().contains(neighbor) || !palette.contains(state.getBlock())
                    || !state.getFluidState().isEmpty() || stairsMayChange) {
                throw Wire.conflict("unsafe_site_boundary", "Sensitive neighbor at " + neighbor.toShortString());
            }
        }
    }

    private JsonArray checkpointConflicts() {
        JsonArray conflicts = new JsonArray();
        current.snapshot.footprint().stream().sorted(BuildPlan.BUILD_ORDER).forEach(pos -> {
            BlockState actual = level.getBlockState(pos);
            BlockState expected = current.snapshot.expected(pos);
            boolean player = current.snapshot.modifiedByPlayer(pos);
            if (actual != expected || player) {
                JsonObject row = new JsonObject();
                row.add("world", Wire.position(pos));
                row.add("expected", Wire.state(expected));
                row.add("actual", Wire.state(actual));
                row.addProperty("reason", player ? "player_modified" : "checkpoint_state_mismatch");
                conflicts.add(row);
            }
        });
        return conflicts;
    }

    private void finish(State state, String reason) {
        worker.halt();
        current.state = state;
        current.reason = reason;
        try {
            site.requireLoaded(level);
            current.verification = verify();
            if (state == State.COMPLETED && !current.verification.get("passed").getAsBoolean()) {
                current.state = State.FAILED;
                current.reason = "world_verification_failed";
            }
        } catch (Wire.Problem problem) {
            if (state == State.COMPLETED) {
                current.state = State.FAILED;
                current.reason = problem.code;
            }
            current.verification = new JsonObject();
            current.verification.addProperty("passed", false);
            current.verification.addProperty("error", problem.code);
        }
        ForgeBridge.LOGGER.info("construction_finish {} state={} reason={} verification={}",
                current.plan.id(), current.state.wire(), current.reason, current.verification);
    }

    private JsonObject verify() {
        Set<BlockPos> footprint = current.snapshot.footprint();
        JsonArray targetStates = new JsonArray();
        JsonArray retiredStates = new JsonArray();
        JsonArray mismatches = new JsonArray();
        JsonArray retiredMismatches = new JsonArray();
        int matched = 0;
        for (BlockPos pos : footprint.stream().sorted(BuildPlan.BUILD_ORDER).toList()) {
            BuildPlan.Target target = current.plan.at(pos);
            BlockState actual = level.getBlockState(pos);
            if (target == null) {
                JsonObject readback = difference(pos, AIR, actual);
                retiredStates.add(readback);
                if (!actual.isAir()) {
                    retiredMismatches.add(readback);
                }
            } else {
                JsonObject readback = difference(pos, target.state(), actual);
                targetStates.add(readback);
                if (actual == target.state()) {
                    matched++;
                } else {
                    mismatches.add(readback);
                }
            }
        }

        JsonArray changes = new JsonArray();
        int outside = 0;
        int outsideChecked = 0;
        int checked = 0;
        int changed = 0;
        BuildSite.Box zone = site.area().halo();
        for (BlockPos pos : BlockPos.betweenClosed(zone.min(), zone.max())) {
            if (footprint.contains(pos)) {
                continue;
            }
            checked++;
            boolean beyond = !site.area().contains(pos);
            if (beyond) {
                outsideChecked++;
            }
            BlockState actual = level.getBlockState(pos);
            BlockState before = current.snapshot.initial(pos);
            if (actual != before) {
                if (beyond) {
                    outside++;
                }
                changed++;
                if (changes.size() < 32) {
                    changes.add(difference(pos, before, actual));
                }
            }
        }
        JsonObject data = new JsonObject();
        data.addProperty("passed", mismatches.isEmpty() && retiredMismatches.isEmpty() && changed == 0);
        data.addProperty("matched", matched);
        data.add("target_mismatches", mismatches);
        data.add("retired_mismatches", retiredMismatches);
        data.add("target_states", targetStates);
        data.add("retired_states", retiredStates);
        data.add("monitored_bounds", zone.json());
        data.addProperty("non_target_cells_checked", checked);
        data.addProperty("outside_approved_cells_checked", outsideChecked);
        data.addProperty("non_target_changes", changed);
        data.addProperty("outside_approved_changes", outside);
        data.add("first_non_target_changes", changes);
        return data;
    }

    private static JsonObject difference(BlockPos pos, BlockState expected, BlockState actual) {
        JsonObject difference = new JsonObject();
        difference.add("world", Wire.position(pos));
        difference.add("expected", Wire.state(expected));
        difference.add("actual", Wire.state(actual));
        return difference;
    }

    private JsonObject cargo() {
        JsonObject state = worker.status();
        JsonObject cargo = new JsonObject();
        cargo.add("materials", state.get("materials"));
        cargo.add("tools", state.get("tools"));
        return cargo;
    }

    private JsonObject recovery() {
        JsonObject details = new JsonObject();
        try {
            site.requireLoaded(level);
            boolean entity = worker.attach(current.npc);
            JsonArray conflicts = checkpointConflicts();
            details.addProperty("world_matches_checkpoint", conflicts.isEmpty());
            details.addProperty("npc_reused", entity);
            details.addProperty("inventory_matches_checkpoint", entity && current.cargo.equals(cargo()));
            details.add("conflicts", conflicts);
            details.addProperty("interaction_confirmed", current.intent < 0);
        } catch (Wire.Problem problem) {
            details.addProperty("waiting", problem.code);
        }
        return details;
    }

    JsonObject status(boolean detailed) {
        JsonObject response = new JsonObject();
        response.addProperty("protocol", 2);
        response.addProperty("world", site.world());
        response.addProperty("dimension", level.dimension().location().toString());
        response.addProperty("game_tick", level.getGameTime());
        response.add("approved_area", site.area().json());
        response.addProperty("journal_available", !journalBlocked);
        response.addProperty("change_pending", candidate != null);
        if (current != null) {
            if (current.reopened && current.state != State.RUNNING) {
                response.add("recovery", recovery());
            }
            response.add("job", current.status(detailed));
        }
        response.add("npc", worker.status());
        return response;
    }

    JsonObject versions() {
        JsonObject response = new JsonObject();
        JsonArray versions = new JsonArray();
        for (Registration entry : registered.values()) {
            JsonObject version = entry.plan().versionInfo();
            version.addProperty("disposition", entry.disposition());
            JsonElement archived = history.get(entry.plan().id().toString());
            if (archived != null) {
                version.add("archived_job", archived.deepCopy());
            }
            versions.add(version);
        }
        response.add("versions", versions);
        if (current != null) {
            response.addProperty("current_job_id", current.plan.id().toString());
        }
        return response;
    }

    private void archiveCurrent(UUID successor) {
        if (current != null) {
            JsonObject archived = current.status(true);
            if (successor != null) {
                archived.addProperty("superseded_by", successor.toString());
            }
            history.add(current.plan.id().toString(), archived);
        }
    }

    private void requireWritable() {
        if (journalBlocked) {
            throw new Wire.Problem(503, "journal_unavailable", "Construction is halted until the journal can be recovered");
        }
    }

    private void persist() {
        requireWritable();
        JsonObject state = new JsonObject();
        JsonArray plans = new JsonArray();
        for (Registration entry : registered.values()) {
            JsonObject row = new JsonObject();
            row.add("plan", entry.payload());
            row.addProperty("disposition", entry.disposition());
            plans.add(row);
        }
        state.add("plans", plans);
        state.add("history", history);
        if (current != null) {
            state.add("current", current.checkpoint());
        }
        if (candidate != null) {
            state.addProperty("candidate", candidate.plan().id().toString());
        }
        try {
            journal.write(state);
        } catch (IOException error) {
            journalBlocked = true;
            if (current != null && current.state.active()) {
                pause("journal_unavailable");
            } else {
                worker.halt();
            }
            ForgeBridge.LOGGER.error("Could not commit the construction journal", error);
            throw new Wire.Problem(503, "journal_unavailable", "The construction journal could not be committed");
        }
    }

    private void restore(JsonObject data) {
        if (data == null) {
            return;
        }
        for (JsonElement element : Wire.array(data.get("plans"), "plans")) {
            JsonObject entry = Wire.object(element, "registration");
            JsonObject payload = Wire.object(entry.get("plan"), "registered plan");
            BuildPlan plan = BuildPlan.read(payload, site, palette);
            String disposition = Wire.string(entry.get("disposition"), "disposition");
            if (!Set.of("approved", "rejected", "proposed").contains(disposition) || registered.containsKey(plan.id())) {
                throw Wire.invalid("Invalid journal plan registration");
            }
            requireImmutableVersion(plan);
            registered.put(plan.id(), new Registration(plan, payload, disposition));
        }
        history = Wire.object(data.get("history"), "journal history");
        if (data.has("current")) {
            JsonObject checkpoint = Wire.object(data.get("current"), "current job");
            Registration entry = registered.get(BuildPlan.id(checkpoint, "job_id"));
            if (entry == null || !entry.disposition().equals("approved")) {
                throw Wire.invalid("The current job is missing its approved version");
            }
            current = ConstructionJob.restore(checkpoint, entry.plan(), palette, site);
        }
        if (data.has("candidate")) {
            candidate = registered.get(BuildPlan.id(data, "candidate"));
            if (current == null || candidate == null || !candidate.disposition().equals("proposed")
                    || !current.plan.samePlacement(candidate.plan()) || current.plan.version() >= candidate.plan().version()
                    || current.state != State.PAUSED) {
                throw Wire.invalid("The journal candidate is not tied to the current paused version");
            }
        }
    }

    void close() {
        if (current != null && current.state == State.RUNNING) {
            pause("server_stopping");
            persistAfterFailure();
        }
        worker.close();
    }
}
