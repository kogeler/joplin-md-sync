# Joplin Headless Service

Run Joplin on a Linux server without a desktop, sync it with your other Joplin
clients, and connect through MCP or ChatGPT Actions.

## Quick runbook

<a id="installation"></a>

Run in Bash as your service user, **not root**. You need Linux/systemd,
Python 3.13/3.14, Node.js/npm, and curl; see [requirements](#requirements).
No clone or local script is needed.

Choose [Nextcloud](#install-with-nextcloud), [S3](#install-with-s3), or
[removal](#remove-the-local-installation), and replace the example values.

<a id="headless-linux-installation"></a>

### Interactive installation and updates

Run the same entry point without any options for guided setup:

```bash
set -o pipefail
curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
  'https://raw.githubusercontent.com/kogeler/joplin-md-sync/main/scripts/joplin_terminal_service/install_joplin_terminal.py' \
  | python3 -
```

CLI options answer the corresponding setup questions. Missing values are
requested only when needed; secret input is hidden. Optional yes/no questions
show `[y/N]`, and Enter means no. Selecting `none` as the sync backend installs
local services without synchronization. Answer yes to service startup and
automatic startup if you want the services running after setup.

When the two services already exist, the same command updates them and keeps
their configured paths, ports, sync credentials, optional features, version
selection and startup choices. It does not repeat setup questions. For example,
adding `--github-oauth` asks only for missing OAuth settings and its hidden
Client Secret. Existing OAuth settings are reused.

If an earlier setup stopped after creating the profile but before installing
both services, rerun the same command. The installer reads that profile's sync
settings through Joplin before asking sync questions, so accepting the defaults
keeps its target, location, account and saved password. Only an unconfigured
profile asks for the sync backend again. An explicit sync option or
`--reconfigure` still changes the configuration; an empty profile directory is
treated as a new installation.

`--reconfigure` starts a complete configuration flow using only this run's CLI,
environment and interactive answers. Optional features default to no. Disabled
features lose their active settings and installer-owned secrets/grants; inactive
sync credentials are cleared through Joplin's supported CLI. Full reconfiguration resets managed OAuth registrations/grants and removes
unused owned keys; reconnect OAuth clients afterward. Existing notes and
operator-owned external secret/state files are retained. Program/profile
relocation is a separate installation, not an in-place reconfiguration.

### Install with Nextcloud

Use the sync URL and account from a working Joplin client. The remote Joplin
directory must already exist.

```bash
set -o pipefail
curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
  'https://raw.githubusercontent.com/kogeler/joplin-md-sync/main/scripts/joplin_terminal_service/install_joplin_terminal.py' \
  | python3 - \
      --sync-target nextcloud \
      --sync-location 'https://cloud.example.com/remote.php/dav/files/user/Joplin' \
      --sync-username 'user'
```

The installer asks for the Nextcloud password without displaying it. Use an
application password if your server requires one.

### Install with S3

Use an existing private bucket and credentials allowed to list the bucket and
read, write, and delete its objects. Replace the bucket, S3 API endpoint, and
region with the values supplied by your provider.

```bash
set -o pipefail
curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
  'https://raw.githubusercontent.com/kogeler/joplin-md-sync/main/scripts/joplin_terminal_service/install_joplin_terminal.py' \
  | python3 - \
      --sync-target s3 \
      --sync-location YOUR_BUCKET_NAME \
      --s3-endpoint 'https://s3.region-1.storage.example.com' \
      --s3-region region-1
```

Both the **access key and secret key are requested interactively and hidden
while you type**. Files are optional, not a setup prerequisite.
Add `--s3-force-path-style` only if your provider requires path-style requests.
The installer does not create the bucket or change its permissions. See
[custom S3 settings](#custom-s3-compatible-provider) for addressing details.

### Remove the local installation

!!! danger "This deletes the local notes profile"
    `--clean` (alias `--purge`) permanently deletes the managed local profile, programs,
    credentials, units, state, and upgrade backups. **It does not delete remote
    Nextcloud/S3 data.** Make a verified JEX export in Joplin first if you need
    to retain local notes. This is not a sync-target migration command.

```bash
set -o pipefail
curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
  'https://raw.githubusercontent.com/kogeler/joplin-md-sync/main/scripts/joplin_terminal_service/install_joplin_terminal.py' \
  | python3 - --clean
```

This command runs without questions or a confirmation flag. Add `--dry-run`
to preview the selected paths. Installed units recover custom paths; keep the
same XDG variables and supply path overrides for incomplete/unreadable setups.
See [custom-path removal](#removal).

### What to expect during installation

The commands execute the installer from GitHub's current `main` branch. For
reviewed/pinned code, use [the advanced download procedure](#review-or-pin-the-installer).
Hidden prompts use the controlling terminal, so they also work when the
script itself arrives through the curl pipeline.

The installer offers to enable **lingering**, which lets your user services
keep running after logout and start at boot without a login. Accept this for
an unattended server. Host policy may require an administrator to allow it.

It installs Joplin Terminal and joplin-md-sync, syncs the selected target, and
asks for the existing E2EE password if the downloaded notes are encrypted.
It only unlocks existing keys; it never enables encryption or creates a key.
Large stores can take a long time: a progress heartbeat appears every minute,
and each initial sync/decryption stage can run for up to 24 hours. Note titles
and bodies are not printed as progress.

If startup was selected, success includes Joplin API, MCP notebook-listing
and Actions authentication smoke checks. Otherwise the units remain stopped. Check the result:

```bash
systemctl --user status joplin-terminal.service joplin-md-sync.service
curl --fail http://127.0.0.1:41185/ping
curl --fail http://127.0.0.1:8765/readyz
```

`/ping` should return `JoplinClipperServer`; `/readyz` should return HTTP 200.
To use the result, configure an [MCP client](#mcp-integration) or follow the
[ChatGPT Actions end-to-end setup](CHATGPT_ACTIONS.md).

## What is installed

This is a per-user setup, separate from any Joplin Desktop profile. It does not
create a Markdown workspace or need a desktop session.

| Service | What it does | Default listener |
| --- | --- | --- |
| `joplin-terminal.service` | Runs Joplin Terminal, owns its profile, decrypts notes, and syncs every 5 minutes. | Joplin Data API: `127.0.0.1:41185` |
| `joplin-md-sync.service` | Connects to that API and serves **both** MCP and ChatGPT Actions. | Combined adapter: `127.0.0.1:8765` |

There is no third Actions service. Both listeners are local by default. The
installer does not configure a firewall, DNS, reverse proxy, or public HTTPS.
Never publish the Joplin Data API itself.

<a id="installed-layout"></a>

### Where your data and credentials live

These are the default paths. `~` means the home of the account that ran the
installer, not root's home.

| Location | Contents |
| --- | --- |
| `~/.local/share/joplin-agent/profile/` | Dedicated Joplin notes and resources, sync settings/credentials, and unlocked E2EE key cache. Treat the whole profile as sensitive. |
| `~/.local/bin/` | `joplin` launcher and `joplin-md-sync` executable. |
| `~/.config/systemd/user/` | `joplin-terminal.service` and `joplin-md-sync.service`. |
| `~/.config/joplin-agent/api-token` | Private Joplin Data API credential; used by the adapter, not by clients. |
| `~/.config/joplin-md-sync/mcp-token` | Bearer credential for MCP clients at `/mcp`. |
| `~/.config/joplin-md-sync/installer-settings.json` | Nonsecret saved version/startup choices; owner-only. |
| `~/.config/joplin-md-sync/oauth-settings.json` | Saved OAuth values and secret-file paths, without secret values. |
| `~/.config/joplin-md-sync/oauth-secrets/` | Owner-only Client Secret/signing-key files created by interactive setup. |
| `~/.config/joplin-md-sync/gpt-actions-token` | Separate bearer credential for ChatGPT Actions at `/api/gpt/v1/*`. |
| `~/.local/state/joplin-agent/` | Profile lock, retained upgrade backups and the `joplin-md-sync` ownership receipt. |
| `~/.local/state/joplin-md-sync/oauth/` | Default encrypted OAuth grants when GitHub OAuth is enabled. |

All three token files are owner-only (`0600`). The installer prints their
paths, not their contents. Reruns preserve valid existing MCP/Actions tokens;
an invalid existing token is rejected, not silently replaced. The two adapter
tokens are generated automatically and differ from each other and the API
token. None is an OpenAI API key.

The [full layout and path overrides](#custom-paths-and-full-file-layout) below
show where the npm packages, helper scripts, and custom installations go.

## Installer CLI reference

The entry point is `scripts/joplin_terminal_service/install_joplin_terminal.py`.
Pass options after `python3 -` in the remote commands above, or after
`python3 install_joplin_terminal.py` when using a downloaded copy. Use
`--help` for the built-in summary.

The default mode automatically installs or updates. `--reconfigure` rebuilds
the selected configuration; `--clean` removes the managed installation.
No setup option is mandatory in interactive mode. `--upgrade` and `--purge`
remain compatibility aliases for explicit update/removal. `--dry-run` previews
any mode without prompting, reading secret files, or changing the installation.
When using the remote form, bootstrap downloads the verified Python package and assets into a private
`/tmp/joplin-installer-*` directory before argument handling, including for
`--help` and `--dry-run`. The temporary directory is removed on exit.
Install/upgrade dry runs also check that the required host programs exist and
that Node/npm report usable versions; they do not require a working user bus.

For settings, precedence is **CLI → environment → existing configuration →
interactive answer/default**. Reconfiguration skips existing configuration.
Boolean feature questions default to no; an explicit positive or negative
flag replaces the question. `--non-interactive` and `--dry-run` are optional
automation controls and never prompt.

### Sync and credential options

| Option | Meaning and default | Environment |
| --- | --- | --- |
| `--sync-target TARGET` | Prompted when not configured. Default interactive choice: `none`. One of the targets in the table below. | `JOPLIN_SYNC_TARGET` |
| `--sync-location VALUE` | Target directory, bucket name, or HTTP(S) URL; required as shown below. No default. | `JOPLIN_SYNC_LOCATION` |
| `--sync-username VALUE` | Nextcloud/WebDAV username or Joplin Server email. Also accepts an S3 access key for compatibility; **prefer the hidden S3 prompt**, not an argument. | `JOPLIN_SYNC_USERNAME` |
| `--sync-username-file PATH` | Optional protected username/access-key file. Replaces only that prompt; cannot be combined with `--sync-username` or its environment variable. | `JOPLIN_SYNC_USERNAME_FILE` |
| `--sync-secret-file PATH` | Optional protected target-password/S3-secret file. Default: hidden prompt when needed. | `JOPLIN_SYNC_SECRET_FILE` |
| `--e2ee-password-file PATH` | Optional protected existing E2EE password. Read only if encrypted data needs unlocking and no working key cache exists. Default: hidden prompt when needed. | `JOPLIN_E2EE_PASSWORD_FILE` |
| `--s3-endpoint URL` | S3 API endpoint. Default: `https://s3.amazonaws.com/`. S3 only. | `JOPLIN_S3_ENDPOINT` |
| `--s3-region REGION` | Required S3 region. No default; S3 only. | `JOPLIN_S3_REGION` |
| `--s3-force-path-style` | Enable bucket-in-path S3 requests. Default: disabled. | none |
| `--no-s3-force-path-style` | Explicitly disable path-style requests. S3 only. | none |

Credential files must be current-user-owned, regular, non-symlink files with
no group/other access (normally `0600`), at most 4096 bytes, containing exactly
one non-empty UTF-8 line. Raw sync/E2EE passwords are not accepted in arguments
or environment variables. [Optional file-based automation](#password-files)
is described below.

### Sync targets

| Target | `--sync-location` | Username and authentication |
| --- | --- | --- |
| `none` | Not used | Disable synchronization and clear inactive sync settings during reconfiguration. |
| `nextcloud` | Existing Nextcloud Joplin directory URL | Username required; hidden password prompt. |
| `s3` | Bucket name, **not** a URL | `--s3-region` required; custom `--s3-endpoint` optional. Both keys use hidden prompts by default. |
| `filesystem` | Existing writable absolute directory | No credentials. The directory is not created for you. |
| `webdav` | WebDAV directory URL | Optional username; hidden password prompt when a username is given. Omit username for anonymous access. |
| `joplin-server` | Server URL | Account email required as username; hidden password prompt. |
| `onedrive` | Not used | Interactive browser callback. |
| `dropbox` | Not used | Interactive browser code. |
| `joplin-cloud` | Not used | Interactive browser confirmation. |
| `joplin-server-saml` | Server URL | Interactive browser and login code. |

HTTP(S) URLs cannot contain credentials, query strings, or fragments. Plain
HTTP is accepted with a warning for private development servers. Browser/SAML
targets cannot use `--non-interactive`; unsupported combinations fail before
the profile is modified.

### Paths, ports, versions, and schedule

| Option | Meaning and default | Environment |
| --- | --- | --- |
| `--profile-dir PATH` | Dedicated profile. Default: `${XDG_DATA_HOME:-$HOME/.local/share}/joplin-agent/profile`. Never use a Desktop profile or one opened by another process. | `JOPLIN_PROFILE_DIR` |
| `--joplin-prefix PATH` | Program prefix. Default: `~/.local`. Does **not** relocate the profile, config, or state. | `JOPLIN_INSTALL_PREFIX` |
| `--api-port PORT` | Private loopback API port, passed to both services. Default: `41185`. | `JOPLIN_API_PORT` |
| `--mcp-port PORT` | Shared MCP/Actions port. Default: `8765`; must differ from the API port. Both ports accept `1..65535`. | `JOPLIN_MCP_PORT` |
| `--github-oauth` | Enable built-in GitHub OAuth on the existing port. Upgrade preserves installed settings. See [Claude setup](CLAUDE_CONNECTOR.md). | `JOPLIN_GITHUB_OAUTH` (`1`, `true` or `yes`; `0`, `false` or `no` acts as `--no-github-oauth`; other values are rejected) |
| `--github-oauth-public-url URL` | Public HTTPS origin for discovery and callbacks; same setting as the service CLI. | `JOPLIN_GITHUB_OAUTH_PUBLIC_URL` |
| `--github-oauth-client-id ID` | GitHub OAuth App Client ID; same setting as the service CLI. | `JOPLIN_GITHUB_OAUTH_CLIENT_ID` |
| `--github-oauth-client-secret-file PATH` | Protected Client Secret file. Default: hidden prompt, saved under `oauth-secrets/`. | `JOPLIN_GITHUB_OAUTH_CLIENT_SECRET_FILE` |
| `--github-oauth-signing-key-file PATH` | Protected signing-key file. Default: a generated owner-only key under `oauth-secrets/`. | `JOPLIN_GITHUB_OAUTH_SIGNING_KEY_FILE` |
| `--github-oauth-allowed-user-id ID` | Single permitted numeric GitHub account ID; same setting as the service CLI. | `JOPLIN_GITHUB_OAUTH_ALLOWED_USER_ID` |
| `--github-oauth-login-hint USER` | Optional GitHub login hint, never an access rule. | `JOPLIN_GITHUB_OAUTH_LOGIN_HINT` |
| `--github-oauth-state-dir PATH` | Absolute private state directory. Default: `~/.local/state/joplin-md-sync/oauth`; a custom directory is operator-owned and kept by purge. | `JOPLIN_GITHUB_OAUTH_STATE_DIR` |
| `--github-oauth-access-token-ttl SECONDS` | Access-token lifetime from `600` to `3600`. Default: `3600`. | `JOPLIN_GITHUB_OAUTH_ACCESS_TOKEN_TTL` |
| `--sync-interval SECONDS` | Default: `300`. Allowed: `300`, `600`, `1800`, `3600`, `43200`, `86400`. | `JOPLIN_SYNC_INTERVAL` |
| `--joplin-version VERSION` | npm Joplin version: `latest` (default) or `X.Y.Z`, optionally with a prerelease suffix. | `JOPLIN_VERSION` |
| `--joplin-md-sync-version VERSION` | Adapter GitHub release: `latest` (default) or `X.Y.Z`, optionally with a prerelease suffix. | `JOPLIN_MD_SYNC_VERSION` |

`latest` selects the current stable release at installation/upgrade time. Pin
both versions for a repeatable deployment. Environment-only path controls are
`HOME`, `XDG_DATA_HOME`, `XDG_CONFIG_HOME`, and `XDG_STATE_HOME`; their exact
effects are in [the full layout](#custom-paths-and-full-file-layout).
`JOPLIN_TERMINAL_ASSET_BASE_URL` selects the HTTPS bundle base when the package
is absent locally; pin it to the same commit as the entry script. A SHA-256
manifest pinned by the entry point verifies every downloaded module/template
before import. An inconsistent bundle fails before setup starts.

### Mode and lifecycle switches

| Option | Effect |
| --- | --- |
| `--installer-ref REF`, `--ref REF` | Installer branch, tag or commit SHA; default `main`, environment `JOPLIN_TERMINAL_INSTALLER_REF`. Fetch the entry script from the same ref. |
| `--upgrade` | Update both programs, helpers, and units; preserve installed settings and tokens. Retain previous program/service files. No sync or E2EE password prompts. Requires both installed units. |
| `--clean`, `--purge` | Without prompts or a confirmation flag, permanently remove the managed local setup, including profile and backups. Remote data and lingering are unchanged. Cannot be combined with `--upgrade`. |
| `--yes` | Compatibility no-op, accepted only with `--clean`/`--purge`, which already need no confirmation flag. |
| `--dry-run` | Validate arguments and print a redacted plan. Do not install programs, open the profile or secret files, generate tokens, sync, modify services, or delete files. |
| `--non-interactive` | Never prompt. Supply required static values and files for every needed credential. Browser/SAML authentication is not supported. |
| `--reconfigure`, `--force-reconfigure` | Rebuild configuration from this run. Prompt for missing settings; remove disabled managed OAuth secrets/grants and inactive sync credentials. Keep notes and external operator-owned files. |
| `--enable-linger`, `--no-enable-linger` | Select whether setup enables systemd user lingering. Without it, interactive install asks; non-interactive install only warns if lingering is off. |
| `--enable-service`, `--no-enable-service` | Enable/disable automatic startup. Interactive default: no; updates preserve the saved choice. |
| `--start-service`, `--no-start-service` | Start services and run smoke checks, or leave them stopped. Interactive default: no; updates preserve the saved choice. |
| `--no-github-oauth` | Disable OAuth in the existing unit while preserving configuration, secret files and grants. Cannot be combined with an explicit OAuth config or `--github-oauth`. `JOPLIN_GITHUB_OAUTH=false` (or `0`/`no`) has the same effect; either command-line flag overrides the variable. |
| `--allow-remote-mcp` | Bind the **shared** authenticated adapter to `0.0.0.0` instead of loopback. Does not expose the Joplin Data API or configure HTTPS/firewall rules. |
| `--no-allow-remote-mcp` | Explicitly bind the adapter to loopback, including on upgrade. First install defaults to loopback; upgrade inherits installed policy. |
| `--verbose` | Enable redacted debug logging; sensitive Joplin responses remain suppressed. |
| `-h`, `--help` | Show built-in help and exit. |

The installer generates MCP/Actions tokens itself; `JOPLIN_MCP_AUTH_TOKEN` and
`JOPLIN_GPT_ACTIONS_TOKEN` are ignored. No raw bearer-token installer options
are provided.

## Advanced installation and maintenance

The following examples use a downloaded `install_joplin_terminal.py`. You can
also pass the same arguments to `python3 -` in the remote runbook commands.

### Requirements

Use a Linux AMD64 or ARM64 host with a working systemd user session. Check
`systemctl --user status` from the installing account. Python 3.13/3.14 and
Node.js/npm must already be installed; Node 22 is tested. Curl is needed for
the remote commands. The installer neither installs OS packages nor uses `sudo`.

Installation needs access to GitHub, the npm registry, and your sync target.
Prepare its directory/bucket and credentials, plus the existing E2EE password
if your notes are encrypted. Only the sync target is needed for ongoing note
sync; the installed services do not fetch installer code from GitHub.

### Run from a branch, tag or commit

`main` is the remote default. To test an unmerged installer version on your
server, choose its branch, tag or full commit SHA once and use the same ref
for the downloaded entry point and `--installer-ref` (`--ref` alias):

```bash
installer_ref='feature/your-branch'
installer_ref_url=$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$installer_ref")
set -o pipefail
curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
  "https://raw.githubusercontent.com/kogeler/joplin-md-sync/${installer_ref_url}/scripts/joplin_terminal_service/install_joplin_terminal.py" \
  | python3 - --installer-ref "$installer_ref"
```

Append `--dry-run` to preview setup before applying it. Replace
`feature/your-branch` with your branch, or with a tag or commit SHA to pin the
exact installer source. All modules and
unit templates come from that selection; no companion files fall back to
`main`. The entry point checks the pinned manifest/file hashes and rejects a
mixed or changed bundle before setup. Local checkouts use their sibling
package by default; an explicit source ref selects a downloaded bundle.

`JOPLIN_TERMINAL_INSTALLER_REF` is the environment equivalent, with CLI taking
precedence. `JOPLIN_TERMINAL_ASSET_BASE_URL` can select a fork/mirror; include
`{ref}` in a custom URL when combining it with ref selection. Existing raw
GitHub base URLs also accept a ref override. Installer source selection is
independent of `--joplin-version` and `--joplin-md-sync-version`.

### Test an unpublished runtime candidate

Selecting an installer branch does not download an unreleased adapter from
that branch. Instead, a pull request from this repository whose `.version`
differs from its merge base publishes every push automatically as a GitHub
prerelease `vX.Y.Z-prN.gSHORTSHA`, for example `v1.8.0-pr48.g7bd6520`. It
contains the smoke-tested Linux amd64/arm64 executables and `SHA256SUMS.txt`;
the executable reports the same candidate version. Only the newest candidate of
a pull request is kept, and closing the pull request removes it. Candidates are
never the latest release, so `latest` installs and update checks ignore them.

The candidate's release notes contain the exact command, which runs the
installer from the same commit:

```bash
installer_ref='FULL_COMMIT_SHA'
set -o pipefail
curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
  "https://raw.githubusercontent.com/kogeler/joplin-md-sync/${installer_ref}/scripts/joplin_terminal_service/install_joplin_terminal.py" \
  | python3 - --installer-ref "$installer_ref" --joplin-md-sync-version 'X.Y.Z-prN.gSHORTSHA'
```

An existing installation keeps its configured settings; add only the options
you want to change, such as `--github-oauth`, and `--joplin-version` to pin
Joplin. The installer verifies the checksum, executable version and OAuth
preflight before replacing a healthy service.

For a build without a candidate prerelease, such as a fork pull request or a
local `make standalone`, serve the executable from a local release layout. For
Linux amd64, place `joplin-md-sync-linux-amd64` in
`$HOME/joplin-candidate/download/v1.8.0/`, using the version it reports, then
run:

```bash
candidate_root="$HOME/joplin-candidate"
candidate_version='1.8.0'
candidate_asset='joplin-md-sync-linux-amd64'
(cd "$candidate_root/download/v$candidate_version" && sha256sum "$candidate_asset" > SHA256SUMS.txt)
candidate_url=$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve().as_uri())' "$candidate_root")
installer_ref='feature/your-branch'
installer_ref_url=$(python3 -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$installer_ref")
set -o pipefail
curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
  "https://raw.githubusercontent.com/kogeler/joplin-md-sync/${installer_ref_url}/scripts/joplin_terminal_service/install_joplin_terminal.py" \
  | JOPLIN_MD_SYNC_RELEASES_URL="$candidate_url" python3 - --installer-ref "$installer_ref" \
      --joplin-md-sync-version "$candidate_version"
```

For Linux arm64, use the `joplin-md-sync-linux-arm64` asset name. The local
checksum describes the file you selected; it does not authenticate an
executable from an unknown source.

### Review or pin the installer

Download and inspect the installer before executing it:

```bash
curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
  'https://raw.githubusercontent.com/kogeler/joplin-md-sync/main/scripts/joplin_terminal_service/install_joplin_terminal.py' \
  --output install_joplin_terminal.py
less install_joplin_terminal.py
python3 install_joplin_terminal.py --help
```

Without the local `joplin_terminal_installer/` package, it downloads that
package, shared primitives, the supervisor and both unit templates into `/tmp`. Reviewing only
the entry point does not review those companions. For a reviewed commit,
replace `REVIEWED_COMMIT_SHA` below with that commit's full SHA, download from
the same base, and inspect the companion files too:

```bash
export JOPLIN_TERMINAL_INSTALLER_REF='REVIEWED_COMMIT_SHA'
export JOPLIN_TERMINAL_ASSET_BASE_URL="https://raw.githubusercontent.com/kogeler/joplin-md-sync/${JOPLIN_TERMINAL_INSTALLER_REF}/scripts/joplin_terminal_service"
curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
  "$JOPLIN_TERMINAL_ASSET_BASE_URL/install_joplin_terminal.py" \
  --output install_joplin_terminal.py
```

Alternatively, use a checkout of the reviewed commit with all sibling files
present. Pin `--joplin-version` and `--joplin-md-sync-version` separately: a
pinned installer commit does not pin the programs it installs. Deployed
services use local files and do not fetch installer code at runtime.

Preview by appending `--dry-run`. Supplied target arguments are validated;
missing setup values are shown as pending interactive setup. Credential-file
contents are not read and no profile/service settings are changed.

### Custom S3-compatible provider

The [S3 quick-start command](#install-with-s3) works with custom providers.
Check these values before running it:

| Setting | What to supply |
| --- | --- |
| Bucket | Existing dedicated bucket name, not `s3://...` or an HTTPS URL. |
| Endpoint | Provider's S3 API endpoint, not its console or a bucket-specific URL. |
| Region | Exact region identifier required by the provider. |
| Addressing | Path style is off by default. Add `--s3-force-path-style` if required, or `--no-s3-force-path-style` to explicitly disable it. |
| Credentials | Interactive hidden access-key and secret-key prompts by default. Each optional file replaces only its corresponding prompt. |

For AWS S3, omit `--s3-endpoint` to use `https://s3.amazonaws.com/`; still supply
the bucket and region. See also [Joplin's S3 guide](https://joplinapp.org/help/apps/sync/s3/).
The installer does not disable Joplin's wipe-out fail-safe. A sync error fails
installation even if Joplin exits with status zero.

### Password files

Use files only when you want unattended setup or already manage secrets that
way. An unattended S3 installation with optional credential files looks like:

```bash
python3 install_joplin_terminal.py \
  --sync-target s3 \
  --sync-location YOUR_BUCKET_NAME \
  --s3-endpoint 'https://s3.region-1.storage.example.com' \
  --s3-region region-1 \
  --sync-username-file /protected/s3-access-key \
  --sync-secret-file /protected/s3-secret-key \
  --non-interactive
```

Add `--e2ee-password-file /protected/e2ee-password` if encrypted remote data needs
unlocking, and `--enable-linger` if the unattended install should enable it.
You can also supply just one key file and enter the other key interactively;
in that case omit `--non-interactive`.

If you need to create a file manually, this Bash example hides input and does
not put the secret in history or process arguments:

```bash
install -m 600 /dev/null "$HOME/s3-secret-key"
read -rsp 'S3 secret key: ' s3_secret; echo
printf '%s\n' "$s3_secret" > "$HOME/s3-secret-key"
unset s3_secret
```

Run without shell tracing (`set -x`). Use a fresh file path, and remove the file
after setup if you do not need to retain it. Joplin keeps the credential and
unlocked E2EE cache in its private profile for unattended restarts. Settings
reach Joplin through its supported JSON stdin import, not process arguments
or temporary settings files.

### Other sync targets

Use the same installer with the required arguments from the CLI table:

```bash
# Existing directory or mounted filesystem; never a missing mount point
python3 install_joplin_terminal.py \
  --sync-target filesystem --sync-location /srv/joplin-sync

# WebDAV; omit the username only for anonymous access
python3 install_joplin_terminal.py \
  --sync-target webdav --sync-location 'https://dav.example.com/Joplin' \
  --sync-username user

# Joplin Server; the username is the account email
python3 install_joplin_terminal.py \
  --sync-target joplin-server --sync-location 'https://joplin.example.com' \
  --sync-username 'user@example.com'
```

### Browser authentication

For OneDrive, Dropbox, or Joplin Cloud, pass just `--sync-target onedrive`,
`--sync-target dropbox`, or `--sync-target joplin-cloud`. These targets require
an interactive terminal, not `--non-interactive`.

OneDrive redirects to `http://127.0.0.1:9967/auth`. When installing over SSH,
connect with forwarding **before** starting the installer:

```bash
ssh -L 9967:127.0.0.1:9967 user@host
```

Dropbox asks for the code returned after browser authorization; Joplin Cloud
asks for confirmation. For SAML, use `--sync-target joplin-server-saml
--sync-location https://joplin.example.com`; follow the printed
`SERVER/login/sso-saml-app` URL and enter its 9-digit code. Authorization is
stored in the profile; no raw browser/session token argument is needed.

### Custom paths and full file layout

Three independent path choices matter: **program prefix**, **profile**, and
**XDG config/state homes**. Changing the prefix does not move the other two.
With all defaults, the complete managed layout is:

```text
~/.local/
├── bin/
│   ├── joplin -> ../share/joplin-agent/npm/bin/joplin
│   └── joplin-md-sync
├── share/joplin-agent/
│   ├── npm/                       isolated Joplin npm installation
│   └── profile/                   notes, resources, sync/E2EE state
├── lib/joplin-terminal-service/
│   ├── run_joplin_terminal.py
│   └── joplin_terminal_common.py
└── state/
    ├── joplin-agent/
    │   ├── profile-*.lock         exclusive lock, keyed by profile
    │   ├── joplin-md-sync-receipt.json  path and SHA-256 of the installed adapter
    │   └── upgrade-backup-*/      previous program/helper/unit files
    └── joplin-md-sync/oauth/      default OAuth grants (with OAuth only)

~/.config/
├── joplin-agent/api-token
├── joplin-md-sync/
│   ├── mcp-token
│   ├── gpt-actions-token
│   ├── installer-settings.json    saved version/startup choices
│   ├── oauth-settings.json        OAuth settings (with OAuth only)
│   ├── oauth-managed-state.json   marks the default OAuth state as managed
│   └── oauth-secrets/             generated OAuth secret files
└── systemd/user/
    ├── joplin-terminal.service
    ├── joplin-md-sync.service
    └── *.service.bak-*            previous revisions of these two units
```

| Override | What moves |
| --- | --- |
| `--joplin-prefix PREFIX` | `PREFIX/bin/{joplin,joplin-md-sync}`, `PREFIX/share/joplin-agent/npm/`, and `PREFIX/lib/joplin-terminal-service/`. |
| `--profile-dir PATH` | Only the dedicated Joplin profile. |
| `XDG_DATA_HOME` | Default profile becomes `$XDG_DATA_HOME/joplin-agent/profile/`; explicit `--profile-dir` wins. Does not move the npm tree. |
| `XDG_CONFIG_HOME` | Both credential directories and `systemd/user/`. Default: `$HOME/.config`. |
| `XDG_STATE_HOME` | `joplin-agent/` lock and upgrade backups, plus the default `joplin-md-sync/oauth/` state. Default: `$HOME/.local/state`. |
| `HOME` | Home used for default locations and `~` expansion. Run consistently as the intended service user. |

For example, keep the normal config/state locations but move programs and the
profile to two different directories in your account:

```bash
python3 install_joplin_terminal.py \
  --sync-target s3 \
  --sync-location YOUR_BUCKET_NAME \
  --s3-endpoint 'https://s3.region-1.storage.example.com' \
  --s3-region region-1 \
  --joplin-prefix "$HOME/apps/joplin-headless" \
  --profile-dir "$HOME/data/joplin-headless-profile" \
  --api-port 41186 --mcp-port 8766 --sync-interval 600
```

The prefix is not a system npm prefix: dependencies stay isolated from other
global packages. Unknown existing launchers/binaries are refused, not
overwritten. A recognized old npm-managed Joplin directly under the prefix
is migrated only after its isolated replacement is verified.

Units record absolute Python, Node, and program paths; they do not depend on
your login-shell `PATH`. If a version manager moves Node, rerun the full
installer. Normal Node installations get a read-only filesystem sandbox with
writes allowed to the profile, lock directory, and any filesystem sync target.
For Snap Node, the stable `/snap/bin/node` alias is retained and incompatible
mount-namespace restrictions are omitted; the adapter's sandbox is unchanged.
The adapter unit is read-only except, with GitHub OAuth enabled, its selected
OAuth state directory.

### Service management

Check status and recent lifecycle logs:

```bash
systemctl --user status joplin-terminal.service joplin-md-sync.service
journalctl --user -u joplin-terminal.service -u joplin-md-sync.service -n 50 --no-pager
```

| Action | Command |
| --- | --- |
| Stop both | `systemctl --user stop joplin-md-sync.service joplin-terminal.service` |
| Start both | `systemctl --user start joplin-terminal.service joplin-md-sync.service` |
| Restart both | `systemctl --user restart joplin-terminal.service joplin-md-sync.service` |
| Follow Joplin logs | `journalctl --user -u joplin-terminal.service -f` |
| Follow adapter logs | `journalctl --user -u joplin-md-sync.service -f` |

The adapter depends on the Joplin unit. The supervisor keeps one Terminal TUI
process running, starts the Data API inside it, and monitors API health. That
same process owns recurrent sync and decryption; repeated health failures
cause a systemd restart. Raw TUI output is discarded because it can contain
note text; lifecycle errors go to the journal.

Do not run a second Joplin CLI process against the managed profile. Before
manual Joplin maintenance, stop **both** services and finish the CLI command
before restarting them. Never remove a live profile lock to force access.

### Lingering

Check whether services can survive logout and start without a login:

```bash
loginctl show-user "$USER" --property=Linger --value
```

If it says `no`, rerun interactive install and accept the offer, use
`--enable-linger`, or run `loginctl enable-linger "$USER"` under the host's
normal permission policy. The installer never escalates through `sudo`.
Verify `yes`, then reboot and check the services without an interactive login.

### API check

```bash
curl --fail http://127.0.0.1:41185/ping
curl --fail http://127.0.0.1:8765/healthz
curl --fail http://127.0.0.1:8765/readyz
```

`/ping` returns `JoplinClipperServer`. Adapter health/readiness routes do not
require a token and return no notes or credentials. Use your chosen ports if
you changed the defaults. Do not expose port 41185 to a public network.

### MCP integration

Configure a Streamable HTTP MCP client with URL `http://127.0.0.1:8765/mcp`
and `Authorization: Bearer <token>`, using the contents of
`~/.config/joplin-md-sync/mcp-token`. Do not use `api-token` or the Actions
token. The headless installer always requires MCP authentication.

For a basic authenticated check without putting the secret in process arguments:

```bash
curl --silent --show-error --output /dev/null --write-out '%{http_code}\n' \
  --config - <<EOF
url = "http://127.0.0.1:8765/mcp"
header = "Authorization: Bearer $(<~/.config/joplin-md-sync/mcp-token)"
EOF
```

Expected HTTP status: `405` with a valid token (MCP uses POST), `401` otherwise.
The installer additionally initializes MCP and calls `joplin_list_notebooks`;
a `405` alone is not an end-to-end test.

### Remote MCP access

For a reverse proxy on the same host, keep the default loopback binding.
If another host must reach the adapter directly, add `--allow-remote-mcp`
during installation or upgrade. This exposes **both** MCP and Actions on
`0.0.0.0`; restrict access with a firewall/private network and a trusted TLS
publishing layer. The flag does not configure either of those for you.

Keep the generated MCP bearer token mandatory and never publish the upstream
Data API. Browser clients that send `Origin` may need additional MCP origin
configuration. See [self-hosted deployment](SELF_HOSTED.md) and the
[MCP API reference](MCP_API.md).

### ChatGPT Actions integration

Actions already run on the same adapter under `/api/gpt/v1/*`. Configure public
HTTPS through your own publishing layer, preferably for that namespace only,
and use the separate `gpt-actions-token` file. Do not log authorization headers
or request bodies. Follow the [ChatGPT Actions end-to-end setup](CHATGPT_ACTIONS.md)
for the public endpoint, GPT configuration, and read/write acceptance checks.

### Updating and rollback

Update both Joplin Terminal and the adapter to current stable versions:

```bash
python3 install_joplin_terminal.py --upgrade
```

Or run the remote command from the quick runbook with only `--upgrade` after
`python3 -`. To preview, add `--dry-run`; to pin versions, for example:

```bash
python3 install_joplin_terminal.py --upgrade \
  --joplin-version 3.7.1 --joplin-md-sync-version 1.8.0
```

The installer resolves versions and downloads, verifies and smoke-tests a
changed `joplin-md-sync` executable before stopping services. It preserves the
profile and valid MCP/Actions tokens, updates both programs/helpers/units, and repeats
the API/MCP/Actions checks. It does not ask for stored sync or E2EE passwords.
Missing adapter token files are generated; existing valid values are not rotated.

Existing units supply the prefix, profile, ports, interval, remote-binding
policy, and filesystem sandbox exceptions. Explicit CLI/environment settings
override them, but relocating the profile or prefix requires a full install,
not `--upgrade`. Keep the original XDG config/state environment. Missing or
inconsistent units are refused before stopping services. `--no-start-service`
deliberately leaves them stopped and skips runtime checks.

Previous binaries, helpers, and units remain under
`~/.local/state/joplin-agent/upgrade-backup-*/` (or the custom state home).
When Joplin changes version, its old isolated npm tree is retained there too.
If the new Joplin package cannot be installed, that tree is moved back, the
units stay unchanged, and services that were running before the upgrade are
started again; the installer still reports the failure. Any later failure after
services are stopped leaves both stopped and reports the backup location; the
profile is never rolled back automatically. **These are program backups, not
note backups.** The profile is not copied and Joplin may migrate its schema.
Keep a verified JEX export before downgrading; selecting an older executable
does not prove that an existing profile can safely be downgraded.

### Switching from Nextcloud to S3

Changing the sync target is not just reinstalling the service. First decide
whether to preserve your notes or start empty.

1. Finish sync on the authoritative Joplin client and make a verified JEX
   export through Joplin. Keep the old remote data and local profile.
2. Prepare a dedicated bucket. To stage a replacement with a **new** profile,
   use a separate Linux service account and follow the installation flow there.
   The installer manages one pair of services per account and refuses to relocate
   an existing pair to another profile or prefix, including with `--reconfigure`.
3. For an empty setup, install against the empty bucket. Existing Nextcloud
   notes are **not** transferred automatically. To retain notes, plan their
   supported import/migration in Joplin and coordinate every other client.
4. Check notes, attachments, decryption, and sync in both directions before
   retiring the old setup. Keep the old profile for recovery.

Do not use `--purge` to remove just the old profile: it also uninstalls both
managed services and their credentials, including the replacement setup.

An explicit target/location selection authorizes that configuration change.
`--reconfigure` rebuilds setup choices and removes inactive managed credentials;
it does not migrate notes to another backend. Never
copy/edit a Joplin database or sync-target objects by hand, and never disable
the wipe-out fail-safe to make a migration proceed.

### Removal

For default paths, use the remote purge command in the quick runbook. For a
custom installation, preview with the **original** prefix, profile, and XDG
environment before deleting:

```bash
python3 install_joplin_terminal.py --purge --dry-run \
  --joplin-prefix "$HOME/apps/joplin-headless" \
  --profile-dir "$HOME/data/joplin-headless-profile"
```

Repeat with `--clean` and without `--dry-run` to remove the selected setup without questions. It stops and
disables both services, verifies they are inactive, acquires the exclusive
profile lock, and deletes only managed local paths. Unrelated files under
the prefix/config directories remain. Repeating purge is safe.
`bin/joplin-md-sync` is deleted only when it is the exact file the installer
last placed there, as recorded by its private receipt. A symlink, a changed
file, or an executable that this installer accepted but never replaced (for
example, one installed by an earlier installer release) is kept with a message;
remove it yourself if nothing else uses it.

If only one unit file remains, or the units cannot supply consistent paths,
normal setup refuses to overwrite the installation. Restore the units, or use `--clean` with
the existing explicit `--profile-dir` and `--joplin-prefix` before reinstalling.
Cleanup still runs without questions; it requires those paths to avoid guessing
which profile belongs to a damaged service pair.

For an explicitly disposable automation account only,
`--clean` runs without input and **permanently deletes the selected local profile**.
The older `--purge --non-interactive --yes` form remains accepted. Remote sync data remains untouched. Lingering
also remains enabled because other user services may use it; disable it
separately only if that is intended for the whole account.

### Troubleshooting

Start with the journal for the failing unit, not a reinstall or purge.

| Symptom | What to check or do |
| --- | --- |
| `node`/`npm` missing or obsolete | Check `node --version` and `npm --version` in the installing account; install a supported Node/npm through the host's normal package method. Node 22 is tested. |
| systemd user bus unavailable | Run `systemctl --user status` in a real login session. Check PAM/user-session and `XDG_RUNTIME_DIR` setup in SSH, cron, or containers. |
| npm install fails | Check registry/proxy access, free space, and prefix write permission. Do not use `sudo npm`; retry with `--verbose`. |
| Release download/checksum/identity fails | Check GitHub access and that the selected release has its Linux AMD64/ARM64 binary and `SHA256SUMS.txt`. Never bypass verification. Missing GLIBC/loader means the host may be too old for that binary. |
| Nextcloud 401/403 or missing directory | Compare URL/account with a working client, check application-password and WebDAV permissions, and confirm the Joplin directory exists. |
| S3 authentication/endpoint failure | Check bucket, region, API endpoint, addressing mode, and object permissions with the provider. Re-enter both keys; a zero Joplin exit status does not mean sync succeeded. |
| Wrong E2EE password or missing master key | Verify the intended encrypted store and password; rerun initial sync. Do **not** run `e2ee enable` to fix an existing store. |
| Encrypted items remain | Stop both services; run the exact `e2ee decrypt --retry-failed-items` command printed by the installer, then retry installation. |
| API fails or service restarts | Read `journalctl --user -u joplin-terminal.service`; check port collisions, moved Node, profile permission, and the selected API port. Joplin diagnostic files inside the profile are sensitive. |
| Snap Node fails with canonical-directory error | Use a current installer so `/snap/bin/node` is kept and incompatible mount-namespace restrictions are omitted. Do not point at a Snap revision directory. |
| Port occupied | Choose distinct `--api-port`/`--mcp-port` values and rerun install so both units agree. The supervisor will not adopt another process's listener. |
| Profile locked | Stop the managed services and any other process using that profile; do not delete the lock. |
| Adapter fails or MCP returns 401 | Read `journalctl --user -u joplin-md-sync.service`; verify backend is active, tokens are readable/owner-only, and the client uses `mcp-token`, not `api-token`. |
| Remote connection refused | Check binding, firewall, TLS proxy, and MCP origin policy. `--allow-remote-mcp` is not a firewall/HTTPS installer. |
| Services stop after logout | Check [lingering](#lingering); it should report `yes`. |

For a diagnostic report, download the collector and run it as the service user:

```bash
curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
  'https://raw.githubusercontent.com/kogeler/joplin-md-sync/main/scripts/joplin_terminal_service/collect_joplin_debug.sh' \
  --output collect-joplin-debug.sh
bash collect-joplin-debug.sh "$HOME/joplin-terminal-debug.txt"
```

It avoids token/password contents and note bodies; its TUI/API probe uses a
temporary empty profile. Review the report before sharing. For custom paths,
set the original `JOPLIN_INSTALL_PREFIX`, `JOPLIN_PROFILE_DIR`,
`JOPLIN_API_PORT`, `JOPLIN_MCP_PORT`, and XDG variables first.

<a id="live-acceptance"></a>
<a id="manual-integration-checklist"></a>

### Check sync after setup

Read a note through MCP, change a test note on another client and sync it, then
wait for the headless sync interval and confirm the change through MCP. Change
that test note through MCP, wait for headless sync, and confirm it on the other
client. Check attachments/decryption, restart the service without a password
prompt, and verify boot startup after enabling lingering.

Installer/runtime tests and maintainer acceptance are described in
[Service maintenance](../maintenance/SERVICE.md). Exact guarantees and evidence
are in the [service contract](../contracts/SERVICE.md).

### Desktop or existing Joplin

Already running Joplin Desktop? Do not install a second profile merely to get
MCP. Use [local stdio or HTTP MCP](MCP_API.md),
[self-hosted deployment](SELF_HOSTED.md), and the
[ChatGPT Actions setup](CHATGPT_ACTIONS.md) instead.

### Windows autostart

This headless installer is Linux-only. For an existing Windows Joplin setup,
use the [`install-service-task.ps1`](../../examples/windows/install-service-task.ps1)
example with its `-JoplinTokenFile`, required `-GptActionsTokenFile`, and
optional `-McpAuthTokenFile`. Use separate owner-only token files; MCP
authentication is mandatory if you publish MCP remotely. The example's
parameters and help describe the per-user Task Scheduler setup.
