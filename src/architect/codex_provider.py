"""Interpret bounded Architect context through the official Codex SDK."""

import asyncio
from contextlib import aclosing
from hashlib import sha256
from importlib.util import find_spec
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from time import perf_counter

from src.architect.codex_budget import CodexBudget
from src.architect.codex_sdk import (
    WORK_ROOT, auth_summary, config_request, require_safe_config, safe_failure,
    safety_options, sdk_config, summarize_config,
)
from src.architect.design import GLASS, MASONRY, ROOFS, STYLES, WOODS
from src.architect.providers import (ACTIONS, PROMPT, InvalidOutput, ModelBudgetExhausted, ModelError,
                                     ModelTimeout, parse_intent)
from src.architect.providers import _bad_constant, _unique_pairs

DEFAULT_MODEL = "gpt-6.2-sol"
DEFAULT_EFFORT = "medium"
REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")
MAX_CONTEXT_BYTES = 4096
MAX_RESPONSE_BYTES = 8192
CONTEXT_KEYS = (
    "message", "dimension", "position", "facing", "suggested_origin", "approved_area",
    "ground_near_anchor", "construction", "design", "supply", "available_mod_blocks",
)
NON_TOOL_ITEMS = {"userMessage", "agentMessage", "reasoning", "functionCallOutput", "contextCompaction"}

WALL_FIELDS = {
    **{key: {"type": ["integer", "null"]} for key in ("width", "height", "window_width", "window_height")},
    **{key: {"type": ["string", "null"], "enum": [*values, None]}
       for key, values in (("style", STYLES), ("wood", WOODS), ("masonry", MASONRY), ("glass", GLASS), ("roof", ROOFS))},
    "porch": {"type": ["boolean", "null"]},
}
FIELDS = {
    "action": {"type": "string", "enum": sorted(ACTIONS)},
    "kind": {"type": ["string", "null"], "enum": ["window_wall", None]},
    "changes": {
        "type": ["object", "null"], "properties": WALL_FIELDS,
        "required": list(WALL_FIELDS), "additionalProperties": False,
    },
    "base_version": {"type": ["integer", "null"]},
    "origin": {"anyOf": [
        {"type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 3},
        {"type": "string", "enum": ["here"]}, {"type": "null"},
    ]},
    "rotation": {"anyOf": [
        {"type": "integer", "enum": [0, 90, 180, 270]},
        {"type": "string", "enum": ["here"]}, {"type": "null"},
    ]},
    "message": {"type": ["string", "null"]},
}
INTENT_SCHEMA = {
    "type": "object", "properties": FIELDS,
    "required": list(FIELDS), "additionalProperties": False,
}


def context_message(context: dict) -> str:
    payload = {key: context[key] for key in CONTEXT_KEYS if key in context}
    recent = context.get("recent", [])[-4:]
    payload["recent"] = [{"message": row["message"], "action": row["action"]} for row in recent]
    while True:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) <= MAX_CONTEXT_BYTES:
            return encoded
        if not payload["recent"]:
            raise ModelError("Codex Architect context exceeds the input limit")
        payload["recent"].pop(0)


def decode_intent(output: str) -> dict:
    if len(output.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise InvalidOutput("Codex intent exceeds the output limit")
    try:
        raw = json.loads(output, parse_constant=_bad_constant, object_pairs_hook=_unique_pairs)
        if not isinstance(raw, dict) or set(raw) - FIELDS.keys():
            raise ValueError("Unexpected intent fields")
        normalized = {key: value for key, value in raw.items() if value is not None}
        if isinstance(normalized.get("changes"), dict):
            if set(normalized["changes"]) - WALL_FIELDS.keys():
                raise ValueError("Unexpected wall fields")
            normalized["changes"] = {key: value for key, value in normalized["changes"].items() if value is not None}
        return parse_intent(normalized)
    except (TypeError, ValueError, RecursionError):
        raise InvalidOutput("Invalid structured Codex intent") from None


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

    def decide_with_metrics(self, context: dict) -> tuple[dict, dict]:
        payload = context_message(context)
        metrics = {
            "requested_model": self.model, "requested_effort": self.effort, "status": "reserved",
            "context_bytes": len(payload.encode("utf-8")),
            "message_sha256": sha256(context["message"].encode("utf-8")).hexdigest(),
        }
        with self._lock:
            if self.turns_started >= self.max_turns:
                raise ModelBudgetExhausted("Codex turn budget exhausted")
            sequence = self.budget.reserve(metrics) if self.budget else None
            self.turns_started += 1
        started = perf_counter()
        try:
            intent = asyncio.run(self._turn(payload, metrics))
            metrics["intent"] = {key: value for key, value in intent.items() if key != "message"}
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
            if self.budget and not self.budget.finish(sequence, metrics):
                raise ModelBudgetExhausted("Codex token budget exceeded")
        return intent, metrics

    async def _turn(self, payload: str, metrics: dict) -> dict:
        from openai_codex import AsyncCodex, AsyncThread, ExternalMessage
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
                        base_instructions=PROMPT + "\nThe only request data is the architect_context message. "
                        "Return the intent without reading files, using tools or taking actions. "
                        "Use JSON null for schema fields that are not part of this intent.",
                    ))
                    self.verify_thread(started, metrics)
                    thread = AsyncThread(codex, started.thread.id)
                    turn = await thread.turn(
                        ExternalMessage(tool_name="architect_context", content=payload),
                        output_schema=INTENT_SCHEMA, model=self.model, effort=self.effort,
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
                        raise InvalidOutput("Codex did not return a text-only completed intent")
                    return decode_intent(result.final_response)

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
