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
    private final boolean passive;
    private JsonObject operation;
    private JsonObject status = NpcObservations.state("idle");
    private ConstructionWorker worker;
    private int steps;
    private long settleUntil;
    private boolean walking;
    private UUID frame;
    private boolean recaptured;
    private ConstructionService.CheckpointView checkpointView;

    SiteSurvey(ConstructionService construction, UUID request, UUID owner, boolean passive) {
        this.construction = construction;
        this.request = request;
        this.owner = owner;
        this.passive = passive;
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
        if (step != steps + 1 || step > (passive ? 2 : LIMIT) || pending()) {
            throw Wire.conflict("survey_step_invalid", "此观察步骤已用完、仍在运行或已过期。");
        }
        String action = Wire.string(body.get("action"), "action");
        if (passive && !STATIONARY.contains(action)) {
            throw Wire.conflict("visual_movement_forbidden", "施工复查的位置由 Forge 按实际目标和安全站位确定。");
        }
        steps = step;
        operation = body.deepCopy();
        frame = null;
        recaptured = false;
        status = NpcObservations.state("pending");
        status.addProperty("step", step);
        status.addProperty("action", action);
        try {
            if (!STATIONARY.contains(action) && !AIMED.contains(action)) {
                throw Wire.invalid("Unknown survey action");
            }
            var npc = passive ? construction.observationTarget() : null;
            if (passive) {
                status.add("from", Wire.position(npc.blockPosition()));
                settleUntil = player.serverLevel().getGameTime() + 2;
                if (action.equals("inspect_build_site")) {
                    var viewpoint = construction.checkpointView(checkpointView);
                    if (viewpoint == null && step > 1) {
                        throw Wire.conflict("visual_viewpoint_unavailable", "没有其他安全的新视角。");
                    }
                    if (viewpoint != null) {
                        checkpointView = viewpoint;
                        worker = construction.checkpointWorker();
                        if (npc.blockPosition().equals(viewpoint.feet())) {
                            worker.lookForSurvey(viewpoint.focus());
                        } else {
                            worker.moveForSurvey(viewpoint.feet());
                            walking = true;
                        }
                    }
                }
                return status.deepCopy();
            }
            worker = construction.beginSurvey();
            npc = construction.observationTarget();
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
                BlockPos focus = passive ? checkpointView.focus() : construction.surveyFocus();
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
                    String reason = result.has("capture_error") ? result.get("capture_error").getAsString()
                            : result.has("world_error") ? result.get("world_error").getAsString() : "survey_unpaired";
                    if (passive && !recaptured && reason.equals("tick_skew")) {
                        // A slow first render in a large pack can arrive outside the pairing window.
                        // Re-sample once at the same settled pose instead of using an expired frame.
                        recaptured = true;
                        status.addProperty("recaptured", true);
                        frame = null;
                        settleUntil = owner.serverLevel().getGameTime() + 8;
                        return;
                    }
                    fail(reason);
                    return;
                }
                status.addProperty("capture_status", outcome);
                status.addProperty("observation_id", frame.toString());
                status.add("to", Wire.position(construction.observationTarget().blockPosition()));
                if (!passive) {
                    status.add("pads", construction.surveyPads());
                    status.add("cottage_pads", construction.cottagePads());
                } else if (checkpointView != null) {
                    status.add("focus", Wire.position(checkpointView.focus()
                            .subtract(construction.observationTarget().blockPosition())));
                }
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
