# Reddit / HN post drafts — Mimir v0.1.0 announcement

Post these *after* you've published to PyPI (or made peace with the
current state: GitHub-only install via `uv sync`). The text plays up
the LLM-guided onboarding angle because that is the actual
differentiator vs MetaMCP and friends. Don't lead with "another MCP
aggregator" — you'll lose 80% of readers in the first sentence.

If you're not 100% sure about posting, sit on it 24h. Releases live
forever; the post can wait a day.

---

## Draft 1 — r/mcp (primary target, friendliest audience)

**Title:**
> Mimir — a declarative MCP router where the LLM walks the operator through onboarding

**Body:**

I wrote a small MCP router and finally pushed v0.1.0 today. Sharing
because the design centre is a bit different from the other
aggregators in the space and I'd love feedback on the contract.

**The pitch in one paragraph:** every plugin ships its own
`plugin.toml` declaring identity, runtime, security boundary, and
*what infra it needs*. When you mount a plugin and the operator
hasn't supplied that infra yet, the router exposes a
`setup_<plugin>()` meta-tool. The LLM calls it, sees what's missing,
and walks the operator through it conversationally — no manual YAML
editing, no docs deep-dive before the first useful call.

**Concretely:** the operator says *"I want Proxmox tools"*, the LLM
runs `router_install_plugin("github:foo/proxmox-mcp")`, the router
returns the exact `git clone` command to run (strict-mode default —
no surprise installs), the operator pastes it, restarts. On next
session `router_status()` shows the plugin in `pending_setup` state
with two missing pieces (a host of type `proxmox` + credential
matching `PROXMOX_*`). The LLM asks for them, calls
`router_add_host()` and `router_add_credential()`, plugin activates,
done.

**What's in the box:**

- Declarative plugin contract (`plugin.toml` schema documented in
  the repo).
- Inventory layer separated from plugins — operators describe their
  hosts/services in YAML once, plugins ask the router *"give me
  hosts of type X"*.
- Layered security: manifest quarantine, JSONL audit log, scoped
  credential vault, profile gate, tool whitelist/blacklist via
  FastMCP middleware, cross-plugin env scoping in subprocess
  (sibling plugins can't see each other's tokens).
- Plugin lifecycle meta-tools (install / remove / enable / disable /
  list) so the LLM drives the full lifecycle.
- Skills/agents discovery — drop a `.md` with frontmatter, becomes
  a `skill_<name>` tool.

**Where it slots in vs the existing tools:** MetaMCP, Local MCP
Gateway, mcp-proxy-server and FastMCP's own `Proxy Provider` already
aggregate. Mimir's bet is the declarative + LLM-guided angle —
plugin authors describe their requirements once, the router lets the
LLM do the onboarding work that humans currently do by hand.

**Status:** Beta. 319 tests + dry-run pass on Linux 3.11/3.12 and
Windows 3.12. End-to-end verified via FastMCP Client over stdio
(spawning the router as a subprocess). Working on validating against
multiple MCP clients next; if you run Mimir against Cursor / Zed /
Cline / Roo / Kilo, a row in `docs/compatibility.md` is the most
useful contribution right now.

**Links:**
- Repo: https://github.com/CTRQuko/mimir-mcp
- Quickstart with onboarding transcript: https://github.com/CTRQuko/mimir-mcp/blob/main/docs/quickstart.md
- Naming guide for plugin authors: https://github.com/CTRQuko/mimir-mcp/blob/main/docs/naming-guide.md
- Security model: https://github.com/CTRQuko/mimir-mcp/blob/main/docs/security-model.md

Built on FastMCP 3.x. MIT.

Honest tradeoffs in the README, comparison table included. Happy to
answer questions.

---

## Draft 2 — r/LocalLLaMA (broader audience, more critical)

**Title:**
> [Tool] Mimir: an MCP router that lets the LLM onboard the operator instead of asking them to write YAML

**Body:** *(same body as Draft 1, but slightly tighter for r/LocalLLaMA's
denser comments.)*

If you run multiple MCP servers — GitHub + your homelab + a Postgres
+ a Slack — you currently configure each one in your client's MCP
config by hand. Mimir is an aggregator that mounts all of them under
one stdio interface and adds two ideas:

1. **Plugins describe their requirements.** A plugin needs a Proxmox
   host? It says so in `plugin.toml`. When the operator hasn't
   supplied one, the router exposes a `setup_<plugin>()` meta-tool
   the LLM can call, and the LLM walks the operator through it.

