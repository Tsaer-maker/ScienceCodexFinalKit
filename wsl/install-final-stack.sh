#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Science SwitchModel / FinalKit v3 installer.
# Windows invokes --system once as root and --user as the ordinary WSL user.
# It never creates a passwordless-sudo rule and never changes Windows/WSL proxy
# settings, .wslconfig, Docker, or another distribution.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Distribution metadata is read from the one source file.  It is never used as
# a runtime permission gate; command support is reported by fkctl capabilities.
PACKAGE_VERSION="$(tr -d '\r\n' <"$SCRIPT_DIR/../VERSION")"
[[ -n "$PACKAGE_VERSION" ]] || {
  printf 'ERROR: VERSION is empty\n' >&2
  exit 1
}
BRIDGE_REPO="https://github.com/haoyuan-sjtu/claude-science-codex-connector.git"
BRIDGE_REF="${BRIDGE_REF:-30b26d7c6f097b186bbd228e93a427a731399960}"
# Exact proxy.py files produced by the managed 3.0.3 through 3.0.7 patches at
# BRIDGE_REF. These hashes distinguish an upgradeable FinalKit owner from
# unknown local edits; no unrecognized connector file is overwritten.
LEGACY_BRIDGE_PROXY_SHA256="b2808deb29d5fa8d7a0f78e8134f0c7b3f59ba6a29cc78cf42bd79bc2bc957e7"
LEGACY_BRIDGE_PROXY_SHA256_304="b986db81f5f30ae1e8083da9f78681c8beafdba1fd439b29ce8fb3f640b7bd7f"
LEGACY_BRIDGE_PROXY_SHA256_306="d405d6a675f4844880aeec182cef6ed7bf424d5b13621f1aa3e12cae3c9d908d"
LEGACY_BRIDGE_PROXY_SHA256_307="4bd365339455b4a44338fe723b646259455a8f260e8aa98b14cc209a52d0a378"
NODE_VERSION="${NODE_VERSION:-v24.19.0}"
CHROME_MCP_VERSION="${CHROME_MCP_VERSION:-1.2.0}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

note() {
  printf '%s\n' "$*" >&2
}

without_proxy() {
  env \
    -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    -u http_proxy -u https_proxy -u all_proxy \
    "$@"
}

network_retry_direct() {
  if "$@"; then
    return 0
  fi
  note "Network command failed through the inherited proxy/environment; retrying this command directly."
  without_proxy "$@"
}

verify_ubuntu() {
  [[ -r /etc/os-release ]] || die "/etc/os-release is missing"
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ "${ID:-}" == "ubuntu" ]] || die "Ubuntu is required; found ${ID:-unknown}"
  [[ "${VERSION_ID:-}" == "24.04" ]] || die "Ubuntu 24.04 is required; found ${VERSION_ID:-unknown}"
  grep -qi microsoft /proc/version || note "Warning: the kernel does not identify itself as WSL."
}

