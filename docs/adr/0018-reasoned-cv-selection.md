# ADR 0018: Compare CV evidence before direct preparation

Status: accepted

Direct preparation chooses an unset base CV by comparing the job posting with the
actual text of every active registered CV version. Track titles and keyword counts
are insufficient to judge which experience best demonstrates a role's requirements.

The server supplies bounded posting and PDF text to one tool-free model completion.
The selected configured CV model compares responsibilities, accomplishments,
seniority, skills and geographic market. Its bounded result contains a track id, a
brief owner-facing decision summary and exact excerpts from the selected CV. The
server validates candidate membership, market compatibility and quoted evidence.
Invalid, uncertain or unsupported selections require owner help; there is no keyword
fallback. Evidence validation does not prove the model's overall suitability judgment.

The comparison runs before bulk filling or uploading. The compared immutable version
is pinned atomically only while the job still has no selected track/version. An owner
choice, edited track version, terminal job or submit fence prevents a stale assignment.
Subsequent runs reuse that version. Existing selections and submitted jobs are preserved.
This change selects existing CVs; it does not generate tailored content or grant approval.

Antigravity runs in an isolated disposable project with an empty MCP config, a custom
agent and a hook denying every tool. Claude runs with tools disabled and no MCPs.
CLI streaming renews the lease and respects stop/drain. Only the final validated
decision summary and evidence reach job events; raw prompts/results and private
reasoning are not logged. The selection uses the existing CV budget and cost ledger.
Other non-CLI engines use their existing text completion adapter.
