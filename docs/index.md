---
title: Safe Joplin access for AI agents
description: Safe, reviewable access to self-hosted Joplin notes for coding agents, MCP clients, and ChatGPT.
template: home.html
hide:
  - navigation
  - toc
---

<section class="home-hero">
  <div class="home-shell home-hero__content">
    <p class="home-kicker" data-reveal>Open source <span></span> Local first <span></span> Agent ready</p>
    <h1 data-reveal>joplin-md-sync</h1>
    <p class="home-hero__lead" data-reveal>
      Give agents useful access to your notes without giving up control of them.
      Bridge self-hosted Joplin to reviewable Markdown, MCP clients, and ChatGPT
      Actions through one safety-focused tool.
    </p>
    <div class="home-actions" data-reveal>
      <a class="home-button home-button--primary" href="user/GETTING_STARTED/">
        Start in five minutes
      </a>
      <a class="home-button home-button--secondary" href="https://github.com/kogeler/joplin-md-sync">
        View on GitHub
      </a>
    </div>
    <p class="home-proof" data-reveal>
      MIT licensed <span></span> Windows and Linux <span></span> Zero runtime dependencies
    </p>

    <div class="hero-console" data-reveal>
      <div class="hero-console__bar">
        <div class="window-dots" aria-hidden="true"><i></i><i></i><i></i></div>
        <span>safe agent session</span>
        <strong>verified</strong>
      </div>
      <div class="hero-console__flow" aria-label="Joplin to agent data flow">
        <div class="flow-node flow-node--joplin">
          <!-- Official brandmark from https://joplinapp.org/brand/, used for identification only. -->
          <img src="site/assets/images/joplin-brandmark.png" alt="" width="44" height="42">
          <div><strong>Joplin</strong><small>Your private source</small></div>
        </div>
        <div class="flow-link">
          <span>local Data API</span>
          <i aria-hidden="true"></i>
        </div>
        <div class="flow-node flow-node--bridge">
          <b>J</b>
          <div><strong>joplin-md-sync</strong><small>Guard, apply, verify</small></div>
        </div>
        <div class="flow-link">
          <span>Markdown / MCP</span>
          <i aria-hidden="true"></i>
        </div>
        <div class="flow-node flow-node--agent">
          <b>&gt;_</b>
          <div><strong>Your agent</strong><small>Structured, bounded access</small></div>
        </div>
      </div>
      <div class="hero-console__run">
        <code><span>$</span> joplin-md-sync push --root ./notes --dry-run --json</code>
        <code class="console-result">{"code":"PENDING_ACTIONS","exit_code":1,"planned_operations":1}</code>
      </div>
      <div class="hero-console__checks">
        <span><i></i> Pulled first</span>
        <span><i></i> Three-way diff</span>
        <span><i></i> No silent overwrite</span>
        <span><i></i> Recovery journal</span>
      </div>
    </div>
  </div>
</section>

<section class="home-band home-intro">
  <div class="home-shell">
    <div class="section-heading" data-reveal>
      <p class="section-label">One knowledge base, two ways to work</p>
      <h2>Your notes stay yours. Agents become useful.</h2>
      <p>
        Joplin remains the place where you capture, organize, encrypt, and sync
        knowledge. The bridge adds controlled interfaces for the work agents do best.
      </p>
    </div>
    <div class="value-columns">
      <article data-reveal>
        <span class="value-number">01</span>
        <h3>Review every edit</h3>
        <p>
          Pull notes into ordinary Markdown, let an agent make a focused change,
          inspect a true three-way diff, then dry-run the exact push plan.
        </p>
        <a href="user/AGENT_WORKFLOWS/">Use the file workflow &rarr;</a>
      </article>
      <article data-reveal>
        <span class="value-number">02</span>
        <h3>Call Joplin directly</h3>
        <p>
          Search, read, create, tag, move, and trash notes through typed MCP tools
          when an immediate structured operation is the better fit.
        </p>
        <a href="user/MCP_API/">Explore the MCP API &rarr;</a>
      </article>
      <article data-reveal>
        <span class="value-number">03</span>
        <h3>Host the whole path</h3>
        <p>
          Run Joplin Terminal and the authenticated bridge on your own Linux host,
          while keeping the upstream Data API private on loopback.
        </p>
        <a href="user/SELF_HOSTED/">Plan a self-hosted setup &rarr;</a>
      </article>
    </div>
  </div>
</section>

