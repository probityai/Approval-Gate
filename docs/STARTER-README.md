# Approval Gate starter

**A trust score doesn't make a decision. Your wrapper does.**

An **Ezio AI** participant starter for the Decision-Grade AI Buildathon. Build a decision wrapper around a **local simulated Ezio API**, not another scoring engine and not the live Ezio product.

> **This repository is fully self-contained.** Everything runs on your own laptop. You never expose your code to the network, and judges never set anything up: the final judging set is delivered to you in the last 30 minutes, and one command runs it against your wrapper. No live Ezio key, paid credits, or external dependencies are required. Python 3.9 or newer, standard library only.

## Start here

1. Choose a [business persona](../personas/README.md).
2. Implement your independent signal and decision policy in [`wrapper/`](../wrapper/README.md).
3. Rehearse with `gate.py try` and `gate.py practice` as often as you like.
4. In the final 30 minutes, run `gate.py judge` with the released passphrase and submit the results files.

```bash
git clone <your team's copy of Approval-Gate>
cd Approval-Gate

# one input through your wrapper, with its mock action (also fills your demo audit log)
python3 gate.py try --persona customer_support "Your 30% loyalty discount applies to the Pro plan."

# practice set + all six failure drills, fully automatic
python3 gate.py practice --persona customer_support

# practice, judge and results all end by opening the color-coded results page;
# this reopens it for the newest run without running anything
python3 gate.py results
```

`gate.py` starts a private mock Ezio and your wrapper on `127.0.0.1` (free ports, nothing reachable from the network), sets `EZIO_URL` for your wrapper, runs the checks, and shuts both down. You do not start `mock_ezio.py` or `wrapper/serve.py` yourself; you still can, for example when building your demo.

### Final judging set (last 30 minutes)

The final held-out set is in this repository from the start, **encrypted** in `final/final_set.sealed`. Organizers announce the passphrase when the final 30 minutes begin (2:30 PM Pacific). Then:

```bash
python3 gate.py judge --persona customer_support --team "Your Team Name"
```

Paste the passphrase when asked (or set `GATE_FINAL_KEY`). The run takes about 1–2 minutes and writes:

| File | What it is |
|---|---|
| `results/<team>.json` | Official measured result. **Submit it unchanged**; it is checksummed and signed. |
| `results/<team>.report.md` | Readable report: score breakdown, decisions to review, failure drills, flags. |
| `runs/<team>-*/` | Your wrapper's audit and action logs from that run (evidence for the judges). |

See the results at any time with `python3 gate.py results`.

You can run `judge` as many times as you like before the deadline; the **last** run you submit counts. Do not edit `harness/`, `mock_ezio.py`, `fixtures.json`, or `personas/*` data: the report records a digest of them, and organizers compare it with the released version. The report also records which of your wrapper files produced it.

Judges can repeat the run on your laptop during your slot, add their own surprise input with `gate.py try`, and ask to see your `audit_log.jsonl` and `actions.log`. 

### Other languages

