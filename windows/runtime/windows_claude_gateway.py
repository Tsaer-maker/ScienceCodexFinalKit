#!/usr/bin/env python3
"""Windows-only loopback gateway for Claude Desktop third-party profiles.

For DeepSeek, Kimi, and GLM, the controller sends the selected API key through
stdin after process creation.  The key is therefore never placed in argv, the
environment, Claude's profile, or a plaintext state file.  The Codex profile
instead uses the official Windows Codex CLI ChatGPT credential cache as its one
credential owner and translates Anthropic Messages to the Codex Responses wire
protocol, including tool calls and streaming events.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import signal
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable, Iterator


MAX_REQUEST_BYTES = 64 * 1024 * 1024
MAX_ERROR_BYTES = 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
ALLOWED_PROFILES = {"deepseek", "kimi", "glm", "codex"}
ALLOWED_PROTOCOLS = {"anthropic-messages", "openai-responses"}
ALLOWED_AUTH_STYLES = {"x-api-key", "bearer", "codex-cli"}
ROLES = ("opus", "sonnet", "haiku")
PROFILE_REASONING = {
    "deepseek": {"auto", "none", "high", "max"},
    "kimi": {"auto", "none", "low", "high", "max"},
    "glm": {"auto", "none", "high", "max"},
    "codex": {"none", "low", "medium", "high", "xhigh", "max", "ultra"},
}
CODEX_AUTH_BASE_URL = "https://auth.openai.com"
CODEX_BACKEND_URL = "https://chatgpt.com/backend-api/codex"
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CLAUDE_ALIASES = (
    "claude-opus-4-8",
    "claude-sonnet-4-5",
    "claude-haiku-4-5-20251001",
)
SAFE_SECRET = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
SAFE_TOOL_NAME = re.compile(r"[^A-Za-z0-9_-]+")


class GatewayError(RuntimeError):
    """A caller-safe gateway failure."""


class CodexAuthError(GatewayError):
    """A credential error that is safe to return without token detail."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward an API credential across a redirect boundary."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def _require_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text or "\r" in text or "\n" in text:
        raise GatewayError(f"{label} is empty or malformed")
    return text


def _validate_effort(value: Any, label: str, *, allow_empty: bool = False) -> str:
    effort = str(value or "").strip()
    if not effort and allow_empty:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", effort):
        raise GatewayError(f"{label} is empty or malformed")
    return effort


