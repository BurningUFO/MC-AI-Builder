"""Dispatch the model's bounded decisions through the Architect Bridge."""

from contextlib import suppress
import json
from time import monotonic, sleep

from src.architect.observations import observation_image
from src.architect.providers import (SKILL_CALLS, InvalidOutput, ModelBudgetExhausted, ModelError,
                                     ModelRateLimited, ModelTimeout, parse_intent)
from src.architect.scene import scene_context_message, world_context
from src.construction.client import BridgeError


MAX_TURNS = 5


def alignment(metadata):
    frame = metadata.get("planning_frame", {})
    point, rotation = frame.get("origin_from_scene"), frame.get("clockwise_degrees")
    if (type(point) is not list or len(point) != 3
            or any(type(value) is not int or abs(value) > 128 for value in point)
            or type(rotation) is not int or rotation not in (0, 90, 180, 270)):
        raise ValueError("planning_frame_unregistered")
    return {"building_origin_relative_to_scene": point, "building_clockwise_degrees": rotation}


def capture(runtime, send, offer):
    requested = send("capture")["observation_id"]
    cache = runtime.observation_cache
    deadline = monotonic() + 3.8
    while monotonic() < deadline:
        metadata = runtime.client.observation()
        if metadata.get("observation_id") == requested and metadata.get("capture_status") != "pending":
            job = metadata.get("world", {}).get("job", {})
            if (job.get("job_id") != offer["job_id"] or job.get("building_version") != offer["building_version"]
                    or job.get("state") != offer["task"]["state"]):
                raise ValueError(metadata.get("world_error", metadata.get("capture_error", "visual_checkpoint_changed")))
            world = world_context(metadata, moving=True)
            if world["unknown_unloaded"]:
                raise ValueError("site_not_loaded")
            image = observation_image(cache, metadata)
            scene = json.loads(scene_context_message(world, max_bytes=4800))
            scene["scene_to_building"] = alignment(metadata)
            runtime.trace.emit("planner_observation", observation_id=requested,
                               sample_span_ticks=metadata["world"]["sample_span_ticks"],
                               paired_tick_delta=metadata["world"]["frame_tick_delta"],
                               capture_ms=metadata.get("capture_ms"), fps=metadata.get("fps"),
                               processed=job["processed"], pose="route_first_person")
            return requested, image, scene
        sleep(.12)
    raise TimeoutError("camera_timeout")


def error_code(error):
    if isinstance(error, BridgeError):
        return error.body.get("error", "bridge_unavailable")
    if isinstance(error, OSError):
        return "bridge_unavailable"
    return {ModelTimeout: "model_timeout", ModelBudgetExhausted: "model_budget_exhausted",
            ModelRateLimited: "model_rate_limited", InvalidOutput: "invalid_model_output"}.get(type(error), "model_unavailable")


def decide(runtime, context, *, request=None, offer=None):
    """Feed tool receipts to another model turn, including the receipt for an accepted strategy."""
    context = dict(context, tool_results=[])
    request_binding = {"request_id": request["request_id"]} if request else {}
    attempted = set()
    applied = False
    observed = False
    image = observation_id = None
    ending = ""

    def send(operation, **data):
        binding = {key: offer[key] for key in ("job_id", "building_version", "lease")} if offer else {}
        return runtime.client.planning(operation, **request_binding, **binding, **data)

    def expose_work():
        context["work"] = {key: offer[key] for key in ("event", "groups", "strategies", "user_hint") if key in offer}
        if "user_hint" in context["work"]:
            context["work"]["user_hint"] = runtime.clean(context["work"]["user_hint"])[:160]
        context["work"]["can_observe"] = (
            bool(offer.get("can_observe")) and runtime.share_observations
            and callable(getattr(runtime.provider, "decide_with_view", None))
        )
        if offer.get("task"):
            context["task"] = runtime.task_context(offer, offer["task"])
        send("thinking")

    try:
        if offer:
            expose_work()
        for remaining in range(MAX_TURNS - 1, -1, -1):
            context["work"] = {**context.get("work", {}), "remaining_calls": remaining}
            if image is not None:
                decision = runtime.provider.decide_with_view(context, image, observation_id=observation_id,
                                                            allow_upload=True)
                image = None
            else:
                decision = runtime.provider.decide(context)
            intent = parse_intent(decision)
            action = intent["action"]
            if action not in SKILL_CALLS:
                ending = runtime.clean(intent.get("message", ""))[:240]
                if request is None and action not in {"chat", "clarify", "get_material_status"}:
                    raise InvalidOutput("A background turn can use work skills and reply")
                return intent
            if not remaining:
                raise InvalidOutput("The Builder reached its skill-call limit")
            result = {"result": "blocked", "code": "work_unavailable"}
            try:
                if action == "inspect_task":
                    if intent["topic"] == "work":
                        if offer is None:
                            offer = runtime.client.planning("claim", **request_binding)
                            if offer:
                                expose_work()
                        if offer:
                            result = {"result": "completed", "work": context["work"]}
                    else:
                        result = send("inspect", topic=intent["topic"])
                elif action == "observe_task":
                    if offer and not observed and context["work"].get("can_observe") and not applied:
                        observed = True
                        observation_id, image, scene = capture(runtime, send, offer)
                        send("thinking", observation_id=observation_id)
                        result = {"result": "completed", "scene": scene}
                elif offer:
                    signature = (intent["strategy"], intent.get("group"))
                    if applied or signature in attempted:
                        result = {"result": "blocked", "code": "strategy_already_attempted"}
                    else:
                        attempted.add(signature)
                        arguments = {"group": intent["group"]} if "group" in intent else {}
                        result = send("apply", strategy=intent["strategy"], **arguments,
                                      reason=runtime.clean(intent.get("message", ""))[:140])
                        applied = result["result"] in {"running", "completed"}
                        if applied:
                            context["work"]["strategies"] = []
            except (BridgeError, OSError, ValueError, KeyError) as error:
                image = None
                code = error_code(error) if isinstance(error, (BridgeError, OSError)) else "observation_unavailable"
                if code == "stale_request":
                    raise
                result = {"result": "blocked", "code": code}
            if action == "choose_work" and offer:
                if result.get("code") != "strategy_already_attempted":
                    runtime.remember_work(offer, intent, result)
            if action == "observe_task" and result["result"] == "completed":
                runtime.remember_observation(offer, result["scene"])
            if "task" in result:
                context["task"] = runtime.task_context(offer or request, result.pop("task"))
            context["tool_results"].append({"call": intent, **result})
            runtime.trace.emit("brain_skill", action=action, result=result["result"], code=result.get("code"))
    except (ModelError, OSError, BridgeError) as error:
        if offer:
            ending = runtime.record_diagnostic(error_code(error)).recovery
        raise
    finally:
        if offer:
            with suppress(BridgeError, OSError):
                send("finish", reason=ending, notify=request is None)


def run_once(runtime):
    offer = runtime.client.planning("claim")
    if not offer:
        return
    with runtime.trace.bind(job_id=offer["job_id"], building_version=offer["building_version"],
                            planning_id=offer["lease"]):
        runtime.trace.emit("planning_claim", trigger=offer["event"])
        context = {"message": "根据这次施工事件决定下一步。", "event": "construction",
                   "task": runtime.task_context(offer, offer["task"])}
        try:
            decide(runtime, context, offer=offer)
        except (ModelError, OSError, BridgeError) as error:
            runtime.record_diagnostic(error_code(error))