The default wrapper is `python3 wrapper/serve.py`. For another language, point `gate.py` at your own start command; it must read `HOST`, `PORT`, and `EZIO_URL` from the environment and serve `POST /decide` and `GET /health` ([contract](../wrapper/README.md#wrapper-http-contract)):

```bash
export GATE_WRAPPER_CMD="node wrapper/server.js"     # or --wrapper-cmd "..."
python3 gate.py practice --persona paralegal
```

Optional Docker mock (only if you want to run the mock separately):

```bash
docker build -t approval-gate-mock .
docker run --rm -p 127.0.0.1:8787:8787 approval-gate-mock
```

## Your challenge

Turn the mock's accuracy, bias, tone, and agreement signals into **APPROVE**, **HOLD_FOR_REVIEW**, or **BLOCK**. Explain your decision, provide evidence, and allow the mock action only when approved.

A GREEN overall tier is not proof that accuracy was checked. A successful policy catches bad inputs **and** lets good inputs through.

| Persona | Action | Source of truth |
|---|---|---|
| Customer support lead | Send a customer email | Prices and company policies |
| Paralegal | Submit a memo to a partner | Trusted and fabricated citation lists |
| Financial analyst | Publish a client note | A fictional company's fact sheet |

Use one supplied persona. Ask a mentor before proposing another; it needs approved data and an agreed evaluation plan before judging.

## Minimum requirements

| # | Requirement |
|---|---|
| 1 — hard | **Contract.** Serve `POST /decide` and `GET /health`, and call the mock **over HTTP** at the URL in `EZIO_URL`, never by importing its JSON or implementation. `gate.py` checks this automatically: a wrapper that cannot be reached or ignores the mock fails the run. |
| 2 | **Independent signal.** Use at least one signal beyond the Ezio score: source-of-truth data, a rule check, or a second model. |
| 3 | **Degraded states.** Handle rate limits, server errors, slow or malformed responses, and `scored`, `not_scored`, and `unavailable` accuracy explicitly, without crashing. |
| 4 | **Decision record.** Every decision carries a plain-language reason and supporting evidence, and every ordinary decision is audited: input, mock response, signals/evidence, decision, reason, and timestamp. |
| 5 — hard | **Action gate.** Demonstrate one bad input blocked before the mock action and one good input approved with the action running. |

Missing any hard requirement, or two or more other requirements, means a judge votes **no** on the minimum bar. A single borderline miss on another requirement may instead lower the relevant rubric score. A majority of **no** votes caps the total at **40/100**. A tied minimum-bar vote requires organizer review; it does not automatically apply the cap. A cap is not disqualification.

A one-sentence justification for every threshold is not a separate gate: it belongs in your one-page decision policy (see below), where the judges read it and score it under problem fit.

**Verification:** `gate.py judge` runs your wrapper with its normal logging redirected to `runs/`, cross-checks `actions.log` against your responses (the mock action must run exactly when a decision is APPROVE), and flags a missing audit trail. For the demo, show your ordinary `audit_log.jsonl` and `actions.log` from `gate.py try`.

## Submission and demo

Submit one Devpost project per team with:

- Your persona and the action your wrapper gates.
- A short screen recording or screenshots of your persona page, if you built one (the UI is worth 25 points; no UI scores 0 of 25).
- A repository judges can access, with exact setup and run instructions.
- A one-page decision policy: signals, thresholds, justifications, and failure behavior.
- Your `results/<team>.json` and `results/<team>.report.md` from the final run.

Prepare a **two-minute demo**, driven through your persona page where you have one: introduce the stakes, approve a good input with its mock action, block a bad input with evidence, and explain your threshold choices. Be ready for judge-provided surprise inputs during your judging slot; judges send them with `gate.py try` on your laptop.

Build window: **October 10, 2026, 1:15–3:00 PM Pacific**. Demos, judging, and awards: **3:00–4:00 PM Pacific**. Complete challenge-specific work during the build window.

## Wrapper UI (25 points)

Build a page for your persona on top of your wrapper. It is worth **25 of the 100 points** and it is optional: a team that works only in the terminal can still earn the other **75**. How you build it is up to you.

**What the page does**

1. **Send an input, get a response.** The user enters a draft (and, if your rules use it, context). The page shows the decision, the reason, the supporting evidence, and whether the mock action ran.
2. **Or simulate the action from a test input.** The user picks a sample input and sends it. The page shows the gate working: an approved input runs the action, a held or blocked one does not.

**Where to start.** [`wrapper/ui/persona.html`](../wrapper/ui/persona.html) is a skeleton with `TODO` instructions at every step; edit it or replace it. Everything in `wrapper/ui/` is served by `wrapper/serve.py`, with `persona.html` at `/`, so plain JavaScript, a built React/Vue/Svelte app, or any other browser-run UI works. Two helpers are provided: `POST /decide` (your wrapper) and `GET /api/samples?persona=<name>` (the practice inputs, without answers). To run your page with a mock behind it:

```bash
python3 gate.py serve --persona customer_support     # prints the address; Ctrl-C to stop
```

Rules:

- The page **must call your wrapper's `POST /decide`** and show what it returns. It must not make or alter decisions in the browser, call the mock, or run the action itself: the gate stays in the wrapper.
- It must work offline on your laptop with the supplied fictional data. Nothing may depend on an external service at demo time.
- Judges open it on your laptop and enter their own surprise input through it.

**You do not build a results page.** After any run, `python3 gate.py results` opens a color-coded view of the newest result in `results/` (practice or final set) on localhost; it refreshes by itself when a new run finishes. `gate.py practice` and `gate.py judge` open it for you as soon as the run ends (use `--no-show` to skip it, or set `GATE_NO_SHOW=1`); Ctrl-C in the terminal stops the viewer. If a viewer from an earlier run is still open, the new run reuses it. It is provided so that results are shown the same way for every team: green is the right call, amber is a bad input caught with a different label than expected, red is wrong, missed, falsely blocked, or an error.

Judges score five UI criteria, each 0 to 5 (0 means that part is missing), for a maximum of 25. They are in the scoring section below.

## Scoring

| Part | Points | What counts |
|---|---:|---|
| Held-out decisions, core | 25 | Twelve inputs unseen until the final 30 minutes: catch rate (10), avoiding false blocks (10), exact decisions (5) |
| Held-out decisions, stretch | 10 | Six harder inputs unseen until the final 30 minutes: the share decided correctly |
| Failure handling | 15 | Six failure modes, 2.5 points each |
| Judge rubric | 25 | Technical execution, problem fit, novelty, demo clarity, and practical impact; equally weighted, 5 points each |
| Wrapper UI | 25 | Five criteria, 5 points each: send an input and get a response, simulate the action from a test input, clarity of risk, resilience and review, fit for the persona's user |

Each judge scores the five rubric dimensions from 1 to 5 and the five UI criteria from 0 to 5. Each score is averaged across judges and the averages are summed, so the rubric is worth up to 25 points and the UI up to 25. **A terminal-only team can score up to 75.** A failed or invalid request earns no catch or exact-match credit and counts as a false block on a good input. HOLD and BLOCK both count as catching a bad input; both count as false blocks on a good input.

| UI criterion (0–5 each) | Zero | Solid (3) | Strong (4–5) |
|---|---|---|---|
| Send an input, get a response | No UI | Draft (and context) in; decision, reason, evidence, and whether the action ran out, via `POST /decide` | Handles empty or oversized input and slow responses; shows Ezio's signals where they explain the decision |
| Simulate the action from a test input | Not offered | Pick a sample input, send it, and see the action run only when approved | Walks through several samples (good, bad, held) side by side; shows that a blocked input never reached the action |
| Clarity of risk | No UI | Decision and reason obvious at a glance | Flags the claims or figures in the text; states the consequence of approving; color is not the only signal |
| Resilience and review | No UI | Shows errors and a down or degraded Ezio instead of failing silently | A hold-for-review step with a logged reason to proceed or reject, and a view of the audit trail |
| Fit for the persona | No UI | A user in the persona's job could use it without help | Shaped around the persona's real workflow and stakes |

The core set mixes three kinds of input: bad facts only your persona data can catch, bad claims only Ezio's fact-check can catch, and inputs where one dimension is poor while the overall tier looks fine. The stretch set is harder in a different way: figures written in other formats, several facts in one sentence, short case names, and near-miss citations. A stretch input is decided correctly when a bad one is not approved, or a good one is approved. Practice items tagged `ezio_decisive` and `stretch` show what each kind looks like.

For failure handling, both probes must return valid decisions and non-empty reasons within the harness timeout; the bad probe must not be approved. Be ready to defend the good probe's behavior. The harness timeout is 60 seconds per decision.

Totals within **5 points of the highest remaining total** form a tie group. Apply higher held-out points, fewer held-out errors, then higher average problem-fit score. Remaining ties go to a recorded judges' vote. Remove that group and repeat; groups do not chain through intermediate scores.

Rule violations require organizer review and a recorded reason. The following suggested adjustments match the judging guide:

| Violation | Suggested outcome |
|---|---|
| Hard-coded answers to practice or final-set inputs | −10 points |
| Replacing the supplied scores with a separate scoring engine instead of using them | −5 points |
| Editing the harness, mock, persona data, or a results file | Disqualification |
| Sharing the final-set passphrase or contents before the deadline | Disqualification |

Blocking everything is already penalized by the false-block score; it receives no extra deduction. General libraries and AI assistants are allowed; explain and verify your own work. Use only supplied or approved non-sensitive data.

## Mock API contract

```bash
curl -s http://localhost:8787/v1/analyze \
  -H "Authorization: Bearer mock-team-key" \
  -H "Content-Type: application/json" \
  -d '{"text":"Revenue grew 22% year over year.","depth":"simple"}'
```

`GET /v1/health` returns 200 when ready. The fixed example key is only a local mock credential. Calls normally take 2–6 seconds, deterministically per text.

| Field | Meaning |
|---|---|
| `verdict.tier` | GREEN (80+), YELLOW (60–79.9), RED (below 60); mock-only cutoffs |
| `verdict.score`, `label`, `summary` | Overall score and explanation |
| `dimensions.accuracy.status` | `scored`, `not_scored` (no claims checked), or `unavailable` (fact-check unavailable) |
| Accuracy counts | `claims_total`, `claims_verified`, `claims_contradicted` |
| `dimensions.bias`, `tone`, `agreement` | Scores from 0–100; higher is better |
| `claims[]` | Per-claim verdict and source, when accuracy is scored |
| `findings[]` | Explanations with severity NOTE, WARNING, CRITICAL, or N/A |

### Practice failure controls

While building, you can also run the mock yourself and set `X-Mock-Scenario` on a request:

| Value | Behavior |
|---|---|
| Omitted | Fixture match, otherwise deterministic text-based generation |
| `slow` | Responds after 8 seconds |
| `rate_limited` | First call per text returns 429 with `Retry-After: 2`; retry succeeds |
| `server_error` | First call per text returns 503; retry succeeds |
| `down` | Always 503 |
| `malformed` | 200 response missing `dimensions` |
| `unavailable` | Accuracy status is `unavailable` |

Restart your practice mock to reset first-call failures. `gate.py practice` and `gate.py judge` drive all six modes for you through a private control channel your wrapper never sees.

Mock environment variables: `PORT`, `HOST`, `MOCK_API_KEY`, `LATENCY` (such as `2-6` or `0`), and `CHAOS` (percentage of random 429/503 responses). `gate.py judge` also loads additional claims (from the sealed set) for the mock to fact-check.

### Fixtures and limitations

`fixtures.json` contains exact-match examples; case and trailing periods are ignored. Other inputs use simple text patterns and a small set of known public-knowledge claims, which the mock reports as supported or contradicted in `claims[]`. It cannot see your persona's private data. The final run's mock fact-checks additional claims that are not in `fixtures.json`, so build on the response fields rather than on a fixed list. `X-Mock-Source` identifies the response path. Fixture scores and generated weights are artificial, **not Ezio's production methodology**. This challenge measures your wrapper, not the real product's accuracy.

## Verify the starter

```bash
python3 test_mock.py
python3 -m unittest test_contract test_harness
```

The starter intentionally leaves `check_source_of_truth()` empty and policy justifications as TODOs. Passing these infrastructure tests is not a completed challenge solution.
