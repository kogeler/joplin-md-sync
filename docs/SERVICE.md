# Joplin API Service

This is the single deployment and operations guide for the Joplin Data API,
MCP, and ChatGPT Actions. MCP and Actions are not separate services. One
`joplin-md-sync` binary runs one HTTP listener and separates the transports only
by URI:

| URI | Client | Authentication |
| --- | --- | --- |
| `/mcp` | MCP clients | Optional dedicated MCP bearer token |
| `/api/gpt/v1/*` | ChatGPT Actions | Required dedicated Actions bearer token |
| `/healthz`, `/readyz` | Local operator checks | Loopback only; never public |

Both transports use the same registry, executor, Joplin service, and upstream
Data API connection. Actions never call MCP over HTTP. The Actions token is the
secret configured in the Custom GPT editor; it is not an OpenAI API key and
must differ from both the optional MCP token and the Joplin Data API token.

For a desktop Joplin instance, start the binary directly as described below.
For a headless Linux host, the installer in
`scripts/joplin_terminal_service/` installs two coordinated systemd user
services:

- `joplin-terminal.service` runs the upstream Joplin Terminal Data API and
  recurrent sync;
- `joplin-md-sync.service` runs the single combined MCP and Actions adapter.

There is never a separate Actions adapter unit. The installer runs without
root and has no third-party Python dependencies.

## Desktop or existing Joplin

Create a required Actions token file first:

```bash
install -d -m 700 ~/.config/joplin-md-sync
umask 077
python3 -c "import secrets; print(secrets.token_urlsafe(32))" \
  > ~/.config/joplin-md-sync/gpt-actions-token
chmod 600 ~/.config/joplin-md-sync/gpt-actions-token
```

Then run the combined listener:

```bash
export JOPLIN_TOKEN=...
joplin-md-sync mcp serve \
  --gpt-actions \
  --gpt-actions-token-file ~/.config/joplin-md-sync/gpt-actions-token
```

This exposes `http://127.0.0.1:8765/mcp` and authenticated Actions routes under
`http://127.0.0.1:8765/api/gpt/v1/` in one foreground process. Add
`--auth-token-file ~/.config/joplin-md-sync/mcp-token` only when MCP clients
must authenticate. Generate that file with the same `secrets.token_urlsafe(32)`
command and `0600` mode used above. Use a different value for each file.
Actions cannot be enabled without their token file.

For Windows secret-file ACLs and Task Scheduler, use the combined
[`install-service-task.ps1`](../examples/windows/install-service-task.ps1)
example. It requires the Actions token file and makes MCP authentication
optional.

## Headless Linux installation

A repository clone is not required. For a direct interactive installation in
Bash, stream the installer into Python without creating a local script file:

```bash
set -o pipefail; curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location 'https://raw.githubusercontent.com/kogeler/joplin-md-sync/main/scripts/joplin_terminal_service/install_joplin_terminal.py' | python3 - --nextcloud-url 'https://cloud.example.com/remote.php/dav/files/user/Joplin' --nextcloud-user 'user'
```

`python3 -` reads the program from standard input; the remaining arguments are
passed to the installer. It generates separate Actions and MCP bearer tokens,
then interactively handles lingering and requests only the Nextcloud and E2EE
passwords through the controlling terminal (`/dev/tty`), not through standard
input. Run this form from an
interactive terminal. In an automation environment without a terminal, use
`--non-interactive` and provide all required choices and secrets explicitly.
`pipefail` prevents a failed download from looking like a successful empty
Python program. `curl` is needed only for this convenience form; the
download-and-review command below uses Python's standard library instead.

This convenience form executes the current remote `main` branch immediately.
For a security-sensitive host, prefer a reviewed commit or download and inspect
the file first:

```bash
python3 -c 'from urllib.request import urlretrieve; urlretrieve("https://raw.githubusercontent.com/kogeler/joplin-md-sync/main/scripts/joplin_terminal_service/install_joplin_terminal.py", "install_joplin_terminal.py")'
less install_joplin_terminal.py
python3 install_joplin_terminal.py --help
```

When run from stdin or when sibling files are unavailable, the installer
downloads its stdlib common module, supervisor, and both systemd templates from
the same GitHub `main` path over HTTPS. It then deploys local copies under
`~/.local`; runtime operation does not depend on GitHub or on keeping the
downloaded installer.

For a reviewed commit, download the installer from that commit and set the
matching asset base before running it:

```bash
export JOPLIN_TERMINAL_ASSET_BASE_URL='https://raw.githubusercontent.com/kogeler/joplin-md-sync/<commit>/scripts/joplin_terminal_service'
```

Unset that non-secret variable after installation if it is no longer needed.

## Requirements

- Linux with systemd user services and a working user bus;
- Python 3.14 is recommended. Python 3.13.5 is also covered by the local test
  suite. Older versions are not tested; the installer has no explicit runtime
  version guard;
