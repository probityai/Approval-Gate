# Probity Gate

Probity Gate checks evidence and applies policy before a customer-support agent sends a mock email.

Built for support leads, it combines the supplied Ezio simulator with company prices, discount eligibility, and authority rules. The authentic [Probity verifier](https://github.com/probityai/probity-verify) produces the evidence decision.

## Set up

Use Python 3.12 from the repository root. Install dependencies before the offline demo:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
```

## Start the page

Run the supplied harness to start the local simulator and wrapper:

```bash
GATE_WRAPPER_CMD='.venv/bin/python wrapper/serve.py' \
  .venv/bin/python gate.py serve --persona customer_support
```

Open the localhost address that the command prints. Enter a draft and customer context, or select a practice sample.

The exact wrapper start command is:

```bash
.venv/bin/python wrapper/serve.py
```

The wrapper reads the simulator's address from `EZIO_URL`. The harness sets this value when you start the page.

## Check the decisions

A supported eligible offer returns `APPROVE` and runs the mock action. A contradiction returns `BLOCK`. Missing evidence or authority returns `HOLD_FOR_REVIEW`.

Run the project checks:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python test_mock.py
```

Replay a downloaded decision without a network request or an action:

```bash
.venv/bin/python wrapper/replay.py downloaded-record.json
```

The event adapter uses the unmodified `probity-verify` kernel at commit `a2a7e23413be0ab0ad1001d60085a2ff21eaa907`. The demo runs offline after installation and sends no real email.

The following documents explain the implementation and demo:

| Task | Document |
| --- | --- |
| Understand the decision policy | [Decision policy](docs/DECISION-POLICY.md) |
| Replay a retained record | [Probity bridge](docs/PROBITY-BRIDGE.md) |
| Check sources and reuse | [Provenance](docs/PROVENANCE.md) |
| Rehearse the demo | [Demo script](docs/DEMO-SCRIPT.md) |
| Explore the public Probity components | [Showcase](docs/SHOWCASE.md) |
