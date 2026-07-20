# ChatGPT Actions Setup

This guide configures the OpenAI side only. Deploy and validate the endpoint
first with [Joplin API service](SERVICE.md).
Creating and editing GPTs currently requires the ChatGPT web experience and a
paid account with permission to build GPTs. Mobile applications can use a
saved GPT but cannot create or edit it.

OpenAI product behavior changes independently of this repository. Before a
release, recheck the official pages for
[creating GPTs](https://help.openai.com/en/articles/8554397-gpts),
[configuring Actions](https://help.openai.com/en/articles/9442513), and
[production limits](https://developers.openai.com/api/docs/actions/production).

## Configure a private GPT

1. On the web, open `https://chatgpt.com/gpts`, select **Create**, and use the
   direct configuration view.
2. Set a name, short description, and conversation starters. Do not include a
   credential in any of these fields.
3. Paste [Joplin Action policy](CHATGPT_INSTRUCTIONS.md) into the
   Instructions field.
4. Disable Apps. A GPT can use Apps or Actions, not both.
5. Select a non-Pro model mode offered by the editor that supports Actions.
   Actions are currently unavailable in Pro mode.
6. In Actions, select **Create new action**. Import or paste the
   `chatgpt-action.openapi.json` exported for the deployed public hostname.
7. Resolve every schema validation error. Confirm that the detected operation
   IDs exactly match the operation IDs in the imported JSON.
8. Open Authentication, select **API key**, and select **Bearer**. Enter only
   the dedicated GPT Actions token. Never enter the Joplin or MCP token.
9. In Preview, test a read Action. Test a narrow write when needed and confirm
   that consequential operations display the platform confirmation prompt.
10. Save the GPT with private visibility. Reopen it and repeat the read test to
    catch draft/import/authentication persistence problems.

Managed workspaces may restrict allowed Action domains. Ask an administrator
to allow the exact public hostname when the editor says no domains are
allowed. Public/link sharing has additional privacy-policy requirements; this
integration is designed for private visibility.

## Android and Voice

Open the saved GPT from the Android GPT list/sidebar and send a text request.
Account, workspace, model, region, and application version can affect
availability. Record those values during acceptance testing.

Custom GPT Actions are not available in Voice mode according to the current
[ChatGPT Voice documentation](https://help.openai.com/en/articles/20001274).
Use a text message when an Action must run.

## Updates and decommissioning

After a registry change, export the OpenAPI JSON again, replace the schema in
the editor, resolve validation errors, save, and repeat Preview tests. For a
token rotation, atomically replace the server token file and promptly replace
the Bearer credential in the editor.

To decommission, remove the public Actions route, disable the server transport,
remove the Action or delete the private GPT, and revoke the token.

## Troubleshooting

- Missing model choices: use a non-Pro model mode that the editor offers for
  Actions.
- Apps/Actions conflict: disable Apps before adding the Action.
- Domain denied: update the Enterprise/Edu Action-domain allowlist.
- Invalid server URL: use one public HTTPS origin on port 443 with no path.
- TLS failure: use a publicly trusted certificate and TLS 1.2 or later.
- 401: replace the editor credential with the dedicated Actions token.
- Confirmation absent: verify `x-openai-isConsequential` in the imported
  operation and reimport the current schema.
- Stale operations: regenerate and reimport after updating the adapter.

There is currently no supported public API for creating or updating a Custom
GPT with Actions. Login, MFA/passkey, credential entry, review, and Save remain
manual web-editor steps.
