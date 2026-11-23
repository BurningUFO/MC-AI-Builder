package dev.mcaibuilder.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;

import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;
import java.util.function.Predicate;

/** A bounded lease on an approved job, with choices from a single placement band. */
final class ConstructionPlanning {
    private static final long LIFETIME = Duration.ofSeconds(120).toNanos();
    private static final int LOOKAHEAD = 96;
    private static final int PROGRESS_INTERVAL = 256;
    private static final int IN_FLIGHT_BUFFER = 12;
    private static final int MAX_EVENTS = 16;

    private record Offer(UUID id, UUID owner, List<WorkStep> queue, ConstructionJob.State state,
                         String failure, int cursor, int first, int end, Map<String, List<WorkStep>> groups,
                         Set<String> strategies, long issued) {}

    private final Set<String> handled = new LinkedHashSet<>();
    private final Set<String> recoveryTried = new LinkedHashSet<>();
    private int recoveryCursor = -1;
    private Offer active;
    private JsonObject record;
    private String hint = "";
    private long thinkingAt;
    private UUID capture;
    private boolean applied;

    JsonObject claim(ConstructionJob job, UUID owner, long tick, boolean manual, Set<String> recovery,
                     Predicate<WorkStep> ready) {
        if (active != null) {
            return new JsonObject();
        }
        boolean paused = job.state == ConstructionJob.State.PAUSED;
        if (!paused && !manual && job.cursor < 6) {
            return new JsonObject();
        }
        Set<String> tried = recoveryCursor == job.cursor ? recoveryTried : Set.of();
        String failedStep = job.cursor < job.steps.size() ? job.steps.get(job.cursor).target().local().toString() : "cleanup";
        String event = paused ? "failure:" + job.cursor + ":" + failedStep
                + ":" + job.reason + ":" + tried.size() : "progress:" + job.cursor / PROGRESS_INTERVAL;
        if (!manual && hint.isEmpty() && handled.contains(event)) {
            return new JsonObject();
        }
        int first = Math.min(job.steps.size(), job.cursor + (paused ? 0 : IN_FLIGHT_BUFFER));
        int end = first;
        Map<BlockPos, List<WorkStep>> cells = new LinkedHashMap<>();
        while (end < job.steps.size() && end - first < LOOKAHEAD) {
            WorkStep step = job.steps.get(end);
            if (!movable(step) || step.pos().getY() != job.steps.get(first).pos().getY()) {
                break;
            }
            if ((!paused || end != job.cursor) && ready.test(step)) {
                BlockPos local = step.target().local();
                BlockPos tile = new BlockPos(Math.floorDiv(local.getX(), 4), local.getY(), Math.floorDiv(local.getZ(), 4));
                cells.computeIfAbsent(tile, ignored -> new ArrayList<>()).add(step);
            }
            end++;
        }
        Map<String, List<WorkStep>> groups = new LinkedHashMap<>();
        if ((paused || cells.size() > 1) && !tried.contains("prioritize") && tried.size() < 3) {
            cells.values().stream().limit(8).forEach(steps -> groups.put("g" + (groups.size() + 1), List.copyOf(steps)));
        }
        if (!paused && !manual && groups.isEmpty()) {
            return new JsonObject();
        }
        Set<String> strategies = new LinkedHashSet<>(recovery);
        strategies.removeAll(tried);
        if (tried.size() >= 3) {
            strategies.clear();
        }
        if (!groups.isEmpty()) {
            strategies.add("prioritize");
        }
        handled.add(event);
        if (handled.size() > MAX_EVENTS) {
            handled.remove(handled.iterator().next());
        }
        active = new Offer(UUID.randomUUID(), owner, job.steps, job.state, job.reason, job.cursor,
                first, end, groups, Set.copyOf(strategies), System.nanoTime());
        applied = false;
        thinkingAt = 0;
        capture = null;
        record = new JsonObject();
        record.addProperty("lease", active.id().toString());
        record.addProperty("state", "observing");
        record.addProperty("event", event);
        record.addProperty("issued_tick", tick);
        JsonObject offer = status();
        offer.addProperty("user_hint", hint);
        JsonArray options = new JsonArray();
        groups.forEach((key, steps) -> {
            JsonObject option = new JsonObject();
            option.addProperty("group", key);
            option.addProperty("placements", steps.size());
            option.add("first_local", Wire.position(steps.get(0).target().local()));
            option.add("last_local", Wire.position(steps.get(steps.size() - 1).target().local()));
            option.add("materials", Warehouse.jsonTotals(Warehouse.needs(steps, 0)));
            options.add(option);
        });
        offer.add("groups", options);
        JsonArray choices = new JsonArray();
        strategies.forEach(choices::add);
        offer.add("strategies", choices);
        hint = "";
        return offer;
    }

    private static boolean movable(WorkStep step) {
        return !step.remove() && step.before().isAir() && !step.target().paired()
                && !step.target().upper() && !step.target().light() && !step.target().topSlab();
    }

    void require(ConstructionJob job, UUID owner, UUID lease, boolean fresh) {
        if (active == null || !active.id().equals(lease) || !active.owner().equals(owner)
                || System.nanoTime() - active.issued() >= LIFETIME || fresh && (applied || !valid(job))) {
            throw Wire.conflict("stale_planning", "当前任务、执行进度或现场已变化。");
        }
    }

    private boolean valid(ConstructionJob job) {
        return job.state == active.state() && Objects.equals(job.reason, active.failure()) && job.steps == active.queue()
                && (job.state == ConstructionJob.State.PAUSED ? job.cursor == active.cursor()
                        : active.groups().isEmpty() || job.cursor < active.end());
    }