- Node.js and npm (Node.js 22 is tested; upstream requires Node.js 12+);
- an existing Nextcloud/WebDAV Joplin directory;
- an existing Joplin E2EE setup and its master password;
- network access to Nextcloud, the npm registry, and GitHub Releases during
  installation.

The installer checks `node`, `npm`, `systemctl`, and `loginctl`. It never runs
`sudo` or installs system packages. Distribution commands, when packages are
missing, are typically:

```bash
# Debian/Ubuntu (distribution versions may be older than the tested Node 22)
sudo apt install nodejs npm

# Fedora
sudo dnf install nodejs npm

# Arch Linux
sudo pacman -S nodejs npm
```

Use the distribution's supported Node.js installation method when its package
is obsolete. These commands are documentation only; review them for the host.

## Installed layout

Defaults:

```text
~/.local/share/joplin-agent/npm/             isolated npm prefix
~/.local/bin/joplin                          stable launcher symlink
~/.local/bin/joplin-md-sync                  verified standalone executable
~/.local/share/joplin-agent/profile/         dedicated Joplin profile
~/.local/lib/joplin-terminal-service/        deployed Python supervisor
~/.local/state/joplin-agent/                 profile lock
~/.config/joplin-agent/api-token             Data API token, mode 0600
~/.config/joplin-md-sync/gpt-actions-token    generated Actions token, mode 0600
~/.config/joplin-md-sync/mcp-token            generated MCP token, mode 0600
~/.config/systemd/user/joplin-terminal.service
~/.config/systemd/user/joplin-md-sync.service combined MCP and Actions adapter
```

The npm dependency tree is not installed into `~/.local/lib/node_modules` and
does not mix with other global npm packages. If an npm-managed Joplin already
exists directly under `~/.local`, a full install or update migrates that one
package to the isolated prefix after verifying the replacement. An unknown
file or symlink at `~/.local/bin/joplin` is never overwritten.

The installer selects the GitHub standalone asset for Linux AMD64 or ARM64,
checks it against the release `SHA256SUMS.txt`, verifies `version --json` and
`capabilities --json`, and only then atomically replaces
`~/.local/bin/joplin-md-sync`. An unknown or non-standalone executable at that
path is not overwritten.

The generated unit records the absolute path of the Node.js executable found
during installation. It therefore does not depend on a login-shell `PATH` and
also works when Node came from a per-user version manager.

For a normal Node executable, the Joplin unit keeps the filesystem read-only
except for the dedicated profile and profile-lock directory, which are emitted
as explicit `ReadWritePaths`. It also uses `NoNewPrivileges=true`.

When Node is the stable Snap command alias `/snap/bin/node`, the unit keeps that
alias so Snap refreshes continue to select the current revision. It does not
use an internal revision path such as `/snap/node/<revision>/bin/node`. The unit
retains `NoNewPrivileges`, address-family, and SUID/SGID restrictions, but
disables systemd directives that create a private mount namespace. On Debian
13 inside LXC, snap-confine fails with `cannot fstatat canonical snap directory`
when any of `PrivateDevices`, `PrivateTmp`, `ProtectSystem`, the related kernel
protection directives, or `ReadWritePaths` creates that namespace. A non-Snap
Node keeps the stronger filesystem isolation. The `joplin-md-sync` unit is
unaffected.

The default profile follows `XDG_DATA_HOME`; the config and state paths follow
`XDG_CONFIG_HOME` and `XDG_STATE_HOME`. The default Data API port is the fixed
non-standard port `41185`, which avoids the normal Joplin Desktop port 41184.
The installer uses that same `--api-port` value when rendering both the Joplin
supervisor and MCP upstream connection. This is the single source for the
shared port. Override it with `--api-port` or `JOPLIN_API_PORT`.

The combined adapter listens on fixed port `8765`, configurable with
`--mcp-port` or `JOPLIN_MCP_PORT`. MCP uses `/mcp`; Actions use
`/api/gpt/v1/*` on the same listener. The adapter and Joplin ports must differ
and both remain on loopback by default. `--allow-remote-mcp` changes the shared
adapter bind address to `0.0.0.0`; the Joplin Data API always remains on
loopback. Public deployments must publish only the Actions namespace and keep
MCP, health, and Joplin routes private.

Environment overrides mirror their CLI options:

```text
JOPLIN_VERSION
JOPLIN_INSTALL_PREFIX
JOPLIN_PROFILE_DIR
JOPLIN_API_PORT
JOPLIN_SYNC_INTERVAL
JOPLIN_MD_SYNC_VERSION
JOPLIN_MCP_PORT
```

The installer does not accept service bearer tokens through command-line
arguments or environment variables. It creates both token files itself.

By default, every full installation resolves the npm `latest` tag for Joplin
and the latest stable GitHub Release for the joplin-md-sync standalone. Use
`--joplin-version X.Y.Z` or `--joplin-md-sync-version X.Y.Z` to pin either
component.

