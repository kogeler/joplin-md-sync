#!/usr/bin/env bash
# Collect secret-safe diagnostics for the headless Joplin Terminal user service.

set -u
set -o pipefail

SERVICE_NAME="joplin-terminal.service"
ADAPTER_SERVICE_NAME="joplin-md-sync.service"
PREFIX="${JOPLIN_INSTALL_PREFIX:-$HOME/.local}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
PROFILE="${JOPLIN_PROFILE_DIR:-$DATA_HOME/joplin-agent/profile}"
STATE_DIR="$STATE_HOME/joplin-agent"
JOPLIN_BIN="$PREFIX/bin/joplin"
ADAPTER_BIN="$PREFIX/bin/joplin-md-sync"
SUPERVISOR="$PREFIX/lib/joplin-terminal-service/run_joplin_terminal.py"
COMMON="$PREFIX/lib/joplin-terminal-service/joplin_terminal_common.py"
API_PORT="${JOPLIN_API_PORT:-41185}"
MCP_PORT="${JOPLIN_MCP_PORT:-8765}"
OUTPUT="${1:-$PWD/joplin-terminal-debug.txt}"

mkdir -p "$(dirname "$OUTPUT")"
exec > >(tee "$OUTPUT") 2>&1

redact() {
    sed -E \
        -e "s/([?&](token|password)=)[^&[:space:]]+/\1[REDACTED]/Ig" \
        -e "s/((token|password)[[:space:]]*[:=][[:space:]]*)[^[:space:]]+/\1[REDACTED]/Ig" \
        -e "s/(Authorization:[[:space:]]*(Bearer)?[[:space:]]*)[^[:space:]]+/\1[REDACTED]/Ig"
}

section() {
    printf '\n===== %s =====\n' "$1"
}

run() {
    printf '\n$'
    printf ' %q' "$@"
    printf '\n'
    "$@" 2>&1 | redact
    local status=${PIPESTATUS[0]}
    printf '[exit=%d]\n' "$status"
    return 0
}

run_shell() {
    printf '\n$ %s\n' "$1"
    bash -o pipefail -c "$1" 2>&1 | redact
    local status=${PIPESTATUS[0]}
    printf '[exit=%d]\n' "$status"
    return 0
}

metadata() {
    local path=$1
    if [[ -e "$path" || -L "$path" ]]; then
        run stat -Lc 'path=%n type=%F mode=%a uid=%u gid=%g size=%s' "$path"
    else
        printf 'missing: %s\n' "$path"
    fi
}

section "collection policy"
printf 'Output: %s\n' "$OUTPUT"
printf '%s\n' \
    'Token/password contents and note bodies are not collected.' \
    'Review this file before sharing it.'

section "identity and platform"
run date --iso-8601=seconds
run id
run uname -a
run systemd-detect-virt
run systemctl --version
run python3 --version
run bash --version
if [[ -r /etc/os-release ]]; then
    run_shell "grep -E '^(PRETTY_NAME|NAME|VERSION|VERSION_ID)=' /etc/os-release"
fi
run loginctl show-user "$(id -un)" -p Linger -p State -p RuntimePath
run systemctl --user is-system-running

section "runtime discovery"
NODE_PATH="$(command -v node 2>/dev/null || true)"
NPM_PATH="$(command -v npm 2>/dev/null || true)"
printf 'node command: %s\n' "${NODE_PATH:-missing}"
printf 'npm command: %s\n' "${NPM_PATH:-missing}"
if [[ -n "$NODE_PATH" ]]; then
    run readlink -v "$NODE_PATH"
    run readlink -f "$NODE_PATH"
    run "$NODE_PATH" --version
fi
if [[ -n "$NPM_PATH" ]]; then
    run "$NPM_PATH" --version
    run "$NPM_PATH" list --global --prefix "$DATA_HOME/joplin-agent/npm" joplin --depth=0 --json
fi
if command -v snap >/dev/null 2>&1; then
    run snap version
    run snap list node
    run snap connections node
fi

section "selected paths"
printf 'prefix=%s\nprofile=%s\nstate=%s\napi_port=%s\nmcp_port=%s\n' \
    "$PREFIX" "$PROFILE" "$STATE_DIR" "$API_PORT" "$MCP_PORT"
for path in \
    "$JOPLIN_BIN" \
    "$ADAPTER_BIN" \
    "$SUPERVISOR" \
    "$COMMON" \
    "$PROFILE" \
    "$STATE_DIR" \
    "$CONFIG_HOME/joplin-agent/api-token" \
    "$CONFIG_HOME/joplin-md-sync/gpt-actions-token" \
    "$CONFIG_HOME/joplin-md-sync/mcp-token"; do
    metadata "$path"
done
run namei -l "$JOPLIN_BIN"
run namei -l "$PROFILE"
run namei -l "$STATE_DIR"
run_shell "df -hT '$PROFILE' '$STATE_DIR' 2>/dev/null || true"
run_shell "df -i '$PROFILE' '$STATE_DIR' 2>/dev/null || true"

section "deployed file identity"
run sha256sum "$ADAPTER_BIN" "$SUPERVISOR" "$COMMON"
run_shell "grep -nE '_check_node_runtime|_check_profile_writable|absolute_path' '$SUPERVISOR' '$COMMON' || true"

section "systemd units"
for service in "$SERVICE_NAME" "$ADAPTER_SERVICE_NAME"; do
    run systemctl --user status "$service" --no-pager -l
    run systemctl --user show "$service" \
        -p LoadState -p ActiveState -p SubState -p Result \
        -p ExecMainCode -p ExecMainStatus -p NRestarts -p FragmentPath \
        -p ExecStart -p Environment
    run systemctl --user cat "$service"
