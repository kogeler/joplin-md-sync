---
title: "Joplin MCP server for Claude, ChatGPT and AI agents"
description: "Self-hosted Joplin MCP server with built-in OAuth for hosted Claude, ChatGPT Actions, local and HTTP MCP, a headless Linux service and Markdown sync."
template: home.html
hide:
  - navigation
  - toc
---

<section class="home-hero">
  <div class="home-shell home-hero__content">
    <p class="home-kicker" data-reveal>Claude &amp; ChatGPT <span></span> Local &amp; HTTP MCP <span></span> Self-hosted Joplin</p>
    <h1 data-reveal>Joplin MCP for Claude &amp; ChatGPT</h1>
    <p class="home-hero__lead" data-reveal>
      Search, read, create, organize, and update your own Joplin notes from the
      assistants you already use. Connect Claude with a GitHub login, give a
      private ChatGPT GPT live access, launch a local MCP process over stdio
      with no listener, or deploy guarded HTTP interfaces while keeping Joplin,
      its sync target, and its Data API under your control.
    </p>
    <div class="home-actions" data-reveal>
      <a class="home-button home-button--primary" href="user/CLAUDE_CONNECTOR/">
        Connect Claude
      </a>
      <a class="home-button home-button--secondary" href="user/CHATGPT_ACTIONS/">
        Connect ChatGPT
      </a>
      <a class="home-button home-button--secondary" href="user/MCP_API/#local-stdio">
        Connect local MCP
      </a>
      <a class="home-button home-button--secondary" href="user/JOPLIN_HEADLESS_SERVICE/">
        Deploy headless
      </a>
    </div>
    <ul class="home-highlights" data-reveal>
      <li>
        <strong>Built-in OAuth for hosted Claude</strong>
        Claude on claude.ai connects straight to Joplin on your own server
        through your GitHub login. OAuth is part of the server, so there is no
        separate OAuth proxy or gateway to deploy and configure.
      </li>
      <li>
        <strong>ChatGPT today</strong>
        A private Custom GPT works through authenticated Actions. OpenAI retires
        Custom GPTs on 2026-12-11; a ChatGPT MCP connector through the same
        built-in OAuth is planned as the replacement.
      </li>
      <li>
        <strong>One installer for the whole stack</strong>
        A single command sets up headless Joplin, sync, MCP, Actions, and
        Claude OAuth. It asks for the values it needs, hides secrets, generates
        keys and tokens, and verifies the result.
      </li>
    </ul>
    <p class="home-proof" data-reveal>
      Open source <span></span> Private by design <span></span> Desktop optional
    </p>

    <div class="hero-console" data-reveal>
      <div class="hero-console__bar">
        <div class="window-dots" aria-hidden="true"><i></i><i></i><i></i></div>
        <span>private Joplin assistant</span>
        <strong>authenticated</strong>
      </div>
      <div class="hero-console__flow" aria-label="Assistant to private Joplin data flow">
        <div class="flow-node flow-node--agent">
          <b>AI</b>
          <div><strong>Claude, ChatGPT or MCP</strong><small>Your chosen client</small></div>
        </div>
        <div class="flow-link">
          <span>typed operations</span>
          <i aria-hidden="true"></i>
        </div>
        <div class="flow-node flow-node--bridge">
          <b>J</b>
          <div><strong>Guarded bridge</strong><small>Authenticate, validate, execute</small></div>
        </div>
        <div class="flow-link">
          <span>loopback Data API</span>
          <i aria-hidden="true"></i>
        </div>
        <div class="flow-node flow-node--joplin">
          <!-- Official brandmark from https://joplinapp.org/brand/, used for identification only. -->
          <img src="site/assets/images/joplin-brandmark.png" alt="" width="44" height="42">
          <div><strong>Your Joplin</strong><small>Your profile and sync target</small></div>
        </div>
      </div>
      <div class="hero-console__run">
        <code><span>You</span> Find my notes about the production migration.</code>
        <code><span>Tool</span> joplin_search_notes {"query":"production migration"}</code>
        <code class="console-result">{"success":true,"results":[{"title":"Migration runbook"}]}</code>
      </div>
      <div class="hero-console__checks">
        <span><i></i> Claude connector</span>
        <span><i></i> ChatGPT Actions</span>
        <span><i></i> MCP tool schemas</span>
        <span><i></i> Private Joplin API</span>
      </div>
    </div>
  </div>
</section>

