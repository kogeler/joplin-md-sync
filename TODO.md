# TODO

## Extend OAuth MCP to ChatGPT

`/oauth/mcp` currently permits only the hosted Claude callback. Extend it so
ChatGPT can connect as an MCP client, which replaces Actions after the Custom
GPT retirement below:

- Confirm the redirect URIs and client registration method (DCR or CIMD) that
  ChatGPT uses.
- Add them to an exact-match redirect allowlist without opening loopback or
  wildcard redirects.
- Cover the new client in the OAuth E2E suite and validate it on a live
  deployment.
- Update the connector guide and the documents that state Claude-only support.

Finish this before removing Actions so ChatGPT access continues without a gap.

## Remove ChatGPT Actions after the Custom GPT retirement

OpenAI retires Custom GPTs on **2026-12-11** for standard ChatGPT plans. It
announced this on 2026-09-11, and FedRAMP and approved Enterprise deferral
workspaces keep them until 2027-02-11. GPT custom actions are not carried over
by the migration to plugins and stop working with the GPT. Confirm the date in
the [OpenAI help center](https://help.openai.com/en/articles/20001519) before
acting.

After that date, remove the ChatGPT Actions interface from code and
documentation completely:

- Code: `/api/gpt/v1/*`, `gpt_actions.py`, `gpt_openapi.py`,
  `gpt-actions export-openapi`, every `--gpt-actions*` option, the Actions
  token handling and their tests.
- Installer: the Actions token file, smoke test, unit options and related
  settings, including cleanup of the managed token file on upgrade.
- Documentation: `docs/user/CHATGPT_ACTIONS.md` and every reference in README,
  AGENTS.md, interface guides, contracts, architecture and site navigation.
- Release: ship the removal as a breaking major release with migration notes in
  the changelog.
