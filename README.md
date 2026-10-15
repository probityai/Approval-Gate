# Probity Gate

This public repository is frozen as a reference for the October 10, 2026 customer-support simulation, with usable commands and retained results. It is not a production service.

Probity Gate checks evidence and applies policy before a customer-support agent sends a mock email.

Built for support leads, it combines the supplied Ezio simulator with company prices, discount eligibility, and authority rules. The authentic [Probity verifier](https://github.com/probityai/probity-verify) produces the evidence decision.

## Set up

Use `uv` from the repository root. The `.python-version` file selects Python 3.12. Install the locked dependencies before the offline demo:

```bash
uv sync --locked
```

The manifest in `pyproject.toml` and resolved versions in `uv.lock` are the dependency source. The `requirements*.txt` files are generated compatibility exports for pip users.

## Start the page

Run the supplied harness to start the local simulator and wrapper:

```bash
uv run --locked gate.py serve --persona customer_support \
  --wrapper-cmd '.venv/bin/python wrapper/serve.py'
```

Open the localhost address that the command prints. Enter a draft and customer context, or select a practice sample.

The exact wrapper start command is:

```bash
.venv/bin/python wrapper/serve.py
```

The wrapper reads the simulator's address from `EZIO_URL`. The harness sets this value when you start the page.

## Run the official judge

After the organizer releases the passphrase, run the judge:

```bash
uv run --locked gate.py judge --persona customer_support \
  --team "Probity Gate" --wrapper-cmd '.venv/bin/python wrapper/serve.py' \
  --no-show
```

Enter the passphrase at the prompt. Keep it out of source files and the submission form.

Open the result from that run:

```bash
uv run --locked gate.py results
```

The locked uv judge run scored 50/50 on October 10, 2026, at 3:39:02 PM Pacific. The [results image](docs/assets/official-results-uv.png) records that run. The judges score the other 50 points separately.

## Check the decisions

A supported eligible offer returns `APPROVE` and runs the mock action. A contradiction returns `BLOCK`. Missing evidence or authority returns `HOLD_FOR_REVIEW`.

Run the project checks:

```bash
uv run --locked python -m pytest -q
uv run --locked test_mock.py
```

Replay a downloaded decision without a network request or an action:

```bash
uv run --locked wrapper/replay.py downloaded-record.json
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