done
run systemd-analyze --user verify \
    "$CONFIG_HOME/systemd/user/$SERVICE_NAME" \
    "$CONFIG_HOME/systemd/user/$ADAPTER_SERVICE_NAME"

section "service journals"
run journalctl --user -u "$SERVICE_NAME" --no-pager -n 250 -o short-precise
run journalctl --user -u "$ADAPTER_SERVICE_NAME" --no-pager -n 100 -o short-precise

section "listeners and health"
run ss -ltnp
run curl --verbose --max-time 3 "http://127.0.0.1:$API_PORT/ping"
run curl --verbose --max-time 3 "http://127.0.0.1:$MCP_PORT/mcp"

section "Joplin error keyword counts"
for log_file in "$PROFILE/log.txt" "$PROFILE/log-clipper.txt"; do
    metadata "$log_file"
    if [[ -r "$log_file" ]]; then
        run_shell "grep -Eaio 'EACCES|EROFS|read.?only|permission denied|address already in use|SQLITE_[A-Z_]+|masterKey[A-Za-z]*|decrypt[A-Za-z]*|snap-confine|error|fatal|exception' '$log_file' | sort | uniq -c || true"
    fi
done

section "Node inside service-compatible systemd sandbox"
if [[ -n "$NODE_PATH" ]] && command -v systemd-run >/dev/null 2>&1; then
    NODE_SANDBOX=(
        -p NoNewPrivileges=true
        -p 'RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6'
        -p RestrictSUIDSGID=true
    )
    if [[ "$NODE_PATH" == /snap/bin/* ]]; then
        NODE_SANDBOX+=(
            -p PrivateDevices=false
            -p PrivateTmp=false
            -p ProtectControlGroups=false
            -p ProtectKernelModules=false
            -p ProtectKernelTunables=false
            -p ProtectSystem=false
        )
    else
        NODE_SANDBOX+=(
            -p PrivateDevices=true
            -p PrivateTmp=true
            -p ProtectControlGroups=true
            -p ProtectKernelModules=true
            -p ProtectKernelTunables=true
            -p ProtectSystem=strict
            -p "ReadWritePaths=$PROFILE $STATE_DIR"
        )
    fi
    run systemd-run --user --wait --pipe --collect \
        --unit="joplin-debug-node-$$" \
        "${NODE_SANDBOX[@]}" \
        "$NODE_PATH" --version
else
    printf 'skipped: node or systemd-run is unavailable\n'
fi

section "empty-profile Joplin command inside matching sandbox"
PROBE_ROOT=""
if [[ -x "$JOPLIN_BIN" && -n "$NODE_PATH" ]] && command -v systemd-run >/dev/null 2>&1; then
    mkdir -p "$HOME/.cache"
    PROBE_ROOT="$(mktemp -d "$HOME/.cache/joplin-debug.XXXXXX")"
    PROBE_PROFILE="$PROBE_ROOT/profile"
    mkdir -p "$PROBE_PROFILE"
    PROBE_SANDBOX=(
        -p NoNewPrivileges=true
        -p 'RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6'
        -p RestrictSUIDSGID=true
    )
    if [[ "$NODE_PATH" == /snap/bin/* ]]; then
        PROBE_SANDBOX+=(
            -p PrivateDevices=false
            -p PrivateTmp=false
            -p ProtectControlGroups=false
            -p ProtectKernelModules=false
            -p ProtectKernelTunables=false
            -p ProtectSystem=false
        )
    else
        PROBE_SANDBOX+=(
            -p PrivateDevices=true
            -p PrivateTmp=true
            -p ProtectControlGroups=true
            -p ProtectKernelModules=true
            -p ProtectKernelTunables=true
            -p ProtectSystem=strict
            -p "ReadWritePaths=$PROBE_ROOT"
        )
    fi
    run systemd-run --user --wait --pipe --collect \
        --unit="joplin-debug-help-$$" \
        "${PROBE_SANDBOX[@]}" \
        "$NODE_PATH" "$JOPLIN_BIN" --profile "$PROBE_PROFILE" help server

    if [[ -x "$SUPERVISOR" ]] && command -v timeout >/dev/null 2>&1; then
        PROBE_PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
        run "$JOPLIN_BIN" --profile "$PROBE_PROFILE" config api.port "$PROBE_PORT"
        run systemd-run --user --wait --pipe --collect \
            --unit="joplin-debug-supervisor-$$" \
            "${PROBE_SANDBOX[@]}" \
            timeout --preserve-status --signal=TERM --kill-after=5s 18s \
            python3 "$SUPERVISOR" \
            --node-path "$NODE_PATH" \
            --joplin-path "$JOPLIN_BIN" \
            --profile-dir "$PROBE_PROFILE" \
            --lock-file "$PROBE_ROOT/profile.lock" \
            --api-port "$PROBE_PORT" \
            --sync-interval 300 \
            --startup-timeout 12 \
            --shutdown-timeout 5 \
            --verbose
    else
        printf 'supervisor probe skipped: deployed supervisor or timeout is unavailable\n'
    fi
else
    printf 'skipped: Joplin, node, or systemd-run is unavailable\n'
fi

if [[ -n "$PROBE_ROOT" && -d "$PROBE_ROOT" ]]; then
    rm -rf -- "$PROBE_ROOT"
fi

section "result"
printf 'Diagnostics complete. Review and share: %s\n' "$OUTPUT"
