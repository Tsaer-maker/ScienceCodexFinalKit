#!/usr/bin/env python3
"""Offline contract for FinalKit's restart-stable local Science identity."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import stat
import sys
import tempfile
import uuid
from pathlib import Path

from cryptography.fernet import Fernet


def load_helper(path: Path):
    spec = importlib.util.spec_from_file_location("finalkit_science_identity_contract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import Science identity helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_keys(module, data_dir: Path) -> dict[str, str]:
    keys = {
        name: base64.b64encode(os.urandom(32)).decode("ascii")
        for name in module.KEY_NAMES
    }
    path = data_dir / "encryption.key"
    path.write_text("".join(f"{name}={keys[name]}\n" for name in module.KEY_NAMES))
    path.chmod(0o600)
    return keys


def legacy_identity(module, data_dir: Path, key: str) -> None:
    token_dir = data_dir / ".oauth-tokens"
    token_dir.mkdir(mode=0o700)
    payload = {
        "access_token": module.LEGACY_ACCESS,
        "refresh_token": module.LEGACY_REFRESH,
        "api_key": None,
        "token_expires_at": "2099-12-31T23:59:59Z",
        "provider": "anthropic",
        "scopes": "openid profile email",
        "email": module.LEGACY_EMAIL,
        "account_uuid": module.LEGACY_ACCOUNT,
        "subscription_type": "max",
        "rate_limit_tier": "tier_5",
        "seat_tier": "enterprise_usage_based",
        "org_uuid": module.LEGACY_ORG,
        "billing_type": "api",
        "has_extra_usage_enabled": True,
    }
    encrypted = Fernet(key.encode("ascii")).encrypt(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    path = token_dir / f"{module.LEGACY_ACCOUNT}.enc"
    path.write_bytes(encrypted)
    path.chmod(0o600)
    journal = token_dir / f"{module.LEGACY_ACCOUNT}.refresh-journal"
    journal.write_text("legacy-invalid-grant\n", encoding="utf-8")
    journal.chmod(0o600)


def legacy_v2_identity(module, data_dir: Path, key: str) -> None:
    token_dir = data_dir / ".oauth-tokens"
    token_dir.mkdir(mode=0o700)
    account = str(uuid.uuid4())
    org = str(uuid.uuid4())
    payload = {
        "access_token": f"sk-ant-virtual-{uuid.uuid4().hex}",
        "refresh_token": "",
        "api_key": None,
        "token_expires_at": "2099-01-01T00:00:00.000Z",
        "provider": "claude_ai",
        "email": module.VIRTUAL_EMAIL,
        "account_uuid": account,
        "subscription_type": "max",
        "org_uuid": org,
    }
    path = token_dir / f"{account}.enc"
    path.write_bytes(
        module._encrypt_v2(
            json.dumps(payload, separators=(",", ":")).encode("utf-8"), key
        )
    )
    path.chmod(0o600)
    active = data_dir / "active-org.json"
    active.write_text(json.dumps({"org_uuid": org}), encoding="utf-8")
    active.chmod(0o600)


def verify(helper_path: Path) -> None:
    module = load_helper(helper_path)
    with tempfile.TemporaryDirectory(prefix="finalkit-science-identity-") as temporary:
        root = Path(temporary)

        created_dir = root / "created"
        created_dir.mkdir(mode=0o700)
        write_keys(module, created_dir)
        created = module.ensure_identity(created_dir)
        assert created["action"] == "created" and created["schema"] == "science-local-v2"
        assert created["authenticated"] is True and created["refresh_disabled"] is True
        token_file = created_dir / ".oauth-tokens" / f"{module.LOCAL_ACCOUNT_UUID}.enc"
        active_file = created_dir / "active-org.json"
        before_token = token_file.read_bytes()
        before_active = active_file.read_bytes()
        reused = module.ensure_identity(created_dir)
        assert reused["action"] == "reused"
        assert reused["schema"] == "science-local-v2"
        assert token_file.read_bytes() == before_token
        assert active_file.read_bytes() == before_active

        legacy_dir = root / "legacy"
        legacy_dir.mkdir(mode=0o700)
        keys = write_keys(module, legacy_dir)
        legacy_identity(module, legacy_dir, keys["OAUTH_ENCRYPTION_KEY"])
        legacy_status = module.inspect_identity(legacy_dir)
        assert legacy_status["schema"] == "science-local-legacy"
        assert legacy_status["migration_required"] is True
        migrated = module.ensure_identity(legacy_dir)
        assert migrated["action"] == "migrated-science-local-legacy"
        assert module.inspect_identity(legacy_dir)["schema"] == "science-local-v2"

        v2_dir = root / "v2"
        v2_dir.mkdir(mode=0o700)
        keys = write_keys(module, v2_dir)
        legacy_v2_identity(module, v2_dir, keys["OAUTH_ENCRYPTION_KEY"])
        v2_status = module.inspect_identity(v2_dir)
        assert v2_status["schema"] == "science-local-v2-legacy"
        assert v2_status["migration_required"] is True
        migrated = module.ensure_identity(v2_dir)
        assert migrated["action"] == "migrated-science-local-v2-legacy"
        assert module.inspect_identity(v2_dir)["schema"] == "science-local-v2"

        unknown_dir = root / "unknown"
        unknown_dir.mkdir(mode=0o700)
        write_keys(module, unknown_dir)
        unknown_tokens = unknown_dir / ".oauth-tokens"
        unknown_tokens.mkdir(mode=0o700)
        unknown = unknown_tokens / "real-or-unknown.enc"
        unknown.write_bytes(b"do-not-overwrite")
        unknown.chmod(0o600)
        before_unknown = unknown.read_bytes()
        preserved = module.ensure_identity(unknown_dir)
        assert preserved["schema"] == "science-credentials-preserved"
        assert preserved["action"] == "reused"
        assert unknown.read_bytes() == before_unknown
        assert not (unknown_dir / "active-org.json").exists()

    print(
        "SCIENCE_IDENTITY_CONTRACT_OK empty=created reuse=byte-stable "
        "legacy=migrated v2=migrated unknown=preserved"
    )


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: science_identity_contract.py /path/to/science_identity.py", file=sys.stderr)
        return 2
    verify(Path(sys.argv[1]).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