Do not point `--profile-dir` at a Desktop profile or another Terminal profile.
The installer and service share an exclusive `flock`; one profile is never
opened by two managed processes.

## Password input

Nextcloud and E2EE passwords are used only during full installation. They are
not written to a systemd unit, an EnvironmentFile, adapter configuration, logs,
or installer-created password files. Joplin itself persists the sync password
and E2EE key cache inside its protected profile so it can restart unattended.

The Joplin API token, Actions bearer token, and MCP bearer token are technical
service credentials, not Nextcloud/E2EE passwords. The installer generates
separate Actions and MCP values with `secrets.token_urlsafe(32)`. They are
stored only in the files shown above with mode `0600`; units contain paths,
never token values. Both token-file flags are always present in an installed
adapter unit, so MCP authentication cannot be disabled for this deployment.
After a successful installation, the installer prints only the two file paths,
not their contents.

An idempotent rerun preserves valid existing service token files. It does not
rotate them. A missing file is generated, while an invalid or unreadable
existing file stops the run instead of being silently replaced.

### Interactive input

This is the preferred method:

```bash
python3 install_joplin_terminal.py \
  --nextcloud-url "https://cloud.example.com/remote.php/dav/files/user/Joplin" \
  --nextcloud-user "user"
```

Before requesting passwords, the installer creates the two service credentials
in memory and checks whether systemd user lingering is enabled. When lingering
is disabled, interactive mode asks whether to enable it. It then uses hidden
`getpass` prompts:

```text
Nextcloud password:
Joplin E2EE password:
```

On an idempotent rerun, Joplin's already-validated E2EE key cache is reused and
the E2EE prompt is skipped. The Nextcloud password is still required because
the installer never reads the stored secure setting.

### Temporary environment variables

```bash
export JOPLIN_NEXTCLOUD_PASSWORD='...'
export JOPLIN_E2EE_PASSWORD='...'

python3 install_joplin_terminal.py \
  --nextcloud-url "https://cloud.example.com/remote.php/dav/files/user/Joplin" \
  --nextcloud-user "user"

unset JOPLIN_NEXTCLOUD_PASSWORD
unset JOPLIN_E2EE_PASSWORD
```

To avoid typing a password as a normal shell command:

```bash
read -rsp "Nextcloud password: " JOPLIN_NEXTCLOUD_PASSWORD
echo
export JOPLIN_NEXTCLOUD_PASSWORD

read -rsp "E2EE password: " JOPLIN_E2EE_PASSWORD
echo
export JOPLIN_E2EE_PASSWORD
```

Environment variables may be inspectable by same-user processes or root on
some systems. The installer removes these variables from Joplin and npm child
process environments, but it cannot alter the parent shell; run the `unset`
commands afterward.

### Command-line arguments

```bash
python3 install_joplin_terminal.py \
  --nextcloud-url "https://cloud.example.com/remote.php/dav/files/user/Joplin" \
  --nextcloud-user "user" \
  --nextcloud-password "..." \
  --e2ee-password "..."
```

This supported form can remain in shell history and can be briefly visible in
the process list. A leading space avoids history only when the shell is
configured to honour it. During configuration, Joplin's `config` command also
receives the Nextcloud password as an argument because Joplin 3.6.2 exposes no
dedicated secret-stdin interface. Commands are redacted in installer logs.

Python cannot guarantee physical erasure of immutable strings from process
memory. The installer drops references as soon as practical and never includes
secrets in raised errors or debug output. There is no raw CLI or environment
input for either service bearer token. Non-interactive mode therefore needs
only the normal installation choices plus the Nextcloud and, when required,
E2EE password sources.

## Nextcloud URL

Joplin sync target `5` is the Nextcloud-specific driver. Target `6` is generic
WebDAV. Supply the URL of the existing Joplin directory, for example:

```text
https://cloud.example.com/remote.php/dav/files/user/Joplin
```

Older Nextcloud deployments may expose a `/remote.php/webdav/Joplin` URL.
Confirm the URL in a working Joplin client. Do not embed credentials, a query
string, or a fragment in it. The installer warns for plain HTTP.

If an existing profile has a different sync target or URL, interactive mode
asks before changing it. Non-interactive mode refuses the change unless
`--force-reconfigure` is present. The profile and database are never deleted.

## Installation

Inspect the plan first:

```bash
python3 install_joplin_terminal.py \
  --nextcloud-url "https://cloud.example.com/remote.php/dav/files/user/Joplin" \
  --nextcloud-user "user" \
  --dry-run
```

`--dry-run` does not prompt, install npm or adapter content, open or modify the
profile, sync, create files, stop a service, reload systemd, or start anything.
With `--non-interactive`, required Nextcloud and E2EE password sources must
still be present. Service tokens are not generated during a dry run.

