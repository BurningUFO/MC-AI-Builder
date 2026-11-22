package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;
import net.minecraft.core.BlockPos;

import java.time.Duration;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.function.Consumer;
import java.util.function.Predicate;
import java.util.function.Supplier;

/** One bounded advisory lease for the future supported edge of an approved cottage roof. */
final class ConstructionPlanning {
    private static final long LIFETIME = Duration.ofSeconds(55).toNanos();
    private static final int MIN_PROGRESS = 6;
    private static final String FRONT = "roof_front";
    private static final String REAR = "roof_rear";

    private record Offer(UUID id, UUID owner, int firstRoof, Map<String, List<WorkStep>> groups, long issued) {}

    private Offer active;
    private JsonObject record;
    private String hint = "";
    private long thinkingAt;
    private UUID capture;
    private int captures;

    JsonObject claim(ConstructionJob job, UUID owner, long tick, Predicate<BlockPos> safeFeet) {
        if (record != null || job.state != ConstructionJob.State.RUNNING || job.cursor < MIN_PROGRESS
                || !job.plan.optimizedCottage() || job.intent >= 0
                || (job.supply != null && job.supply.intent != null)) {
            return new JsonObject();
        }
        int roofY = job.plan.worldBounds().max().getY();
        int firstRoof = job.steps.size();
        Map<String, List<WorkStep>> groups = new LinkedHashMap<>();
        groups.put(FRONT, new ArrayList<>());
        groups.put(REAR, new ArrayList<>());
        for (int index = 0; index < job.steps.size(); index++) {
            WorkStep step = job.steps.get(index);
            if (step.pos().getY() == roofY && !step.remove()) {
                firstRoof = Math.min(firstRoof, index);
                if (step.before().isAir() && job.plan.at(step.pos().below()) != null) {
                    groups.get(step.target().local().getZ() < 0 ? FRONT : REAR).add(step);
                }
            }
        }
        if (roofY - job.plan.origin().getY() < 3 || firstRoof <= job.cursor + 12
                || groups.values().stream().anyMatch(List::isEmpty)
                || job.steps.subList(firstRoof, job.steps.size()).stream()
                    .anyMatch(step -> step.remove() || step.pos().getY() != roofY)) {
            return new JsonObject();
        }
        Map<String, Integer> standing = new LinkedHashMap<>();
        groups.forEach((name, steps) -> standing.put(name, standableCells(job, steps, safeFeet)));
        if (standing.values().stream().anyMatch(count -> count == 0)) {
            return new JsonObject();
        }
        active = new Offer(UUID.randomUUID(), owner, firstRoof, groups, System.nanoTime());
        record = new JsonObject();
        record.addProperty("lease", active.id.toString());
        record.addProperty("state", "observing");
        record.addProperty("phase", "roof");
        record.addProperty("issued_tick", tick);
        record.addProperty("first_step", firstRoof);
        JsonObject offer = status();
        offer.addProperty("job_id", job.plan.id().toString());
        offer.addProperty("building_version", job.plan.version());
        offer.addProperty("processed", job.cursor);
        offer.addProperty("total", job.steps.size());
        offer.addProperty("user_hint", hint);
        offer.addProperty("default_group", job.steps.get(firstRoof).target().local().getZ() < 0 ? FRONT : REAR);
        JsonArray options = new JsonArray();
        groups.forEach((key, steps) -> {
            JsonObject option = new JsonObject();
            option.addProperty("group", key);
            option.addProperty("placements", steps.size());
            option.addProperty("verified_nearby_ground_stances", standing.get(key));
            option.add("relative_min", Wire.position(extreme(steps, false)));
            option.add("relative_max", Wire.position(extreme(steps, true)));
            options.add(option);
        });
        offer.add("groups", options);
        Map<String, Integer> needs = new LinkedHashMap<>();
        job.steps.subList(firstRoof, job.steps.size()).forEach(step ->
                needs.merge(Warehouse.id(step.target().item()), 1, Integer::sum));
        JsonObject materials = new JsonObject();
        needs.forEach((id, count) -> materials.addProperty(id, count));
        offer.add("roof_materials", materials);
        return offer;
    }

    private static int standableCells(ConstructionJob job, List<WorkStep> steps, Predicate<BlockPos> safeFeet) {
        Set<BlockPos> possible = new HashSet<>();
        int floorY = job.plan.origin().getY();
        for (WorkStep step : steps) {
            for (BlockPos ground : BlockPos.betweenClosed(
                    new BlockPos(step.pos().getX() - 2, floorY - 1, step.pos().getZ() - 2),
                    new BlockPos(step.pos().getX() + 2, floorY + 1, step.pos().getZ() + 2))) {
                possible.add(ground.immutable());
            }
        }
        return (int) possible.stream().filter(safeFeet).count();
    }