<section class="home-band home-intro">
  <div class="home-shell">
    <div class="section-heading" data-reveal>
      <p class="section-label">One knowledge base, direct agent access</p>
      <h2>Use Joplin where you already think and work.</h2>
      <p>
        Joplin remains the system of record. The bridge adds authenticated,
        structured interfaces without introducing another note store or requiring
        a permanent desktop session.
      </p>
    </div>
    <div class="value-columns">
      <article data-reveal>
        <span class="value-number">01</span>
        <h3>Talk to your notes in Claude or ChatGPT</h3>
        <p>
          Connect hosted Claude through your own GitHub login, or give a private
          Custom GPT live access to search, read, create, update, move, tag, and
          trash exact Joplin objects.
        </p>
        <a href="user/CLAUDE_CONNECTOR/">Connect Claude to Joplin &rarr;</a>
      </article>
      <article data-reveal>
        <span class="value-number">02</span>
        <h3>Connect any MCP client</h3>
        <p>
          Launch typed local stdio tools with no listener, or expose Streamable
          HTTP for notes, notebooks, tags, search, and attachments.
        </p>
        <a href="user/MCP_API/">Explore the MCP tools &rarr;</a>
      </article>
      <article data-reveal>
        <span class="value-number">03</span>
        <h3>Run Joplin without a desktop</h3>
        <p>
          One interactive installer sets up Joplin Terminal, recurrent sync, MCP,
          Actions, and Claude OAuth as coordinated rootless services on a Linux
          host.
        </p>
        <a href="user/JOPLIN_HEADLESS_SERVICE/">Deploy Joplin Headless Service &rarr;</a>
      </article>
    </div>
  </div>
</section>

<section class="home-band home-compare" id="built-in-mcp">
  <div class="home-shell">
    <div class="section-heading section-heading--split" data-reveal>
      <div>
        <p class="section-label">Joplin's built-in MCP server or this bridge</p>
        <h2>Reach your notes beyond the desktop.</h2>
      </div>
      <p>
        Joplin Desktop can expose its own MCP server on the Web Clipper port to
        clients on the same computer. Use this bridge when the assistant runs
        elsewhere, Joplin runs without a desktop, or a change needs review. Its
        OAuth is built in, so reaching hosted Claude takes no extra proxy.
      </p>
    </div>
    <div class="compare-table" data-reveal>
      <table>
        <thead>
          <tr><th scope="col"></th><th scope="col">Joplin's built-in MCP</th><th scope="col">joplin-md-sync</th></tr>
        </thead>
        <tbody>
          <tr><th scope="row">Hosted assistants</th><td>Not reachable: clients must run on the same computer</td><td>Hosted Claude through built-in GitHub OAuth with no separate OAuth proxy, and ChatGPT through a private Custom GPT with Actions</td></tr>
          <tr><th scope="row">Where it runs</th><td>Inside Joplin Desktop, which must stay open with the Web Clipper service</td><td>Beside Joplin Desktop, or headless on a Linux server with Joplin Terminal and recurrent sync</td></tr>
          <tr><th scope="row">Other MCP clients</th><td>Local clients through 127.0.0.1</td><td>Local stdio with no listener, or authenticated Streamable HTTP over HTTPS</td></tr>
          <tr><th scope="row">Setup</th><td>Switches in the desktop settings</td><td>One installer configures Joplin Terminal, sync, tokens, and OAuth from your answers</td></tr>
          <tr><th scope="row">Authentication</th><td>The Web Clipper token in the request URL</td><td>Separate MCP and Actions bearer tokens, plus GitHub OAuth limited to one account</td></tr>
          <tr><th scope="row">Attachments</th><td>Not exposed as tools</td><td>Read, upload, replace, and delete resources and their note links</td></tr>
          <tr><th scope="row">Bulk changes</th><td>Individual tool calls</td><td>Two-way Markdown sync with three-way diffs and dry-run pushes</td></tr>
          <tr><th scope="row">Search</th><td>Keyword and semantic search over the local embeddings index</td><td>Joplin keyword search; the tool guides assistants to retry with synonyms and word stems</td></tr>
        </tbody>
      </table>
    </div>
    <p class="compare-note" data-reveal>
      Only local desktop clients, and semantic search matters most? The built-in
      server is enough.
      <a class="text-link" href="user/AGENT_INTERFACES/">Choose an interface &rarr;</a>
    </p>
  </div>
</section>