2. **Inventory is separated from plugins.** The operator describes
   their hosts and services in `inventory/*.yaml` once. Plugins
   query the router *"give me hosts of type X"* — no hardcoded IPs,
   no hardcoded credentials, no per-plugin config drift.

The combination means a fresh install + your first plugin is a
~5-minute conversation with the LLM, not a documentation deep-dive.

**Layered security** because aggregators with secrets handling
deserve it: scoped credential vault, JSONL audit log, tool
whitelist/blacklist via FastMCP middleware, cross-plugin env scoping
in subprocess. Filesystem/network/exec interceptors are deferred —
documented as such in the security model, not hidden.

**Comparison vs the existing aggregators** (MetaMCP, Local MCP
Gateway, mcp-proxy-server, FastMCP Proxy Provider) is in the
README, no spin.

Repo: https://github.com/CTRQuko/mimir-mcp

Beta release v0.1.0 just landed. 319 tests green, end-to-end smoke
against the live router subprocess. Open to feedback on the
contract before stabilising the API.

---

## Draft 3 — Hacker News "Show HN" (only if r/mcp goes well)

**Title:**
> Show HN: Mimir – a declarative MCP router with LLM-guided onboarding

**Body:**

Hi HN. I've been building an MCP aggregator for a while and just
released v0.1.0 of what came out of it: Mimir, a router for
[Anthropic's Model Context Protocol](https://modelcontextprotocol.io)
servers.

The MCP ecosystem has a few aggregators already (MetaMCP, Local MCP
Gateway, mcp-proxy-server, FastMCP's own Proxy Provider). Mimir's
angle is different in two specific ways:

1. **Plugins are declarative.** Each one ships a `plugin.toml`
   declaring identity, runtime command, security boundary,
   credentials it expects, and infra it needs. The aggregator
   doesn't have a central config file listing downstreams.

2. **Onboarding is conversational.** When a plugin needs infra the
   operator hasn't supplied, the router exposes a setup tool the
   LLM calls. The LLM asks the operator the missing questions, the
   router writes the answers into `inventory/*.yaml` and the scoped
   credential vault. No manual YAML editing.

There's a transcript walkthrough in `docs/quickstart.md` showing
what this looks like in practice. (Real tool calls, illustrative
phrasing.)

**Built on:** Python 3.11+, FastMCP 3.x. MIT licensed. 319 tests
including end-to-end smoke against a live router subprocess via
FastMCP Client over stdio.

**Status:** Beta. Working CLI install via `uv sync` from a clone;
PyPI publish pending while I sort credentials. Pre-built wheel
available on the GitHub release page.

**Where it falls short today:** No web UI (CLI + LLM are the UX);
filesystem/network/exec interceptors are deferred (documented as
such); only validated against my own MCP clients so far —
compatibility reports against Cursor / Zed / Cline / Roo welcome.

Repo: https://github.com/CTRQuko/mimir-mcp

Honest comparison with the alternatives in the README. Happy to
answer technical questions.

---

## Tone guardrails (read before posting)

- **Don't oversell.** Mimir is small. If a comment says
  *"MetaMCP has more features"*, the answer is *"yes, MetaMCP is
  bigger; Mimir picks a different design centre"*. Not *"but
  actually..."*.
- **Don't flame.** Some commenters will say *"another aggregator,
  why?"*. Reply once, calmly, link the comparison table. Then walk
  away. Reddit doesn't reward defensive threads.
- **Do answer technical questions.** If someone asks *"how does
  cross-plugin env scoping work?"*, link `docs/security-model.md`
  section 6 and quote 3 lines. Effort signals quality.
- **Do thank PRs and bug reports more than upvotes.** A row in
  `compatibility.md` from a stranger is worth ten upvotes.
- **Do cap engagement at 2-3 hours after posting.** After that,
  responses get worse and your sleep matters more than thread
  rank.

## Pre-publish checklist (do before posting any draft)

- [ ] Repo description on GitHub mentions "declarative" + "LLM-
      guided onboarding". (Done.)
- [ ] Release notes on GitHub release link to quickstart.md.
      (Done.)
- [ ] PyPI URL works → `pip install mimir-router-mcp`. (Pending until
      `uv publish` runs with credentials.)
- [ ] CI badge on README is green. (Will be after `main` push and
      first CI run.)
- [ ] At least one fresh-machine smoke test: clone repo, `uv sync`,
      run a plugin. (Recommended manual step.)
- [ ] You have 2-3 hours of free time after posting to answer
      replies.

If any of those are missing, post tomorrow.