install_system() {
  [[ "$EUID" -eq 0 ]] || die "--system must run as WSL root"
  verify_ubuntu
  local packages missing package status
  packages=(ca-certificates curl bubblewrap socat git jq python3 python3-venv python3-pip rsync xdg-utils xz-utils)
  missing=()
  for package in "${packages[@]}"; do
    status="$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)"
    [[ "$status" == "install ok installed" ]] || missing+=("$package")
  done
  if ((${#missing[@]} == 0)); then
    note "System dependencies are already ready; skipping apt network work."
    return
  fi
  export DEBIAN_FRONTEND=noninteractive
  network_retry_direct apt-get update
  network_retry_direct apt-get install -y --no-install-recommends \
    "${missing[@]}"
  apt-get clean
  note "System dependencies are ready."
}

download_and_run() {
  local url="$1"
  local label="$2"
  local mode="${3:-default}"
  local installer
  installer="$(mktemp --suffix=.sh)"
  trap 'rm -f "${installer:-}"' RETURN
  network_retry_direct curl -fL --retry 3 --connect-timeout 20 "$url" -o "$installer"
  printf '%s installer sha256: ' "$label"
  sha256sum "$installer" | awk '{print $1}'
  if [[ "$mode" == "codex" ]]; then
    CODEX_NON_INTERACTIVE=1 bash "$installer"
  elif [[ "$mode" == "claude" ]]; then
    bash "$installer" stable
  else
    bash "$installer"
  fi
  rm -f "$installer"
  trap - RETURN
}

install_node_and_browser_mcp() {
  local root="$1"
  local real_home="$2"
  local machine node_arch archive base_url checksum archive_path expected actual node_dir mcp_dir temp_dir
  machine="$(uname -m)"
  case "$machine" in
    x86_64) node_arch="x64" ;;
    aarch64|arm64) node_arch="arm64" ;;
    *) die "Unsupported Node.js architecture: $machine" ;;
  esac
  archive="node-${NODE_VERSION}-linux-${node_arch}.tar.xz"
  base_url="https://nodejs.org/dist/${NODE_VERSION}"
  node_dir="$root/node-${NODE_VERSION}"
  mcp_dir="$root/browser-mcp"
  if [[ ! -x "$node_dir/bin/node" ]]; then
    checksum="$(mktemp)"
    archive_path="$(mktemp --suffix=.tar.xz)"
    temp_dir="$(mktemp -d "$root/.node.XXXXXX")"
    trap 'rm -f "${checksum:-}" "${archive_path:-}"; [[ -z "${temp_dir:-}" ]] || rm -rf -- "$temp_dir"' RETURN
    network_retry_direct curl -fL --retry 3 --connect-timeout 20 "$base_url/SHASUMS256.txt" -o "$checksum"
    network_retry_direct curl -fL --retry 3 --connect-timeout 20 "$base_url/$archive" -o "$archive_path"
    expected="$(awk -v name="$archive" '$2 == name {print $1}' "$checksum")"
    [[ -n "$expected" ]] || die "Node.js checksum entry is missing for $archive"
    actual="$(sha256sum "$archive_path" | awk '{print $1}')"
    [[ "$actual" == "$expected" ]] || die "Node.js checksum verification failed"
    tar -xJf "$archive_path" --strip-components=1 -C "$temp_dir"
    mv -- "$temp_dir" "$node_dir"
    temp_dir=""
    rm -f "$checksum" "$archive_path"
    trap - RETURN
  fi
  ln -sfn "$node_dir/bin/node" "$real_home/.local/bin/node"
  ln -sfn "$node_dir/bin/npm" "$real_home/.local/bin/npm"
  ln -sfn "$node_dir/bin/npx" "$real_home/.local/bin/npx"
  ln -sfn "$node_dir" "$root/node-current"
  if [[ ! -x "$mcp_dir/node_modules/.bin/chrome-devtools-mcp" ]] || \
     [[ "$(jq -r '.version // empty' "$mcp_dir/node_modules/chrome-devtools-mcp/package.json" 2>/dev/null || true)" != "$CHROME_MCP_VERSION" ]]; then
    mkdir -p "$mcp_dir"
    network_retry_direct "$node_dir/bin/npm" --prefix "$mcp_dir" install --no-audit --no-fund \
      "chrome-devtools-mcp@$CHROME_MCP_VERSION"
  fi
  [[ -x "$mcp_dir/node_modules/.bin/chrome-devtools-mcp" ]] || die "Chrome DevTools MCP installation failed"
}