<section class="home-band home-workflow home-deploy">
  <div class="home-shell">
    <div class="section-heading" data-reveal>
      <p class="section-label">From sync target to your assistant</p>
      <h2>Deploy the complete headless path.</h2>
      <p>
        One installer coordinates Joplin Terminal and the agent adapter. It asks
        for the sync target, tokens, and the GitHub OAuth settings for Claude,
        hides secrets, and checks the running services. You bring an HTTPS
        hostname; the upstream Data API never needs to leave loopback.
      </p>
      <a class="text-link" href="user/SELF_HOSTED/">Review the deployment boundaries &rarr;</a>
    </div>
    <div class="workflow-layout">
      <ol class="workflow-steps" data-reveal>
        <li><span>1</span><div><strong>Choose storage</strong><small>Connect the headless profile to your existing Joplin sync target.</small></div></li>
        <li><span>2</span><div><strong>Install services</strong><small>Deploy Joplin Terminal and the shared MCP, OAuth, and Actions adapter.</small></div></li>
        <li><span>3</span><div><strong>Publish safely</strong><small>Route only the authenticated MCP, OAuth, or Actions paths through HTTPS.</small></div></li>
        <li><span>4</span><div><strong>Connect your assistant</strong><small>Add the connector in Claude, or generate the Action schema for a private GPT.</small></div></li>
        <li><span>5</span><div><strong>Use your notes</strong><small>Search and change current Joplin data from Claude, ChatGPT, or MCP.</small></div></li>
      </ol>
      <div class="workflow-terminal" data-reveal>
        <div class="workflow-terminal__title">
          <span>headless-joplin.sh</span>
          <small>rootless systemd services</small>
        </div>
        <pre><code><em>$</em> set -o pipefail
<em>$</em> curl --proto '=https' --tlsv1.2 --fail \
    --silent --show-error --location \
    https://raw.githubusercontent.com/kogeler/\
joplin-md-sync/main/scripts/joplin_terminal_service/\
install_joplin_terminal.py | python3 - \
    --sync-target nextcloud \
    --sync-location https://cloud.example/Joplin \
    --sync-username user

<b>OK</b>  joplin-terminal.service active
<b>OK</b>  joplin-md-sync.service active
<b>OK</b>  separate MCP and Actions tokens created
<b>OK</b>  GitHub OAuth for Claude enabled and verified</code></pre>
      </div>
    </div>
  </div>
</section>

<section class="home-band home-use-cases" id="use-cases">
  <div class="home-shell">
    <div class="section-heading section-heading--split" data-reveal>
      <div>
        <p class="section-label">Useful every day</p>
        <h2>Current notes, not another stale export.</h2>
      </div>
      <p>
        Both direct interfaces use the same validated operation registry. Choose
        Claude or ChatGPT for conversation, or MCP for any compatible client.
      </p>
    </div>
    <div class="use-case-grid">
      <article class="use-case" data-reveal>
        <span class="use-case__tag">Claude &amp; ChatGPT</span>
        <h3>Search and synthesize your knowledge</h3>
        <p>Find the relevant Joplin notes, read only the selected results, and turn current private context into a focused answer.</p>
      </article>
      <article class="use-case" data-reveal>
        <span class="use-case__tag">Capture</span>
        <h3>Turn a conversation into a note</h3>
        <p>Create a decision record, meeting follow-up, research summary, or checklist in the exact notebook you name.</p>
      </article>
      <article class="use-case" data-reveal>
        <span class="use-case__tag">Organize</span>
        <h3>Maintain notebooks and tags</h3>
        <p>Rename, move, tag, restore, and trash exact objects while duplicate identities are rejected instead of silently multiplied.</p>
      </article>
      <article class="use-case" data-reveal>
        <span class="use-case__tag">MCP</span>
        <h3>Bring Joplin into an agent workspace</h3>
        <p>Let an MCP-capable editor or coding assistant inspect runbooks and update targeted notes without exporting the whole archive.</p>
      </article>
      <article class="use-case" data-reveal>
        <span class="use-case__tag">Resources</span>
        <h3>Work with attachments through MCP</h3>
        <p>Read, upload, replace, and traverse note-resource relationships with bounded payloads and explicit destructive operations.</p>
      </article>
      <article class="use-case" data-reveal>
        <span class="use-case__tag">Always on</span>
        <h3>Keep the assistant available headlessly</h3>
        <p>Run recurrent Joplin sync and the adapter as user services on your server while desktop and laptop clients come and go.</p>
      </article>
    </div>
  </div>
</section>

<section class="home-band home-control">
  <div class="home-shell control-layout">
    <div class="control-copy" data-reveal>
      <p class="section-label">Control is the feature</p>
      <h2>Your assistant should adapt to your notes, not own them.</h2>
      <p>
        Keep the Joplin clients, sync provider, encryption choices, profile, and
        backups you already trust. The bridge is a replaceable open-source adapter,
        not a new proprietary knowledge store.
      </p>
      <a class="text-link" href="contracts/SECURITY/">Read the security contract &rarr;</a>
    </div>
    <div class="control-stack" data-reveal>
      <div class="control-layer">
        <span>Clients</span>
        <strong>Claude, a private GPT <i>or</i> an MCP client</strong>
        <small>Separate transports over one shared, validated operation registry</small>
      </div>
      <div class="control-layer">
        <span>Boundary</span>
        <strong>Dedicated MCP, Actions, and OAuth credentials</strong>
        <small>Independent tokens, authenticated routes, and bounded payloads</small>
      </div>
      <div class="control-layer">
        <span>Joplin</span>
        <strong>Loopback Data API</strong>
        <small>The upstream token and API never need public network exposure</small>
      </div>
      <div class="control-layer control-layer--owner">
        <span>Owner</span>
        <strong>Your Joplin profile, sync target, encryption, and backups</strong>
        <small>Remove the adapter without migrating or deleting your knowledge base</small>
      </div>
    </div>
  </div>