def _validate_url(raw: Any, protocol: str) -> str:
    value = _require_text(raw, "upstream URL").rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise GatewayError("upstream URL must be an absolute HTTP(S) URL")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise GatewayError("non-loopback upstream URLs must use HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise GatewayError("upstream URL cannot contain credentials, query, or fragment")
    lowered = value.lower()
    if any(marker in lowered for marker in ("/mnt/", "/home/", "\\\\wsl", "wsl.exe")):
        raise GatewayError("Windows Claude upstream cannot reference WSL")
    if parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.port in {9876, 18987}:
        raise GatewayError("Windows Claude cannot use a reserved FinalKit gateway port")
    if protocol == "openai-responses" and parsed.path.rstrip("/").endswith("/messages"):
        raise GatewayError("OpenAI Responses upstream cannot end in /messages")
    return value


def _validate_windows_auth_path(raw: Any) -> str:
    value = _require_text(raw, "Windows Codex auth path")
    lowered = value.lower()
    if any(marker in lowered for marker in ("\\\\wsl$", "\\\\wsl.localhost", "/mnt/", "/home/", "wsl.exe")):
        raise GatewayError("Windows Codex auth path cannot reference WSL")
    path = Path(value)
    if not path.is_absolute() or path.name.lower() != "auth.json":
        raise GatewayError("Windows Codex auth path must be an absolute auth.json path")
    return str(path)


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GatewayError(f"runtime config is unreadable: {type(exc).__name__}") from exc
    if not isinstance(config, dict):
        raise GatewayError("runtime config must be a JSON object")

    required = {
        "schema_version",
        "instance_id",
        "profile",
        "profile_id",
        "profile_name",
        "host",
        "port",
        "path_secret",
        "client_token",
        "control_secret",
        "protocol",
        "upstream",
        "auth_style",
    }
    missing = sorted(required - set(config))
    if missing:
        raise GatewayError(f"runtime config is missing: {', '.join(missing)}")
    if config["schema_version"] not in {1, 2, 3}:
        raise GatewayError("unsupported runtime config schema")
    schema_version = int(config["schema_version"])
    if config["host"] != "127.0.0.1":
        raise GatewayError("Windows Claude gateway may bind only to 127.0.0.1")
    try:
        port = int(config["port"])
    except (TypeError, ValueError) as exc:
        raise GatewayError("runtime port is invalid") from exc
    if not 1024 <= port <= 65535:
        raise GatewayError("runtime port is outside the allowed range")
    config["port"] = port

    profile = str(config["profile"])
    protocol = str(config["protocol"])
    auth_style = str(config["auth_style"])
    if profile not in ALLOWED_PROFILES:
        raise GatewayError("profile is not in the Windows allowlist")
    if protocol not in ALLOWED_PROTOCOLS:
        raise GatewayError("unsupported upstream protocol")
    if auth_style not in ALLOWED_AUTH_STYLES:
        raise GatewayError("unsupported upstream authentication style")
    if profile == "codex" and protocol != "openai-responses":
        raise GatewayError("the Codex login profile must use OpenAI Responses")
    if profile != "codex" and protocol != "anthropic-messages":
        raise GatewayError("the direct provider profiles must use Anthropic Messages")
    if profile == "codex":
        if auth_style != "codex-cli":
            raise GatewayError("the Codex profile must use the Windows Codex CLI login")
        if config.get("upstream", "").rstrip("/") != CODEX_BACKEND_URL:
            raise GatewayError("the Codex profile must use the official ChatGPT Codex backend")
        if "codex_auth_file" not in config:
            raise GatewayError("runtime config is missing: codex_auth_file")
        config["codex_auth_file"] = _validate_windows_auth_path(config["codex_auth_file"])
    elif auth_style == "codex-cli":
        raise GatewayError("only the Codex profile may use the Windows Codex CLI login")

    if schema_version == 3:
        route_required = {
            *(f"model_{role}" for role in ROLES),
            *(f"reasoning_{role}" for role in ROLES),
        }
        route_missing = sorted(route_required - set(config))
        if route_missing:
            raise GatewayError(f"runtime config is missing: {', '.join(route_missing)}")
    elif profile == "codex" and schema_version == 2:
        for role in ROLES:
            config[f"reasoning_{role}"] = config.get(f"reasoning_effort_{role}", "")
    else:
        legacy_required = {"model_default", "model_fast"}
        legacy_missing = sorted(legacy_required - set(config))
        if legacy_missing:
            raise GatewayError(f"runtime config is missing: {', '.join(legacy_missing)}")
        shared_reasoning = _validate_effort(
            config.get("reasoning_effort") or ("auto" if profile != "codex" else "max"),
            "reasoning",
        )
        config["model_opus"] = config["model_default"]
        config["model_sonnet"] = config["model_default"]
        config["model_haiku"] = config["model_fast"]
        for role in ROLES:
            config[f"reasoning_{role}"] = shared_reasoning

    for role in ROLES:
        config[f"model_{role}"] = _require_text(config.get(f"model_{role}"), f"{role} model")
        reasoning = _validate_effort(config.get(f"reasoning_{role}"), f"{role} reasoning").lower()
        if reasoning not in PROFILE_REASONING[profile]:
            choices = ", ".join(sorted(PROFILE_REASONING[profile]))
            raise GatewayError(f"{role} reasoning must be one of: {choices}")
        config[f"reasoning_{role}"] = reasoning

    for field in ("instance_id", "profile_id"):
        try:
            config[field] = str(uuid.UUID(str(config[field])))
        except (ValueError, AttributeError) as exc:
            raise GatewayError(f"{field} is not a UUID") from exc
    for field in ("path_secret", "client_token", "control_secret"):
        value = str(config[field])
        if not SAFE_SECRET.fullmatch(value):
            raise GatewayError(f"{field} is malformed")
        config[field] = value

    config["profile_name"] = _require_text(config["profile_name"], "profile name")
    config["upstream"] = _validate_url(config["upstream"], protocol)
    config["offline_smoke"] = bool(config.get("offline_smoke", False))
    return config


def read_api_key() -> str:
    raw = sys.stdin.buffer.readline(16 * 1024 + 1)
    if len(raw) > 16 * 1024:
        raise GatewayError("API key exceeded the private input limit")
    raw = raw.rstrip(b"\r\n")
    try:
        key = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GatewayError("API key is not valid UTF-8") from exc
    if not key or "\r" in key or "\n" in key:
        raise GatewayError("API key is empty or malformed")
    return key


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        value = json.loads(base64.urlsafe_b64decode(payload))
        return value if isinstance(value, dict) else {}
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return {}


def _account_id_from_id_token(id_token: str) -> str:
    claims = _decode_jwt_claims(id_token)
    auth = claims.get("https://api.openai.com/auth")
    if isinstance(auth, dict):
        account = auth.get("chatgpt_account_id") or auth.get("chatgpt_account", {})
        if isinstance(account, dict):
            account = account.get("id", "")
        if account:
            return str(account)
    return str(claims.get("chatgpt_account_id", "") or "")


class CodexAuthStore:
    """Adapter over the official Windows Codex CLI ChatGPT auth.json."""

    def __init__(self, path: Path, opener=None):
        self.path = path
        self._lock = threading.RLock()
        self._opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler(),
            NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )

    def _read_root(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError as exc:
            raise CodexAuthError("Windows Codex is not logged in; run: codex login") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CodexAuthError("Windows Codex auth.json is unreadable; run: codex login") from exc
        if not isinstance(value, dict):
            raise CodexAuthError("Windows Codex auth.json is invalid; run: codex login")
        return value

    def _normalize(self, root: dict[str, Any]) -> dict[str, str]:
        if str(root.get("auth_mode") or "").lower() != "chatgpt":
            raise CodexAuthError("Windows Codex must be logged in using ChatGPT; run: codex login")
        tokens = root.get("tokens")
        if not isinstance(tokens, dict):
            raise CodexAuthError("Windows Codex ChatGPT tokens are missing; run: codex login")
        id_token = str(tokens.get("id_token") or "")
        normalized = {
            "access_token": str(tokens.get("access_token") or ""),
            "refresh_token": str(tokens.get("refresh_token") or ""),
            "id_token": id_token,
            "account_id": str(tokens.get("account_id") or "") or _account_id_from_id_token(id_token),
        }
        if any("\r" in value or "\n" in value for value in normalized.values()):
            raise CodexAuthError("Windows Codex auth.json contains a malformed token; run: codex login")
        return normalized

    def load(self) -> dict[str, str]:
        return self._normalize(self._read_root())

    def assert_configured(self) -> None:
        data = self.load()
        if not data["access_token"] or not data["refresh_token"]:
            raise CodexAuthError("Windows Codex ChatGPT login is incomplete; run: codex login")

    @staticmethod
    def _is_expiring(access_token: str) -> bool:
        exp = _decode_jwt_claims(access_token).get("exp")
        return isinstance(exp, (int, float)) and float(exp) <= time.time() + 300

    def _adopt_newer_cache(self, previous: dict[str, str]) -> dict[str, str] | None:
        current = self.load()
        if (
            current["access_token"] != previous["access_token"]
            or current["refresh_token"] != previous["refresh_token"]
        ):
            return current
        return None

    def _write_refreshed(self, previous: dict[str, str], refreshed: dict[str, Any]) -> dict[str, str]:
        root = self._read_root()
        current = self._normalize(root)
        if (
            current["access_token"] != previous["access_token"]
            or current["refresh_token"] != previous["refresh_token"]
        ):
            return current

        tokens = root.get("tokens")
        assert isinstance(tokens, dict)
        merged = dict(tokens)
        for name in ("access_token", "refresh_token", "id_token"):
            if refreshed.get(name):
                merged[name] = str(refreshed[name])
        account_id = refreshed.get("account_id") or refreshed.get("chatgpt_account_id")
        if account_id:
            merged["account_id"] = str(account_id)
        root["auth_mode"] = "chatgpt"
        root["OPENAI_API_KEY"] = None
        root["tokens"] = merged
        root["last_refresh"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(root, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            newest = self.load()
            if (
                newest["access_token"] != previous["access_token"]
                or newest["refresh_token"] != previous["refresh_token"]
            ):
                return newest
            os.replace(temporary, self.path)
        except OSError as exc:
            raise CodexAuthError("Windows Codex auth refresh could not be saved; run: codex login") from exc
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        return self._normalize(root)

    def _refresh(self, previous: dict[str, str]) -> dict[str, str]:
        if not previous["refresh_token"]:
            raise CodexAuthError("Windows Codex refresh token is missing; run: codex login")
        form = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": previous["refresh_token"],
                "client_id": CODEX_CLIENT_ID,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{CODEX_AUTH_BASE_URL}/oauth/token",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        response = None
        try:
            try:
                response = self._opener.open(request, timeout=20)
            except urllib.error.HTTPError as exc:
                response = exc
            status = int(getattr(response, "status", response.code))
            if status != 200:
                adopted = self._adopt_newer_cache(previous)
                if adopted is not None:
                    return adopted
                raise CodexAuthError("Windows Codex ChatGPT auth refresh failed; run: codex login")
            raw = response.read(MAX_ERROR_BYTES + 1)
            if len(raw) > MAX_ERROR_BYTES:
                raise CodexAuthError("Windows Codex auth refresh response was too large")
            refreshed = json.loads(raw)
            if not isinstance(refreshed, dict) or not refreshed.get("access_token"):
                raise CodexAuthError("Windows Codex auth refresh returned no access token")
            return self._write_refreshed(previous, refreshed)
        except CodexAuthError:
            raise
        except (OSError, TimeoutError, UnicodeError, json.JSONDecodeError) as exc:
            adopted = self._adopt_newer_cache(previous)
            if adopted is not None:
                return adopted
            raise CodexAuthError("Windows Codex ChatGPT auth refresh failed; run: codex login") from exc
        finally:
            if response is not None:
                response.close()

    def headers(self, rejected_access_token: str = "") -> dict[str, str]:
        with self._lock:
            data = self.load()
            if rejected_access_token and data["access_token"] != rejected_access_token:
                pass
            elif rejected_access_token or not data["access_token"] or self._is_expiring(data["access_token"]):
                data = self._refresh(data)
            if not data["access_token"]:
                raise CodexAuthError("Windows Codex access token is missing; run: codex login")
            headers = {
                "Authorization": f"Bearer {data['access_token']}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "OpenAI-Beta": "responses=experimental",
                "originator": "codex_cli_rs",
                "User-Agent": "codex_cli_rs",
            }
            if data["account_id"]:
                headers["chatgpt-account-id"] = data["account_id"]
            return headers


def _join_anthropic_url(base: str) -> str:
    parsed = urllib.parse.urlsplit(base)
    path = parsed.path.rstrip("/")
    if path.endswith("/v1/messages"):
        target = path
    elif path.endswith("/v1"):
        target = path + "/messages"
    else:
        target = path + "/v1/messages"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, target, "", ""))


def _join_responses_url(base: str) -> str:
    parsed = urllib.parse.urlsplit(base)
    path = parsed.path.rstrip("/")
    if path.endswith("/responses"):
        target = path
    elif path.endswith("/backend-api/codex"):
        target = path + "/responses"
    elif path.endswith("/v1"):
        target = path + "/responses"
    else:
        target = path + "/v1/responses"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, target, "", ""))