ensure_bridge_checkout() {
  local bridge_dir="$1"
  local origin current changed proxy_sha

  if [[ ! -e "$bridge_dir" ]]; then
    network_retry_direct git clone "$BRIDGE_REPO" "$bridge_dir"
  fi
  [[ -d "$bridge_dir/.git" ]] || die "Connector path is not a Git checkout: $bridge_dir"

  origin="$(git -C "$bridge_dir" remote get-url origin)"
  [[ "$origin" == "$BRIDGE_REPO" ]] || die "Unexpected connector origin: $origin"
  if ! git -C "$bridge_dir" cat-file -e "$BRIDGE_REF^{commit}" 2>/dev/null; then
    network_retry_direct git -C "$bridge_dir" fetch --no-tags origin "$BRIDGE_REF"
  else
    note "Pinned connector commit is already local; skipping git network work."
  fi
  current="$(git -C "$bridge_dir" rev-parse HEAD 2>/dev/null || true)"
  if [[ "$current" != "$BRIDGE_REF" ]]; then
    [[ -z "$(git -C "$bridge_dir" status --porcelain 2>/dev/null || true)" ]] || \
      die "Connector has local changes and cannot be pinned safely: $bridge_dir"
    git -C "$bridge_dir" checkout --detach "$BRIDGE_REF"
  fi

  if git -C "$bridge_dir" apply --reverse --check "$SCRIPT_DIR/connector-security.patch" >/dev/null 2>&1; then
    note "FinalKit connector security patch is already applied."
  else
    if ! git -C "$bridge_dir" apply --check "$SCRIPT_DIR/connector-security.patch" >/dev/null 2>&1; then
      changed="$(git -C "$bridge_dir" diff --name-only)"
      proxy_sha="$(sha256sum "$bridge_dir/proxy.py" | awk '{print $1}')"
      if [[ "$changed" == "proxy.py" ]] && \
         [[ "$proxy_sha" == "$LEGACY_BRIDGE_PROXY_SHA256" || \
            "$proxy_sha" == "$LEGACY_BRIDGE_PROXY_SHA256_304" || \
            "$proxy_sha" == "$LEGACY_BRIDGE_PROXY_SHA256_306" || \
            "$proxy_sha" == "$LEGACY_BRIDGE_PROXY_SHA256_307" ]]; then
        note "Upgrading the verified previous FinalKit connector owner to $PACKAGE_VERSION..."
        git -C "$bridge_dir" restore --source=HEAD --worktree -- proxy.py
      else
        die "Connector has unknown local changes; refusing to replace $bridge_dir/proxy.py"
      fi
    fi
    git -C "$bridge_dir" apply --check "$SCRIPT_DIR/connector-security.patch" || \
      die "Security patch does not match pinned connector commit $BRIDGE_REF"
    git -C "$bridge_dir" apply "$SCRIPT_DIR/connector-security.patch"
  fi
  git -C "$bridge_dir" diff --check
  changed="$(git -C "$bridge_dir" diff --name-only)"
  [[ "$changed" == "proxy.py" ]] || die "Unexpected patched connector files: ${changed:-none}"
}

generate_identity_files() {
  local root="$1"
  /usr/bin/python3 - "$root" <<'PY'
import os
import secrets
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1])
secrets_dir = root / "secrets"
secrets_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

def create_once(path: Path, value: str) -> None:
    if path.exists() and path.stat().st_size:
        os.chmod(path, 0o600)
        return
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

create_once(root / "instance.id", secrets.token_hex(16))
create_once(secrets_dir / "gateway-path.token", secrets.token_urlsafe(32))
create_once(secrets_dir / "connector-control.token", secrets.token_urlsafe(32))
PY
}

