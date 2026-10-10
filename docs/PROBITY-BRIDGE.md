# Authentic Probity kernel reuse

The challenge wrapper calls the unmodified `probity-verify` consumer kernel
from `probityai/probity-verify`, pinned to commit
`a2a7e23413be0ab0ad1001d60085a2ff21eaa907`. GitHub's `main` branch was checked
during the October 10, 2026 build and resolved to that commit.

`vendor/probity-verify/SOURCE.json` records every retained file's original Git
blob identity, SHA-256, and byte count. The original Apache-2.0 license is
retained. All vendored Python source is byte-for-byte upstream source. The
vendored subset is the kernel and its complete transitive import closure,
including all original adapter modules; there are no stub implementations.
It omits optional A2A transports and CLI components. The upstream eager
imports require `cryptography>=50.0.2,<51` even though the challenge adapter
itself does not use signatures.

`wrapper/probity_bridge.py` registers the new event-specific claim type
`customer_support_policy/v1` alongside the kernel's existing registrations.
This new adapter reduces the wrapper's supplied checks:

| Supplied evidence | Evidence decision |
| --- | --- |
| Any contradicted check | `contradicted` |
| Unresolved check, or no checks | `not_established` |
| Every supplied check supported | `supported` |

The upstream kernel validates the stable `probity-decision/v1` envelope and
binds it to the exact effective consumer policy with SHA-256. The bridge
retains that effective policy in `consumer_policy` for replay. It is the
wrapper policy plus `assessments[case_id] = {"claim_type":
"customer_support_policy/v1"}`. Its digest uses the upstream kernel's
compact, sorted, UTF-8 JSON with a trailing newline. This is distinct from
the raw wrapper action-policy hash.

The bridge
preserves the scope, source, provenance, and other fields of each check.
Caller mappings are not mutated. Malformed check statuses are refused with
`CaseError`, rather than assigned a verdict.

This is an explicit reduction of supplied records, not an independent audit
of their production. Local company-data checks are distinct from Ezio's
producer-reported assertions. No signature or witness claim is fabricated.
The source hash check detects accidental vendored-source edits; its manifest
is not an independent trust root.

Evidence adjudication and action authorization are separate: the wrapper's
action policy chooses APPROVE, HOLD_FOR_REVIEW, or BLOCK and controls the mock
send. A supported packet does not prove an email was delivered, that every
claim was extracted, or that a real-world action was independently witnessed.

## Replay a retained decision offline

Run replay with the project environment. It reads local files, regenerates
the company checks, reapplies action policy to the retained Ezio response,
and calls the pinned Probity kernel. It does not call Ezio or execute actions.

```bash
.venv/bin/python wrapper/replay.py wrapper/audit_log.jsonl
.venv/bin/python wrapper/replay.py downloaded-record.json
```

The default consumer-selected policy is `wrapper/policy.json`. Use
`--policy path/to/policy.json` to select another retained policy. Replay
compares that selection with both the recorded action policy and the exact
effective consumer policy. A changed policy cannot reuse the old binding.

Replay accepts these formats:

| Format | Required contents |
| --- | --- |
| Ordinary audit JSON object | `persona`, `input` as draft text, `context`, `decision`, `reason`, `evidence`, `ezio`, and `decision_packet` |
| Audit JSONL file | One ordinary audit object per nonempty line; replay checks every record |
| Page Download JSON | The exact structure below; replay converts it to an ordinary audit object |
| Bare decision packet | Kernel packet plus its retained action policy and source bindings; raw input and report checks remain incomplete |

The page export has these exact fields. `review_handoff` is optional.
The `result` fields are the server response; `decision_packet` contains the
full retained packet, not the abbreviated object in this format example.

```json
{
  "created_at": "2026-10-10T21:00:00.000Z",
  "workflow": "customer_support",
  "input": {
    "persona": "customer_support",
    "text": "The Pro plan costs $49 per month.",
    "context": {"plan": "Pro"}
  },
  "result": {
    "decision": "APPROVE",
    "reason": "Retained server reason",
    "evidence": ["Retained server evidence"],
    "ezio": {},
    "decision_packet": {},
    "action_executed": true
  },
  "review_handoff": {
    "note": "Retained reviewer note",
    "recorded_at": "2026-10-10T21:01:00.000Z",
    "action": "note_only"
  }
}
```

The format example shows field names only. Replay refuses it until `ezio`
and `decision_packet` contain the complete downloaded response and packet.
It refuses malformed exports and alternate field names.

Replay checks the input and case hashes, current company source hashes,
regenerated check contents, policy bindings, kernel verdict and scope,
action decision, audit reason, and audit evidence. It detects a changed
claim even when the edited check retains the same verdict. A company source
mismatch appears as a separate `source_bindings` failure.

The command emits deterministic JSON without the draft or raw report.
Exit `0` means every replay check matched. Exit `1` means a mismatch.
Exit `2` means malformed input or incomplete evidence. A bare packet cannot
establish the omitted draft, context, report, or regenerated checks. Missing
company bytes also prevent a complete replay.

A matched replay preserves the factual verdict: `supported`, `contradicted`,
or `not_established`. A matched contradiction remains a contradiction.
Replay checks consistency against the supplied record and selected local
bytes. It does not authenticate an editable record or prove that its producer
told the truth. A person who rewrites the record and all its matching evidence
can create another internally consistent record.

`created_at`, audit timestamps, and `review_handoff` are retained metadata.
`action_executed` is a server assertion. None establishes authority, independent
witnessing, actual execution, or email delivery. A review note does not release
a held action.
