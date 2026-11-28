---
title: "Connect Claude to Joplin with GitHub login"
description: "Connect hosted Claude to your Joplin notes with built-in GitHub OAuth and no separate OAuth proxy: OAuth app, service setup, HTTPS routes and troubleshooting."
---

# Claude connector with GitHub login

Connect hosted Claude to Joplin at `https://EXISTING_HOST/oauth/mcp` and allow
one explicitly configured GitHub account. OAuth is part of the standard package
and native executable, so no separate OAuth proxy or identity gateway is
needed. Enable it with `--github-oauth` on the existing MCP service and port;
the headless installer asks for the few values it needs and configures the
rest.

## Scope and limitations

- **Hosted Claude only.** The only permitted client redirect URI is
  `https://claude.ai/api/mcp/auth_callback`, used by custom connectors added in
  Claude. Other MCP clients are rejected at registration, including ChatGPT
  connectors and Claude Code or other local clients with loopback redirects.
  Local clients keep using `/mcp` with its Bearer token.
- **One GitHub.com account.** Access is bound to one numeric GitHub user ID.
  GitHub Enterprise Server and organization or team rules are not supported.
- **Public HTTPS.** Claude connects from the internet, so the OAuth routes need
  a public HTTPS origin through a reverse proxy or tunnel. The listener itself
  can stay on loopback.
- **Separate credentials.** `/mcp` keeps its own Bearer token and does not
  accept OAuth tokens; `/oauth/mcp` accepts only OAuth tokens. Enabling
  OAuth requires a protected `/mcp` Bearer token, even on loopback. Joplin's
  Data API stays local.
- **Full tool access.** The account can use every registered MCP tool; there are
  no read-only scopes.
- **GitHub dependency.** Every request rechecks the GitHub identity, so a GitHub
  outage or revoked grant blocks OAuth access. Claude asks for a new sign-in
  about every six months.

## Prepare the account and OAuth App

Register a GitHub.com OAuth App under **Settings > Developer settings > OAuth
Apps**:

| Field | Value |
| --- | --- |
| Homepage URL | `https://EXISTING_HOST` |
| Redirect URI (authorization callback URL) | Exactly `https://EXISTING_HOST/auth/callback` |
| Allow wildcard matching | Off |
| Enable Device Flow | Off |
| Expire user access tokens | On; older forms show it under **Optional features** |

The service requires expiring access and refresh tokens and does not request
`offline_access`. It requests `read:user` and no repository permissions. Then
generate a Client Secret; it stays on your server and is never entered in
Claude. See
[GitHub OAuth authorization](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps).

Resolve the account during setup:

```bash
joplin-md-sync mcp github-user-id --login YOUR_LOGIN --json
```

Save the returned `github_user_id` in the configuration. The service checks
that numeric ID through GitHub; a username is only a display/login hint.
Renaming the account does not transfer access to whoever takes its former name.
If the lookup is unavailable, supply an independently verified numeric ID.

## Create protected configuration