write_versions_metadata() {
  local root="$1" real_home="$2" science_bin="$3" claude_bin="$4" codex_bin="$5" runtime_version="$6"
  local versions_pending bridge_commit
  versions_pending="$root/.versions.txt.pending.$$"
  bridge_commit="$(tr -d '\r\n' <"$root/bridge.commit")"
  trap 'rm -f -- "${versions_pending:-}"' RETURN
  {
    printf 'package=Science SwitchModel / FinalKit\n'
    printf 'package_version=%s\n' "$runtime_version"
    printf 'installed_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'bridge_commit=%s\n' "$bridge_commit"
    printf 'claude_science='; "$science_bin" --version | head -n 1
    printf 'claude_code='; "$claude_bin" --version | head -n 1
    printf 'codex='; "$codex_bin" --version | head -n 1
    printf 'node='; "$real_home/.local/bin/node" --version
    printf 'chrome_devtools_mcp=%s\n' "$CHROME_MCP_VERSION"
    printf 'python='; python3 --version
  } >"$versions_pending"
  chmod 600 "$versions_pending"
  mv -f -- "$versions_pending" "$root/versions.txt"
  versions_pending=""
  trap - RETURN
}

install_user() {
  local install_mode="${1:-full}"
  [[ "$install_mode" == "full" || "$install_mode" == "runtime" ]] || die "invalid user install mode: $install_mode"
  [[ "$EUID" -ne 0 ]] || die "--user/--runtime must run as the ordinary WSL user"
  verify_ubuntu

  local real_home root bridge_dir science_bin claude_bin codex_bin science_home legacy_science_home
  real_home="$(getent passwd "$(id -un)" | cut -d: -f6)"
  [[ -n "$real_home" && "$real_home" != "/root" ]] || die "Could not resolve an ordinary-user home"
  export HOME="$real_home"
  export PATH="$real_home/.local/bin:$PATH"
  root="${FINALKIT_ROOT:-$real_home/.local/share/science-codex-finalkit}"
  bridge_dir="$root/bridge"
  science_home="$real_home/.science-finalkit"
  legacy_science_home="$root/science-home"
  science_bin="$real_home/.local/bin/claude-science"
  claude_bin="$real_home/.local/bin/claude"
  codex_bin="$real_home/.local/bin/codex"

  install -d -m 700 \
    "$real_home/.local/bin" "$root" "$root/runtime" "$root/run" \
    "$root/logs" "$root/secrets" "$root/profiles" "$root/config"

  if [[ -d "$legacy_science_home" && ! -e "$science_home" ]]; then
    note "Migrating the draft Science profile to the short AF_UNIX-safe path..."
    mv -- "$legacy_science_home" "$science_home"
  fi
  install -d -m 700 "$science_home"

  if [[ "$install_mode" == "full" && ( ! -x "$science_bin" || "${FINALKIT_UPGRADE_TOOLS:-0}" == "1" ) ]]; then
    note "Installing official Claude Science..."
    download_and_run "https://claude.ai/install-claude-science.sh" "Claude Science"
  fi
  [[ -x "$science_bin" ]] || die "Claude Science was not installed at $science_bin"

  if [[ "$install_mode" == "full" && ( ! -x "$claude_bin" || "${FINALKIT_UPGRADE_TOOLS:-0}" == "1" ) ]]; then
    note "Installing official native Linux Claude Code (stable)..."
    download_and_run "https://claude.ai/install.sh" "Claude Code" "claude"
  fi
  [[ -x "$claude_bin" ]] || die "Claude Code was not installed at $claude_bin"

  if [[ "$install_mode" == "full" && ( ! -x "$codex_bin" || "${FINALKIT_UPGRADE_TOOLS:-0}" == "1" ) ]]; then
    note "Installing official Linux Codex CLI..."
    download_and_run "https://chatgpt.com/codex/install.sh" "Codex CLI" "codex"
  fi
  [[ -x "$codex_bin" ]] || die "Codex CLI was not installed at $codex_bin"

  if [[ "$install_mode" == "full" ]]; then
    note "Installing pinned Node.js LTS and optional Chrome DevTools MCP bridge..."
    install_node_and_browser_mcp "$root" "$real_home"
  else
    [[ -x "$real_home/.local/bin/node" ]] || die "Node.js is missing; use --tools or the full Build"
    [[ -x "$real_home/.local/bin/chrome-devtools-mcp-finalkit" ]] || \
      die "FinalKit browser wrapper is missing; use the full Build once"
    [[ -d "$bridge_dir/.git" ]] || die "Connector checkout is missing; use the full Build once"
    git -C "$bridge_dir" cat-file -e "$BRIDGE_REF^{commit}" 2>/dev/null || \
      die "Pinned connector commit is not available locally; use the full Build with network access"
  fi

  note "Installing the pinned MIT ChatGPT/Codex connector..."
  ensure_bridge_checkout "$bridge_dir"
  printf '%s\n' "$BRIDGE_REF" >"$root/bridge.commit"

  if [[ ! -x "$bridge_dir/.venv/bin/python" ]]; then
    python3 -m venv "$bridge_dir/.venv"
  fi
  local expected_requirements installed_requirements
  # Source archives extracted on Windows may expose CRLF through /mnt/<drive>.
  # Compare package semantics, not the transport newline representation.
  expected_requirements="$(tr -d '\r' <"$SCRIPT_DIR/requirements.lock" | \
    sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' | LC_ALL=C sort -f)"
  installed_requirements="$("$bridge_dir/.venv/bin/pip" freeze | LC_ALL=C sort -f)"
  if [[ "$expected_requirements" == "$installed_requirements" ]] && \
     "$bridge_dir/.venv/bin/pip" check >/dev/null; then
    note "Pinned connector Python environment already matches; skipping pip network work."
  elif [[ "$install_mode" == "runtime" ]]; then
    die "Connector Python dependencies changed; run --tools or the full Build before the offline runtime update"
  else
    network_retry_direct "$bridge_dir/.venv/bin/pip" install \
      --disable-pip-version-check \
      --requirement "$SCRIPT_DIR/requirements.lock"
  fi
  "$bridge_dir/.venv/bin/pip" check
  "$bridge_dir/.venv/bin/pip" freeze >"$root/bridge.requirements.resolved.txt"

  note "Verifying the offline Codex route and model-catalog contract..."
  "$bridge_dir/.venv/bin/python" \
    "$SCRIPT_DIR/tests/connector_contract.py" "$bridge_dir/proxy.py"
  note "Verifying the offline Claude Science ownership/control contract..."
  python3 "$SCRIPT_DIR/tests/runtime_control_contract.py" \
    "$SCRIPT_DIR/runtime/switch_manager.py"
  note "Verifying the restart-stable Claude Science local-identity contract..."
  "$bridge_dir/.venv/bin/python" \
    "$SCRIPT_DIR/tests/science_identity_contract.py" \
    "$SCRIPT_DIR/runtime/science_identity.py"
  note "Verifying persistent model-route migration and update semantics..."
  python3 "$SCRIPT_DIR/tests/model_routes_contract.py" \
    "$SCRIPT_DIR/runtime/switch_manager.py"
  if [[ "$install_mode" == "full" ]]; then
    note "Verifying runtime-update rollback semantics..."
    bash "$SCRIPT_DIR/tests/installer_update_contract.sh" "$SCRIPT_DIR/install-final-stack.sh"
  fi

  install -m 700 "$SCRIPT_DIR/runtime/direct_gateway.py" "$root/runtime/direct_gateway.py"
  install -m 700 "$SCRIPT_DIR/runtime/science_identity.py" "$root/runtime/science_identity.py"
  install -m 700 "$SCRIPT_DIR/runtime/switch_manager.py" "$root/runtime/switch_manager.py"
  install -m 700 "$SCRIPT_DIR/fkctl" "$real_home/.local/bin/fkctl"
  install -m 700 "$SCRIPT_DIR/chrome-devtools-mcp-finalkit" "$real_home/.local/bin/chrome-devtools-mcp-finalkit"
  generate_identity_files "$root"
  chmod 600 "$root/bridge.commit" "$root/bridge.requirements.resolved.txt" \
    "$root/instance.id" "$root/secrets/gateway-path.token" \
    "$root/secrets/connector-control.token"

  # A repair install replaces runtime owners.  Close only this FinalKit
  # instance first; the manager refuses to kill a PID whose identity differs.
  "$real_home/.local/bin/fkctl" stop
  "$real_home/.local/bin/fkctl" prepare
  "$real_home/.local/bin/fkctl" init-profile

  FINALKIT_CANDIDATE_VERSION="$PACKAGE_VERSION" "$real_home/.local/bin/fkctl" doctor
  "$real_home/.local/bin/fkctl" smoke
  # Publish release metadata only after every runtime check passes.
  write_versions_metadata "$root" "$real_home" "$science_bin" "$claude_bin" "$codex_bin" "$PACKAGE_VERSION"

  if [[ "$install_mode" == "runtime" ]]; then
    note "FinalKit runtime update completed; provider auth and model routes were preserved."
    note "The runtime is stopped. Start the provider you want from the Windows menu."
  else
    note "Science SwitchModel installation completed."
    note "Next: configure DeepSeek, Kimi, GLM and/or ChatGPT Codex per Linux user."
  fi
}

