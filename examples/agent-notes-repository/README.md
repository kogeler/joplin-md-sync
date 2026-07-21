# Joplin notes agent repository

This repository keeps a reviewable Git history of Joplin notes as Markdown and
gives a coding agent a safe procedure for editing and synchronizing them. The
locally installed `joplin-md-sync` executable, its state database, backups, and
credentials are deliberately excluded from Git.

## 1. Enable the Joplin API and identify its token

Do this before asking an agent to initialize or sync the notes:

1. Start **Joplin Desktop** and keep it running while synchronization commands
   execute.
2. On Windows or Linux, open **Tools > Options > Web Clipper**.
3. Enable **Web Clipper Service** and wait until its service status says it is
   running (normally on port `41184`).
4. Locate the **Authorization token** shown on that page. This is the Web
   Clipper token, not a Joplin Cloud password or an end-to-end-encryption
   password. You will copy it into a protected file in step 3.
5. After copying this template in the next step, put the token by itself on one
   line in `<your-new-repository>/.secrets/joplin-token`. Do not create the
   secret in the `joplin-md-sync` source checkout. Do not paste it into an
   agent chat, a command argument, a Git file, or a log.

The repository-local `.secrets/joplin-token` path is the recommended token
location throughout this runbook. It is outside the `./notes` sync workspace
and is covered by the template's `.gitignore`.

## 2. Create this repository

Copy this template out of a `joplin-md-sync` checkout, then initialize Git:

```bash
cp -R examples/agent-notes-repository ~/my-joplin-notes
cd ~/my-joplin-notes
git init
git add .
git commit -m "Initialize Joplin notes workspace"
```

PowerShell equivalent:

```powershell
Copy-Item -Recurse examples\agent-notes-repository $HOME\my-joplin-notes
Set-Location $HOME\my-joplin-notes
git init
git add .
git commit -m "Initialize Joplin notes workspace"
```

## 3. Store the token inside the new repository

From the new repository root, these Linux commands prompt without echoing the
token:

```bash
install -d -m 700 .secrets
read -rsp 'Joplin Web Clipper token: ' JOPLIN_TOKEN; printf '\n'
printf '%s\n' "$JOPLIN_TOKEN" > .secrets/joplin-token
unset JOPLIN_TOKEN
chmod 600 .secrets/joplin-token
```

On Windows PowerShell 7, these commands mask the prompt:

```powershell
New-Item -ItemType Directory -Force .secrets | Out-Null
$token = Read-Host -MaskInput "Joplin Web Clipper token"
[IO.File]::WriteAllText(
  (Join-Path (Get-Location) ".secrets\joplin-token"),
  $token + [Environment]::NewLine
)
Remove-Variable token
```

The `.secrets/` directory is ignored by Git. After creating it and before any
later commit, verify that `git status --short --ignored` reports it with `!!`,
never as an untracked or staged file. On a shared computer, also restrict the
Windows ACL to your account.

## 4. Install the local standalone

```bash
python3 scripts/install-joplin-md-sync.py
```

PowerShell:

```powershell
python scripts\install-joplin-md-sync.py
```

The installer selects the latest stable standalone release for Linux AMD64,
Linux ARM64, or Windows AMD64. It verifies the release SHA-256 and the binary's
reported origin/version before installing it under the ignored `.tools/`
directory. Python 3.9 or newer is needed only to run this small installer; the
installed executable contains its own Python runtime.

## 5. Import the existing Joplin notes

Use `remote-first` when Joplin already contains the source notes:

```bash
TOOL=./.tools/joplin-md-sync
"$TOOL" version --json
"$TOOL" init --root ./notes --mode remote-first
"$TOOL" doctor --root ./notes --token-file ./.secrets/joplin-token --json
"$TOOL" pull --root ./notes --token-file ./.secrets/joplin-token --json
git add notes
git commit -m "Import notes from Joplin"
```

In PowerShell, use `$TOOL = ".\.tools\joplin-md-sync.exe"` and invoke commands
with `& $TOOL ...`. Do not use `local-first` merely to bypass an initialization
error: that mode treats existing local Markdown as notes to create in Joplin.
Its guarded migration procedure is documented in [RUNBOOK.md](RUNBOOK.md).

## Working with an agent

Ask the agent for the note outcome, not low-level file edits. For example:

> Update my deployment note with the actual rollback steps from this project,
> show me the planned Joplin operations, then synchronize the changes.

The repository's [AGENTS.md](AGENTS.md) tells the agent to pull first, preserve
note identities, show a three-way diff, run a dry-run, and only then push the
authorized changes. [RUNBOOK.md](RUNBOOK.md) covers routine use, recovery,
conflicts, deletions, updates, and the optional MCP mode.