</section>

<section class="home-band home-workflow home-files">
  <div class="home-shell">
    <div class="section-heading" data-reveal>
      <p class="section-label">A second interface for review-heavy work</p>
      <h2>Use ordinary Markdown when the diff matters.</h2>
      <p>
        Direct tools are ideal for current, targeted operations. For broad changes,
        repository context, or Git review, pull selected Joplin notebooks to files
        and approve the exact push plan.
      </p>
    </div>
    <div class="workflow-layout">
      <ol class="workflow-steps" data-reveal>
        <li><span>1</span><div><strong>Pull</strong><small>Start from current Joplin state.</small></div></li>
        <li><span>2</span><div><strong>Edit</strong><small>Work in plain Markdown with normal agent tools.</small></div></li>
        <li><span>3</span><div><strong>Diff</strong><small>Compare base, local, and remote states.</small></div></li>
        <li><span>4</span><div><strong>Dry-run</strong><small>Review the exact operation plan.</small></div></li>
        <li><span>5</span><div><strong>Push</strong><small>Guard, apply, verify, and journal.</small></div></li>
      </ol>
      <div class="workflow-terminal" data-reveal>
        <div class="workflow-terminal__title">
          <span>reviewed-note-change.sh</span>
          <small>stable JSON and exit codes</small>
        </div>
        <pre><code><em>$</em> joplin-md-sync pull --root ./notes --json
<b>OK</b>  remote and base refreshed

<em>$</em> joplin-md-sync diff --root ./notes \
    --three-way --unified
<b>1 note</b>  body changed locally

<em>$</em> joplin-md-sync push --root ./notes \
    --dry-run --json
<b>PENDING_ACTIONS</b>  push_update_remote

<em>$</em> joplin-md-sync push --root ./notes --json
<b>OK</b>  applied 1, failed 0</code></pre>
      </div>
    </div>
  </div>
</section>

<section class="home-band home-safety">
  <div class="home-shell">
    <div class="section-heading section-heading--split" data-reveal>
      <div>
        <p class="section-label">Predictable failure boundaries</p>
        <h2>Agents get explicit errors, not accidental damage.</h2>
      </div>
      <p>
        Direct tools and file synchronization use different consistency models,
        but both expose failures instead of guessing or silently repeating writes.
      </p>
    </div>
    <div class="safety-grid">
      <div data-reveal><strong>No duplicate create retries</strong><span>Occupied note, notebook, tag, and resource identities return the existing IDs and recommended update tool.</span></div>
      <div data-reveal><strong>No ambiguous write replay</strong><span>A timed-out direct mutation is reported for inspection instead of being sent again.</span></div>
      <div data-reveal><strong>No public Joplin API</strong><span>Remote clients reach the authenticated adapter; Joplin stays on loopback.</span></div>
      <div data-reveal><strong>No permanent note deletion</strong><span>Notes and notebooks move to normal Joplin trash and can be restored.</span></div>
      <div data-reveal><strong>No silent file overwrite</strong><span>Divergent edits produce a three-way conflict bundle and exit code 2.</span></div>
      <div data-reveal><strong>No hidden partial sync</strong><span>A journal blocks later file writes until recovery verifies what completed.</span></div>
    </div>
  </div>
</section>

<section class="home-band home-cta">
  <div class="home-shell cta-layout" data-reveal>
    <div>
      <p class="section-label">Keep Joplin. Add the interfaces you need.</p>
      <h2>Bring your private notes into Claude, ChatGPT, and MCP.</h2>
    </div>
    <div class="cta-actions">
      <a class="home-button home-button--light" href="user/CLAUDE_CONNECTOR/">Connect Claude</a>
      <a class="home-button home-button--outline" href="user/AGENT_INTERFACES/">Compare interfaces</a>
    </div>
  </div>
</section>

<div class="home-affiliation">
  <div class="home-shell">
    joplin-md-sync is an independent open-source project and is not affiliated
    with or endorsed by the Joplin project.
  </div>
</div>
