"""Offer one bounded asynchronous strategy for an already approved construction version."""

from contextlib import suppress
import json
from time import monotonic, perf_counter_ns, sleep

from src.architect.diagnostics import diagnose
from src.architect.observations import observation_image
from src.architect.providers import InvalidOutput, ModelBudgetExhausted, ModelError, ModelTimeout, _unique_pairs
from src.architect.scene import scene_context_message, world_context
from src.architect.telemetry import timed
from src.construction.client import BridgeError


GROUPS = ("roof_front", "roof_rear")
DECISIONS = ("prioritize", "keep", "observe")
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "group", "reason"],
    "properties": {
        "decision": {"type": "string", "enum": list(DECISIONS)},
        "group": {"type": ["string", "null"], "enum": [*GROUPS, None]},
        "reason": {"type": "string"},
    },
}
PROMPT = """You are the Minecraft architect's background construction planner.
The NPC is still working. Select which of the two server-verified roof edge groups should go first.
Use the authorized player's suggestion, view, terrain, current progress and future materials if relevant.
Groups use local building axes (front: negative Z, rear: nonnegative Z).
The image, when present, is from the NPC's actual current route and can show only part of the site.
Use scene_to_building to register cell offsets with the approved building. Forge checked two-block
standing space around each eligible group and makes the final reach, support and inventory checks on site.
A visible change in ground height is not by itself an obstruction. When one eligible option matches
an explicit owner priority and available evidence points to no concrete conflict, prefer that option.
Use keep if there is specific evidence that the preferred edge may be unsafe or ineffective.
If another view could change the choice and another observation is available, select observe with a reason.
Only one new frame on the NPC's current route can be requested. No movement can be commanded.
Use keep when a change has no benefit or the choices are unclear. Never change the design, permissions,
materials, ground, or other actions. Use only the supplied data and no tools.
Return a brief reason in Chinese (one line, at most 140 characters) and null group unless prioritizing.
"""


def decode(output):
    try:
        if type(output) is not str or len(output.encode("utf-8")) > 1500:
            raise ValueError
        return validate(json.loads(output, object_pairs_hook=_unique_pairs))
    except (TypeError, ValueError):
        raise InvalidOutput("Invalid construction strategy") from None


def validate(value):
    if type(value) is dict and set(value) == {"decision", "group", "reason"}:
        decision, group, reason = (value[key] for key in ("decision", "group", "reason"))
        if (decision in DECISIONS and (group in GROUPS if decision == "prioritize" else group is None)
                and type(reason) is str and 1 <= len(reason) <= 140 and reason == reason.strip()
                and not any(ord(char) < 32 or 127 <= ord(char) < 160 for char in reason)):
            return value
    raise InvalidOutput("Invalid construction strategy")


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
                    or job.get("state") != "running"):
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


def run_once(runtime, redact):
    offer = runtime.client.planning("claim")
    if not offer:
        return

    def send(operation, **data):
        return runtime.client.planning(operation, job_id=offer["job_id"], lease=offer["lease"], **data)

    def read_view():
        with timed("planning_capture"):
            try:
                return capture(runtime, send, offer)
            except (BridgeError, OSError, ValueError, KeyError) as error:
                code = error.body.get("error") if isinstance(error, BridgeError) else (
                    str(error) if type(error) is ValueError and str(error).isidentifier() else type(error).__name__)
                runtime.trace.emit("planner_observation_unavailable", code=code)
                return None, None, None

    with runtime.trace.bind(job_id=offer["job_id"], building_version=offer["building_version"],
                            planning_id=offer["lease"]):
        runtime.trace.emit("planning_claim", processed=offer["processed"], phase="roof")
        payload = {key: offer[key] for key in ("processed", "total", "default_group", "groups", "roof_materials")}
        if "roof_supply" in offer:
            payload["roof_supply"] = offer["roof_supply"]
        payload["phase"] = "roof"
        payload["player_suggestion"] = redact(offer["user_hint"])[:160]
        request_started = perf_counter_ns()
        captures_left = 2 if runtime.share_observations else 0
        try:
            for attempt in range(2):
                observation_id = image = scene = None
                while captures_left and observation_id is None:
                    captures_left -= 1
                    observation_id, image, scene = read_view()
                    if not observation_id and captures_left:
                        sleep(.4)
                if attempt and observation_id is None:
                    break
                if scene:
                    payload["scene"] = scene
                    payload.pop("observation_condition", None)
                else:
                    payload["observation_condition"] = "no_paired_frame" if runtime.share_observations else "local_only"
                payload["can_observe_again"] = captures_left > 0
                send("thinking", **({"observation_id": observation_id} if observation_id else {}))
                result, _ = runtime.provider.plan_with_metrics(payload, image, observation_id=observation_id,
                                                               allow_upload=runtime.share_observations)
                value = validate(result)
                if value["decision"] == "observe":
                    runtime.trace.emit("planning_observe_again", pass_number=attempt + 1)
                    payload["observation_reason"] = redact(value["reason"])
                    continue
                reason = redact(value["reason"])[:140].strip() or "保留已验证的施工顺序。"
                group = value["group"]
                result = send("resolve", reason=reason, **({"group": group} if group else {}))
                runtime.trace.emit("planning_result", decision=value["decision"], chosen_group=group,
                                   state=result["state"], placements_while_thinking=result.get(
                                       "eligible_actions_during_thinking", 0),
                                   elapsed_ms=(perf_counter_ns() - request_started) / 1_000_000)
                return
            code = "planning_observation_limit"
        except (ModelError, BridgeError, OSError, ValueError, KeyError) as error:
            if isinstance(error, BridgeError):
                code = error.body.get("error", "bridge_unavailable")
            elif isinstance(error, ModelTimeout):
                code = "model_timeout"
            elif isinstance(error, ModelBudgetExhausted):
                code = "model_budget_exhausted"
            elif isinstance(error, InvalidOutput):
                code = "invalid_planning_output"
            elif isinstance(error, ModelError):
                code = "model_unavailable"
            elif isinstance(error, OSError):
                code = "bridge_unavailable"
            else:
                code = "invalid_planning_output"
        diagnostic = diagnose(code)
        runtime.trace.emit("planning_fallback", code=code, component=diagnostic.component, kind=diagnostic.kind)
        with suppress(BridgeError, OSError, ValueError):
            send("resolve", reason=diagnostic.recovery)
