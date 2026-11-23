"""Interpret bounded Architect context through the official Codex SDK."""

import asyncio
from base64 import b64encode
from contextlib import aclosing
from hashlib import sha256
from importlib.util import find_spec
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from time import perf_counter
from uuid import UUID

from src.architect.codex_budget import CodexBudget
from src.architect.codex_sdk import (
    WORK_ROOT, auth_summary, config_request, require_safe_config, safe_failure,
    safety_options, sdk_config, summarize_config,
)
from src.architect.design import (COTTAGE_ROOFS, COTTAGE_SIZES, DESIGNS, ENTRIES, GLASS, INTERIORS,
                                  MASONRY, ROOFS, ROOF_FORMS, STYLES, WINDOW_LAYOUTS, WOODS)
from src.architect.observations import rgb_png
from src.architect.telemetry import model_turn
from src.architect.providers import (ACTIONS, PROMPT, TASK_TOPICS, WORK_STRATEGIES, InvalidOutput,
                                     ModelBudgetExhausted, ModelError, ModelTimeout, parse_intent)
from src.architect.providers import _bad_constant, _unique_pairs
from src.architect.grammar import LIGHTING, ROOF_STYLES
from src.architect.scene import SCENE_SCHEMA, VISION_PROMPT, decode_scene, scene_context_message
from src.architect import feedback, survey

DEFAULT_MODEL = "gpt-6.2-sol"
DEFAULT_EFFORT = "medium"
REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")
MAX_CONTEXT_BYTES = 20000
MAX_RESPONSE_BYTES = 32768
CONTEXT_KEYS = (
    "message", "dimension", "position", "facing", "suggested_origin", "approved_area",
    "ground_near_anchor", "fixed_site", "construction", "design", "supply", "available_mod_blocks",
    "phase", "event", "sites", "gaze", "workspace_projects", "task", "work", "tool_results",
)
NON_TOOL_ITEMS = {"userMessage", "agentMessage", "reasoning", "functionCallOutput", "contextCompaction"}

