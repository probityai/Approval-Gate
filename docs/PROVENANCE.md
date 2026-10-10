# Provenance, reuse, and limitations

## Event starter

The project reuses the public [Ezio Approval-Gate starter](https://github.com/ezioai/Approval-Gate) at revision `fa098c4b829ad0dc639c429410aabed6ed956f78`.

The starter supplies the HTTP/mock infrastructure, fictional company data, practice fixtures, audit/action helpers, results viewer, and official evaluation harness. Challenge-specific work is the new customer-support checks, explicit decision policy, evidence packet, persona interface, and supporting verification/documentation.

The organizer's harness, `mock_ezio.py`, `fixtures.json`, and persona data are not challenge implementation targets and must remain unchanged. The sealed final set is reserved for its organizer-directed judging release.

## Relationship to Probity AI

The implementation uses the actual [Probity AI](https://github.com/probityai) decision kernel, with its Python import dependencies vendored unmodified at revision `a2a7e23413be0ab0ad1001d60085a2ff21eaa907`. The local `vendor/probity-verify/SOURCE.json` manifest retains individual source hashes and its Apache-2.0 license is included. The bridge checks those hashes at startup; the manifest itself is a local record, not an independently authenticated trust root.

The new `customer_support_policy/v1` adapter reduces wrapper-supplied check statuses through the upstream kernel and receives its validated, policy-bound envelope. That evidence decision remains separate from the APPROVE/HOLD/BLOCK action permission. Probity's existing adapters are not replaced. This applies consumer-selected policy, scoped claims, retained evidence, and the separation of evidence validity from permission and task success.

Relevant inspected sources include:

- [`probity-verify` decision kernel](https://github.com/probityai/probity-verify/blob/a2a7e23413be0ab0ad1001d60085a2ff21eaa907/src/probity_verify/core.py).
- [`agent-evidence-observer` protected dispatch](https://github.com/probityai/agent-evidence-observer/blob/ab93f9995afa5568e02c4d443bb06e6dd790087c/src/probity_observer/protected_dispatch.py).
- [`agent-evidence-admission` spend reservation](https://github.com/probityai/agent-evidence-admission/blob/6aa41e6471dfa8639fe4571dbcf13179cd0a67b9/consumers/spend-reservation/probity_spend_reservation.py).

Those references describe prior infrastructure and design, not newly implemented production guarantees in this demo. An event-local Ezio adapter is new work; the inspected Probity verifier does not already ship an Ezio adapter. This repository does not establish that Observer's protected-dispatch protocol has been integrated into the starter's mock-email action.

## Evidence labels

| Evidence | How to interpret it |
|---|---|
| Ezio response | Producer-reported findings from the supplied local simulator, obtained over its HTTP API. |
| Company checks | Consumer-selected checks against the supplied fictional price/policy files and request context. “Independent” means separate from Ezio's score, not independently operated custody. |
| Input/source/policy hashes | Bind retained local bytes to a decision. They do not authenticate an external authority or prove the completeness of observation. |
| Audit/action logs | Local exercise records. The action logs a simulated email; no customer message is sent. |
| Official results | Measurements emitted by the unmodified supplied harness on the selected judging run. They do not measure real Ezio accuracy or general agent safety. |

The checker uses bounded explicit rules rather than arbitrary-language semantic verification. Missing evidence or authority is reviewable; a favorable aggregate score alone is insufficient.

## Tools and access

OpenAI Codex AI assistants were used for source inspection, planning, implementation, tests, UI, and documentation, with parallel agents assigned separate responsibilities. The participant remains responsible for reviewing the actual behavior and submitting the work. GitHub access was used to obtain project sources and preserve the event work.

The runtime uses the supplied mock Ezio over localhost and local fictional data. The project supports Python 3.10 or later. `.python-version` selects Python 3.12. Install runtime and development dependencies with `uv sync --locked`.

`pyproject.toml` defines the dependencies, and `uv.lock` pins their resolved versions. The `requirements.txt` and `requirements-dev.txt` files are generated compatibility exports for pip users. They preserve the published pip setup workflow. CI checks that those exports match the lockfile.

After installation, the demo needs no external service. It requires no live Ezio credentials, TokenRouter credits, Keenable credits, external LLM calls, or Gmail data. Do not include personal or confidential information in demo inputs.

Only claim verification outcomes demonstrated by retained runs. No performance numbers are asserted in this document.