def _route_for(config: dict[str, Any], requested: Any) -> tuple[str, str]:
    alias = str(requested or "").lower()
    role = "haiku" if "haiku" in alias else "opus" if "opus" in alias else "sonnet"
    return config[f"model_{role}"], config[f"reasoning_{role}"]


def apply_provider_reasoning(
    body: dict[str, Any], profile: str, reasoning: str
) -> dict[str, Any]:
    """Map a compact profile Reasoning value to the provider's real wire fields."""

    if profile == "codex" or reasoning == "auto":
        return body
    normalized = dict(body)
    if reasoning == "none":
        normalized["thinking"] = {"type": "disabled"}
        normalized.pop("reasoning_effort", None)
        output_config = normalized.get("output_config")
        if isinstance(output_config, dict) and "effort" in output_config:
            output_config = dict(output_config)
            output_config.pop("effort", None)
            if output_config:
                normalized["output_config"] = output_config
            else:
                normalized.pop("output_config", None)
        return normalized

    normalized["thinking"] = {"type": "enabled"}
    if profile == "deepseek":
        output_config = normalized.get("output_config")
        output_config = dict(output_config) if isinstance(output_config, dict) else {}
        output_config["effort"] = reasoning
        normalized["output_config"] = output_config
        normalized.pop("reasoning_effort", None)
    else:
        normalized["reasoning_effort"] = reasoning
        output_config = normalized.get("output_config")
        if isinstance(output_config, dict) and "effort" in output_config:
            output_config = dict(output_config)
            output_config.pop("effort", None)
            if output_config:
                normalized["output_config"] = output_config
            else:
                normalized.pop("output_config", None)
    return normalized


