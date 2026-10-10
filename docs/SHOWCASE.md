# Probity Gate — evidence before action

## Ninety-second pitch

I'm Sankalp Gilda, and I build Probity. We make agent decisions inspectable and replayable.

Our demo protects a support lead from sending an incorrect price, an ineligible discount, or a promise outside their authority. It combines Ezio's report with the company's own records. A favorable score alone cannot authorize the email.

First, a correct eligible offer passes and the mock email runs. Next, a false discount blocks, with the exact conflicting evidence visible. Finally, a feature promise goes to review because the support lead lacks authority.

The decision is more than a colored badge. It retains the draft, context, evidence, and selected policy. Another consumer can replay that record offline and see whether the same checks and decision still follow. A changed input, source, or policy breaks the binding.

This uses Probity's actual verifier today. Our wider public work covers policy admission, records of agent actions, and conformance tests. The opportunity is to carry this evidence boundary across agent runtimes.

## Public repositories and this demo

| Component | Public source | Connection to this demo |
| --- | --- | --- |
| Verify | [probity-verify](https://github.com/probityai/probity-verify) | **Runs in this wrapper.** The authentic kernel reduces retained evidence under a consumer-selected policy. |
| Admission | [agent-evidence-admission](https://github.com/probityai/agent-evidence-admission) | Existing policy infrastructure. It does not run in this wrapper. |
| Observer | [agent-evidence-observer](https://github.com/probityai/agent-evidence-observer) | Existing infrastructure for records and protected dispatch. It does not run in this mock-email boundary. |
| Vectors | [agent-evidence-vectors](https://github.com/probityai/agent-evidence-vectors) | Existing conformance corpora and verifier harnesses. This demo does not run that suite. |

Open the [Probity AI organization](https://github.com/probityai) after the working example. Use one concrete artifact for the next discussion.

## What this event improved

The new repository applies the verifier to a support workflow. It adds claim binding for several prices in one sentence, independent policy checks, an action gate, a persona UI, and offline replay.

The authentic upstream verifier remains unchanged. The event improvements live in the public [Probity Approval Gate repository](https://github.com/probityai/Approval-Gate). The demo does not expose the private sandbox product.

Integration details and exact scope: [PROBITY-BRIDGE.md](PROBITY-BRIDGE.md). Reuse details and pinned sources: [PROVENANCE.md](PROVENANCE.md).