update_runtime() {
  [[ "$EUID" -ne 0 ]] || die "--runtime must run as the ordinary WSL user"
  verify_ubuntu
  local real_home root backup target label
  local runtime_update_success=0
  local -a targets
  real_home="$(getent passwd "$(id -un)" | cut -d: -f6)"
  root="${FINALKIT_ROOT:-$real_home/.local/share/science-codex-finalkit}"
  [[ -x "$real_home/.local/bin/fkctl" && -d "$root/bridge/.git" ]] || \
    die "FinalKit is not installed for this Linux user; use the full Build"
  backup="$(mktemp -d /tmp/finalkit-runtime-update.XXXXXX)"
  targets=(
    "$root/runtime/direct_gateway.py"
    "$root/runtime/science_identity.py"
    "$root/runtime/switch_manager.py"
    "$root/bridge/proxy.py"
    "$root/bridge/config.json"
    "$root/config/model-routes.json"
    "$root/bridge.commit"
    "$root/bridge.requirements.resolved.txt"
    "$root/versions.txt"
    "$real_home/.local/bin/fkctl"
    "$real_home/.local/bin/chrome-devtools-mcp-finalkit"
  )
  mkdir -p "$backup/files"
  for target in "${targets[@]}"; do
    label="$(printf '%s' "$target" | sha256sum | awk '{print $1}')"
    printf '%s\n' "$target" >"$backup/$label.path"
    if [[ -e "$target" ]]; then
      cp -a -- "$target" "$backup/files/$label"
      : >"$backup/$label.present"
    fi
  done
  rollback_runtime_update() {
    local rollback_target rollback_label
    for rollback_target in "${targets[@]}"; do
      rollback_label="$(printf '%s' "$rollback_target" | sha256sum | awk '{print $1}')"
      rm -f -- "$rollback_target"
      if [[ -f "$backup/$rollback_label.present" ]]; then
        install -d -m 700 "$(dirname "$rollback_target")"
        cp -a -- "$backup/files/$rollback_label" "$rollback_target"
      fi
    done
    rm -rf -- "$backup"
  }
  runtime_update_success=0
  trap 'rc=$?; if [[ "${runtime_update_success:-0}" != "1" ]]; then note "Runtime update failed; restoring the previous managed files. The prior runtime may need to be started again."; rollback_runtime_update; fi; exit "$rc"' EXIT
  trap 'exit 130' INT TERM
  install_user runtime
  runtime_update_success=1
  trap - EXIT INT TERM
  rm -rf -- "$backup"
}

