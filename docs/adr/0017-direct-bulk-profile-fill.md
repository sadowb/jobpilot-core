# ADR 0017: Bulk profile fill before direct model execution

Status: accepted

Direct preparation previously asked the model to discover and fill ordinary fields
even though About me and saved answers already supplied those values. Preparation
now inventories the recorded tab once, uses the existing pure `build_plan` matcher,
and applies native text/select fields in a single batch with a single read-back.
The known data is the same profile and answer bank used by the toolbox path.

The batch refuses credentials, consent/signature controls, custom widgets, ambiguous
options and stale labels. Nonempty values are preserved; matching values count as
already filled. An unambiguous resume input may receive only the server-selected CV
before text filling. The agent still checks parser changes and the entire final form.
Submit runs never use this path, and this optimization grants no submit approval.

Successful fields and remaining controls are supplied in DATA, reducing model tool
round trips. Stored posting metadata is reused on resumes. Final direct drafts stay
agent-reported; the software batch is neither a full-form verifier nor an approval.

Bulk timing and agent timing events contain only counts and seconds. The trusted
harness adapter measures helper wall time; stream wall time minus those helpers is
reported as model and CLI startup overhead, not as pure model inference time. Source,
tool arguments, page text, profile values and private model reasoning are not metrics.
