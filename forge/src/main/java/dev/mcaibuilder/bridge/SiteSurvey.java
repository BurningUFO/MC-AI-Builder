package dev.mcaibuilder.bridge;

import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerPlayer;

import java.util.Set;
import java.util.UUID;

/** Bounded observation-only work owned by one signed Architect request. */
final class SiteSurvey {
    static final int LIMIT = 3;
    private static final Set<String> STATIONARY = Set.of("observe_here", "inspect_build_site");
    private static final Set<String> AIMED = Set.of("look_at", "compare_viewpoints", "move_to_observation_point");

    private final ConstructionService construction;
    private final UUID request;
    private final UUID owner;
    private JsonObject operation;
    private JsonObject status = NpcObservations.state("idle");
    private ConstructionWorker worker;
    private int steps;
    private long settleUntil;
    private boolean walking;
    private UUID frame;

    SiteSurvey(ConstructionService construction, UUID request, UUID owner) {
        this.construction = construction;
        this.request = request;
        this.owner = owner;
    }

    boolean matches(UUID requestId, UUID ownerId) {
        return request.equals(requestId) && owner.equals(ownerId);
    }

    JsonObject handle(JsonObject body, ServerPlayer player) {
        if (!body.has("action")) {
            return status.deepCopy();
        }
        int step = Wire.integer(body.get("step"), "step");
        if (step == steps && body.equals(operation)) {
            return status.deepCopy();
        }
        if (step != steps + 1 || step > LIMIT || pending()) {
            throw Wire.conflict("survey_step_invalid", "此观察步骤已用完、仍在运行或已过期。");
        }
        String action = Wire.string(body.get("action"), "action");
        steps = step;
        operation = body.deepCopy();
        frame = null;
        status = NpcObservations.state("pending");
        status.addProperty("step", step);
        status.addProperty("action", action);
        try {
            if (!STATIONARY.contains(action) && !AIMED.contains(action)) {
                throw Wire.invalid("Unknown survey action");
            }
            worker = construction.beginSurvey();
            var npc = construction.observationTarget();
            status.add("from", Wire.position(npc.blockPosition()));
            if (step == 1 && STATIONARY.contains(action)) {
                BlockPos focus = construction.surveyFocus();
                if (npc.blockPosition().closerThan(focus, 12)) {
                    worker.lookForSurvey(focus);
                }
            }
            if (AIMED.contains(action)) {
                BlockPos offset = Wire.position(body.get("offset"), "offset");
                if (Math.abs((long) offset.getX()) > 12 || Math.abs((long) offset.getY()) > 6
                        || Math.abs((long) offset.getZ()) > 12) {
                    throw Wire.invalid("Survey offsets must be near the NPC");
                }
                BlockPos point = npc.blockPosition().offset(offset);
                if (action.equals("move_to_observation_point")) {
                    worker.moveForSurvey(point);
                    walking = true;
                } else {
                    worker.lookForSurvey(point);
                }
            }
            settleUntil = player.serverLevel().getGameTime() + 2;
        } catch (Wire.Problem problem) {
            fail(problem.code);
        }
        return status.deepCopy();
    }

    void tick(ServerPlayer owner, NpcObservations observations) {
        if (!pending()) {
            return;
        }
        try {
            if (observations == null) {
                fail("observation_unavailable");
                return;
            }
            if (walking) {
                if (!worker.surveyArrived()) {
                    return;
                }
                walking = false;
                settleUntil = owner.serverLevel().getGameTime() + 2;
                var npc = construction.observationTarget();
                status.add("to", Wire.position(npc.blockPosition()));
                BlockPos focus = construction.surveyFocus();
                if (npc.blockPosition().closerThan(focus, 12)) {
                    worker.lookForSurvey(focus);
                }
            }
            if (owner.serverLevel().getGameTime() < settleUntil) {
                return;
            }
            if (frame == null) {
                frame = observations.request(owner, construction);
                return;
            }
            JsonObject result = observations.latest();
            if (!result.has("observation_id") || !frame.equals(BuildPlan.id(result, "observation_id"))) {
                fail("survey_frame_replaced");
                return;
            }
            String outcome = result.get("capture_status").getAsString();
            if (!outcome.equals("pending")) {
                if (!outcome.equals("captured") || !result.has("world")) {
                    fail(result.has("capture_error") ? result.get("capture_error").getAsString() : "survey_unpaired");
                    return;
                }
                status.addProperty("capture_status", outcome);
                status.addProperty("observation_id", frame.toString());
                status.add("to", Wire.position(construction.observationTarget().blockPosition()));
                status.add("pads", construction.surveyPads());
            }
        } catch (Wire.Problem problem) {
            fail(problem.code);
        }
    }

    void requireFresh(NpcObservations observations, ArchitectService architect) {
        if (frame == null || !status.get("capture_status").getAsString().equals("captured")
                || observations == null
                || !frame.equals(BuildPlan.id(observations.observeWorld(construction, architect), "observation_id"))) {
            throw Wire.conflict("survey_frame_stale", "请重新观察，当前采样已失效。");
        }
    }

    private boolean pending() {
        return status.get("capture_status").getAsString().equals("pending");
    }

    private void fail(String reason) {
        stop();
        status.addProperty("capture_status", "failed");
        status.addProperty("capture_error", reason);
    }

    void stop() {
        if (walking && worker != null) {
            worker.halt();
        }
        walking = false;
    }
}