update_tools() {
  [[ "$EUID" -ne 0 ]] || die "--tools must run as the ordinary WSL user"
  verify_ubuntu
  local real_home root bridge_dir science_bin claude_bin codex_bin auth_file auth_before runtime_version
  local expected_requirements installed_requirements
  real_home="$(getent passwd "$(id -un)" | cut -d: -f6)"
  export HOME="$real_home"
  export PATH="$real_home/.local/bin:$PATH"
  root="${FINALKIT_ROOT:-$real_home/.local/share/science-codex-finalkit}"
  bridge_dir="$root/bridge"
  science_bin="$real_home/.local/bin/claude-science"
  claude_bin="$real_home/.local/bin/claude"
  codex_bin="$real_home/.local/bin/codex"
  auth_file="$real_home/.science-finalkit/.codex/auth.json"
  [[ -x "$real_home/.local/bin/fkctl" && -f "$root/bridge.commit" ]] || \
    die "FinalKit is not installed for this Linux user; use the full Build"
  auth_before=""
  [[ ! -f "$auth_file" ]] || auth_before="$(sha256sum "$auth_file" | awk '{print $1}')"
  "$real_home/.local/bin/fkctl" stop
  note "Updating official Claude Science, Claude Code and Codex CLI..."
  download_and_run "https://claude.ai/install-claude-science.sh" "Claude Science"
  download_and_run "https://claude.ai/install.sh" "Claude Code" "claude"
  download_and_run "https://chatgpt.com/codex/install.sh" "Codex CLI" "codex"
  note "Updating the package-pinned Node.js and Chrome DevTools MCP dependencies..."
  install_node_and_browser_mcp "$root" "$real_home"
  [[ -x "$bridge_dir/.venv/bin/pip" ]] || die "Connector Python environment is missing; use the full Build"
  expected_requirements="$(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' \
    "$SCRIPT_DIR/requirements.lock" | LC_ALL=C sort -f)"
  installed_requirements="$("$bridge_dir/.venv/bin/pip" freeze | LC_ALL=C sort -f)"
  if [[ "$expected_requirements" != "$installed_requirements" ]] || \
     ! "$bridge_dir/.venv/bin/pip" check >/dev/null; then
    note "Updating the package-pinned connector Python dependencies..."
    network_retry_direct "$bridge_dir/.venv/bin/pip" install \
      --disable-pip-version-check \
      --requirement "$SCRIPT_DIR/requirements.lock"
  fi
  "$bridge_dir/.venv/bin/pip" check
  "$bridge_dir/.venv/bin/pip" freeze >"$root/bridge.requirements.resolved.txt"
  chmod 600 "$root/bridge.requirements.resolved.txt"
  [[ -x "$science_bin" && -x "$claude_bin" && -x "$codex_bin" ]] || die "an official client update did not install all expected binaries"
  if [[ -n "$auth_before" ]]; then
    [[ -f "$auth_file" ]] || die "Codex auth disappeared during the tool update"
    [[ "$(sha256sum "$auth_file" | awk '{print $1}')" == "$auth_before" ]] || \
      die "Codex auth changed during the tool update; refusing to claim success"
  fi
  "$real_home/.local/bin/fkctl" doctor
  runtime_version="$(awk -F= '$1 == "package_version" {print $2}' "$root/versions.txt" 2>/dev/null || true)"
  [[ -n "$runtime_version" ]] || runtime_version="unknown"
  write_versions_metadata "$root" "$real_home" "$science_bin" "$claude_bin" "$codex_bin" "$runtime_version"
  note "Official tool update completed; auth and model routes were preserved. The runtime remains stopped."
}

show_help() {
  cat <<'EOF'
Usage:
  install-final-stack.sh --system   # WSL root phase
  install-final-stack.sh --user     # ordinary-user phase
  install-final-stack.sh --runtime  # offline FinalKit runtime-only update
  install-final-stack.sh --tools    # network update of official clients/dependencies

Normal Windows users should double-click 00-Install.cmd instead.
EOF
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  case "${1:-}" in
    --system) install_system ;;
    --user) install_user full ;;
    --runtime) update_runtime ;;
    --tools) update_tools ;;
    -h|--help|help) show_help ;;
    *) show_help >&2; exit 2 ;;
  esac
fi
