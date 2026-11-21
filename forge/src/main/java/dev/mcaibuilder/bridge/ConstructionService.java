package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraftforge.registries.ForgeRegistries;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

final class ConstructionService {
    private static final int ACTION_INTERVAL_TICKS = 6;

    private enum State {
        RUNNING, PAUSED, CANCELLED, COMPLETED, FAILED;

        boolean active() {
            return this == RUNNING || this == PAUSED;
        }

        String wire() {
            return name().toLowerCase(Locale.ROOT);
        }
    }

    private final ServerLevel level;
    private final BuildSite site;
    private final Set<Block> palette;
    private final Set<Block> borderBlocks;
    private final ConstructionWorker worker;
    private final Set<UUID> submitted = new HashSet<>();
    private Job current;

    ConstructionService(ServerLevel level, BuildSite site, Set<Block> palette, ConstructionWorker worker) {
        this.level = level;
        this.site = site;
        this.palette = palette;
        this.worker = worker;
        borderBlocks = Set.of(Blocks.DIRT, Blocks.GRASS_BLOCK, Blocks.BEDROCK, Blocks.STONE,
                Blocks.COBBLESTONE, Blocks.STONE_BRICKS, Blocks.OAK_PLANKS, Blocks.SPRUCE_PLANKS, Blocks.GLASS);
    }

    JsonObject submit(JsonObject payload) {
        UUID id = BuildPlan.id(payload, "plan_id");
        if (current != null && current.plan.id().equals(id) && current.payload.equals(payload)) {
            return status(false);
        }
        if (submitted.contains(id)) {
            throw Wire.conflict("plan_already_used", "Export a new plan to get a new plan ID");
        }
        if (current != null && current.state.active()) {
            throw Wire.conflict("job_active", "The active job must finish or be cancelled");
        }
        BuildPlan plan = BuildPlan.read(payload, site, palette);
        site.requireLoaded(level);
        List<BuildPlan.Target> toPlace = new ArrayList<>();
        Set<BlockPos> targets = new HashSet<>();
        for (BuildPlan.Target target : plan.targets()) {
            BlockPos pos = target.world();
            targets.add(pos);
            BlockState actual = level.getBlockState(pos);
            if (actual == target.state()) {
                continue;
            }
            if (!actual.isAir()) {
                throw Wire.conflict("occupied_target", "Existing block at " + pos.toShortString());
            }
            requireSafeBoundary(pos);
            toPlace.add(target);
        }
        Map<BlockPos, BlockState> guard = new HashMap<>();
        BuildSite.Box zone = site.area().halo();
        for (BlockPos pos : BlockPos.betweenClosed(zone.min(), zone.max())) {
            if (!targets.contains(pos)) {
                guard.put(pos.immutable(), level.getBlockState(pos));
            }
        }
        worker.prepare(toPlace);
        current = new Job(plan, payload.deepCopy(), guard, level.getGameTime());
        submitted.add(id);
        ForgeBridge.LOGGER.info("m1_submit {} {} targets={} supplied={}",
                id, plan.buildingId(), plan.targets().size(), toPlace.size());
        return status(false);
    }

    JsonObject control(String operation, UUID id) {
        if (current == null || !current.plan.id().equals(id)) {
            throw new Wire.Problem(404, "job_not_found", "The job ID is not the current job");
        }
        switch (operation) {
            case "pause" -> {
                if (current.state != State.PAUSED) {
                    requireState(State.RUNNING);
                    current.state = State.PAUSED;
                    worker.halt();
                }
            }
            case "resume" -> {
                if (current.state != State.RUNNING) {
                    requireState(State.PAUSED);
                    current.state = State.RUNNING;
                    current.nextTick = level.getGameTime() + ACTION_INTERVAL_TICKS;
                }
            }
            case "cancel" -> {
                if (current.state != State.CANCELLED) {
                    if (!current.state.active()) {
                        throw Wire.conflict("job_finished", "Only a running or paused job can be cancelled");
                    }
                    finish(State.CANCELLED, "cancelled_by_request");
                }
            }
            default -> throw new IllegalArgumentException(operation);
        }
        ForgeBridge.LOGGER.info("m1_control {} {} tick={}", id, operation, level.getGameTime());
        return status(false);
    }

