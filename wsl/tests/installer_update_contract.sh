#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Offline rollback contract for install-final-stack.sh --runtime.  Every file
# lives under a temporary fixture; no WSL runtime, credential, process, or
# network endpoint is touched.

[[ $# -eq 2 ]] || {
  printf 'usage: installer_update_contract.sh /path/to/install-final-stack.sh /path/to/patched/proxy.py\n' >&2
  exit 2
}

installer="$(readlink -f "$1")"
installed_proxy="$(readlink -f "$2")"
# shellcheck disable=SC1090
BRIDGE_REF="refs/heads/untrusted-environment"
NODE_VERSION="v0.0.0"
CHROME_MCP_VERSION="0.0.0"
source "$installer"

is_managed_bridge_proxy "$MANAGED_BRIDGE_PROXY_SHA256_320" || {
  printf 'managed connector hash was not admitted\n' >&2
  exit 1
}
is_managed_bridge_proxy "$RECOVERY_BRIDGE_PROXY_SHA256_330" || {
  printf '3.3.0 recovery connector hash was not admitted\n' >&2
  exit 1
}
is_managed_bridge_proxy "$RECOVERY_BRIDGE_PROXY_SHA256_330_AUTO" || {
  printf '3.3.0 auto connector hash was not admitted\n' >&2
  exit 1
}
is_managed_bridge_proxy "$RECOVERY_BRIDGE_PROXY_SHA256_330_READONLY" || {
  printf '3.3.0 read-only connector hash was not admitted\n' >&2
  exit 1
}
is_managed_bridge_proxy "$RECOVERY_BRIDGE_PROXY_SHA256_330_MODEL_WIRE" || {
  printf '3.3.0 model-wire recovery connector hash was not admitted\n' >&2
  exit 1
}
is_managed_bridge_proxy "$MANAGED_BRIDGE_PROXY_SHA256_330" || {
  printf '3.3.0 managed connector hash was not admitted\n' >&2
  exit 1
}
if is_managed_bridge_proxy "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"; then
  printf 'unknown connector hash was admitted\n' >&2
  exit 1
fi
[[ "$BRIDGE_REF" == "30b26d7c6f097b186bbd228e93a427a731399960" ]] || {
  printf 'connector pin is not the release commit\n' >&2
  exit 1
}
[[ "$NODE_VERSION" == "v24.19.0" && "$CHROME_MCP_VERSION" == "1.2.0" ]] || {
  printf 'release dependency pins drifted\n' >&2
  exit 1
}

requirements_fixture="$(mktemp -d /tmp/finalkit-requirements-crlf.XXXXXX)"
printf 'Alpha==1\r\nBravo==2\r\n' >"$requirements_fixture/requirements.lock"
cat >"$requirements_fixture/pip" <<'PIP'
#!/usr/bin/env bash
case "${1:-}" in
  freeze) printf 'Bravo==2\nAlpha==1\n' ;;
  check) exit 0 ;;
  install) : >"${REQUIREMENTS_INSTALL_MARKER:?}" ;;
  *) exit 2 ;;
esac
PIP
chmod 700 "$requirements_fixture/pip"
export REQUIREMENTS_INSTALL_MARKER="$requirements_fixture/install-called"
connector_requirements_match \
  "$requirements_fixture/requirements.lock" "$requirements_fixture/pip" || {
  printf 'CRLF requirements did not match the exact installed environment\n' >&2
  exit 1
}
[[ ! -e "$REQUIREMENTS_INSTALL_MARKER" ]] || {
  printf 'matching CRLF requirements triggered an install path\n' >&2
  exit 1
}
rm -rf -- "$requirements_fixture"

# The preceding real installer path produced this exact pinned-commit + patch
# owner. Reverse-apply alone is insufficient: an additional line in the same
# proxy must fail the final release hash gate.
verify_bridge_proxy_hash "$installed_proxy"
hash_fixture="$(mktemp -d /tmp/finalkit-connector-hash.XXXXXX)"
cp -- "$installed_proxy" "$hash_fixture/proxy.py"
printf '\n# unknown credential-path edit\n' >>"$hash_fixture/proxy.py"
if (verify_bridge_proxy_hash "$hash_fixture/proxy.py" >/dev/null 2>&1); then
  printf 'connector hash gate admitted an extra same-file edit\n' >&2
  exit 1
fi
rm -rf -- "$hash_fixture"

fixture="$(mktemp -d /tmp/finalkit-installer-update.XXXXXX)"
trap 'rm -rf -- "${fixture:-}"' EXIT
test_home="$fixture/home"
test_root="$test_home/.local/share/science-codex-finalkit"
export FINALKIT_ROOT="$test_root"
mkdir -p \
  "$test_home/.local/bin" "$test_root/runtime" "$test_root/bridge/.git" \
  "$test_root/config"