<section class="home-band home-use-cases" id="use-cases">
  <div class="home-shell">
    <div class="section-heading section-heading--split" data-reveal>
      <div>
        <p class="section-label">Built for real note workflows</p>
        <h2>Six jobs, one controlled bridge</h2>
      </div>
      <p>
        Use the interface that matches the task. Reviewed file transformations
        and immediate API operations are intentionally separate.
      </p>
    </div>
    <div class="use-case-grid">
      <article class="use-case" data-reveal>
        <span class="use-case__tag">Developers</span>
        <h3>Agent-maintained runbooks</h3>
        <p>Let a coding agent update commands, rollback steps, and incident notes from the project it is already working in.</p>
      </article>
      <article class="use-case" data-reveal>
        <span class="use-case__tag">Second brain</span>
        <h3>Private semantic work</h3>
        <p>Find related notes, summarize a topic, and organize results without exporting your whole notebook archive to another note platform.</p>
      </article>
      <article class="use-case" data-reveal>
        <span class="use-case__tag">Git</span>
        <h3>Versioned knowledge</h3>
        <p>Keep a reviewable history of selected Joplin notebooks as Markdown while local state, credentials, backups, and conflicts stay out of Git.</p>
      </article>
      <article class="use-case" data-reveal>
        <span class="use-case__tag">MCP</span>
        <h3>Tools for any compatible client</h3>
        <p>Expose notes, notebooks, tags, search, and attachments over Streamable HTTP with explicit schemas and bounded payloads.</p>
      </article>
      <article class="use-case" data-reveal>
        <span class="use-case__tag">ChatGPT</span>
        <h3>Your own Joplin assistant</h3>
        <p>Connect a private Custom GPT through authenticated Actions, generated OpenAPI, isolated credentials, and a tested HTTPS endpoint.</p>
      </article>
      <article class="use-case" data-reveal>
        <span class="use-case__tag">Homelab</span>
        <h3>Headless knowledge service</h3>
        <p>Pair Joplin Terminal with systemd services and your chosen sync target, without exposing Joplin's own Data API to the internet.</p>
      </article>
    </div>
  </div>
</section>

<section class="home-band home-control">
  <div class="home-shell control-layout">
    <div class="control-copy" data-reveal>
      <p class="section-label">Control is the feature</p>
      <h2>Self-hosted notes should not require blind trust.</h2>
      <p>
        Your notes can stay in Joplin, on infrastructure and sync targets you
        choose. joplin-md-sync works through the documented Data API, never the
        Joplin database or profile internals.
      </p>
      <a class="text-link" href="contracts/SECURITY/">Read the security contract &rarr;</a>
    </div>
    <div class="control-stack" data-reveal>
      <div class="control-layer">
        <span>Interface</span>
        <strong>Markdown workspace <i>or</i> MCP / Actions</strong>
        <small>Choose reviewed changes or direct tools per task</small>
      </div>
      <div class="control-layer">
        <span>Safety</span>
        <strong>Plan &rarr; guard &rarr; apply &rarr; verify</strong>
        <small>Stable exit codes and deterministic JSON at every boundary</small>
      </div>
      <div class="control-layer">
        <span>Joplin</span>
        <strong>Loopback Data API</strong>
        <small>Tokens remain separate; remote access requires an explicit override</small>
      </div>
      <div class="control-layer control-layer--owner">
        <span>Owner</span>
        <strong>Your profile, storage, sync, and backups</strong>
        <small>No replacement cloud and no proprietary note format</small>
      </div>
    </div>
  </div>
</section>

<section class="home-band home-workflow">
  <div class="home-shell">
    <div class="section-heading" data-reveal>
      <p class="section-label">A boringly explicit write path</p>
      <h2>See what will change before Joplin changes.</h2>
      <p>
        The file workflow gives autonomous agents the same guardrails a careful
        operator would use by hand.
      </p>
    </div>
    <div class="workflow-layout">
      <ol class="workflow-steps" data-reveal>
        <li><span>1</span><div><strong>Pull</strong><small>Start from current Joplin state.</small></div></li>
        <li><span>2</span><div><strong>Edit</strong><small>Work in plain Markdown with normal tools.</small></div></li>
        <li><span>3</span><div><strong>Diff</strong><small>Compare base, local, and remote states.</small></div></li>
        <li><span>4</span><div><strong>Dry-run</strong><small>Review the exact operation plan.</small></div></li>
        <li><span>5</span><div><strong>Push</strong><small>Guard, apply, verify, and journal.</small></div></li>
      </ol>
      <div class="workflow-terminal" data-reveal>
        <div class="workflow-terminal__title">
          <span>notes-agent-session.sh</span>
          <small>exit codes are part of the API</small>
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
        <p class="section-label">Failure is a first-class state</p>
        <h2>Designed to stop, explain, and recover.</h2>
      </div>
      <p>
        Automation is only useful when its boundaries are predictable. The CLI
        never hides a divergent edit behind a successful-looking message.
      </p>
    </div>
    <div class="safety-grid">
      <div data-reveal><strong>No silent overwrites</strong><span>Divergent edits produce a conflict bundle and exit code 2.</span></div>
      <div data-reveal><strong>No default deletion</strong><span>Deletes are reported until an explicit propagation flag is approved.</span></div>
      <div data-reveal><strong>No mystery partial runs</strong><span>A journal blocks further writes until recovery verifies what completed.</span></div>
      <div data-reveal><strong>No token in arguments</strong><span>Credentials come from protected files or the environment and are redacted.</span></div>
      <div data-reveal><strong>No direct database access</strong><span>Only Joplin's documented local Data API is used.</span></div>
      <div data-reveal><strong>No mutating diff</strong><span><code>diff</code> is guaranteed to inspect state without changing it.</span></div>
    </div>
  </div>
</section>

<section class="home-band home-cta">
  <div class="home-shell cta-layout" data-reveal>
    <div>
      <p class="section-label">Keep Joplin. Add a safer interface.</p>
      <h2>Make your private notes useful to agents.</h2>
    </div>
    <div class="cta-actions">
      <a class="home-button home-button--light" href="user/GETTING_STARTED/">Install and connect</a>
      <a class="home-button home-button--outline" href="user/AGENT_INTERFACES/">Choose an interface</a>
    </div>
  </div>
</section>

<div class="home-affiliation">
  <div class="home-shell">
    joplin-md-sync is an independent open-source project and is not affiliated
    with or endorsed by the Joplin project.
  </div>
</div>
