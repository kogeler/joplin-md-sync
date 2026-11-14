# Source in Bash after replacing every placeholder with your own public settings.
# Only paths to private secret files appear in arguments; never put secrets here.
OAUTH_ARGS=(
  --github-oauth
  --github-oauth-public-url https://joplin.example.invalid
  --github-oauth-client-id YOUR_GITHUB_OAUTH_APP_CLIENT_ID
  --github-oauth-client-secret-file /home/USER/.config/joplin-md-sync/github-client-secret
  --github-oauth-signing-key-file /home/USER/.config/joplin-md-sync/oauth-signing-key
  --github-oauth-allowed-user-id 12345678
  --github-oauth-login-hint YOUR_LOGIN
  --github-oauth-state-dir /home/USER/.local/state/joplin-md-sync/oauth
  --github-oauth-access-token-ttl 3600
)