    private void requireState(State state) {
        if (current.state != state) {
            throw Wire.conflict("invalid_job_state", "Job state is " + current.state.wire());
        }
    }

    void tick() {
        if (current == null || current.state != State.RUNNING || level.getGameTime() < current.nextTick) {
            return;
        }
        try {
            site.requireLoaded(level);
            executeNext();
        } catch (Wire.Problem problem) {
            finish(State.FAILED, problem.code);
        } catch (RuntimeException error) {
            ForgeBridge.LOGGER.error("M1 worker failed for job {}", current.plan.id(), error);
            finish(State.FAILED, "worker_error");
        }
    }

    private void executeNext() {
        int index = current.records.size();
        BuildPlan.Target target = current.plan.targets().get(index);
        BlockState actual = level.getBlockState(target.world());
        ConstructionWorker.Result result;
        if (actual == target.state()) {
            result = new ConstructionWorker.Result("already_present", null, null);
            worker.halt();
        } else if (!actual.isAir()) {
            result = new ConstructionWorker.Result("failed", "occupied_target", null);
        } else {
            requireSafeBoundary(target.world());
            result = worker.act(target);
        }
        if (result == null) {
            return;
        }

        JsonObject record = action(target, result.status());
        record.addProperty("tick", level.getGameTime());
        record.add("before", Wire.state(actual));
        record.add("actual", Wire.state(level.getBlockState(target.world())));
        if (result.reason() != null) {
            record.addProperty("reason", result.reason());
        }
        if (result.evidence() != null) {
            record.add("interaction_evidence", result.evidence());
        }
        current.records.add(record);
        if (result.status().equals("placed")) {
            current.placed++;
        }
        ForgeBridge.LOGGER.info("m1_action {} {} {}", current.plan.id(), index, record);
        if (result.status().equals("failed")) {
            finish(State.FAILED, result.reason());
        } else if (current.records.size() == current.plan.targets().size()) {
            finish(State.COMPLETED, null);
        } else {
            current.nextTick = level.getGameTime() + ACTION_INTERVAL_TICKS;
        }
    }

    private void requireSafeBoundary(BlockPos target) {
        for (Direction direction : Direction.values()) {
            BlockPos neighbor = target.relative(direction);
            if (!site.area().contains(neighbor)) {
                BlockState border = level.getBlockState(neighbor);
                if (!border.isAir() && !borderBlocks.contains(border.getBlock())) {
                    throw Wire.conflict("unsafe_site_boundary", "Sensitive neighbor outside the site at " + neighbor.toShortString());
                }
            }
        }
    }

    private static JsonObject action(BuildPlan.Target target, String status) {
        JsonObject data = new JsonObject();
        data.add("local", Wire.position(target.local()));
        data.add("world", Wire.position(target.world()));
        data.add("expected", Wire.state(target.state()));
        data.addProperty("item_id", ForgeRegistries.ITEMS.getKey(target.item()).toString());
        data.addProperty("status", status);
        return data;
    }

    private void finish(State state, String reason) {
        worker.halt();
        current.state = state;
        current.reason = reason;
        try {
            site.requireLoaded(level);
            current.verification = verify(current);
            if (state == State.COMPLETED && !current.verification.get("passed").getAsBoolean()) {
                current.state = State.FAILED;
                current.reason = "world_verification_failed";
            }
        } catch (Wire.Problem problem) {
            if (state == State.COMPLETED) {
                current.state = State.FAILED;
            }
            current.verification = new JsonObject();
            current.verification.addProperty("passed", false);
            current.verification.addProperty("error", problem.code);
        }
        ForgeBridge.LOGGER.info("m1_finish {} state={} reason={} verification={}",
                current.plan.id(), current.state.wire(), current.reason, current.verification);
    }

