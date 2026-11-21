"""Launch the official SDK with a text-only ChatGPT account session."""

import os
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[2]
WORK_ROOT = ROOT / ".cache" / "m3-codex-sdk"

DISABLED_FEATURES = (
    "shell_tool", "shell_snapshot", "apps", "plugins", "hooks", "browser_use",
    "computer_use", "code_mode", "multi_agent", "multi_agent_v2", "memories",
    "skill_mcp_dependency_install", "unbounded_connection_retries",
)

CHATGPT_URL = "https://chatgpt.com/backend-api/"


def sdk_config(workspace: Path):
    from openai_codex import CodexConfig

    # An empty map in Codex configuration does not erase inherited entries.
    config_path = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"
    servers = tomllib.loads(config_path.read_text(encoding="utf-8")).get("mcp_servers", {}) if config_path.is_file() else {}
    if any(not re.fullmatch(r"[A-Za-z0-9_-]+", server) for server in servers):
        raise RuntimeError("Codex isolation settings cannot override this MCP configuration")
    # Per-process overrides. The existing login store remains available to the SDK.
    return CodexConfig(
        cwd=str(workspace),
        config_overrides=(
            'model_provider="openai"',
            'forced_login_method="chatgpt"',
            f'chatgpt_base_url="{CHATGPT_URL}"',
            f'openai_base_url="{CHATGPT_URL}codex"',
            'service_tier="default"',
            'model_verbosity="low"',
            'web_search="disabled"',
            'history.persistence="none"',
            "project_doc_max_bytes=0",
            "project_root_markers=[]",
            *(f"mcp_servers.{server}.enabled=false" for server in servers),
            "notify=[]",
            "allow_login_shell=false",
            "analytics.enabled=false",
            "tools.update_plan.enabled=false",
            "tools.experimental_request_user_input.enabled=false",
            *(f"features.{feature}=false" for feature in DISABLED_FEATURES),
        ),
        env={
            "OPENAI_API_KEY": "", "CODEX_API_KEY": "", "CODEX_ACCESS_TOKEN": "",
            "OPENAI_BASE_URL": "", "MC_ARCHITECT_API_KEY": "",
        },
    )


def config_request(workspace: Path):
    return {"cwd": str(workspace), "includeLayers": False}


def inspect_config(client, workspace: Path):
    from openai_codex.generated.v2_all import ConfigReadResponse

    reply = client.request("config/read", config_request(workspace), response_model=ConfigReadResponse)
    return summarize_config(reply)


def summarize_config(reply):
    config = reply.config.model_dump(mode="json", by_alias=True, exclude_none=True)
    flags = config.get("features") or {}
    servers = config.get("mcp_servers") or {}
    return {
        "shell_disabled": flags.get("shell_tool") is False,
        "active_mcp_servers": sum(server.get("enabled", True) for server in servers.values()),
        "notification_commands": len(config.get("notify") or []),
        "expected_disabled_features": all(flags.get(key) is False for key in DISABLED_FEATURES),
        "web_search_disabled": config.get("web_search") == "disabled",
        "project_instructions_disabled": config.get("project_doc_max_bytes") == 0,
        "official_chatgpt_endpoint": config.get("chatgpt_base_url") == CHATGPT_URL
        and config.get("openai_base_url") == CHATGPT_URL + "codex",
        "chatgpt_login_required": config.get("forced_login_method") == "chatgpt",
    }


def require_safe_config(info):
    if (info["active_mcp_servers"] or info["notification_commands"] or
            any(value is not True for key, value in info.items()
                if key not in {"active_mcp_servers", "notification_commands"})):
        raise RuntimeError("Codex isolation settings failed validation")


def auth_summary(account_reply):
    root = account_reply.account.root if account_reply.account else None
    mode = getattr(root, "type", None)
    result = {"mode": mode, "requires_openai_auth": account_reply.requires_openai_auth}
    if mode == "chatgpt":
        result["plan"] = getattr(root, "plan_type", None)
    return result


def rate_limits_summary(client):
    from openai_codex.generated.v2_all import GetAccountRateLimitsResponse

    reply = client.request("account/rateLimits/read", None, response_model=GetAccountRateLimitsResponse)
    snapshot = reply.rate_limits
    return {
        name: window.model_dump(mode="json", exclude_none=True)
        for name in ("primary", "secondary")
        if (window := getattr(snapshot, name)) is not None
    }


def safety_options():
    from openai_codex import ApprovalMode, Sandbox

    return {"sandbox": Sandbox.read_only, "approval_mode": ApprovalMode.deny_all}


def usage_summary(result):
    return result.usage.last.model_dump(exclude_none=True) if result.usage else None


def safe_failure(error):
    """Keep diagnostics useful without emitting SDK response bodies or identity fields."""
    message = str(getattr(error, "message", error)).lower()
    if "isolation settings" in message:
        kind = "isolation_verification"
    elif any(word in message for word in ("usage limit", "rate limit", "quota", "too many requests", "429")):
        kind = "rate_or_usage_limit"
    elif any(word in message for word in ("401", "403", "auth", "login", "sign in")):
        kind = "authentication_or_access"
    elif any(word in message for word in ("timeout", "timed out", "deadline")):
        kind = "timeout"
    elif any(word in message for word in ("connect", "tls", "disconnected", "stream")):
        kind = "connection_or_stream"
    elif "schema" in message or "structured" in message:
        kind = "output_schema"
    elif "model" in message and any(word in message for word in ("unsupported", "unavailable", "not found")):
        kind = "model_unavailable"
    else:
        kind = "sdk_error"
    statuses = set(re.findall(r"\b(?:400|401|403|404|408|429|500|502|503|504)\b", message))
    return {"kind": kind, "type": type(error).__name__, **({"http_statuses": sorted(statuses)} if statuses else {})}
