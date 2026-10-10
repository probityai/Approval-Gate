# Probity Gate — customer-support decision policy

**Consumer:** a Lumen Cloud support lead. **Action:** the supplied local `send_email` mock; no real email is sent. **Consequence:** a customer could receive an incorrect price, discount, entitlement, or unauthorized promise.

## Signals and precedence

| Signal | Decision | Reason |
|---|---|---|
| Company price, discount, refund, trial, or response-time claim contradicts `price_sheet.csv` or `policies.json` | **BLOCK** | A known company-policy contradiction must be corrected before sending. |
| Discount contradicts supplied plan/tenure eligibility or attempts forbidden stacking | **BLOCK** | Correct arithmetic does not make an ineligible offer permissible. |
| Required eligibility context is absent or cannot be interpreted | **HOLD_FOR_REVIEW** | A reviewer must establish eligibility; absence is not proof of ineligibility. |
| Ezio reports any contradicted claim | **BLOCK** | Claim-level contradiction takes precedence over an attractive overall score. |
| Ezio leaves a claim unresolved | **HOLD_FOR_REVIEW** | A consequential assertion lacks the support needed to send it. |
| Product/delivery promise, exception, hype, flattery, or guarantee outside support authority | **HOLD_FOR_REVIEW** | A manager must resolve authority or communication risk. |
| Tone, bias, or agreement score below **70** | **HOLD_FOR_REVIEW** | Each communication-risk dimension needs an acceptable floor; a high average cannot hide one weak dimension. The value is an event policy choice, not a calibrated safety probability. |
| Overall tier YELLOW or RED | **HOLD_FOR_REVIEW** | A weak aggregate signal requires review, but does not itself establish a false fact. Explicit company/report contradictions block; GREEN alone never establishes approval. |
| No contrary evidence, no review condition, acceptable signals | **APPROVE** | Permit the ordinary supported workflow instead of refusing every draft. |

**BLOCK outranks HOLD, which outranks APPROVE.** Independently established company contradictions still block when Ezio is degraded. Missing information causes review rather than an invented contradiction. A supported public-knowledge claim does not require a duplicate company-file source.

## Accuracy states and failures

- `scored`: inspect claim verdicts and counts, not just the summary score.
- `not_scored`: means no claims were checked. It may approve an ordinary draft with clean company checks and acceptable other signals; it is not an accuracy pass.
- `unavailable`, invalid response, or exhausted HTTP retries: hold unless an independent contradiction already requires block. Do not send the mock email.
- Use the starter's bounded HTTP retries and timeout budget, below the harness's 60-second request limit. Do not replace Ezio with a second scoring engine.

## Records and scope

Every ordinary decision audits the input/context, Ezio response, company checks, action permission, reason, and timestamp. The HTTP/UI response separately reports action execution; `actions.log` records mock sends. The evidence packet identifies input, selected source files, and policy by hashes, and distinguishes **producer-reported Ezio findings** from **consumer-selected local company evidence**. The mock action runs only on APPROVE.

These are local decision records. Hashes bind retained bytes; they do not authenticate Ezio's production service, prove independent observation, or establish that an external email was delivered. Pattern-based checks cover the declared fictional customer-support workflow; arbitrary language remains outside a universal correctness guarantee.