Use a private directory outside any Markdown workspace. This Linux example
uses the default XDG config location; replace paths if you use custom XDG homes.
The following command reads the GitHub Client Secret through a hidden prompt
and generates a separate signing key. Neither secret is printed or placed in
the process arguments. Existing files are preserved. For a headless
installation, you can skip this step and let the
[installer](#enable-the-existing-service) create its own managed files.

```bash
python3 - <<'PY'
import base64
import getpass
import os
import secrets
from pathlib import Path

os.umask(0o077)
config = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'joplin-md-sync'
config.mkdir(mode=0o700, parents=True, exist_ok=True)
secret_path = config / 'github-client-secret'
key_path = config / 'oauth-signing-key'
if secret_path.exists() or key_path.exists():
    raise SystemExit('Secret files already exist; preserve them or select a new private directory.')
secret = getpass.getpass('GitHub OAuth App Client Secret: ')
with secret_path.open('x', encoding='ascii') as output:
    output.write(secret + '\n')
with key_path.open('x', encoding='ascii') as output:
    output.write(base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode() + '\n')
PY
```

Configure nonsecret settings with explicit CLI values. Only the Client Secret
and signing key are read from private files, matching the existing token-file
interface. The repository provides [example CLI arguments](../../examples/oauth/github-oauth-args.sh)
and [Nginx routes](../../examples/oauth/nginx-routes.conf).

For repeated commands in Bash, prepare an argument array and replace the
example ID with your verified account ID:

```bash
OAUTH_ARGS=(
  --github-oauth
  --github-oauth-public-url https://EXISTING_HOST
  --github-oauth-client-id YOUR_GITHUB_OAUTH_APP_CLIENT_ID
  --github-oauth-client-secret-file /home/USER/.config/joplin-md-sync/github-client-secret
  --github-oauth-signing-key-file /home/USER/.config/joplin-md-sync/oauth-signing-key
  --github-oauth-allowed-user-id 12345678
  --github-oauth-login-hint YOUR_LOGIN
  --github-oauth-state-dir /home/USER/.local/state/joplin-md-sync/oauth
  --github-oauth-access-token-ttl 3600
)
```

The numeric ID is required; the login hint is optional and does not grant access.
TTL is an integer from 600 to 3600 seconds. Missing required flags, relative
paths, unsafe secret permissions and ambiguous routes fail before binding.
On Linux use `0600` secret files and `0700` private directories; on Windows use
owner-only ACLs as described in the [CLI reference](CLI.md).

For source/wheel installation, install the ordinary package. Native executables
bundle Python and the runtime. A Python zipapp runs in an environment containing
the standard package dependencies; it does not embed native extension wheels.

## Enable the existing service

Validate with the same connection and Bearer options used by your service:

```bash
joplin-md-sync mcp oauth-check \
  "${OAUTH_ARGS[@]}" \
  --token-file /protected/joplin-token \
  --auth-token-file /protected/mcp-token --json
```

This command makes no GitHub calls, opens no listener and creates no OAuth
state. Startup in the actual service sandbox checks writing and locking.

Foreground operation uses the same port as before:

```bash
joplin-md-sync mcp serve \
  --token-file /protected/joplin-token \
  --auth-token-file /protected/mcp-token \
  "${OAUTH_ARGS[@]}"
```

For a headless installation, pass `--github-oauth` to the
[installer](JOPLIN_HEADLESS_SERVICE.md). An existing installation does not ask
whether to enable OAuth, so the flag is required there; the installer then asks
only for missing OAuth settings, hides the Client Secret input and creates
private secret/key files. The same settings flags can answer individual
questions for automation. Later upgrades recover the installed OAuth flags from
the unit and preserve them unless explicitly overridden. Pin `--joplin-version`
to the version you want to retain when upgrading only the adapter. The
installer checks the new executable and OAuth config before stopping a healthy
service, retains the old executable and units, and grants `ReadWritePaths` only
for the selected OAuth state path.

The Linux user unit explicitly enables `PrivateUsers=true` so filesystem
hardening applies in the user manager. The host must support unprivileged user
namespaces; see [systemd sandbox requirements](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#PrivateUsers=).
On Ubuntu hosts that restrict unprivileged user namespaces with AppArmor, the
administrator must also allow `userns` for `/usr/lib/systemd/systemd-executor`
through a local AppArmor profile. Follow the
[Ubuntu namespace policy instructions](https://documentation.ubuntu.com/release-notes/24.04/#unprivileged-user-namespace-restrictions).
An error at `CAPABILITIES` before application startup can indicate this host
policy; keep the service sandbox enabled and correct the host policy instead.
`make test-oauth-systemd` proves that the built executable can write its selected
state while the surrounding install directory is read-only.

`--no-github-oauth` removes OAuth from the installed unit and preserves config
and grants. Re-enable with `--github-oauth`; saved settings are reused unless a
settings flag overrides them. Purge removes the installer-registered default
OAuth state and the installer-created OAuth settings and secret files. Custom
state directories and operator-owned config/secret files are preserved.

## Add public routes on the existing HTTPS hostname

Keep the current `/mcp` proxy rules. Add the OAuth routes to the
same upstream listener. The public hostname/origin is fixed by configuration;
the service does not trust forwarded headers to construct it.

For an existing Nginx HTTPS server, the additional routing can be:

```nginx
location = /healthz { return 404; }
location = /readyz { return 404; }

location ~ ^/(oauth/mcp|authorize|register|token|consent|auth/callback|\.well-known/oauth-authorization-server|\.well-known/oauth-protected-resource(/oauth/mcp)?)$ {
    access_log off;
    error_log /dev/null crit;
    proxy_pass http://127.0.0.1:8765;
    proxy_set_header Host $http_host;
    proxy_http_version 1.1;
    proxy_buffering off;
    client_max_body_size 16m;
}
```

Use the existing adapter port if it differs from `8765`. The discovery document
and callback refer to the public HTTPS origin, never to the private listener.
Before browser login, an anonymous request to `/oauth/mcp` returns `401` and
a protected-resource metadata challenge.

`/healthz` and `/readyz` must stay private. A tunnel or proxy that forwards the
whole hostname, such as Cloudflare Tunnel, must block them itself. For a locally
managed `cloudflared` configuration, put this ingress rule before the
hostname's main rule:

```yaml
- hostname: EXISTING_HOST
  path: ^/(healthz|readyz)$
  service: http_status:404
```

OAuth callback URLs contain codes and state. The example suppresses raw access
and request error logs for these routes; troubleshoot through the service's safe
errors. If adding proxy auditing, record only method, normalized path and status,
without query strings, headers or bodies. Apply the same policy to any outer
proxy/CDN. See Nginx [access logging](https://nginx.org/en/docs/http/ngx_http_log_module.html#access_log)
and [error logging](https://nginx.org/en/docs/ngx_core_module.html#error_log).

## Connect Claude and verify

Before connecting, check the public routes from any machine:

```bash
origin=https://EXISTING_HOST
for path in /.well-known/oauth-protected-resource/oauth/mcp \
  /.well-known/oauth-authorization-server /healthz /readyz; do
  echo "$(curl -s -o /dev/null -w '%{http_code}' "$origin$path") $path"
done
echo "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$origin/oauth/mcp") POST /oauth/mcp"
```

Expect `200` for both discovery documents, `401` for the anonymous `POST`, and
anything except `200` for the health routes.

Then add a custom connector in hosted Claude with the URL
`https://EXISTING_HOST/oauth/mcp`. Leave the optional OAuth Client ID and
Client Secret fields in its advanced settings empty; never enter the GitHub App
values there. Claude registers a downstream client through DCR. Approve the
service's consent screen, then sign in to GitHub with the configured account.
The consent screen warns you to approve only a connection you started yourself
in the last few minutes; the application name it shows is chosen by the
requester. If you did not just add the connector, choose Deny.
See [Claude connector authentication](https://claude.com/docs/connectors/building/authentication).

Validate the deployed connection with these checks:

1. Run the read-only `oauth-check` with the intended CLI values, check the
   public routes as shown above, and confirm continued access for existing
   MCP clients.
2. Complete Claude registration, consent and GitHub login with the permitted
   account, then list tools and perform a read.
3. In a dedicated temporary notebook, create, read and update a test note and
   exercise the attachment operations you need. Keep test object IDs and clean
   up through supported Joplin operations, touching only those test objects.
4. Restart the service with the same signing key and state directory; confirm
   Claude reconnects and existing MCP clients still work.
5. After the configured access-token lifetime expires, confirm Claude can
   continue calling tools without another browser login. Do not extract or log
   credentials. Replay rejection is covered by mandatory automated E2E.
6. If practical, also wait for real upstream GitHub expiry and verify continued
   access through refresh. Record explicitly if this longer check was not run;
   controlled-time upstream refresh remains part of automated acceptance.

Record the deployed version and results without credentials or note content.
Expiry/refresh and foreign-account checks are also covered by the mandatory
mocked E2E suite; the hosted connection needs its own deployment validation.

All registered MCP tools are exposed. Note deletion moves notes to trash;
tag and resource deletion is permanent. Finish pending Markdown edits before
direct writes, then pull to refresh the workspace.

## Maintain access and recover

Configuration, allowed account and signing-key changes take effect after a
service restart. Keep the same signing key and state directory for ordinary
restarts. A new public origin also needs the matching App redirect URI and a
reconnected Claude connector. Every protected request rechecks GitHub identity;
a GitHub outage or revoked grant denies OAuth access while the existing Bearer
interfaces remain independent.

To revoke every local OAuth grant, stop the service and run:

```bash
joplin-md-sync mcp oauth-reset \
  "${OAUTH_ARGS[@]}" \
  --confirm-reset --json
```

Restart and reconnect Claude. The command requires the state lock and leaves
config/secrets intact. To revoke the GitHub-side grant as well, use GitHub's
Authorized OAuth Apps settings. For signing-key loss/rotation, provision the
new protected key and reset local grants before reconnecting.

Upgrade backups preserve executables and units, not old OAuth grants. Keep
current compatible state during a binary rollback. Restoring an older OAuth
state snapshot can restore revoked refresh grants: invalidate those grants by
rotating the signing key and resetting state before reconnecting. Application
rollback does not reverse already applied note changes.

## Monitor OAuth access

The service writes security events to its log at default verbosity, without
tokens, codes, secrets or request content. On a headless installation:

```bash
journalctl --user -u joplin-md-sync -g oauth_audit
```

| Event | Meaning |
| --- | --- |
| `service_started`, `client_registered`, `consent_approved`, `token_issued`, `refresh_succeeded` | Normal connection activity; `client` is the first 8 characters of the client ID |
| `consent_denied` | The consent screen was answered with Deny |
| `consent_rejected reason=session`, `callback_rejected reason=binding` | A consent or login completed in a browser that did not start it: a forged or shared link |
| `foreign_account github_id=N` | A different GitHub account tried to sign in |
| `refresh_denied reason=unknown_token` | A rotated, expired or revoked refresh token was presented; repeated events can mean a stolen token |
| `resource_denied`, `rate_limited`, `registration_rejected` | Rejected tokens, exhausted rate budgets or refused registrations |
| `github_unavailable`, `login_denied`, `state_reset` | GitHub outages, refused logins and local grant resets |

Events that anyone can trigger are written at most once per minute per reason;
the next line reports the `suppressed` count. To revoke access after a
suspicious event, stop the service and run `mcp oauth-reset` as described above.

## Troubleshoot sign-in

OAuth errors return a safe `error` and `error_description` without provider
responses or credentials.

| Description or status | Cause and action |
| --- | --- |
| `GitHub rejected the OAuth App configuration` (`500 server_error`) | GitHub refused the Client Secret or the callback URL. Check the secret file and that the App callback is exactly `https://EXISTING_HOST/auth/callback`, then restart. Existing grants are kept. |
| `GitHub expiring token profile required` | GitHub returned no refresh token or token lifetimes. Enable expiring user tokens for the App, then reconnect. |
| `GitHub account is not permitted` | A GitHub account other than the configured numeric ID signed in. |
| `GitHub grant is invalid or expired` | The upstream grant was revoked or expired. Reconnect Claude. |
| `Only the hosted Claude callback is permitted` | The client used a redirect URI other than `https://claude.ai/api/mcp/auth_callback`. |
| `Invalid Host` | The proxy did not forward the configured public host; keep `proxy_set_header Host $http_host`. |
| `503 temporarily_unavailable` | GitHub timed out, failed or rate limited the request. Retry after `Retry-After`. Refresh grants are kept, including when GitHub already rotated the grant before the identity check failed. An outage during the first sign-in needs a new sign-in. |
| `Client registration capacity exceeded; retry later` | 64 registrations are waiting for an owner sign-in, usually from a registration flood. Unused registrations expire after 30 minutes and free their slots; established connections keep working. |
| `Client capacity exceeded` (`invalid_client`) | 512 established clients are still live. Expired records free their slots; otherwise stop the service, run `mcp oauth-reset --confirm-reset`, which revokes every local grant, and reconnect. |
| `429` with `Retry-After` | The registration, token or browser-flow rate budget is exhausted. Each budget is separate, so a registration flood does not block refresh, and each connected client refreshes from its own token budget. `rate_limited` events name the exhausted budget. |
| Sign-in requested again after about six months | An established client record lives 15897600 seconds (about 184 days) from its first sign-in, even while in use. Sign in again; Claude registers a new client. |