getent() {
  if [[ "${1:-}" == "passwd" ]]; then
    printf 'contract-user:x:12345:12345::%s:/bin/bash\n' "$test_home"
  else
    command getent "$@"
  fi
}

id() {
  if [[ "${1:-}" == "-un" ]]; then
    printf 'contract-user\n'
  else
    command id "$@"
  fi
}

verify_ubuntu() { :; }

targets=(
  "$test_root/runtime/direct_gateway.py"
  "$test_root/runtime/science_identity.py"
  "$test_root/runtime/switch_manager.py"
  "$test_root/bridge/proxy.py"
  "$test_root/bridge.commit"
  "$test_root/bridge.requirements.resolved.txt"
  "$test_root/versions.txt"
  "$test_home/.local/bin/fkctl"
  "$test_home/.local/bin/chrome-devtools-mcp-finalkit"
)
state_targets=(
  "$test_root/bridge/config.json"
  "$test_root/config/model-routes.json"
  "$test_home/.science-finalkit/.codex/auth.json"
  "$test_home/.finalkit-client/.codex/auth.json"
)
new_runtime_target="$test_root/runtime/agents_manager.py"
rollback_signal_marker="$fixture/rollback-signal-injected"

for target in "${targets[@]}"; do
  mkdir -p "$(dirname "$target")"
  printf 'before:%s\n' "$(basename "$target")" >"$target"
  chmod 700 "$target"
done
for target in "${state_targets[@]}"; do
  mkdir -p "$(dirname "$target")"
  printf 'before-state:%s\n' "$(basename "$target")" >"$target"
  chmod 600 "$target"
done

before_manifest="$fixture/before.sha256"
before_modes="$fixture/before.modes"
sha256sum "${targets[@]}" >"$before_manifest"
stat -c '%a %n' "${targets[@]}" >"$before_modes"

# Inject both TERM and INT at the first restore copy. The update EXIT trap must
# already have masked them, otherwise the managed owner set is left partial.
cp() {
  if [[
    ! -e "$rollback_signal_marker" &&
    -n "${FINALKIT_RUNTIME_UPDATE_BACKUP:-}" &&
    "${3:-}" == "$FINALKIT_RUNTIME_UPDATE_BACKUP/files/"*
  ]]; then
    : >"$rollback_signal_marker"
    kill -TERM "$BASHPID"
    kill -INT "$BASHPID"
  fi
  command cp "$@"
}

install_user() {
  local target
  for target in "${targets[@]}"; do
    printf 'failed-update:%s\n' "$(basename "$target")" >"$target"
  done
  printf 'failed-update:agents_manager.py\n' >"$new_runtime_target"
  # Model/config/auth writes here model another terminal (and the official
  # Codex CLI) committing while the updater is in flight. Runtime rollback
  # must not own or overwrite any of this user state.
  for target in "${state_targets[@]}"; do
    printf 'concurrent-state:%s\n' "$(basename "$target")" >"$target"
  done
  return 73
}

set +e
(
  set -e
  update_runtime
)
failure_status=$?
set -e
[[ "$failure_status" -eq 73 ]] || {
  printf 'expected update failure 73; got %s\n' "$failure_status" >&2
  exit 1
}
[[ -f "$rollback_signal_marker" ]] || {
  printf 'rollback signal injection was not exercised\n' >&2
  exit 1
}
sha256sum --check --status "$before_manifest" || {
  printf 'runtime update did not restore the exact managed file set\n' >&2
  exit 1
}
[[ ! -e "$new_runtime_target" ]] || {
  printf 'runtime update did not remove a newly introduced runtime owner\n' >&2
  exit 1
}
for target in "${state_targets[@]}"; do
  grep -q '^concurrent-state:' "$target" || {
    printf 'runtime rollback overwrote newer state: %s\n' "$target" >&2
    exit 1
  }
done
after_modes="$fixture/after.modes"
stat -c '%a %n' "${targets[@]}" >"$after_modes"
cmp --silent "$before_modes" "$after_modes" || {
  printf 'runtime update did not restore managed file permissions\n' >&2
  exit 1
}

install_user() {
  local target
  for target in "${targets[@]}"; do
    printf 'successful-update:%s\n' "$(basename "$target")" >"$target"
  done
  printf 'successful-update:agents_manager.py\n' >"$new_runtime_target"
}
update_runtime
grep -q '^successful-update:' "$test_root/runtime/switch_manager.py"
grep -q '^successful-update:' "$new_runtime_target"

printf 'INSTALLER_UPDATE_CONTRACT_OK rollback=package-bytes+mode+signal-safe state=preserved connector-hash=exact success=committed\n'