Run the same command without `--dry-run`. The installer:

1. checks systemd user lingering and, in interactive mode, offers to enable it;
2. generates separate Actions and MCP bearer tokens when their files are absent;
3. stops the active adapter and Joplin services in order and acquires the
   profile lock;
4. resolves the npm `latest` tag and installs that Joplin version in the
   isolated npm prefix, unless `--joplin-version` pins a version;
5. smoke-tests the `server` and `e2ee` commands;
6. configures target 5, credentials, sync interval, and API port;
7. performs the initial sync unless `--skip-initial-sync` is set;
8. verifies that the downloaded target has E2EE enabled;
9. unlocks existing master keys over a pseudo-terminal without calling
   `e2ee enable`, decrypts pending items, and verifies the key cache in a fresh
   process;
10. performs a second sync and a metadata-only status check whose output is not
   logged;
11. extracts only `api.token` to the protected token file;
12. resolves the latest stable `joplin-md-sync` GitHub Release, unless
    `--joplin-md-sync-version` pins it, downloads the host-architecture asset, verifies
    its release SHA-256, standalone identity, version, and MCP capability, then
    installs it atomically;
13. verifies that all three service tokens differ and stores both generated
    adapter tokens with mode `0600`;
14. deploys the supervisor plus `joplin-terminal.service` and the one combined
    `joplin-md-sync.service`, backs up changed units, and reloads systemd;
15. enables and restarts Joplin followed by the adapter, verifies `/ping`,
    initializes `/mcp` and calls `joplin_list_notebooks`, then verifies that
    `/api/gpt/v1/*` rejects an unauthenticated probe and accepts the dedicated
    Actions token without exposing a real tool. Only after successful completion
    does it report the Actions and MCP token file paths.

The initial sync, E2EE decryption, fresh-process E2EE verification, and second
sync each wait for Joplin to finish and allow up to 24 hours. Large stores may
therefore remain in one installation stage for tens of minutes. While a stage
is running, the installer writes an elapsed-time heartbeat every 60 seconds.
It does not log Joplin's raw progress because that output can contain notebook
or note names. Long-command stdout and stderr are consumed continuously and
only a bounded 256 KiB tail per stream is retained in memory; the E2EE PTY uses
one bounded 256 KiB buffer. These limits do not truncate the sync itself.

The full TUI remains the one Joplin process for the profile. This lifecycle
keeps recurrent sync and `DecryptionWorker` in that process. The
supervisor enters `server start --exit-early` in that same process. It never
runs a parallel `joplin sync` while the service owns the profile.

Supported `--sync-interval` values are `300`, `600`, `1800`, `3600`, `43200`,
and `86400` seconds. The installer rejects other values before changing the
profile.

Useful control flags:

```text
--non-interactive
--force-reconfigure
--skip-initial-sync
--skip-e2ee-bootstrap
--no-enable-service
--no-start-service
--profile-dir PATH
--joplin-prefix PATH
--api-port PORT
--mcp-port PORT
--allow-remote-mcp
--sync-interval SECONDS
--joplin-version VERSION
--joplin-md-sync-version VERSION
--upgrade
--enable-linger
--purge
--yes
--verbose
```

`--skip-e2ee-bootstrap` does not bypass validation. It succeeds only if the
stored E2EE password already works in a new process. If Joplin changes its
prompt or cannot automate the operation, the installer stops with an exact
manual `joplin --profile ... e2ee decrypt --retry-failed-items` command. Run it
interactively and rerun the installer with `--skip-e2ee-bootstrap`.

Some Joplin releases, including 3.6.2, have a packaging bug in `joplin version`.
The installer therefore checks the exact version through
`npm list --global --prefix ... --json` and uses `joplin help server`/`help
e2ee` as executable smoke tests.

## Service management

```bash
systemctl --user start joplin-terminal.service joplin-md-sync.service
systemctl --user stop joplin-md-sync.service joplin-terminal.service
systemctl --user restart joplin-terminal.service joplin-md-sync.service
systemctl --user status joplin-terminal.service joplin-md-sync.service
```

`joplin-md-sync.service` has `Requires=` and `After=` dependencies on
`joplin-terminal.service`. It passes the generated API token file and the same
Data API port configured for Joplin. The `joplin-md-sync` process tolerates
temporary upstream unavailability, but systemd starts it only after the Joplin
service.

The supervisor owns a PTY, waits for actual TUI readiness, starts the API,
checks `/ping`, and monitors both the child process and API. Repeated API health
failures terminate Joplin with a failure exit so systemd restarts it. SIGTERM
and SIGINT are forwarded to the process group; a bounded timeout prevents
orphan processes.

Raw TUI rendering is discarded because it can contain note titles and bodies.
Lifecycle messages are available in the journal:

```bash
journalctl --user -u joplin-terminal.service
journalctl --user -u joplin-terminal.service -f
journalctl --user -u joplin-md-sync.service -f
```

