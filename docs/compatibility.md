# Client compatibility

A matrix of MCP clients we have validated Mimir against. Mimir speaks
the standard MCP stdio transport, so anything that conforms to the
spec should work — but reality is messier than spec, and clients
sometimes filter tool names, choke on unicode, or skip
`tools/list_changed` notifications. This page is the empirical truth.

If you run Mimir against a client we don't list, please open a PR
adding a row (template at the bottom). A line that says
*"works on Cline 0.4.2 against Mimir 0.1.0"* is a meaningful
contribution.

## Status

Validated end-to-end means:

1. The client loads the router config without errors at startup.
2. `tools/list` returns the `router_*` meta-tools plus any plugin
   namespaces that should be visible.
3. `router_help()` and `router_status()` return their expected
   payloads when the LLM (or the operator manually) calls them.
4. A non-trivial plugin tool (e.g. `echo_reverse` from
   `examples/echo-plugin/`) round-trips arguments and result.

| Client                     | Versions tested | Status        | Notes |
|----------------------------|-----------------|---------------|-------|
| Claude Desktop             | —               | Not validated | Stable on the legacy server; expected to work on Mimir, not yet exercised end-to-end. |
| Claude Code CLI            | —               | Not validated | The author's main agentic harness. To be exercised first. |
| MCP Inspector              | —               | Not validated | Recommended for protocol-level validation. |
| Zed                        | —               | Not validated | Has native MCP support; behaviour with `setup_<plugin>()` dynamic tools unverified. |
| Cursor                     | —               | Not validated | — |
| Cline (VS Code)            | —               | Not validated | — |
| Roo Code (VS Code)         | —               | Not validated | — |
| Kilo Code                  | —               | Not validated | — |

The matrix is intentionally open — most rows are empty until someone
runs the validation. **Help wanted**.

## How to validate a client

1. Configure the client to launch Mimir over stdio. The exact form
   depends on the client; in practice this is the snippet from
   [`docs/INSTALL.md`](INSTALL.md) adapted to the client's config
   file.
2. Mount the example plugin so there is at least one non-router
   tool to exercise:

   ```bash
   ln -s "$(pwd)/examples/echo-plugin" plugins/echo
   ```

3. Open a session in the client. Verify:

   - The client lists the tools (in agent UIs this is usually the
     "tools" or "MCP" panel).
   - You can see `router_help`, `router_status`,
     `router_list_plugins`, and `echo_echo` / `echo_reverse`.

4. Drive each through the LLM:

   - *"Show me what Mimir can do"* → should call `router_help()`.
   - *"List the plugins"* → `router_list_plugins()`.
   - *"Reverse the string `mimir`"* → `echo_reverse({"text":"mimir"})`
     → returns `"rimim"`.

5. If anything misbehaves, capture the exact error from the client's
   logs *and* from `config/audit.log` (Mimir-side). The combination
   is much more useful than either alone.

## How to add a row

Open a PR editing this file. Use this template:

```markdown
| Client name | Version(s)   | Status   | Notes              |
|-------------|--------------|----------|--------------------|
| <name>      | <versions>   | <status> | <freeform notes>   |
```

`status` should be one of:

- **Validated** — all four steps above passed.
- **Validated with caveat** — works, but has a documented quirk
  (truncated tool names, unicode handling, slow startup, etc.).
- **Broken** — does not work today; please link an issue with the
  exact failure.
- **Not validated** — placeholder; nobody has run the validation yet.

## Known caveats (any client)

These are limitations rooted in MCP itself, not in any specific
client:

- **Tool names are session-stable.** A plugin that becomes `ok`
  after `setup_<plugin>()` runs cannot inject its tools mid-session
  on most clients. The operator restarts the session to see the
  full set.
- **Credential values flow through the client transcript.** When
  the LLM calls `router_add_credential(ref, value)`, the value
  appears in the client's chat history because the MCP transport
  is not encrypted at the application level. To avoid this, write
  the credential into the vault out-of-band (file or env var) and
  have the LLM only confirm the ref exists.
- **Long tool descriptions may get truncated.** Some clients impose
  a per-tool description budget. Plugin authors should keep
  descriptions under ~200 characters for portability.