    private JsonObject verify(Job job) {
        JsonArray mismatches = new JsonArray();
        for (BuildPlan.Target target : job.plan.targets()) {
            BlockState actual = level.getBlockState(target.world());
            if (actual != target.state()) {
                JsonObject difference = action(target, "mismatch");
                difference.add("actual", Wire.state(actual));
                mismatches.add(difference);
            }
        }
        JsonArray changes = new JsonArray();
        int outside = 0;
        int outsideChecked = 0;
        int changed = 0;
        for (Map.Entry<BlockPos, BlockState> entry : job.guard.entrySet()) {
            BlockPos pos = entry.getKey();
            boolean beyond = !site.area().contains(pos);
            if (beyond) {
                outsideChecked++;
            }
            BlockState actual = level.getBlockState(pos);
            if (actual != entry.getValue()) {
                if (beyond) {
                    outside++;
                }
                changed++;
                if (changes.size() < 32) {
                    JsonObject difference = new JsonObject();
                    difference.add("world", Wire.position(pos));
                    difference.add("before", Wire.state(entry.getValue()));
                    difference.add("actual", Wire.state(actual));
                    changes.add(difference);
                }
            }
        }
        JsonObject data = new JsonObject();
        data.addProperty("passed", mismatches.isEmpty() && changed == 0);
        data.addProperty("matched", job.plan.targets().size() - mismatches.size());
        data.add("target_mismatches", mismatches);
        data.add("monitored_bounds", site.area().halo().json());
        data.addProperty("non_target_cells_checked", job.guard.size());
        data.addProperty("outside_approved_cells_checked", outsideChecked);
        data.addProperty("non_target_changes", changed);
        data.addProperty("outside_approved_changes", outside);
        data.add("first_non_target_changes", changes);
        return data;
    }

    JsonObject status(boolean detailed) {
        JsonObject response = new JsonObject();
        response.addProperty("protocol", 1);
        response.addProperty("world", site.world());
        response.addProperty("dimension", level.dimension().location().toString());
        response.addProperty("game_tick", level.getGameTime());
        response.add("approved_area", site.area().json());
        response.add("npc", worker.status());
        if (current != null) {
            JsonObject job = new JsonObject();
            job.addProperty("job_id", current.plan.id().toString());
            job.addProperty("building_id", current.plan.buildingId());
            job.addProperty("building_version", current.plan.version());
            job.addProperty("state", current.state.wire());
            job.addProperty("total", current.plan.targets().size());
            job.addProperty("processed", current.records.size());
            job.addProperty("placed", current.placed);
            job.add("world_bounds", current.plan.worldBounds().json());
            if (current.reason != null) {
                job.addProperty("reason", current.reason);
            }
            if (detailed) {
                JsonArray actions = current.records.deepCopy();
                for (int i = actions.size(); i < current.plan.targets().size(); i++) {
                    String status = current.state.active() ? "pending" : "skipped";
                    actions.add(action(current.plan.targets().get(i), status));
                }
                job.add("actions", actions);
                if (current.verification != null) {
                    job.add("verification", current.verification.deepCopy());
                }
            }
            response.add("job", job);
        }
        return response;
    }

    void close() {
        if (current != null && current.state.active()) {
            finish(State.CANCELLED, "server_stopping");
        }
        worker.close();
    }

    private static final class Job {
        final BuildPlan plan;
        final JsonObject payload;
        final Map<BlockPos, BlockState> guard;
        final JsonArray records = new JsonArray();
        State state = State.RUNNING;
        long nextTick;
        int placed;
        String reason;
        JsonObject verification;

        Job(BuildPlan plan, JsonObject payload, Map<BlockPos, BlockState> guard, long now) {
            this.plan = plan;
            this.payload = payload;
            this.guard = guard;
            this.nextTick = now + ACTION_INTERVAL_TICKS;
        }
    }
}