Joplin's own diagnostic files remain inside the dedicated profile. Treat that
profile as sensitive.

## API check

```bash
curl http://127.0.0.1:41185/ping
```

Expected body:

```text
JoplinClipperServer
```

The observed 3.6.2 server binds `127.0.0.1`; the systemd unit does not publish
the port. Never expose it directly to the internet.

## MCP integration

The default endpoint is `http://127.0.0.1:8765/mcp`. Its generated bearer token
is stored in `~/.config/joplin-md-sync/mcp-token`; configure a Streamable HTTP
MCP client with the URL and an
`Authorization: Bearer <token>` header. Do not use the Joplin API token as the
MCP bearer token; they have different purposes.

The installer first performs an authenticated readiness `GET`. A healthy
endpoint returns HTTP `405` with `Allow: POST`. It then sends
MCP `initialize` and calls the read-only `joplin_list_notebooks` tool with
`limit: 1`. Installation succeeds only when the result contains a valid Joplin
object listing. The same bearer token is used for all three requests. Responses,
notebook names, and tokens are not logged.

Check basic readiness manually without placing the token in a process argument:

```bash
curl --silent --show-error --output /dev/null --write-out '%{http_code}\n' \
  --config - <<EOF
url = "http://127.0.0.1:8765/mcp"
header = "Authorization: Bearer $(<~/.config/joplin-md-sync/mcp-token)"
EOF
```

Expected status is `405`. A missing or incorrect bearer token returns `401`.
The upstream Joplin token remains local to the joplin-md-sync process and is
never sent to MCP clients.

### Remote MCP access

Remote access is opt-in:

```bash
python3 install_joplin_terminal.py \
  --allow-remote-mcp \
  ...
```

This renders `--host 0.0.0.0 --allow-remote-mcp` and keeps the Joplin Data API
on `127.0.0.1`. The generated MCP token remains mandatory. `0.0.0.0` exposes
plain HTTP on every IPv4 interface. Restrict the port with the host firewall
and normally use a trusted TLS publishing layer or private network. The
installer does not alter firewall or public HTTPS configuration. Browser
clients that send an `Origin` header may additionally require an origin
configured by the MCP server; the generated service is intended for
non-browser MCP clients.

## ChatGPT Actions integration

The same listener serves Actions under `/api/gpt/v1/*`. The installer always
adds both `--gpt-actions` and `--gpt-actions-token-file` to
`joplin-md-sync.service`. It verifies the boundary without invoking a real
tool: an unauthenticated probe must return `401`, while the same unknown route
with the configured Actions token must return `404`.

OpenAI calls Actions through a public HTTPS origin on port 443 with a publicly
trusted certificate and TLS 1.2 or later. This repository does not provide or
prescribe public HTTPS infrastructure. Configure the operator's existing
publishing layer to expose only `/api/gpt/v1/*`; `/mcp`, `/healthz`, `/readyz`,
and ports 41184-41194 and 8765 must remain private. Do not log request bodies or
Authorization headers.

Export the OpenAPI contract with the real public origin:

```bash
joplin-md-sync gpt-actions export-openapi \
  --server-url https://notes.example.com \
  --output ./chatgpt-action.openapi.json \
  --json
python3 -m json.tool ./chatgpt-action.openapi.json >/dev/null
```

The repository does not keep a generated OpenAPI file. The command above is the
only supported export path because it binds the schema to the operator's real
public origin. Regenerate it after every tool-registry upgrade. Large binary
resource read/create/update operations remain MCP-only because Actions payloads
are text and must remain below 100,000 characters.

From a machine outside the service host and its private network, with a
protected copy of the Actions token file available there, verify the public
boundary before importing the contract:

```bash
host=notes.example.com

for path in /mcp /healthz /readyz; do
  status=$(curl --proto '=https' --tlsv1.2 --silent --show-error \
    --output /dev/null --write-out '%{http_code}' "https://$host$path")
  case "$status" in 403|404) ;; *) echo "public route: $path ($status)"; exit 1;; esac
done

test "$(curl --proto '=https' --tlsv1.2 --silent --show-error \
  --output /dev/null --write-out '%{http_code}' \
  "https://$host/api/gpt/v1/tools/setup-probe-not-a-tool")" = 401

curl --config - <<EOF
url = "https://$host/api/gpt/v1/tools/setup-probe-not-a-tool"
header = "Authorization: Bearer $(<~/.config/joplin-md-sync/gpt-actions-token)"
output = "/dev/null"
proto = "=https"
silent
show-error
tlsv1.2
write-out = "%{http_code}\n"
EOF

for port in $(seq 41184 41194) 8765; do
  if timeout 1 bash -c "exec 3<>/dev/tcp/$host/$port" 2>/dev/null; then
    echo "public TCP port: $port"
    exit 1
  fi
done
```

