# ADR 0016: Assigned harness tabs and owner guidance

Status: accepted

JobPilot provisions a tab before starting a direct agent. The browser-harness named
daemon also provisioned a blank tab, leaving an orphan after the agent switched to
the server's tab. A trusted sandbox entry adapter now attaches to the server-provided
target at startup and recovery, and fails when that target disappears. The daemon
never owns or closes the job tab on shutdown. The upstream package is unchanged.

Owner guidance is a bounded, authenticated user-session operation recorded as a job
event. It resumes preparation; the running worker observes the changed guidance and
stops its process group before retrying with the latest hint in DATA. Guidance cannot
authorize submission. Existing owner-reported submission is exposed for all pre-submit
jobs; monitors stop on terminal state and late direct results cannot overwrite it.

Progress comes from fixed descriptions of harness helper names. Python source,
arguments, page contents and private model reasoning are never persisted as progress.
Summaries are throttled to avoid flooding the database and dashboard.

Antigravity runs in a per-run workspace with an explicitly selected custom agent,
the scoped harness MCP server and a deny-by-default tool hook. Its NDJSON stream is
monitored for Stop, lease renewal and guidance changes. Only the final envelope is
retained; raw intermediate output and CLI error prose are discarded.

The run directory has its own empty Git repository to bound CLI workspace discovery;
no owner files are copied or committed. Without that boundary the CLI can resolve
the outer project and miss the run's MCP configuration. The CLI automatically allows
the scoped tool call while the hook hard-denies shell, file and other MCP tools.

Configuration references: [CLI headless streams](https://antigravity.google/docs/cli/headless/),
[workspace MCP configuration](https://antigravity.google/docs/mcp/), and
[custom agent configuration](https://antigravity.google/docs/subagents/).
