#!/usr/bin/env python3
"""Minimal loopback Anthropic gateway for approved Anthropic-compatible APIs.

FinalKit owns this implementation.  It deliberately does not expose a
dashboard or accept configuration over HTTP.  Runtime configuration and the
provider key arrive through inherited file descriptors, never argv or the
environment.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import signal
import ssl
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


MAX_REQUEST_BYTES = 64 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
FAKE_ACCOUNT_ID = "finalkit-byok-user"
FAKE_ORG_ID = "finalkit-byok-org"
PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek",
        "upstream": "https://api.deepseek.com/anthropic",
        "auth": "x-api-key",
    },
    "kimi": {
        "label": "Kimi",
        "upstream": "https://api.moonshot.ai/anthropic",
        "auth": "bearer",
    },
    "glm": {
        "label": "GLM",
        "upstream": "https://open.bigmodel.cn/api/anthropic",
        "auth": "x-api-key",
    },
}


def read_private_fd(fd: int, *, limit: int) -> bytes:
    """Read a bounded private value and close the inherited descriptor."""

    with os.fdopen(fd, "rb", closefd=True) as handle:
        value = handle.read(limit + 1)
    if len(value) > limit:
        raise ValueError("private input exceeded its size limit")
    return value


def load_runtime(config_fd: int, key_fd: int) -> tuple[dict[str, Any], str]:
    config_raw = read_private_fd(config_fd, limit=64 * 1024)
    key_raw = read_private_fd(key_fd, limit=16 * 1024)
    config = json.loads(config_raw.decode("utf-8"))
    if not isinstance(config, dict):
        raise ValueError("runtime config must be a JSON object")

    required = {
        "host",
        "port",
        "path_secret",
        "provider",
        "upstream",
        "model_default",
        "model_fast",
        "instance_id",
        "profile_id",
    }
    missing = sorted(required - set(config))
    if missing:
        raise ValueError(f"runtime config is missing: {', '.join(missing)}")

    if config["host"] != "127.0.0.1":
        raise ValueError("the direct gateway may bind only to 127.0.0.1")
    port = int(config["port"])
    if not (1024 <= port <= 65535):
        raise ValueError("port is outside the allowed range")
    config["port"] = port

    secret = str(config["path_secret"])
    if len(secret) < 32 or not secret.replace("-", "").replace("_", "").isalnum():
        raise ValueError("path secret is malformed")

    provider = str(config["provider"])
    if provider not in PROVIDERS:
        raise ValueError("provider is not in FinalKit's fixed allowlist")
    expected = PROVIDERS[provider]
    upstream = urllib.parse.urlsplit(str(config["upstream"]))
    expected_upstream = urllib.parse.urlsplit(expected["upstream"])
    if (
        upstream.scheme != "https"
        or upstream.hostname != expected_upstream.hostname
        or upstream.path.rstrip("/") != expected_upstream.path.rstrip("/")
        or upstream.query
        or upstream.fragment
        or str(config["upstream"]).rstrip("/") != expected["upstream"]
    ):
        raise ValueError(f"upstream does not match the official {expected['label']} Anthropic endpoint")
    config["provider_label"] = expected["label"]
    config["auth_style"] = expected["auth"]

    key = key_raw.decode("utf-8").strip()
    if not key or "\n" in key or "\r" in key:
        raise ValueError("provider key is empty or malformed")
    return config, key


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Return 3xx responses to the caller without forwarding credentials."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def fake_org() -> dict[str, Any]:
    return {
        "id": FAKE_ORG_ID,
        "uuid": FAKE_ORG_ID,
        "name": "FinalKit BYOK",
        "type": "organization",
        "status": "active",
        "default_role": "admin",
        "subscription": {"type": "api", "status": "active"},
        "rate_limit_tier": "api",
        "billing_type": "api",
    }


def fake_user() -> dict[str, Any]:
    return {
        "id": FAKE_ACCOUNT_ID,
        "uuid": FAKE_ACCOUNT_ID,
        "sub": FAKE_ACCOUNT_ID,
        "email": "byok@localhost",
        "email_verified": True,
        "name": "FinalKit BYOK User",
        "organization": fake_org(),
        "organization_uuid": FAKE_ORG_ID,
        "org_uuid": FAKE_ORG_ID,
        "subscription_type": "api",
        "rate_limit_tier": "api",
        "seat_tier": "api",
        "billing_type": "api",
        "has_extra_usage_enabled": True,
    }


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True
    # Transactional mode switches reuse the same verified loopback port.  This
    # permits rebinding sockets in TIME_WAIT but never takes over a live listener.
    allow_reuse_address = True

    def __init__(self, address, handler, config: dict[str, Any], api_key: str):
        super().__init__(address, handler)
        self.config = config
        self.api_key = api_key
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler(),
            NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )


class GatewayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FinalKitGateway/2"
    sys_version = ""

    @property
    def cfg(self) -> dict[str, Any]:
        return self.server.config  # type: ignore[attr-defined]

    @property
    def prefix(self) -> str:
        return "/" + str(self.cfg["path_secret"])

    def log_message(self, format_string: str, *args: Any) -> None:
        # The URL contains a bearer-like path secret.  Never put it in logs.
        return

    def local_path(self) -> str | None:
        parsed = urllib.parse.urlsplit(self.path)
        raw_prefix = parsed.path[: len(self.prefix)]
        if not hmac.compare_digest(raw_prefix, self.prefix):
            return None
        remainder = parsed.path[len(self.prefix) :]
        return remainder if remainder.startswith("/") else None

    def host_allowed(self) -> bool:
        host = self.headers.get("Host", "")
        port = self.cfg["port"]
        return host in {
            f"127.0.0.1:{port}",
            f"localhost:{port}",
            "127.0.0.1",
            "localhost",
        }

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def reject(self, status: int, message: str) -> None:
        self.send_json(status, {"type": "error", "error": {"type": "api_error", "message": message}})

    def do_GET(self) -> None:  # noqa: N802
        if not self.host_allowed():
            self.reject(400, "invalid Host header")
            return
        path = self.local_path()
        if path is None:
            self.reject(404, "not found")
            return

        if path == "/health":
            self.send_json(
                200,
                {
                    "status": "ok",
                    "finalkit_instance": self.cfg["instance_id"],
                    "finalkit_backend": self.cfg["provider"],
                    "profile_id": self.cfg["profile_id"],
                    "upstream_host": urllib.parse.urlsplit(self.cfg["upstream"]).hostname,
                    "model_default": self.cfg["model_default"],
                    "model_fast": self.cfg["model_fast"],
                    "offline_smoke": bool(self.cfg.get("offline_smoke", False)),
                },
            )
            return

        if path.startswith("/v1/oauth/"):
            self.send_json(
                200,
                {
                    "token_type": "bearer",
                    "access_token": "finalkit-local-token",
                    "refresh_token": "finalkit-local-refresh",
                    "expires_in": 2_147_483_647,
                    "scope": "openid profile email",
                },
            )
            return

        if path == "/v1/models":
            models = [
                {
                    "id": "claude-opus-4-8",
                    "type": "model",
                    "display_name": f"{self.cfg['provider_label']} via {self.cfg['model_default']}",
                },
                {
                    "id": "claude-sonnet-4-5",
                    "type": "model",
                    "display_name": f"{self.cfg['provider_label']} via {self.cfg['model_default']}",
                },
                {
                    "id": "claude-haiku-4-5-20251001",
                    "type": "model",
                    "display_name": f"{self.cfg['provider_label']} via {self.cfg['model_fast']}",
                },
            ]
            self.send_json(
                200,
                {
                    "data": models,
                    "has_more": False,
                    "first_id": models[0]["id"],
                    "last_id": models[-1]["id"],
                },
            )
            return

        if path in {"/v1/userinfo", "/v1/me", "/v1/user", "/v1/profile", "/v1/account"}:
            self.send_json(200, fake_user())
            return

        if path in {"/v1/organization"} or path.startswith("/v1/organizations/"):
            self.send_json(200, fake_org())
            return

        if path == "/v1/organizations":
            org = fake_org()
            self.send_json(
                200,
                {
                    **org,
                    "data": [org],
                    "organizations": [org],
                    "has_more": False,
                    "first_id": org["id"],
                    "last_id": org["id"],
                },
            )
            return

        self.reject(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        if not self.host_allowed():
            self.reject(400, "invalid Host header")
            return
        path = self.local_path()
        if path is None:
            self.reject(404, "not found")
            return

        if path.startswith("/v1/oauth/"):
            self.send_json(
                200,
                {
                    "token_type": "bearer",
                    "access_token": "finalkit-local-token",
                    "refresh_token": "finalkit-local-refresh",
                    "expires_in": 2_147_483_647,
                    "scope": "openid profile email",
                },
            )
            return

        if path not in {"/v1/messages", "/v1/messages/count_tokens"}:
            self.reject(404, "not found")
            return
        self.forward_anthropic(path)

    def forward_anthropic(self, path: str) -> None:
        if self.cfg.get("offline_smoke", False):
            self.reject(503, "offline smoke mode does not contact the provider")
            return
        length_raw = self.headers.get("Content-Length")
        if length_raw is None:
            self.reject(411, "Content-Length is required")
            return
        try:
            length = int(length_raw)
        except ValueError:
            self.reject(400, "invalid Content-Length")
            return
        if length < 0 or length > MAX_REQUEST_BYTES:
            self.reject(413, "request exceeds the 64 MiB limit")
            return

        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.reject(400, "request body must be valid JSON")
            return
        if not isinstance(body, dict):
            self.reject(400, "request body must be a JSON object")
            return

        if "model" in body:
            requested = str(body.get("model", ""))
            body["model"] = (
                self.cfg["model_fast"] if "haiku" in requested.lower() else self.cfg["model_default"]
            )
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            self.reject(413, "normalized request exceeds the 64 MiB limit")
            return

        upstream_base = str(self.cfg["upstream"]).rstrip("/")
        url = upstream_base + path
        headers = {
            "Content-Type": "application/json",
            "Accept": self.headers.get("Accept", "application/json"),
            "User-Agent": "ScienceCodexFinalKit/3",
        }
        if self.cfg["auth_style"] == "bearer":
            headers["Authorization"] = f"Bearer {self.server.api_key}"  # type: ignore[attr-defined]
        else:
            headers["x-api-key"] = self.server.api_key  # type: ignore[attr-defined]
        for name in ("Anthropic-Version", "Anthropic-Beta"):
            value = self.headers.get(name)
            if value:
                headers[name] = value

        request = urllib.request.Request(url, data=encoded, headers=headers, method="POST")
        response = None
        response_started = False
        try:
            try:
                response = self.server.opener.open(request, timeout=300)  # type: ignore[attr-defined]
            except urllib.error.HTTPError as exc:
                # HTTPError is also a readable response.  Preserve the status
                # and body, including redirects, without following them.
                response = exc
            status = int(getattr(response, "status", response.code))
            self.send_response(status)
            upstream_headers = response.headers
            for name in (
                "Content-Type",
                "Content-Encoding",
                "Content-Length",
                "Retry-After",
                "Request-Id",
                "X-Request-Id",
                "Anthropic-Request-Id",
            ):
                value = upstream_headers.get(name)
                if value:
                    self.send_header(name, value)
            if not upstream_headers.get("Content-Length"):
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
            elif not self.wfile.closed:
                try:
                    self.reject(502, f"provider upstream connection failed: {type(exc).__name__}")
                except (BrokenPipeError, ConnectionResetError):
                    pass
        finally:
            if response is not None:
                response.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="FinalKit direct provider gateway")
    parser.add_argument("--config-fd", type=int, required=True)
    parser.add_argument("--key-fd", type=int, required=True)
    args = parser.parse_args()

    os.umask(0o077)
    config, api_key = load_runtime(args.config_fd, args.key_fd)
    server = GatewayServer((config["host"], config["port"]), GatewayHandler, config, api_key)

    def stop_server(_signum, _frame) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)
    print(
        f"FinalKit {config['provider_label']} gateway ready on 127.0.0.1:{config['port']} "
        f"profile={config['profile_id']}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        server.api_key = ""
    return 0


if __name__ == "__main__":
    sys.exit(main())