The authenticated unknown route must print `404`; all tested TCP ports must be
unreachable. The token is sent to `curl` through standard input rather than a
process argument. Continue with [ChatGPT Actions setup](CHATGPT_ACTIONS.md) for
the manual editor steps.

Rotate the Actions credential by atomically replacing
`~/.config/joplin-md-sync/gpt-actions-token`; the service reloads it per
request. Update the credential in the Custom GPT editor immediately. To
revoke Actions, remove the public Actions route, stop the adapter, remove the
Action from the GPT, and remove the token file. Because the installed service
requires Actions, rerun the installer with a new token before starting it
again.

Actions failures use stable JSON envelopes. Common statuses are `401` for an
invalid Actions token, `413` for an oversized request, `422` for validation,
`429` for rate limiting, `502` for a safe upstream or oversized-result error,
and `503` for Joplin unavailability or concurrency exhaustion. Never retry an
ambiguous write until its state has been inspected in Joplin.

## Windows autostart

The PowerShell example registers the same combined process as one per-user
Task Scheduler task. The Actions token file is mandatory; the MCP token file
is optional:

```powershell
$dir = "$env:APPDATA\joplin-md-sync"
New-Item -ItemType Directory -Force $dir | Out-Null
$bytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes); $rng.Dispose()
$token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
[IO.File]::WriteAllText("$dir\gpt-actions-token", $token)
icacls "$dir\gpt-actions-token" /inheritance:r /grant:r "${env:USERNAME}:(R)"

.\examples\windows\install-service-task.ps1 `
  -Executable "$env:USERPROFILE\.local\bin\joplin-md-sync.exe" `
  -JoplinTokenFile "$env:APPDATA\joplin-md-sync\joplin-token" `
  -GptActionsTokenFile "$env:APPDATA\joplin-md-sync\gpt-actions-token" `
  -McpAuthTokenFile "$env:APPDATA\joplin-md-sync\mcp-token"
```

Restrict all token files to the current account with Windows ACLs. Omit
`-McpAuthTokenFile` to leave MCP authentication disabled. Remove the task with
`Unregister-ScheduledTask -TaskName joplin-md-sync -Confirm:$false`.

## Updating and rollback

Upgrade both Joplin and the joplin-md-sync standalone to their current stable releases:

```bash
python3 install_joplin_terminal.py --upgrade
```

`--upgrade` resolves the npm `latest` tag and the latest stable GitHub Release
before stopping either service. It then stops the adapter and Joplin, acquires the
profile lock, updates and verifies both components, restarts both services, and
runs Joplin API, MCP `joplin_list_notebooks`, and Actions authentication smoke
tests. Nextcloud and E2EE passwords are not requested. The required Actions
and MCP tokens are preserved from their protected files. If either file is
missing, the installer generates it; it never rotates an existing valid token.

Pin only Joplin while updating the adapter to latest:

```bash
python3 install_joplin_terminal.py \
  --upgrade \
  --joplin-version 3.7.1
```

Pin only the adapter while updating Joplin to latest:

```bash
python3 install_joplin_terminal.py \
  --upgrade \
  --joplin-md-sync-version 1.4.0
```

Pin both versions, including for rollback:

```bash
python3 install_joplin_terminal.py \
  --upgrade \
  --joplin-version 3.6.2 \
  --joplin-md-sync-version 1.2.0
```

Use `--dry-run` to preview resolution targets without modifying the
installation. `--no-start-service` deliberately leaves both services stopped
and skips runtime smoke tests. Custom installations must repeat their original
`--api-port`, `--mcp-port`, `--joplin-prefix`, `--profile-dir`, and relevant XDG
overrides so upgrade locates the existing installation and checks the correct
listeners. `--upgrade` refuses to operate when either installed unit is absent.

## Removal

Preview a complete local uninstall without changing anything:

```bash
python3 install_joplin_terminal.py --purge --dry-run
```

Run the purge interactively. It prints the selected profile path and requires
typing the exact word `PURGE`:

```bash
python3 install_joplin_terminal.py --purge
```

For a clean automated test account, confirmation can be explicit and
non-interactive:

```bash
python3 install_joplin_terminal.py --purge --non-interactive --yes
```

The same operation can be run without a repository clone:

```bash
set -o pipefail; curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location 'https://raw.githubusercontent.com/kogeler/joplin-md-sync/main/scripts/joplin_terminal_service/install_joplin_terminal.py' | python3 - --purge
```

Purge stops and disables both user units, verifies that they are inactive,
takes the exclusive profile lock, then removes the units and their installer
backups, isolated npm prefix, Joplin launcher, standalone adapter binary,
deployed supervisor, profile, API token, Actions token, MCP token, and
Joplin-specific state directories. It leaves
unrelated files below `~/.local`, `~/.config`, and the systemd user directory
untouched. Repeating purge is safe.