def _block_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif isinstance(block, dict):
            parts.append(json.dumps(block, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(part for part in parts if part)


def _image_url(block: dict[str, Any]) -> str | None:
    source = block.get("source")
    if not isinstance(source, dict):
        return None
    source_type = source.get("type")
    if source_type in {"url", "image_url"}:
        value = str(source.get("url", ""))
        return value if value.startswith(("https://", "http://", "data:")) else None
    if source_type == "base64":
        media = str(source.get("media_type", "application/octet-stream"))
        data = str(source.get("data", ""))
        try:
            base64.b64decode(data, validate=True)
        except (ValueError, TypeError):
            return None
        return f"data:{media};base64,{data}"
    return None


def _safe_tool_name(name: Any, fallback: str) -> str:
    original = str(name or fallback)
    cleaned = SAFE_TOOL_NAME.sub("_", original).strip("_") or fallback
    if len(cleaned) <= 64:
        return cleaned
    digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:10]
    return f"{cleaned[:53]}_{digest}"


def _sanitize_schema(value: Any, *, root: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"type": "object", "properties": {}} if root else {}
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"$schema", "$id", "examples", "default"}:
            continue
        if key == "properties" and isinstance(item, dict):
            cleaned[key] = {str(k): _sanitize_schema(v) for k, v in item.items()}
        elif key == "items":
            if isinstance(item, list):
                cleaned[key] = [_sanitize_schema(v) for v in item if isinstance(v, dict)]
            else:
                cleaned[key] = _sanitize_schema(item)
        elif key in {"oneOf", "anyOf", "allOf"} and isinstance(item, list):
            cleaned[key] = [_sanitize_schema(v) for v in item if isinstance(v, dict)]
        elif isinstance(item, dict):
            cleaned[key] = _sanitize_schema(item)
        else:
            cleaned[key] = item
    if root:
        cleaned["type"] = "object"
        cleaned.setdefault("properties", {})
    return cleaned


