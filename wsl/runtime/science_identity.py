#!/usr/bin/env python3
"""Audit FinalKit's isolated Claude Science credential boundary.

Claude Science 0.1.27 requires its own supported Claude account sign-in before
the web application will load provider models.  A DeepSeek/Kimi/GLM API key or
a ChatGPT/Codex login authenticates FinalKit's local gateway, not the Science
web application.  Older FinalKit builds wrote two exact synthetic OAuth
shapes; current Science refreshes those values against the real Claude OAuth
service and then signs the UI out.

This helper removes only those cryptographically recognized legacy FinalKit
identities.  An empty profile remains explicitly ``login-required`` and every
unknown or real credential is preserved byte-for-byte for Claude Science to
own.  FinalKit never manufactures a replacement account identity.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


KEY_NAMES = (
    "ANTHROPIC_API_KEY_ENCRYPTION_KEY",
    "OAUTH_ENCRYPTION_KEY",
    "JWT_SIGNING_SECRET",
    "USER_SECRET_ENCRYPTION_KEY",
)
HKDF_INFO = b"operon:aes-256-gcm:oauth"
OAUTH_AAD = b"v2:oauth"
VIRTUAL_EMAIL = "virtual@localhost.invalid"
LEGACY_ACCOUNT = "byok-user-000000000000000000"
LEGACY_ORG = "org_byok_000000000000"
LEGACY_EMAIL = "byok@localhost"
LEGACY_ACCESS = "fake-bearer-token-for-proxy"
LEGACY_REFRESH = "fake-refresh-token"
MAX_PRIVATE_BYTES = 1024 * 1024
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class IdentityError(RuntimeError):
    pass


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def _private_directory(path: Path, *, create: bool) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise IdentityError("Science data directory must be absolute and normalized")
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise IdentityError(f"Science data directory is missing: {path}") from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise IdentityError(f"Science data directory is not a real directory: {path}")
    if info.st_uid != os.geteuid():
        raise IdentityError(f"Science data directory is not owned by this Linux user: {path}")
    if _mode(path) & 0o077:
        raise IdentityError(f"Science data directory must not be group/other accessible: {path}")
    path.chmod(0o700)


def _read_private(path: Path, *, required: bool = True) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if required:
            raise IdentityError(f"private Science credential is missing: {path}")
        return None
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) & 0o077
        or info.st_size > MAX_PRIVATE_BYTES
    ):
        raise IdentityError(f"unsafe private Science credential: {path}")
    data = path.read_bytes()
    if len(data) > MAX_PRIVATE_BYTES:
        raise IdentityError(f"private Science credential is too large: {path}")
    return data


def _atomic_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _parse_keys(path: Path) -> dict[str, str]:
    raw = _read_private(path)
    assert raw is not None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IdentityError("Science encryption key file is not UTF-8") from exc
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        if not line:
            continue
        if "=" not in line:
            raise IdentityError("Science encryption key file has an invalid line")
        name, value = line.split("=", 1)
        if name not in KEY_NAMES or name in parsed:
            raise IdentityError("Science encryption key file has unknown or duplicate fields")
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise IdentityError("Science encryption key file has invalid base64") from exc
        if len(decoded) != 32:
            raise IdentityError("Science encryption keys must decode to 32 bytes")
        parsed[name] = value
    if tuple(parsed) != KEY_NAMES:
        raise IdentityError("Science encryption key file does not contain the four expected fields")
    path.chmod(0o600)
    return parsed


def _hkdf_sha256(ikm: bytes) -> bytes:
    prk = hmac.new(b"", ikm, hashlib.sha256).digest()
    return hmac.new(prk, HKDF_INFO + b"\x01", hashlib.sha256).digest()


def _encrypt_v2(plaintext: bytes, oauth_key_b64: str) -> bytes:
    ikm = base64.b64decode(oauth_key_b64, validate=True)
    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(_hkdf_sha256(ikm)).encrypt(nonce, plaintext, OAUTH_AAD)
    return ("v2:" + base64.b64encode(nonce + encrypted).decode("ascii")).encode("ascii")


def _decrypt_v2(body: bytes, oauth_key_b64: str) -> bytes:
    try:
        text = body.decode("ascii")
        if not text.startswith("v2:"):
            raise ValueError("not v2")
        framed = base64.b64decode(text[3:], validate=True)
        ikm = base64.b64decode(oauth_key_b64, validate=True)
        if len(framed) < 28:
            raise ValueError("short envelope")
        return AESGCM(_hkdf_sha256(ikm)).decrypt(
            framed[:12], framed[12:], OAUTH_AAD
        )
    except (UnicodeDecodeError, ValueError, InvalidTag) as exc:
        raise IdentityError("local Science v2 identity failed authenticated decryption") from exc


def _uuid4(value: Any) -> bool:
    return isinstance(value, str) and UUID_RE.fullmatch(value) is not None


def _token_files(token_dir: Path) -> list[Path]:
    try:
        info = token_dir.lstat()
    except FileNotFoundError:
        return []
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise IdentityError("unsafe Science OAuth token directory")
    files = sorted(token_dir.iterdir(), key=lambda item: item.name)
    return files


def _decode_json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IdentityError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise IdentityError(f"{label} is not a JSON object")
    return value


def _validate_current(data_dir: Path, keys: dict[str, str]) -> dict[str, str] | None:
    files = _token_files(data_dir / ".oauth-tokens")
    if not files:
        return None
    if len(files) != 1 or not files[0].name.endswith(".enc"):
        return None
    raw = _read_private(files[0])
    assert raw is not None
    if not raw.startswith(b"v2:"):
        return None
    try:
        token = _decode_json(
            _decrypt_v2(raw, keys["OAUTH_ENCRYPTION_KEY"]), "local Science v2 identity"
        )
        active_raw = _read_private(data_dir / "active-org.json")
        assert active_raw is not None
        active = _decode_json(active_raw, "Science active organization")
    except IdentityError:
        return None
    account = token.get("account_uuid")
    org = token.get("org_uuid")
    checks = (
        _uuid4(account),
        _uuid4(org),
        files[0].name == f"{account}.enc",
        active.get("org_uuid") == org,
        token.get("email") == VIRTUAL_EMAIL,
        token.get("provider") == "claude_ai",
        isinstance(token.get("access_token"), str)
        and token.get("access_token", "").startswith("sk-ant-virtual-"),
        token.get("refresh_token") == "",
        token.get("api_key") is None,
        token.get("token_expires_at") == "2099-01-01T00:00:00.000Z",
        token.get("subscription_type") == "max",
    )
    if not all(checks):
        return None
    return {"account_uuid": str(account), "org_uuid": str(org)}


def _is_exact_legacy(data_dir: Path, keys: dict[str, str]) -> bool:
    files = _token_files(data_dir / ".oauth-tokens")
    enc = [path for path in files if path.name.endswith(".enc")]
    journals = [path for path in files if path.name.endswith(".refresh-journal")]
    if len(enc) != 1 or len(journals) > 1:
        return False
    if enc[0].name != f"{LEGACY_ACCOUNT}.enc":
        return False
    if journals and journals[0].name != f"{LEGACY_ACCOUNT}.refresh-journal":
        return False
    raw = _read_private(enc[0])
    assert raw is not None
    try:
        decrypted = Fernet(keys["OAUTH_ENCRYPTION_KEY"].encode("ascii")).decrypt(raw)
    except (InvalidToken, ValueError):
        return False
    try:
        token = _decode_json(decrypted, "legacy FinalKit Science identity")
    except IdentityError:
        return False
    active = _read_private(data_dir / "active-org.json", required=False)
    return bool(
        token.get("account_uuid") == LEGACY_ACCOUNT
        and token.get("org_uuid") == LEGACY_ORG
        and token.get("email") == LEGACY_EMAIL
        and token.get("provider") == "anthropic"
        and token.get("access_token") == LEGACY_ACCESS
        and token.get("refresh_token") == LEGACY_REFRESH
        and token.get("api_key") is None
        and active is None
    )


def _remove_recognized_identity(data_dir: Path) -> None:
    """Atomically detach and then delete one recognized virtual identity."""

    token_dir = data_dir / ".oauth-tokens"
    suffix = secrets.token_hex(8)
    previous = data_dir / f".oauth-tokens.finalkit-prev-{suffix}"
    active_path = data_dir / "active-org.json"
    active_previous = data_dir / f".active-org.finalkit-prev-{suffix}.json"
    moved_tokens = False
    moved_active = False
    try:
        if token_dir.exists():
            os.replace(token_dir, previous)
            moved_tokens = True
        if active_path.exists():
            os.replace(active_path, active_previous)
            moved_active = True
    except BaseException:
        if moved_tokens and previous.exists() and not token_dir.exists():
            os.replace(previous, token_dir)
        if moved_active and active_previous.exists() and not active_path.exists():
            os.replace(active_previous, active_path)
        raise
    if previous.exists():
        for path in previous.iterdir():
            path.unlink()
        previous.rmdir()
    active_previous.unlink(missing_ok=True)


def inspect_identity(data_dir: Path) -> dict[str, Any]:
    _private_directory(data_dir, create=False)
    keys = _parse_keys(data_dir / "encryption.key")
    files = _token_files(data_dir / ".oauth-tokens")
    active = _read_private(data_dir / "active-org.json", required=False)
    if not files and active is None:
        return {
            "ok": True,
            "schema": "science-login-required",
            "authenticated": False,
        }
    current = _validate_current(data_dir, keys)
    if current is not None:
        return {
            "ok": True,
            "schema": "science-local-v2",
            "removal_required": True,
            **current,
        }
    if _is_exact_legacy(data_dir, keys):
        return {
            "ok": True,
            "schema": "science-local-legacy",
            "removal_required": True,
        }
    return {
        "ok": True,
        "schema": "science-credentials-preserved",
        "authenticated": None,
        "credential_files": len(files),
        "active_org_present": active is not None,
    }


def ensure_identity(data_dir: Path) -> dict[str, Any]:
    _private_directory(data_dir, create=True)
    status = inspect_identity(data_dir)
    schema = str(status.get("schema"))
    if schema in {"science-login-required", "science-credentials-preserved"}:
        return {**status, "action": "reused"}
    if schema not in {"science-local-legacy", "science-local-v2"}:
        raise IdentityError("unrecognized Science auth state; refusing overwrite")
    _remove_recognized_identity(data_dir)
    verified = inspect_identity(data_dir)
    if verified.get("schema") != "science-login-required":
        raise IdentityError("obsolete FinalKit Science identity removal did not verify")
    return {**verified, "action": f"removed-{schema}"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("ensure", "check"))
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = (
            ensure_identity(args.data_dir.resolve())
            if args.command == "ensure"
            else inspect_identity(args.data_dir.resolve())
        )
    except (IdentityError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