WALL_FIELDS = {
    **{key: {"type": ["integer", "null"]} for key in ("width", "height", "window_width", "window_height")},
    **{key: {"type": ["string", "null"], "enum": [*values, None]}
       for key, values in (("style", STYLES), ("wood", WOODS), ("masonry", MASONRY), ("glass", GLASS), ("roof", ROOFS))},
    "porch": {"type": ["boolean", "null"]},
}
DESIGN_FIELDS = {
    **WALL_FIELDS,
    "depth": {"type": ["integer", "null"], "enum": [*COTTAGE_SIZES, None]},
    **{key: {"type": ["string", "null"], "enum": [*values, None]}
       for key, values in (("entry", ENTRIES), ("windows", WINDOW_LAYOUTS), ("roof_form", ROOF_FORMS),
                           ("interior", INTERIORS), ("roof", tuple(dict.fromkeys((*ROOFS, *COTTAGE_ROOFS)))))},
}
POINT = {"type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 3}
MODULE_FIELDS = {
    "mode": {"type": "string", "enum": ["fill", "shell", "erase", "roof", "framing"]},
    "from": POINT, "to": POINT,
    "style": {"type": ["string", "null"], "enum": [*ROOF_STYLES, None]},
    "overhang": {"type": ["integer", "null"]},
    "ridge_axis": {"type": ["string", "null"], "enum": ["x", "z", None]},
    "spacing": {"type": ["integer", "null"]},
    "block": {"type": ["string", "null"]},
    "properties": {
        "type": ["object", "null"],
        "properties": {key: {"type": ["string", "null"]} for key in ("axis", "facing", "half", "type", "hanging")},
        "required": ["axis", "facing", "half", "type", "hanging"], "additionalProperties": False,
    },
}
INTENT_DESIGN_FIELDS = {
    **DESIGN_FIELDS,
    "name": {"type": ["string", "null"]},
    "overlay": {"type": ["boolean", "null"]},
    "lighting": {"type": ["string", "null"], "enum": [*LIGHTING, None]},
    "modules": {"type": ["array", "null"], "items": {
        "type": "object", "properties": MODULE_FIELDS,
        "required": list(MODULE_FIELDS), "additionalProperties": False,
    }},
}
EDIT_FIELDS = {
    "surface": {"type": "string", "enum": ["wall", "window", "roof"]},
    "material": {"type": "string"},
    "width": {"type": ["integer", "null"]},
    "height": {"type": ["integer", "null"]},
    "floor_to_ceiling": {"type": ["boolean", "null"]},
}
FIELDS = {
    "action": {"type": "string", "enum": sorted(ACTIONS)},
    "project_name": {"type": ["string", "null"]},
    "kind": {"type": ["string", "null"], "enum": [*DESIGNS, None]},
    "changes": {
        "type": ["object", "null"], "properties": INTENT_DESIGN_FIELDS,
        "required": list(INTENT_DESIGN_FIELDS), "additionalProperties": False,
    },
    "base_version": {"type": ["integer", "null"]},
    "edit": {"type": ["object", "null"], "properties": EDIT_FIELDS,
             "required": list(EDIT_FIELDS), "additionalProperties": False},
    "origin": {"anyOf": [
        {"type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 3},
        {"type": "string", "enum": ["here"]}, {"type": "null"},
    ]},
    "rotation": {"anyOf": [
        {"type": "integer", "enum": [0, 90, 180, 270]},
        {"type": "string", "enum": ["here"]}, {"type": "null"},
    ]},
    "message": {"type": ["string", "null"]},
    "topic": {"type": ["string", "null"], "enum": [*TASK_TOPICS, None]},
    "strategy": {"type": ["string", "null"], "enum": [*WORK_STRATEGIES, None]},
    "group": {"type": ["string", "null"]},
}
INTENT_SCHEMA = {
    "type": "object", "properties": FIELDS,
    "required": list(FIELDS), "additionalProperties": False,
}


def context_message(context: dict) -> str:
    payload = {key: context[key] for key in CONTEXT_KEYS if key in context}
    recent = context.get("recent", [])[-4:]
    payload["recent"] = [{key: row[key] for key in ("message", "action", "reply") if key in row}
                         for row in recent if len(row.get("message", "")) <= 240]
    while True:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) <= MAX_CONTEXT_BYTES:
            return encoded
        if payload["recent"]:
            payload["recent"].pop(0)
        elif payload.get("workspace_projects"):
            payload["workspace_projects"] = payload["workspace_projects"][:-1]
        elif len(payload.get("tool_results", ())) > 1:
            payload["tool_results"] = payload["tool_results"][1:]
        else:
            raise ModelError("Codex Architect context exceeds the input limit")


def decode_intent(output: str) -> dict:
    if len(output.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise InvalidOutput("Codex intent exceeds the output limit")
    try:
        raw = json.loads(output, parse_constant=_bad_constant, object_pairs_hook=_unique_pairs)
        if not isinstance(raw, dict) or set(raw) - FIELDS.keys():
            raise ValueError("Unexpected intent fields")
        normalized = {key: value for key, value in raw.items() if value is not None}
        if isinstance(normalized.get("changes"), dict):
            if set(normalized["changes"]) - INTENT_DESIGN_FIELDS.keys():
                raise ValueError("Unexpected building fields")
            normalized["changes"] = {key: value for key, value in normalized["changes"].items() if value is not None}
            for module in normalized["changes"].get("modules", ()):
                if isinstance(module.get("properties"), dict):
                    module["properties"] = {key: value for key, value in module["properties"].items() if value is not None}
        if isinstance(normalized.get("edit"), dict):
            normalized["edit"] = {key: value for key, value in normalized["edit"].items() if value is not None}
        return parse_intent(normalized)
    except (AttributeError, TypeError, ValueError, RecursionError):
        raise InvalidOutput("Invalid structured Codex intent") from None


def scene_input(payload, image):
    from openai_codex import ExternalMessage

    return ExternalMessage(tool_name="architect_scene", content=[
        {"type": "input_text", "text": payload},
        {"type": "input_image", "image_url": "data:image/png;base64," + b64encode(image).decode("ascii"), "detail": "low"},
    ])


class CodexProvider:
    """One short-lived, read-only Codex thread per bounded M3 request."""

    def __init__(self, *, model=None, effort=DEFAULT_EFFORT, timeout=30.0, max_turns=12,
                 budget_log=None, max_tokens=150_000):
        if find_spec("openai_codex") is None:
            raise ValueError("Install the optional openai-codex package before selecting the Codex provider.")
        if not 0 < timeout <= 120 or not 1 <= max_turns <= 100:
            raise ValueError("Codex timeout must be in (0, 120] seconds and the turn budget in 1–100.")
        if effort not in REASONING_EFFORTS:
            raise ValueError("Unsupported Codex reasoning effort.")
        self.model = model or DEFAULT_MODEL
        self.effort = effort
        self.timeout = timeout
        self.max_turns = max_turns
        self.turns_started = 0
        self._lock = Lock()
        self.budget = CodexBudget(budget_log, max_turns, max_tokens) if budget_log is not None else None

    def decide(self, context: dict) -> dict:
        intent, _ = self.decide_with_metrics(context)
        return intent

    def decide_with_view(self, context, image, *, observation_id, allow_upload=False):
        result, _ = self._vision_decision(context_message(context), image, observation_id, allow_upload,
                                          self._intent_view_turn, "intent")
        return result

    def decide_with_metrics(self, context: dict) -> tuple[dict, dict]:
        payload = context_message(context)
        metrics = {
            "requested_model": self.model, "requested_effort": self.effort, "status": "reserved",
            "context_bytes": len(payload.encode("utf-8")),
            "message_sha256": sha256(context["message"].encode("utf-8")).hexdigest(),
        }
        return self._metered(metrics, lambda: self._turn(payload, metrics), "intent")

    def analyze_scene_with_metrics(self, context, image, *, observation_id, allow_upload=False):
        return self._vision_decision(scene_context_message(context), image, observation_id, allow_upload,
                                     self._scene_turn, "scene")

    def survey_with_metrics(self, payload, image, *, observation_id, allow_upload=False):
        message = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(message.encode("utf-8")) > 7600:
            raise ValueError("Survey prompt exceeds its input limit")
        return self._vision_decision(message, image, observation_id, allow_upload, self._survey_turn, "survey")

    def survey_text_with_metrics(self, payload):
        if payload.get("worksite") is not True:
            raise InvalidOutput("The structured-only survey is for an authorized worksite")
        message = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(message.encode("utf-8")) > 7600:
            raise InvalidOutput("Survey prompt exceeds its input limit")
        metrics = {"requested_model": self.model, "requested_effort": self.effort,
                   "status": "reserved", "context_bytes": len(message.encode("utf-8"))}
        return self._metered(metrics, lambda: self._survey_turn(message, None, metrics), "worksite_survey")

    def feedback_with_metrics(self, payload, image, *, observation_id, allow_upload=False):
        message = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(message.encode("utf-8")) > 7600:
            raise ValueError("Feedback prompt exceeds its input limit")
        return self._vision_decision(message, image, observation_id, allow_upload, self._feedback_turn, "feedback")

    def _vision_decision(self, payload, image, observation_id, allow_upload, invoke, kind):
        if allow_upload is not True:
            raise ValueError("Explicit image-sharing approval is required.")
        if type(observation_id) is not str or str(UUID(observation_id)) != observation_id:
            raise ValueError("A selected observation ID is required for image sharing.")
        png, width, height = rgb_png(image)
        metrics = {
            "purpose": "vision", "requested_model": self.model, "requested_effort": self.effort,
            "status": "reserved", "observation_id": observation_id,
            "context_bytes": len(payload.encode("utf-8")), "context_sha256": sha256(payload.encode("utf-8")).hexdigest(),
            "image_sha256": sha256(png).hexdigest(), "image_bytes": len(png), "image_size": [width, height],
        }
        return self._metered(metrics, lambda: invoke(payload, png, metrics), kind)

    def _metered(self, metrics, invoke, kind):
        with self._lock:
            if self.turns_started >= self.max_turns:
                raise ModelBudgetExhausted("Codex turn budget exhausted")
            sequence = self.budget.reserve(metrics) if self.budget else None
            self.turns_started += 1
        started = perf_counter()
        try:
            result = asyncio.run(invoke())
            if kind == "intent":
                metrics["intent"] = {key: value for key, value in result.items() if key != "message"}
            elif kind == "scene":
                metrics["scene"] = {"consistency": result["consistency"],
                                    "center_color": result["focal_surface"]["color"],
                                    "center_material": result["focal_surface"]["material"]}
            else:
                metrics["decision"] = {key: value for key, value in result.items() if key != "reason"}
            metrics["status"] = "completed"
        except TimeoutError:
            metrics.update(status="timeout", error={"kind": "timeout"})
            raise ModelTimeout("Codex turn timed out") from None
        except ModelError as error:
            metrics.update(status="rejected", error=metrics.get("error", {"kind": type(error).__name__}))
            raise
        except Exception as error:
            metrics.update(status="failed", error=safe_failure(error))
            raise ModelError(f"Codex {metrics['error']['kind']}") from None
        finally:
            metrics["elapsed_ms"] = round((perf_counter() - started) * 1000)
            model_turn(kind, metrics)
            if self.budget and not self.budget.finish(sequence, metrics):
                raise ModelBudgetExhausted("Codex token budget exceeded")
        return result, metrics

    async def _turn(self, payload: str, metrics: dict) -> dict:
        from openai_codex import ExternalMessage

        return await self._intent_turn(ExternalMessage(tool_name="architect_context", content=payload), metrics)

    async def _intent_view_turn(self, payload, image, metrics):
        return await self._intent_turn(scene_input(payload, image), metrics)

    async def _intent_turn(self, inputs, metrics):
        instructions = PROMPT + "\nReturn the next typed decision using the supplied data. " \
            "Use JSON null for schema fields that are not part of this intent."
        return await self._sdk_turn(inputs, instructions, INTENT_SCHEMA, decode_intent, metrics)

    async def _scene_turn(self, payload, image, metrics):
        return await self._sdk_turn(scene_input(payload, image), VISION_PROMPT, SCENE_SCHEMA, decode_scene, metrics)

    async def _survey_turn(self, payload, image, metrics):
        from openai_codex import ExternalMessage

        instructions = survey.PROMPT
        worksite = json.loads(payload).get("worksite") is True
        if worksite:
            instructions += "\n" + survey.WORKSITE_PROMPT
        inputs = scene_input(payload, image) if image is not None else ExternalMessage(
            tool_name="worksite_survey", content=payload)
        if image is None:
            instructions += "\nUse the bounded Forge facts. No image is provided; avoid visual claims."
        return await self._sdk_turn(inputs, instructions, survey.schema(INTENT_DESIGN_FIELDS, worksite=worksite),
                                    lambda output: survey.decode(output, INTENT_DESIGN_FIELDS, worksite=worksite), metrics)

    async def _feedback_turn(self, payload, image, metrics):
        return await self._sdk_turn(scene_input(payload, image), feedback.PROMPT, feedback.schema(DESIGN_FIELDS),
                                    lambda output: feedback.decode(output, DESIGN_FIELDS), metrics)

    async def _sdk_turn(self, inputs, instructions, schema, decode, metrics):
        from openai_codex import AsyncCodex, AsyncThread
        from openai_codex._run import _collect_async_turn_result
        from openai_codex.generated.v2_all import ConfigReadResponse, ThreadStartParams

        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="intent-", dir=WORK_ROOT) as folder:
            async with asyncio.timeout(self.timeout):
                async with AsyncCodex(sdk_config(Path(folder))) as codex:
                    metrics["account"] = auth_summary(await codex.account(refresh_token=False))
                    if metrics["account"]["mode"] != "chatgpt":
                        raise ModelError("Codex requires an existing ChatGPT login")
                    config_reply = await codex._client.request(
                        "config/read", config_request(Path(folder)), response_model=ConfigReadResponse,
                    )
                    metrics["tools"] = summarize_config(config_reply)
                    require_safe_config(metrics["tools"])
                    # The SDK's high-level thread_start drops the protocol reply.
                    # Check the actual model, effort and permissions before running.
                    started = await codex._client.thread_start(ThreadStartParams(
                        cwd=folder, ephemeral=True, model=self.model, model_provider="openai",
                        config={"model_reasoning_effort": self.effort},
                        sandbox="read-only", approval_policy="never",
                        base_instructions=instructions,
                    ))
                    self.verify_thread(started, metrics)
                    thread = AsyncThread(codex, started.thread.id)
                    turn = await thread.turn(
                        inputs, output_schema=schema, model=self.model, effort=self.effort,
                        turn_service_tier="default", **safety_options(),
                    )
                    async with aclosing(turn.stream()) as stream:
                        result = await _collect_async_turn_result(self.checked_events(stream, metrics), turn_id=turn.id)
                    metrics["sdk_duration_ms"] = result.duration_ms
                    if result.error:
                        metrics["error"] = safe_failure(result.error)
                        raise ModelError("Codex turn failed")
                    status = getattr(result.status, "value", result.status)
                    item_types = [getattr(getattr(item, "root", item), "type", "unknown") for item in result.items]
                    metrics["item_types"] = item_types
                    if status != "completed" or not result.final_response or set(item_types) - NON_TOOL_ITEMS:
                        raise InvalidOutput("Codex did not return a text-only completed result")
                    return decode(result.final_response)

    def verify_thread(self, started, metrics):
        sandbox = started.sandbox.root
        approval = started.approval_policy.root
        effort = started.reasoning_effort
        metrics["effective"] = {
            "model": started.model, "model_provider": started.model_provider,
            "effort": effort.value if effort else None,
            "sandbox": sandbox.type, "sandbox_network": getattr(sandbox, "network_access", None),
            "approval": getattr(approval, "value", None), "ephemeral": started.thread.ephemeral,
            "instruction_sources": len(started.instruction_sources or []),
        }
        if started.model != self.model or effort is None or effort.value != self.effort:
            metrics["error"] = {"kind": "requested_model_or_effort_mismatch"}
            raise ModelError("Codex did not apply the requested model and effort")
        if (started.model_provider != "openai" or sandbox.type != "readOnly" or sandbox.network_access is not False
                or getattr(approval, "value", None) != "never" or not started.thread.ephemeral
                or started.instruction_sources):
            raise ModelError("Codex thread isolation settings failed validation")

    async def checked_events(self, stream, metrics):
        from openai_codex.generated.v2_all import ModelReroutedNotification, ThreadTokenUsageUpdatedNotification

        async for event in stream:
            if isinstance(event.payload, ModelReroutedNotification) and event.payload.to_model != self.model:
                metrics["error"] = {"kind": "model_rerouted", "model": event.payload.to_model}
                raise ModelError("Codex rerouted the requested model")
            if isinstance(event.payload, ThreadTokenUsageUpdatedNotification):
                metrics["tokens"] = event.payload.token_usage.total.model_dump(exclude_none=True)
            yield event
