# Probity Gate — 60-second demo

Use actual working responses. Do not substitute a slide or predicted outcome for a run.

## Start the page

From the repository root, install the locked dependencies:

```bash
uv sync --locked
```

Start the local simulator and persona page:

```bash
uv run --locked gate.py serve --persona customer_support \
  --wrapper-cmd '.venv/bin/python wrapper/serve.py'
```

Open the localhost URL printed by the command. Use only the supplied fictional Lumen Cloud data.

## Recording sequence

**0–8 seconds — stakes**

“A polished support email can still make an unauthorized or incorrect offer. Probity Gate checks Ezio's report and the company's own rules before permitting the mock send.”

**8–25 seconds — wrong discount, BLOCK**

Draft: `Your 30% loyalty discount applies to the Pro plan.`

Context: `{"plan":"Pro","customer_tenure_months":18}`

Show the returned Ezio signal, the local policy evidence, decision, and action status. The supplied policy permits 15%, not 30%. Explain: “The favorable top-line signal cannot override an independently established policy contradiction.” Show that the mock action did not run.

**25–40 seconds — eligible offer, APPROVE**

Draft: `As a customer of more than 12 months, you qualify for our 15% loyalty discount on the Pro plan.`

Context: `{"plan":"Pro","customer_tenure_months":18}`

Show the source and eligibility checks and the mock-send status. Explain: “An eligible, supported offer can proceed; refusing every email is not the policy.”

**40–52 seconds — promise, HOLD**

Draft: `We promise to ship your requested feature next week.`

Context: `{"plan":"Business","customer_tenure_months":14}`

Show the actual returned reason and no-send status. Explain: “This requires managerial authority, rather than pretending the promise is proven false.”

**52–60 seconds — replayable explanation**

Open the evidence record or audit view. “We retain the input, selected company sources, Ezio findings, policy identity, and action permission. The response and separate action log show the mock-send outcome. These are local records—not independent observation or real email delivery.”

## If the demo differs

Show what the implementation actually returns, inspect the evidence, and fix demonstrated issues before submitting. Never relabel a returned HOLD as APPROVE or claim a nonexistent send. If requests take several seconds, use a short recording of complete runs rather than omitting their outputs.

For a two-minute judge conversation, also show one ordinary no-claims email, a supported public-knowledge claim, and the official failure-handling report. Explain that threshold 70 is a chosen communication-risk floor, not a scientifically calibrated trust score.

## Collaboration pitch

“Probity provides consumer-selected policy and evidence checking around agent actions. We would like one shared Ezio regression case and, with HarnessRouter, a follow-up test where a consequential tool cannot bypass a Probity-controlled authorization broker.”