Custom installations must repeat the original `--joplin-prefix`,
`--profile-dir`, and relevant XDG environment overrides so purge selects the
same paths. Unsafe installation-prefix or profile paths are refused.

Purge does not access or delete the Nextcloud/WebDAV sync target. It also does
not disable systemd lingering because other user services may depend on it. On
an isolated disposable test account, reset lingering separately only when
needed:

```bash
loginctl disable-linger "$USER"
```

After purge, rerun the normal installation command to exercise the complete
initial sync and E2EE bootstrap from a clean local state.

## Lingering

At the start of a full installation, before asking for any token or password,
the installer checks:

```bash
loginctl show-user "$USER" --property=Linger --value
```

When the result is `no`, interactive mode offers to run:

```bash
loginctl enable-linger "$USER"
```

The command is shown before execution and the result is verified. Declining
does not abort installation, but the services may stop after logout. In
non-interactive mode no change is made unless `--enable-linger` is explicitly
passed. Whether enabling it requires additional permission depends on host
policy; a permission failure aborts with the `loginctl` error and no `sudo` is
attempted. After enabling it, reboot and verify both services without an
interactive login.

## Troubleshooting

### `node` or `npm` not found

Install both with the host's supported package method. Confirm `node --version`
and `npm --version` in the same login environment used by the installer.

### Incompatible Node.js

Node older than the upstream minimum is rejected. Node 22 is the tested
version. Upgrade to an active LTS release when the installer warns that an old
but nominally compatible version is obsolete. Snap aliases such as
`/snap/bin/node` are supported and intentionally kept as symlinks: resolving
that alias to `/usr/bin/snap` would run `snap --version` instead of
`node --version`. The generated service avoids the private mount namespace
that prevents snap-confine from finding the canonical snap directory in LXC.
Update the installer if an older copy reports snap metadata as the Node.js
version or the service reports `cannot fstatat canonical snap directory`.

### npm installation fails

Check registry access, proxy settings, free space, and write permission below
`~/.local`. No system npm-prefix or `sudo npm` is used. Rerun with `--verbose`;
passwords remain redacted.

### joplin-md-sync release download or checksum fails

Confirm GitHub Releases is reachable and that the requested
`--joplin-md-sync-version`
exists with `SHA256SUMS.txt` plus a Linux AMD64 or ARM64 standalone asset. A
checksum, identity, or capability failure leaves the previously installed
binary unchanged. Do not bypass checksum verification; retry or select a known
release explicitly. If executable verification reports a missing `GLIBC`
version or dynamic loader, the host is older than the release build runtime;
use a newer distribution or explicitly select an earlier compatible release
instead of bypassing executable verification.

### Nextcloud URL is wrong or the directory is absent

Compare it with a working Joplin client. Target 5 expects a Nextcloud URL, not
a generic target-6 WebDAV URL. The remote Joplin directory must already exist
for this existing-store workflow.

### Nextcloud returns 401 or 403

401 normally means wrong credentials. 403 can indicate WebDAV policy,
read-only access, an application-password requirement, or a blocked path. Test
the same account and URL in a normal Joplin client.

### Wrong E2EE password

The installer treats `Invalid password` as failure even though Joplin 3.6.2
returns exit code 0. Correct the secret and rerun; no new key is created.

### Master key has not downloaded

Do not run `e2ee enable`. Rerun the initial sync, confirm the target is the
existing E2EE store, then retry. `--skip-initial-sync` is unsuitable for a new
empty profile.

### Encrypted items remain

Stop the service before manual profile commands, then run the exact `e2ee
decrypt --retry-failed-items` command printed by the installer. Restart with
`systemctl --user start joplin-terminal.service` only after decryption succeeds.

### Data API does not start or `/ping` does not answer

Check the service journal and the profile's `log-clipper.txt`. Confirm the unit
uses the same `--api-port` configured during installation and that local
firewall policy permits loopback.

For a complete secret-safe diagnostic report, download and run the repository
collector. It does not read token/password contents or print note bodies, and
its TUI/API probe uses a new temporary empty profile:

```bash
curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
  'https://raw.githubusercontent.com/kogeler/joplin-md-sync/main/scripts/joplin_terminal_service/collect_joplin_debug.sh' \
  --output /tmp/collect-joplin-debug.sh
bash /tmp/collect-joplin-debug.sh "$HOME/joplin-terminal-debug.txt"
```

Review `~/joplin-terminal-debug.txt` before sharing it. Custom installations
must set their original `JOPLIN_INSTALL_PREFIX`, `JOPLIN_PROFILE_DIR`, API/MCP
ports, and XDG variables before running the collector.

### Port is already occupied

The supervisor refuses to treat an existing listener as its own API. Choose a
different fixed port with `--api-port` and rerun the full installer. It rewrites
both units from that one value. If port 8765 is occupied instead, choose a
different `--mcp-port`. The API and MCP listener ports may not be equal.