def anthropic_to_responses(
    body: dict[str, Any], model: str, reasoning_effort: str = ""
) -> tuple[dict[str, Any], dict[str, str]]:
    """Translate one Anthropic Messages request to OpenAI Responses."""

    payload: dict[str, Any] = {
        "model": model,
        "input": [],
        "stream": bool(body.get("stream", False)),
        "store": False,
    }
    system = _block_text(body.get("system", "")).strip()
    if system:
        payload["instructions"] = system
    reverse_names: dict[str, str] = {}

    def name_for(original: Any, fallback: str) -> str:
        text = str(original or fallback)
        safe = _safe_tool_name(text, fallback)
        suffix = 1
        base = safe
        while safe in reverse_names and reverse_names[safe] != text:
            suffix += 1
            tail = f"_{suffix}"
            safe = base[: 64 - len(tail)] + tail
        reverse_names[safe] = text
        return safe

    input_items: list[dict[str, Any]] = payload["input"]
    for message_index, message in enumerate(body.get("messages", [])):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "user"))
        if role not in {"user", "assistant"}:
            role = "user"
        content = message.get("content", "")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": str(content)}]
        message_parts: list[dict[str, Any]] = []
        deferred_tool_outputs: list[dict[str, Any]] = []

        def flush_message() -> None:
            if message_parts:
                input_items.append({"type": "message", "role": role, "content": list(message_parts)})
                message_parts.clear()

        for block_index, block in enumerate(blocks):
            if isinstance(block, str):
                block = {"type": "text", "text": block}
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text_type = "output_text" if role == "assistant" else "input_text"
                message_parts.append({"type": text_type, "text": str(block.get("text", ""))})
            elif block_type == "image" and role == "user":
                image_url = _image_url(block)
                if image_url:
                    message_parts.append({"type": "input_image", "image_url": image_url})
            elif block_type == "tool_use" and role == "assistant":
                flush_message()
                call_id = str(block.get("id") or f"call_{uuid.uuid4().hex}")
                arguments = block.get("input", {})
                if not isinstance(arguments, (dict, list)):
                    arguments = {"value": arguments}
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": name_for(block.get("name"), f"tool_{message_index}_{block_index}"),
                        "arguments": json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
                    }
                )
            elif block_type == "tool_result" and role == "user":
                deferred_tool_outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": str(block.get("tool_use_id", "")),
                        "output": _block_text(block.get("content", "")),
                    }
                )
        if deferred_tool_outputs:
            input_items.extend(deferred_tool_outputs)
        flush_message()

    tools: list[dict[str, Any]] = []
    for index, tool in enumerate(body.get("tools") or []):
        if not isinstance(tool, dict):
            continue
        tools.append(
            {
                "type": "function",
                "name": name_for(tool.get("name"), f"tool_{index}"),
                "description": str(tool.get("description", "")),
                "parameters": _sanitize_schema(tool.get("input_schema", {}), root=True),
            }
        )
    if tools:
        payload["tools"] = tools
        choice = body.get("tool_choice")
        if isinstance(choice, dict):
            choice_type = choice.get("type")
            if choice_type == "tool":
                payload["tool_choice"] = {
                    "type": "function",
                    "name": name_for(choice.get("name"), "tool_0"),
                }
            elif choice_type == "any":
                payload["tool_choice"] = "required"
            elif choice_type in {"auto", "none"}:
                payload["tool_choice"] = choice_type
        elif choice in {"auto", "none"}:
            payload["tool_choice"] = choice
    # The ChatGPT-account Codex backend rejects sampling and output-limit
    # parameters accepted by the public Responses API.  Keep the Claude limit
    # local to the compatibility surface rather than sending an invalid field.
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}
    return payload, reverse_names


def _response_usage(response: dict[str, Any]) -> tuple[int, int]:
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)


