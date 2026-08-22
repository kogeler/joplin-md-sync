---
title: Joplin for ChatGPT and MCP
description: Use self-hosted Joplin notes directly from ChatGPT and MCP clients, with an automated headless deployment and a separate reviewable Markdown workflow.
template: home.html
hide:
  - navigation
  - toc
---

<section class="home-hero">
  <div class="home-shell home-hero__content">
    <p class="home-kicker" data-reveal>ChatGPT Actions <span></span> Local &amp; HTTP MCP <span></span> Self-hosted Joplin</p>
    <h1 data-reveal>Joplin for ChatGPT &amp; MCP</h1>
    <p class="home-hero__lead" data-reveal>
      Search, read, create, organize, and update your own Joplin notes from the
      assistants you already use. Keep Joplin, its sync target, and its Data API
      under your control while a guarded bridge exposes only the operations you need.
    </p>
    <div class="home-actions" data-reveal>
      <a class="home-button home-button--primary" href="user/CHATGPT_ACTIONS/">
        Connect ChatGPT
      </a>
      <a class="home-button home-button--secondary" href="user/SERVICE/">
        Deploy headless
      </a>
    </div>
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
          <div><strong>ChatGPT or MCP</strong><small>Your chosen client</small></div>
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
        <span><i></i> ChatGPT Actions</span>
        <span><i></i> MCP tool schemas</span>
        <span><i></i> Duplicate guards</span>
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
        <h3>Talk to your notes in ChatGPT</h3>
        <p>
          Give a private Custom GPT live access to search, read, create, update,
          move, tag, and trash exact Joplin objects through generated Actions.
        </p>
        <a href="user/CHATGPT_ACTIONS/">Configure your Joplin GPT &rarr;</a>
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
          Install Joplin Terminal, recurrent sync, MCP, and Actions as coordinated
          rootless services on a Linux host with one interactive installer.
        </p>
        <a href="user/SERVICE/">Deploy the complete service &rarr;</a>
      </article>
    </div>
  </div>
</section>

<section class="home-band home-workflow home-deploy">
  <div class="home-shell">
    <div class="section-heading" data-reveal>
      <p class="section-label">From sync target to private GPT</p>
      <h2>Deploy the complete headless path.</h2>
      <p>
        The installer coordinates Joplin Terminal and the agent adapter. You choose
        the Joplin sync target and the HTTPS publishing layer; the upstream Data API
        never needs to leave loopback.
      </p>
      <a class="text-link" href="user/SELF_HOSTED/">Review the deployment boundaries &rarr;</a>
    </div>
    <div class="workflow-layout">
      <ol class="workflow-steps" data-reveal>
        <li><span>1</span><div><strong>Choose storage</strong><small>Connect the headless profile to your existing Joplin sync target.</small></div></li>
        <li><span>2</span><div><strong>Install services</strong><small>Deploy Joplin Terminal and the shared MCP/Actions adapter.</small></div></li>
        <li><span>3</span><div><strong>Publish Actions</strong><small>Route only the authenticated Actions namespace through HTTPS.</small></div></li>
        <li><span>4</span><div><strong>Generate the schema</strong><small>Validate TLS, authentication, and live reads before opening ChatGPT.</small></div></li>
        <li><span>5</span><div><strong>Use your notes</strong><small>Search and change current Joplin data from a private Custom GPT.</small></div></li>
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
<b>OK</b>  separate MCP and Actions tokens created</code></pre>
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
        ChatGPT for conversation or MCP for any compatible client.
      </p>
    </div>
    <div class="use-case-grid">
      <article class="use-case" data-reveal>
        <span class="use-case__tag">ChatGPT</span>
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
        <strong>Private Custom GPT <i>or</i> MCP client</strong>
        <small>Separate transports over one shared, validated operation registry</small>
      </div>
      <div class="control-layer">
        <span>Boundary</span>
        <strong>Dedicated Actions and MCP credentials</strong>
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
      <h2>Bring your private notes into ChatGPT and MCP.</h2>
    </div>
    <div class="cta-actions">
      <a class="home-button home-button--light" href="user/CHATGPT_ACTIONS/">Connect ChatGPT</a>
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