    private static BlockPos extreme(List<WorkStep> steps, boolean high) {
        var locals = steps.stream().map(step -> step.target().local()).toList();
        var x = locals.stream().mapToInt(BlockPos::getX).summaryStatistics();
        var y = locals.stream().mapToInt(BlockPos::getY).summaryStatistics();
        var z = locals.stream().mapToInt(BlockPos::getZ).summaryStatistics();
        return high ? new BlockPos(x.getMax(), y.getMax(), z.getMax())
                : new BlockPos(x.getMin(), y.getMin(), z.getMin());
    }

    void require(ConstructionJob job, UUID owner, UUID lease) {
        if (active == null || !active.id.equals(lease) || !active.owner.equals(owner) || !valid(job)) {
            throw Wire.conflict("stale_planning", "施工阶段、授权或版本已经变化，策略已失效。");
        }
    }

    private boolean valid(ConstructionJob job) {
        return job.state == ConstructionJob.State.RUNNING && job.cursor < active.firstRoof
                && System.nanoTime() - active.issued < LIFETIME;
    }

    void expire(ConstructionJob job) {
        if (active != null && !valid(job)) {
            invalidate("stale_planning");
        }
    }

    UUID capture(Supplier<UUID> request) {
        if (captures >= 2) {
            throw Wire.conflict("planning_capture_limit", "这份施工版本的观察次数已用完。");
        }
        capture = request.get();
        captures++;
        return capture;
    }

    boolean ownsCapture(UUID id) {
        return id != null && id.equals(capture);
    }

    boolean hint(String message) {
        hint = message;
        boolean willRead = record == null;
        if (active != null) {
            invalidate("new_player_hint");
        }
        return willRead;
    }

    void thinking(ConstructionJob job, long tick, String observation) {
        if (observation != null) {
            if (!record.has("observation_id")) {
                record.addProperty("observation_id", observation);
                record.add("observation_ids", new JsonArray());
            }
            JsonArray frames = record.getAsJsonArray("observation_ids");
            JsonPrimitive identity = new JsonPrimitive(observation);
            if (!frames.contains(identity)) {
                frames.add(identity);
            }
        }
        if (thinkingAt != 0) {
            return;
        }
        thinkingAt = System.nanoTime();
        record.addProperty("state", "thinking");
        record.addProperty("model_start_tick", tick);
        record.addProperty("model_start_ms", job.performance.offsetMs());
        record.addProperty("placed_at_start", job.placed);
        record.addProperty("processed_at_start", job.cursor);
    }

    void resolve(ConstructionJob job, String group, String reason, long tick,
                 Consumer<WorkStep> recheck, Predicate<BlockPos> safeFeet) {
        if (thinkingAt == 0 && group != null) {
            throw Wire.conflict("planning_not_started", "此策略尚未开始，保留现有施工顺序。");
        }
        boolean reordered = false;
        if (group != null) {
            List<WorkStep> selected = active.groups.get(group);
            if (selected == null) {
                throw Wire.conflict("invalid_planning_group", "只能选择当前授权队列提供的工作组。");
            }
            if (standableCells(job, selected, safeFeet) == 0) {
                throw Wire.conflict("planning_group_no_stance", "候选工作组附近没有经过现场核对的站位。");
            }
            selected.forEach(recheck);
            Set<WorkStep> priority = Set.copyOf(selected);
            List<WorkStep> order = new ArrayList<>(job.steps.subList(0, active.firstRoof));
            order.addAll(selected);
            for (WorkStep step : job.steps.subList(active.firstRoof, job.steps.size())) {
                if (!priority.contains(step)) {
                    order.add(step);
                }
            }
            reordered = !order.equals(job.steps);
            if (reordered) {
                job.steps = List.copyOf(order);
            }
            record.addProperty("group", group);
            record.add("first_local", Wire.position(selected.get(0).target().local()));
        }
        record.addProperty("state", reordered ? "applied" : "kept");
        record.addProperty("reason", reason);
        record.addProperty("resolved_tick", tick);
        record.addProperty("resolved_ms", job.performance.offsetMs());
        if (thinkingAt != 0) {
            record.addProperty("thinking_elapsed_ms", (System.nanoTime() - thinkingAt) / 1_000_000.0);
            record.addProperty("eligible_actions_during_thinking", job.placed - record.get("placed_at_start").getAsInt());
            record.addProperty("processed_at_result", job.cursor);
        }
        active = null;
    }

    void invalidate(String reason) {
        if (active != null) {
            record.addProperty("state", "discarded");
            record.addProperty("reason", reason);
            active = null;
        }
    }

    boolean hasRecord() {
        return record != null;
    }

    JsonObject status() {
        return record == null ? new JsonObject() : record.deepCopy();
    }

    void restore(JsonObject saved) {
        record = saved.deepCopy();
        String state = record.get("state").getAsString();
        if (state.equals("observing") || state.equals("thinking")) {
            record.addProperty("state", "discarded");
            record.addProperty("reason", "restart_verification");
        }
    }
}