def _tool_input(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    try:
        value = json.loads(str(arguments or "{}"))
        return value if isinstance(value, dict) else {"value": value}
    except json.JSONDecodeError:
        return {"_raw": str(arguments or "")}


def responses_to_anthropic(
    response: dict[str, Any], original_model: str, reverse_names: dict[str, str]
) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    saw_tool = False
    for item in response.get("output") or []:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "message":
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") in {"output_text", "text"}:
                    content.append({"type": "text", "text": str(part.get("text", ""))})
                elif part.get("type") == "refusal":
                    content.append({"type": "text", "text": str(part.get("refusal", ""))})
        elif item_type == "function_call":
            saw_tool = True
            safe_name = str(item.get("name", ""))
            content.append(
                {
                    "type": "tool_use",
                    "id": str(item.get("call_id") or item.get("id") or f"toolu_{uuid.uuid4().hex}"),
                    "name": reverse_names.get(safe_name, safe_name),
                    "input": _tool_input(item.get("arguments")),
                }
            )
    if not content:
        text = response.get("output_text")
        if text:
            content.append({"type": "text", "text": str(text)})
    input_tokens, output_tokens = _response_usage(response)
    stop_reason = "tool_use" if saw_tool else "end_turn"
    if response.get("status") == "incomplete":
        details = response.get("incomplete_details") or {}
        if isinstance(details, dict) and details.get("reason") == "max_output_tokens":
            stop_reason = "max_tokens"
    return {
        "id": str(response.get("id") or f"msg_{uuid.uuid4().hex}"),
        "type": "message",
        "role": "assistant",
        "model": original_model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def _sse(event: str, payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n".encode("utf-8")


def _iter_sse(response) -> Iterator[tuple[str, str]]:
    event = "message"
    data: list[str] = []
    while True:
        raw = response.readline()
        if not raw:
            if data:
                yield event, "\n".join(data)
            return
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if data:
                yield event, "\n".join(data)
            event, data = "message", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())


def collect_responses_stream(response) -> dict[str, Any]:
    """Collect the final Responses object for a non-streaming Claude caller."""

    for event_name, raw_data in _iter_sse(response):
        if raw_data == "[DONE]":
            break
        try:
            event = json.loads(raw_data)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or event_name)
        if event_type == "response.completed" and isinstance(event.get("response"), dict):
            return event["response"]
        if event_type in {"response.failed", "error"}:
            raise GatewayError("Codex Responses stream reported a failure")
    raise GatewayError("Codex Responses stream ended before response.completed")


def translate_responses_stream(
    response, original_model: str, reverse_names: dict[str, str]
) -> Iterator[bytes]:
    """Translate OpenAI Responses SSE to Anthropic Messages SSE."""

    message_id = f"msg_{uuid.uuid4().hex}"
    yield _sse(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "model": original_model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        },
    )
    next_index = 0
    text_index: int | None = None
    tool_indices: dict[str, int] = {}
    open_indices: set[int] = set()
    saw_tool = False
    completed = False
    input_tokens = 0
    output_tokens = 0

    def start_text() -> tuple[int, bytes | None]:
        nonlocal next_index, text_index
        if text_index is None:
            text_index = next_index
            next_index += 1
            open_indices.add(text_index)
            return text_index, _sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": text_index,
                    "content_block": {"type": "text", "text": ""},
                },
            )
        return text_index, None

    def close_blocks() -> Iterable[bytes]:
        for index in sorted(open_indices):
            yield _sse("content_block_stop", {"type": "content_block_stop", "index": index})
        open_indices.clear()

    for event_name, raw_data in _iter_sse(response):
        if raw_data == "[DONE]":
            break
        try:
            event = json.loads(raw_data)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or event_name)
        if event_type == "response.created":
            created = event.get("response")
            if isinstance(created, dict) and created.get("id"):
                message_id = str(created["id"])
        elif event_type in {"response.output_text.delta", "response.refusal.delta"}:
            index, start_event = start_text()
            if start_event:
                yield start_event
            yield _sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "text_delta", "text": str(event.get("delta", ""))},
                },
            )
        elif event_type == "response.output_item.added":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                saw_tool = True
                key = str(item.get("id") or item.get("call_id") or event.get("output_index"))
                index = next_index
                next_index += 1
                tool_indices[key] = index
                open_indices.add(index)
                safe_name = str(item.get("name", ""))
                yield _sse(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": {
                            "type": "tool_use",
                            "id": str(item.get("call_id") or item.get("id") or f"toolu_{uuid.uuid4().hex}"),
                            "name": reverse_names.get(safe_name, safe_name),
                            "input": {},
                        },
                    },
                )
        elif event_type == "response.function_call_arguments.delta":
            key = str(event.get("item_id") or event.get("call_id") or event.get("output_index"))
            index = tool_indices.get(key)
            if index is None and tool_indices:
                index = list(tool_indices.values())[-1]
            if index is not None:
                yield _sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": "input_json_delta", "partial_json": str(event.get("delta", ""))},
                    },
                )
        elif event_type == "response.completed":
            completed = True
            final = event.get("response") if isinstance(event.get("response"), dict) else {}
            input_tokens, output_tokens = _response_usage(final)
            yield from close_blocks()
            stop_reason = "tool_use" if saw_tool else "end_turn"
            if final.get("status") == "incomplete":
                details = final.get("incomplete_details") or {}
                if isinstance(details, dict) and details.get("reason") == "max_output_tokens":
                    stop_reason = "max_tokens"
            yield _sse(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                    "usage": {"output_tokens": output_tokens},
                },
            )
            yield _sse("message_stop", {"type": "message_stop"})
            break
        elif event_type in {"response.failed", "error"}:
            error = event.get("error")
            if not isinstance(error, dict):
                failed = event.get("response")
                error = failed.get("error") if isinstance(failed, dict) else {}
            message = str((error or {}).get("message") or "OpenAI Responses request failed")
            yield _sse(
                "error",
                {"type": "error", "error": {"type": "api_error", "message": message[:4096]}},
            )
            return

    if not completed:
        yield from close_blocks()
        yield _sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use" if saw_tool else "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": output_tokens},
            },
        )
        yield _sse("message_stop", {"type": "message_stop"})


def estimate_tokens(body: dict[str, Any]) -> int:
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return max(1, (len(raw) + 3) // 4)


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address,
        handler,
        config: dict[str, Any],
        api_key: str,
        codex_auth: CodexAuthStore | None = None,
    ):
        super().__init__(address, handler)
        self.config = config
        self.api_key = api_key
        self.codex_auth = codex_auth
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler(),
            NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )


class GatewayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FinalKitWindowsClaude/1"
    sys_version = ""

    @property
    def cfg(self) -> dict[str, Any]:
        return self.server.config  # type: ignore[attr-defined]

    @property
    def prefix(self) -> str:
        return "/" + self.cfg["path_secret"]

    def log_message(self, format_string: str, *args: Any) -> None:
        return

    def host_allowed(self) -> bool:
        host = self.headers.get("Host", "").lower()
        port = self.cfg["port"]
        return host in {f"127.0.0.1:{port}", f"localhost:{port}", "127.0.0.1", "localhost"}

    def local_path(self) -> str | None:
        parsed = urllib.parse.urlsplit(self.path)
        candidate = parsed.path[: len(self.prefix)]
        if not hmac.compare_digest(candidate, self.prefix):
            return None
        remainder = parsed.path[len(self.prefix) :]
        return remainder if remainder.startswith("/") else None

    def client_authorized(self) -> bool:
        expected = self.cfg["client_token"]
        supplied = self.headers.get("Authorization", "")
        if supplied.startswith("Bearer "):
            supplied = supplied[7:]
        elif self.headers.get("x-api-key"):
            supplied = self.headers.get("x-api-key", "")
        return hmac.compare_digest(str(supplied), str(expected))

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def reject(self, status: int, message: str, error_type: str = "invalid_request_error") -> None:
        self.send_json(status, {"type": "error", "error": {"type": error_type, "message": message}})

    def read_json(self) -> dict[str, Any] | None:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            self.reject(411, "Content-Length is required")
            return None
        try:
            length = int(raw_length)
        except ValueError:
            self.reject(400, "invalid Content-Length")
            return None
        if not 0 <= length <= MAX_REQUEST_BYTES:
            self.reject(413, "request exceeds the 64 MiB limit")
            return None
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.reject(400, "request body must be valid JSON")
            return None
        if not isinstance(body, dict):
            self.reject(400, "request body must be a JSON object")
            return None
        return body

    def _admit(self) -> str | None:
        if not self.host_allowed():
            self.reject(400, "invalid Host header")
            return None
        path = self.local_path()
        if path is None:
            self.reject(404, "not found")
            return None
        if not self.client_authorized():
            self.reject(401, "invalid Windows Claude loopback credential", "authentication_error")
            return None
        return path

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        path = self._admit()
        if path is None:
            return
        if path == "/health":
            routes = {
                role: {
                    "model": self.cfg[f"model_{role}"],
                    "reasoning": self.cfg[f"reasoning_{role}"],
                }
                for role in ROLES
            }
            self.send_json(
                200,
                {
                    "status": "ok",
                    "owner": "ScienceCodexFinalKit-WindowsClaude",
                    "profile": self.cfg["profile"],
                    "profile_id": self.cfg["profile_id"],
                    "instance_id": self.cfg["instance_id"],
                    "pid": os.getpid(),
                    "host": "127.0.0.1",
                    "port": self.cfg["port"],
                    "protocol": self.cfg["protocol"],
                    "auth_owner": "windows-codex-cli" if self.cfg["profile"] == "codex" else "windows-dpapi",
                    "routes": routes,
                },
            )
            return
        if path in {"/v1/models", "/api/models"}:
            models = []
            for alias in CLAUDE_ALIASES:
                target, effort = _route_for(self.cfg, alias)
                effort_label = f" | reasoning={effort}" if effort else ""
                models.append(
                    {
                        "id": alias,
                        "type": "model",
                        "display_name": f"{self.cfg['profile_name']} | {target}{effort_label}",
                    }
                )
            self.send_json(200, {"data": models, "has_more": False})
            return
        self.reject(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        path = self._admit()
        if path is None:
            return
        if path == "/control/stop":
            supplied = self.headers.get("X-FinalKit-Control", "")
            if not hmac.compare_digest(supplied, self.cfg["control_secret"]):
                self.reject(403, "invalid control credential", "permission_error")
                return
            self.send_json(200, {"status": "stopping", "instance_id": self.cfg["instance_id"]})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if path == "/v1/messages/count_tokens":
            body = self.read_json()
            if body is not None:
                self.send_json(200, {"input_tokens": estimate_tokens(body)})
            return
        if path != "/v1/messages":
            self.reject(404, "not found")
            return
        body = self.read_json()
        if body is None:
            return
        if self.cfg.get("offline_smoke"):
            self.reject(503, "offline smoke mode does not contact an upstream API")
            return
        original_model = str(body.get("model", CLAUDE_ALIASES[1]))
        body["model"], reasoning = _route_for(self.cfg, original_model)
        if self.cfg["protocol"] == "anthropic-messages":
            body = apply_provider_reasoning(body, str(self.cfg["profile"]), reasoning)
            self.forward_anthropic(body)
        else:
            self.forward_responses(body, original_model, reasoning)

    def _open(self, request: urllib.request.Request):
        try:
            return self.server.opener.open(request, timeout=300)  # type: ignore[attr-defined]
        except urllib.error.HTTPError as exc:
            return exc

    def forward_anthropic(self, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            self.reject(413, "normalized request exceeds the 64 MiB limit")
            return
        headers = {
            "Content-Type": "application/json",
            "Accept": self.headers.get("Accept", "application/json"),
            "User-Agent": "ScienceCodexFinalKit-WindowsClaude/1",
            "Anthropic-Version": self.headers.get("Anthropic-Version", "2023-06-01"),
        }
        beta = self.headers.get("Anthropic-Beta")
        if beta:
            headers["Anthropic-Beta"] = beta
        if self.cfg["auth_style"] == "bearer":
            headers["Authorization"] = f"Bearer {self.server.api_key}"  # type: ignore[attr-defined]
        else:
            headers["x-api-key"] = self.server.api_key  # type: ignore[attr-defined]
        request = urllib.request.Request(
            _join_anthropic_url(self.cfg["upstream"]), data=encoded, headers=headers, method="POST"
        )
        response = None
        response_started = False
        try:
            response = self._open(request)
            status = int(getattr(response, "status", response.code))
            self.send_response(status)
            for name in (
                "Content-Type",
                "Content-Encoding",
                "Content-Length",
                "Retry-After",
                "Request-Id",
                "X-Request-Id",
                "Anthropic-Request-Id",
            ):
                value = response.headers.get(name)
                if value:
                    self.send_header(name, value)
            if not response.headers.get("Content-Length"):
                self.send_header("Connection", "close")
                self.close_connection = True
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            response_started = True
            while True:
                chunk = response.read(READ_CHUNK_BYTES)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if response_started:
                self.close_connection = True
            else:
                self.reject(502, f"provider upstream connection failed: {type(exc).__name__}", "api_error")
        finally:
            if response is not None:
                response.close()

    def _openai_error(self, response) -> None:
        status = int(getattr(response, "status", response.code))
        raw = response.read(MAX_ERROR_BYTES + 1)
        if len(raw) > MAX_ERROR_BYTES:
            raw = raw[:MAX_ERROR_BYTES]
        message = f"Codex Responses upstream returned HTTP {status}"
        try:
            payload = json.loads(raw)
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict) and error.get("message"):
                message = str(error["message"])
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        self.reject(status, message[:4096], "api_error")

    def forward_responses(
        self, body: dict[str, Any], original_model: str, reasoning_effort: str
    ) -> None:
        payload, reverse_names = anthropic_to_responses(
            body, body["model"], reasoning_effort
        )
        client_stream = bool(payload["stream"])
        # The ChatGPT-account backend reliably exposes the Responses protocol
        # as SSE.  Aggregate its final response locally when Claude requested a
        # non-streaming Anthropic response.
        payload["stream"] = True
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            self.reject(413, "normalized request exceeds the 64 MiB limit")
            return
        response = None
        response_started = False
        try:
            codex_auth = self.server.codex_auth  # type: ignore[attr-defined]
            if codex_auth is None:
                raise CodexAuthError("Windows Codex auth owner is unavailable; run: codex login")
            rejected_token = ""
            for attempt in range(2):
                headers = codex_auth.headers(rejected_token)
                request = urllib.request.Request(
                    _join_responses_url(self.cfg["upstream"]), data=encoded, headers=headers, method="POST"
                )
                response = self._open(request)
                status = int(getattr(response, "status", response.code))
                if status == 401 and attempt == 0:
                    rejected_token = headers["Authorization"].removeprefix("Bearer ")
                    response.close()
                    response = None
                    continue
                if status in {502, 503, 504} and attempt == 0:
                    response.close()
                    response = None
                    continue
                break
            if not 200 <= status < 300:
                self._openai_error(response)
                return
            if client_stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                response_started = True
                self.close_connection = True
                for event in translate_responses_stream(response, original_model, reverse_names):
                    self.wfile.write(event)
                    self.wfile.flush()
            else:
                result = collect_responses_stream(response)
                self.send_json(200, responses_to_anthropic(result, original_model, reverse_names))
        except CodexAuthError as exc:
            if response_started:
                self.close_connection = True
            else:
                self.reject(401, str(exc), "authentication_error")
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError, GatewayError) as exc:
            if response_started:
                self.close_connection = True
            else:
                self.reject(502, f"OpenAI Responses request failed: {type(exc).__name__}", "api_error")
        finally:
            if response is not None:
                response.close()