    void expire(ConstructionJob job) {
        if (active != null && (System.nanoTime() - active.issued() >= LIFETIME || !applied && !valid(job))) {
            invalidate("stale_planning");
        }
    }

    void capture(UUID id) {
        capture = id;
    }

    boolean canCapture() {
        return capture == null;
    }

    boolean ownsCapture(UUID id) {
        return id != null && id.equals(capture);
    }

    void hint(String message) {
        hint = message;
        if (active != null && !applied) {
            invalidate("new_player_hint");
        }
    }

    void thinking(ConstructionJob job, long tick, UUID observation) {
        if (observation != null) {
            record.addProperty("observation_id", observation.toString());
        }
        if (thinkingAt == 0) {
            thinkingAt = System.nanoTime();
            record.addProperty("state", "thinking");
            record.addProperty("model_start_tick", tick);
            record.addProperty("model_start_ms", job.performance.offsetMs());
            record.addProperty("placed_at_start", job.placed);
        }
    }

    void requireStrategy(String strategy) {
        if (!active.strategies().contains(strategy)) {
            throw Wire.conflict("invalid_planning_strategy", "这个工作策略未获 Forge 提供。");
        }
    }

    List<WorkStep> prioritize(ConstructionJob job, String group, Predicate<WorkStep> ready) {
        List<WorkStep> selected = active.groups().get(group);
        if (selected == null) {
            throw Wire.conflict("invalid_planning_group", "请选择当前租约提供的工作组。");
        }
        int first = job.state == ConstructionJob.State.RUNNING ? Math.max(job.cursor + 1, active.first()) : active.first();
        Set<WorkStep> remaining = Collections.newSetFromMap(new IdentityHashMap<>());
        remaining.addAll(job.steps.subList(Math.min(first, active.end()), active.end()));
        if (!remaining.containsAll(selected)) {
            throw Wire.conflict("stale_planning", "选定工作组已经开始执行。");
        }
        if (!selected.stream().allMatch(ready)) {
            throw Wire.conflict("planning_world_changed", "所选工作组的支持、空间或保护条件已变化。");
        }
        Set<WorkStep> priority = Collections.newSetFromMap(new IdentityHashMap<>());
        priority.addAll(selected);
        List<WorkStep> order = new ArrayList<>(job.steps.subList(0, first));
        order.addAll(selected);
        for (WorkStep step : job.steps.subList(first, active.end())) {
            if (!priority.contains(step)) {
                order.add(step);
            }
        }
        order.addAll(job.steps.subList(active.end(), job.steps.size()));
        if (order.equals(job.steps)) {
            throw Wire.conflict("planning_order_unchanged", "这组已经排在当前可调整队列的最前面。");
        }
        record.addProperty("first_step", first);
        record.add("first_local", Wire.position(selected.get(0).target().local()));
        return List.copyOf(order);
    }

    void applied(ConstructionJob job, String strategy, String group, String reason, long tick) {
        if (active.state() == ConstructionJob.State.PAUSED) {
            if (recoveryCursor != job.cursor) {
                recoveryCursor = job.cursor;
                recoveryTried.clear();
            }
            recoveryTried.add(strategy);
        }
        applied = true;
        record.addProperty("state", "applied");
        record.addProperty("strategy", strategy);
        if (group != null) {
            record.addProperty("group", group);
        }
        record.addProperty("reason", reason);
        recordResult(job, tick);
    }

    private void recordResult(ConstructionJob job, long tick) {
        record.addProperty("resolved_tick", tick);
        record.addProperty("resolved_ms", job.performance.offsetMs());
        if (thinkingAt != 0) {
            record.addProperty("thinking_elapsed_ms", (System.nanoTime() - thinkingAt) / 1_000_000.0);
            record.addProperty("eligible_actions_during_thinking", job.placed - record.get("placed_at_start").getAsInt());
            record.addProperty("processed_at_result", job.cursor);
        }
    }

    void finish(ConstructionJob job, String reason, long tick) {
        if (!applied) {
            record.addProperty("state", "kept");
            record.addProperty("reason", reason);
            recordResult(job, tick);
        }
        active = null;
    }

    void invalidate(String reason) {
        if (active != null) {
            if (!applied) {
                record.addProperty("state", "discarded");
                record.addProperty("reason", reason);
            }
            active = null;
        }
    }

    boolean hasRecord() {
        return record != null;
    }

    JsonObject status() {
        return record == null ? new JsonObject() : record.deepCopy();
    }

    JsonObject checkpoint() {
        JsonObject saved = status();
        JsonArray events = new JsonArray();
        handled.forEach(events::add);
        saved.add("handled_events", events);
        saved.addProperty("recovery_cursor", recoveryCursor);
        JsonArray choices = new JsonArray();
        recoveryTried.forEach(choices::add);
        saved.add("recovery_tried", choices);
        return saved;
    }

    void restore(JsonObject saved) {
        record = saved.deepCopy();
        if (record.remove("handled_events") instanceof JsonArray events) {
            events.forEach(event -> handled.add(event.getAsString()));
        }
        if (record.remove("recovery_cursor") instanceof com.google.gson.JsonPrimitive cursor) {
            recoveryCursor = cursor.getAsInt();
        }
        if (record.remove("recovery_tried") instanceof JsonArray choices) {
            choices.forEach(choice -> recoveryTried.add(choice.getAsString()));
        }
        String state = record.get("state").getAsString();
        if (state.equals("observing") || state.equals("thinking")) {
            record.addProperty("state", "discarded");
            record.addProperty("reason", "restart_verification");
        }
    }
}