### Profile is locked

Do not start another Joplin command against this profile while the service is
running. Stop the user service and retry. A live lock is never deleted to force
entry.

### Service continually restarts

Inspect `journalctl --user -u joplin-terminal.service`. Common causes are an
occupied port, moved executable, invalid profile path, API startup timeout, or
repeated API health failure. If a version manager removed or moved Node.js,
rerun the full installer so the unit records the new absolute path. The
supervisor checks `node --version` and profile write access inside the service
sandbox before it starts Joplin, so failures in either check are reported
directly. If startup verification fails, the installer stops the failed unit instead of
leaving it in an unlimited restart loop.

For adapter restart loops, inspect
`journalctl --user -u joplin-md-sync.service`. Verify the standalone binary
is executable, the Joplin API token is readable by the user, port 8765 is free,
and `joplin-terminal.service` is active. The required Actions token file must
be readable and mode `0600`. The MCP token file is also required, readable, and
mode `0600`; the unit must include both token-file arguments.

### systemd user bus is unavailable

Run `systemctl --user status` from a real login session. Containers, cron jobs,
SSH setups without PAM user sessions, and incomplete `XDG_RUNTIME_DIR` setups
may not have a user bus.

### Service stops after logout

Run the full installer again and accept its lingering prompt, pass
`--enable-linger` in non-interactive mode, or configure lingering manually as
described above. Confirm `loginctl show-user "$USER" -p Linger --value` prints
`yes`.

### MCP cannot read the API token

Confirm the joplin-md-sync process runs as the same user, the path is
`~/.config/joplin-agent/api-token`, its mode is 0600, and the deployed adapter
`ExecStart` passes both `--port 41185` and that `--token-file` path.

### Actions endpoint returns 401

Use the contents of `~/.config/joplin-md-sync/gpt-actions-token` only as the
Bearer credential in the Custom GPT editor. Confirm the unit includes
`--gpt-actions` and the matching `--gpt-actions-token-file` path. Do not replace
it with the Joplin API token, MCP token, or an OpenAI API key. On a clean
installation this file is generated with the required entropy before the
service unit is started.

### MCP client receives 401

Use the contents of `~/.config/joplin-md-sync/mcp-token` as the MCP bearer
token. It is not the Joplin API token. Reruns and upgrades preserve an existing
valid token; the headless installer does not provide an unauthenticated mode.

### Remote MCP bind is rejected

`--allow-remote-mcp` uses the generated MCP token. Verify the generated unit
contains `--host 0.0.0.0`, `--allow-remote-mcp`, and `--auth-token-file`, then
inspect the MCP journal. A firewall may still block access even when the
process is listening successfully.

## Live acceptance

With Joplin running locally, place its token in the ignored repository-root
`token` file and run both protocol suites together:

```bash
chmod 600 token
make test-live
```

The MCP suite exercises every MCP capability, authentication, Origin handling,
upstream outages, and notebook-icon rejection. The Actions suite starts the
same combined listener, invokes every Actions-exposed operation, and verifies
credential isolation and rotation, routing, validation, request/response
limits, rate limiting, and upstream errors.

Both suites use randomized UUID-owned entities, preserve the pre-existing
Joplin state, clean up owned notes, notebooks, tags, and resources after
success or failure, verify their absence, stop temporary processes, and remove
temporary credentials. Always run the complete target after changing either
transport.

## Manual integration checklist

1. Install in a clean user account.
2. Complete initial sync against the real Nextcloud target.
3. Confirm existing notes decrypt and no new E2EE key appears on other clients.
4. Confirm both generated services are active and that the installer reports
   successful MCP and Actions smoke checks.
5. Configure an MCP client with the generated bearer token and read a note.
6. Change a note on a phone and sync the phone.
7. Wait for the headless recurrent interval and confirm MCP sees the change.
8. Change a note through MCP.
9. Wait for headless sync, sync the phone, and confirm the phone sees it.
10. Restart `joplin-terminal.service` and confirm no E2EE prompt is required.
11. Configure a private Custom GPT and verify an Actions read and narrow write.
12. Confirm lingering is enabled, reboot, and verify both services start.

## Developer tests

The subproject is deliberately outside the repository's package and CI. Run
its dependency-free suite locally in Podman:

```bash
podman run --rm \
  -v "$PWD:/workspace:ro" \
  -w /workspace/scripts/joplin_terminal_service \
  python:3.14-slim \
  python3 -m unittest discover -s tests -v
```

The suite checks PTY prompts, secret non-echo, dynamic latest-version
resolution, release architecture selection, SHA-256 verification, combined
upgrades, the single adapter unit, mandatory generated Actions and MCP tokens,
both URI smoke checks, lingering decisions, API health loss, signals, forced
shutdown, and orphan prevention. Real Nextcloud
credentials, phone propagation, and reboot persistence remain explicit manual
acceptance checks.