def configure_log(path: Path | None) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a", encoding="utf-8", buffering=1)
    sys.stdout = handle
    sys.stderr = handle


def main() -> int:
    parser = argparse.ArgumentParser(description="FinalKit Windows Claude API gateway")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log-file", type=Path)
    args = parser.parse_args()
    configure_log(args.log_file)
    try:
        config = load_config(args.config.resolve())
        codex_auth = None
        if config["profile"] == "codex":
            api_key = ""
            codex_auth = CodexAuthStore(Path(config["codex_auth_file"]))
            codex_auth.assert_configured()
        else:
            api_key = read_api_key()
        server = GatewayServer(
            (config["host"], config["port"]), GatewayHandler, config, api_key, codex_auth
        )
    except Exception as exc:  # startup must leave a durable, credential-free diagnostic
        print(f"STARTUP_ERROR={type(exc).__name__}: {exc}", flush=True)
        return 1

    def stop_server(_signum, _frame) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)
    print(
        f"WINDOWS_CLAUDE_GATEWAY_READY profile={config['profile']} "
        f"pid={os.getpid()} port={config['port']} instance={config['instance_id']}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        server.api_key = ""
        server.codex_auth = None
        print(f"WINDOWS_CLAUDE_GATEWAY_STOPPED instance={config['instance_id']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
