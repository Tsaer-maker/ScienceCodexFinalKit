#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Offline rollback contract for install-final-stack.sh --runtime.  Every file
# lives under a temporary fixture; no WSL runtime, credential, process, or
# network endpoint is touched.

[[ $# -eq 1 ]] || {
  printf 'usage: installer_update_contract.sh /path/to/install-final-stack.sh\n' >&2
  exit 2
}

installer="$(readlink -f "$1")"
# shellcheck disable=SC1090
source "$installer"

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
  "$test_root/bridge/config.json"
  "$test_root/config/model-routes.json"
  "$test_root/bridge.commit"
  "$test_root/bridge.requirements.resolved.txt"
  "$test_root/versions.txt"
  "$test_home/.local/bin/fkctl"
  "$test_home/.local/bin/chrome-devtools-mcp-finalkit"
)

for target in "${targets[@]}"; do
  mkdir -p "$(dirname "$target")"
  printf 'before:%s\n' "$(basename "$target")" >"$target"
  chmod 700 "$target"
done

before_manifest="$fixture/before.sha256"
before_modes="$fixture/before.modes"
sha256sum "${targets[@]}" >"$before_manifest"
stat -c '%a %n' "${targets[@]}" >"$before_modes"

install_user() {
  local target
  for target in "${targets[@]}"; do
    printf 'failed-update:%s\n' "$(basename "$target")" >"$target"
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
sha256sum --check --status "$before_manifest" || {
  printf 'runtime update did not restore the exact managed file set\n' >&2
  exit 1
}
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
}
update_runtime
grep -q '^successful-update:' "$test_root/runtime/switch_manager.py"

printf 'INSTALLER_UPDATE_CONTRACT_OK rollback=bytes+mode success=committed\n'
