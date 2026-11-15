# Second Look: a pre-trade check for self-directed investors

PolyFinances Datathon 2026, AlphaForge AI challenge (National Bank x AWS). Team: Alex Zhao, Ariane Nguyen, Daniel Puente, Oliver Zhou. Simulation only: fictional clients, no real orders, not investment advice.

**Live demo:** https://t7tfflhogj5caf4un4hbxqol6i0nddsl.lambda-url.us-east-1.on.aws/ (runs on the event's AWS account, which closes on November 16, 2026 at 13:59).

A deterministic rule engine decides green, yellow or red for a trade against the investor's stored profile, goals and ESG preferences. On a red trade, AI agents on Amazon Bedrock explain why with dated evidence and talk it through with the investor; they never change the verdict.

## Installation guide (for judges)

### 1. Get the code and the environment

You need Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```sh
git clone https://github.com/Alexkyoya/datathon2026.git
cd datathon2026
uv sync --locked
```

### 2. Add the starter data

Copy the four CSVs from the organizers' starter zip (`prices_daily.csv`, `fx_usdcad_daily.csv`, `macro_rates.csv`, `assets_universe.csv`) into `data/`. They are not committed because of the Yahoo Finance terms.

### 3. Run it locally (no AWS needed)

```sh
uv run python tests/test_tools_local.py          # tools on real data, ends with ALL GOOD
uv run --with pytest python -m pytest tests      # rule, tools, guardrail and conversation tests
cd second_look && uv run --project .. python -m second_look serve
# open http://127.0.0.1:8000 for the brokerage dashboard (rules only, no AI chat)
```

### 4. Deploy the full product to AWS (us-east-1)

Configure AWS credentials (`aws configure`, region `us-east-1`; never commit keys) with access to Amazon Bedrock (Claude Sonnet 5.5 and Amazon Nova models), Lambda, S3 and IAM. Then run, in order:

```sh
uv run python scripts/upload_to_s3.py       # data and profiles to S3
uv run python scripts/deploy_lambda.py      # tools Lambda (second-look-tools)
uv run python scripts/setup_guardrails.py   # Bedrock Guardrails, input and output
uv run python scripts/deploy_api.py         # API Lambda with the dashboard; prints the public URL
```

All four scripts are safe to re-run. `uv run python scripts/check_chat_runtime.py` checks Bedrock access, and `uv run python scripts/evaluate_conversation.py` runs the 24 test conversations.

### Demo trades to try

Pick a client in the dashboard and try: Robert NVDA CAD 360,000 (red, all his cash into one stock), Robert SPY CAD 30,000 (green), Mateo XOM CAD 3,000 (red, ESG exclusion), Mateo XIU.TO CAD 5,000 (yellow, energy inside the fund), Amira ETH-USD CAD 3,000 (red, crypto cap). On a red trade, write a reason to start the conversation.

The sections below are the team's working notes.

---

# datathon2026

Use **uv** to give each teammate their own `.venv` with the same locked package
versions. Run commands inside `datathon2026`, even if it lives inside `Datathon`.

## First setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) once.
On macOS/Linux:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On Windows PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Restart your terminal after installing uv. After cloning and switching to `ari.dev`,
or after receiving these setup files, run from the repository root:

```sh
uv sync --locked
uv run python -c "import sys, numpy, pandas, scipy, sklearn, matplotlib, ipykernel; print(sys.executable); print('Imports OK')"
```

uv creates `.venv` and installs the versions from `uv.lock`. It can download the
Python version in `.python-version` if missing. The baseline is **Python 3.12.14**,
chosen from the original developer's installed version. This setup is verified on
macOS ARM64; your teammate should confirm it on their computer.

## Everyday use

- After pulling changes: `uv sync --locked`.
- Run a script: `uv run python path/to/script.py`.
- Add a package: `uv add PACKAGE`.
- Remove a package: `uv remove PACKAGE`.
- Upgrade one package: `uv lock --upgrade-package PACKAGE`, then `uv sync --locked`.
- Share changes to both `pyproject.toml` and `uv.lock` together.

No activation is needed with `uv run`. If you prefer activation, use
`source .venv/bin/activate` on macOS/Linux, `.\.venv\Scripts\Activate.ps1`
in PowerShell, or `.venv\Scripts\activate.bat` in Windows Command Prompt.

## VS Code / notebooks

Open the **datathon2026 repository folder** directly in VS Code. Install the
recommended Microsoft Python and Jupyter extensions.

1. Command Palette > **Python: Select Interpreter** > choose this repository's
   `.venv` (`.venv/bin/python` on macOS/Linux, `.venv\Scripts\python.exe` on Windows).
2. Open a notebook > **Select Kernel > Select Another Kernel... > Python
   Environments** > choose the same `.venv`, then run cells or **Run All**.
3. Verify with `import sys; print(sys.executable)` in a cell; it must point into
   this repository's `.venv`. These UI selections must be made on each computer.

The environment includes ipykernel, so no separate kernel registration is needed
for VS Code. The Second Look component and its tests are described below.

## What the files do

| File | Purpose |
| --- | --- |
| `pyproject.toml` | The packages the project needs. Edit using `uv add` / `uv remove`. |
| `uv.lock` | Exact resolved versions and platform details. Generated by uv; commit it. |
| `.python-version` | The Python version uv should use. |
| `.vscode/` | Portable environment settings and extension recommendations. |
| `.venv/` | Your local installed environment. Ignored by Git; never share it. |

Use project-relative data paths, e.g. `Path("data") / "input.csv"`, from the
repository root. `.env`, `.venv`, and Python caches are already ignored. The
starter needs no environment variables; keep credentials out of committed files.

More details: [uv project workflow](https://docs.astral.sh/uv/guides/projects/).

---

# Second Look (agents + AWS)

A pre-trade check for self-directed investors. Simulation only, not investment advice.

**Flow:** the rule engine (`rule_check`, plain code in Lambda) runs first. Green passes and yellow asks for a confirmation. Red asks the client why, then the Strands agents on Bedrock (Coordinator → Profile, Financial Data, News) write a JSON explanation for the dashboard. The verdict always comes from the rule engine. Every tool call is logged in CloudWatch, and every run (result + trace) is saved to `s3://<bucket>/runs/`.

## Files

- `config.py`: region (`us-east-1`), bucket, Lambda name, Bedrock model ID
- `lambda/lambda_function.py`: all tools: `get_profile`, `rule_check`, `goal_check`, `risk_metrics`, `macro_snapshot`, `news_check`
- `agents/tools.py`: Strands tools that call the Lambda, plus the trace
- `agents/second_look.py`: agents, prompts and the green/yellow/red flow
- `agents/universe.py`: the tickers the agents may propose, generated from `assets.json` at run time
- `data/profiles.json`: NBF ranges, team assumptions and fictional clients (holdings, income, goals, ESG preferences)
- `data/news_snapshot.json`: official news items (SEC EDGAR, NVIDIA IR, Fed, Bank of Canada) dated on or before the decision date
- `scripts/provided_universe.py`: prints the provided universe and checks every file against it (`--fix-data` strips non-provided rows from `data/`)
- `scripts/add_asset_horizons.py`: writes each asset's worst past fall and recovery time into `assets.json`
- `scripts/test_bedrock.py`, `scripts/upload_to_s3.py`
- `tests/`: tools on real data (acceptance table) and universe checks; `second_look/tests/`: rule, schema and generality tests

## Setup

1. Copy the starter CSVs (`prices_daily.csv`, `fx_usdcad_daily.csv`, `macro_rates.csv`, `assets_universe.csv`) from the organizers' zip into `data/`. They're git-ignored because of the Yahoo Finance terms.
2. Add the AWS packages once (this updates `pyproject.toml` and `uv.lock`, so commit both): `uv add boto3 strands-agents`
3. AWS credentials: `aws configure` (region `us-east-1`). Never commit keys.

## Run

- Local tools test: `uv run python tests/test_tools_local.py` prints the acceptance table and `ALL GOOD`.
- All tests: `uv run --with pytest python -m pytest tests` and `cd second_look && uv run --project .. --with pytest python -m pytest tests`
- Bedrock check: `uv run python scripts/test_bedrock.py`
- Upload data to S3: `uv run python scripts/upload_to_s3.py`
- Agents with local tools: `uv run python agents/second_look.py --client robert --ticker NVDA --amount 360000 --local`
- Agents with the Lambda: the same command without `--local`

Demo cases (purchases are paid from cash): `robert SPY 30000` (green), `robert NVDA 360000` (red, all his cash into one stock), `robert XBB.TO 340000` (red, goal rule: cash for his first retirement withdrawal moved into a bond fund that once took 3 years to recover), `mateo ESGV 12000` (yellow, concentration watch), `mateo XOM 3000` (red, ESG exclusion), `mateo XIU.TO 5000` (yellow, ESG look-through: the fund holds 18.6% Energy), `amira QQQ 5000` (green), `amira ETH-USD 3000` (red, crypto cap), `amira NVDA 5000` (red, single stock).

## Lambda

One command, safe to re-run after every change to `lambda_function.py`: `uv run python scripts/deploy_lambda.py`. It creates the IAM role, finds the pandas layer, creates or updates the function, and runs a test call.

Manual alternative (console):

1. Create the function `second-look-tools` (Python 3.12) and paste `lambda/lambda_function.py`.
2. Add the layer `AWSSDKPandas-Python312`. Set memory to 1024 MB and the timeout to 1 min.
3. Environment variables: `DATA_BUCKET=<bucket>`, `DATA_PREFIX=data/`. Give the execution role S3 read access.
4. Test event: `{"action": "rule_check", "client_id": "robert", "ticker": "NVDA", "amount_cad": 360000}` should return red.

## Dashboard and API on AWS

`uv run python scripts/deploy_api.py` deploys one Lambda (`second-look-api`) with a public URL. It serves Ariane's dashboard (`second_look/web`), her API routes running the team rule engine, and the agents. Simulation state is saved to `s3://<bucket>/second-look/`. It's safe to re-run after any change.

- `GET /`: the dashboard. Pick a client (robert, mateo, amira, demo_balanced) and use the demo buttons.
- `GET /api`: usage. `GET /api/context?client_id=robert`: stored client and reference portfolios.
- `POST /api/evaluate` with `{"client_id","decision_date":"2026-11-13","ticker","side","amount_cad"}` returns green, yellow or red.
- `POST /api/simulations/{id}/decision` confirms/cancels, or proceeds after explicit risk acknowledgement. The old `explain` action remains for API compatibility; the dashboard uses the persisted conversation endpoint below.
- `GET/POST /api/simulations/{id}/conversation` restores/sends chat turns. The first message runs the existing specialists/coordinator; subsequent messages reuse their reports. See [conversation setup and contracts](second_look/CONVERSATION.md).
- `POST /` with `{"client_id","ticker","amount_cad"[,"reason"]}` or `{"action": ...}`: the simple JSON API.

The URL is public with no login, for the demo only; a production version would add authentication.

## Contributing to the agent pipeline

- Write plain functions with JSON in and JSON out, and keep the output fields of the action you replace.
- Read data through `load_csv` / `load_json`. No local paths, no keys in code.
- `rule_check` runs the team rule engine in `second_look/` (Ariane's `rules.py` and `config/policy.json`), bundled into the tools Lambda by `deploy_lambda.py`. Purchases are paid from cash only. The demo personas live in `data/profiles.json` (CAD), and `second_look/scripts/make_persona_clients.py` writes them in the component's client format. `news_snapshot.json` holds official items (Daniel).

Data: AlphaForge starter dataset (Yahoo Finance, Bank of Canada, FRED), 2021-11-12 to 2026-11-12, in CAD. Ranges: National Bank Financial, Investment Strategy, Fall 2026. Everything else in `profiles.json` is a team assumption or fictional.

## Universe, rules and client schema

**Universe = the provided data only.** The investable universe is exactly the 31 tickers in the organizers' price file (committed copy: `second_look/data/raw/prices_daily.csv`): 10 Canadian stocks, 10 U.S. stocks, 9 ETFs (XIU.TO, SPY, QQQ, ESGV, XBB.TO, ZST.TO, AGG, TLT, GLD), BTC-USD and ETH-USD. `uv run python scripts/provided_universe.py` prints it and checks `assets.json`, `profiles.json` and `data/`. Nothing else is downloaded, and the code has no price downloads anywhere. A trade, holding or exclusion on another ticker is rejected with "X is not in the asset universe (the provided datathon data)". The agents' allowed list is generated from `assets.json` at run time, and the dashboard picker shows only these tickers. Reference data that is not prices (ETF sector weights, ESGV's ESG screen) is kept only for tickers in the universe, with its source and date.

**Rules** (`second_look/second_look/rules.py`, pure functions, no I/O). Every threshold is in `config/policy.json`, with a source and a rationale under `threshold_sources`.

- Allocation ranges per profile (NBF Fall 2026), single-stock cap, crypto cap, concentration (HHI), Growth sector cap: unchanged.
- **A. Goal coverage** (`goals.py`). Money needed by a date only counts if the asset holding it recovered from its worst past fall (5-year provided window, in CAD) in less time than is left before the date. It counts at the value left after a repeat of that fall. Cash and cash-like funds (asset class `cash`, i.e. ZST.TO) always count in full. Goals are funded earliest first, each from the most liquid assets first, and each dollar is used once. Recurring goals become yearly installments, minus any other income assigned to them, indexed at 2% if marked. Only installments within 5 years are checked. A goal is flagged only when the trade lowers its coverage below 100% *and* lowers the money available for it and all earlier goals. That is red for an essential goal due within 24 months, yellow otherwise. For clients without goals the rule is not applied (noted in `coverage.notes`).
- **B. ESG preferences** (`esg.py`). One screen for every client; the older top-level `excluded_tickers` / `excluded_sectors` are read into it. Buying an excluded ticker, or a single stock in an excluded sector: red. Buying a fund whose share in excluded sectors exceeds the client's `max_lookthrough_excluded_share`: yellow, with the fact-sheet date. The whole portfolio's look-through exposure rising above that maximum: yellow. A fund without sector data: unresolved. Selling is never flagged.
- The goal and ESG rules flag a trade only when it creates or worsens a problem. Missing data is never guessed: the rule returns an unresolved item, and `unresolved_handling` in the policy decides between a yellow note (the default) and a manual review (used for stored constraints the engine cannot read).
- Every alert carries `rule, severity, before, after, limit, effect (creates/worsens/improves/unchanged), source, message`.

**Client schema** (`contracts.validate_client`; invalid input is rejected with a plain message):

```json
"income": {"other_income_cad_per_year": 0, "source": "..."},
"goals": [
  {"name": "Emergency fund", "type": "one_off", "amount_cad": 12000, "target_date": "2026-11-13", "priority": "essential", "source": "..."},
  {"name": "Retirement income", "type": "recurring", "amount_cad_per_year": 70000, "start_date": "2027-07-01",
   "years": 30, "funded_by_other_income_cad_per_year": 0, "indexed_to_inflation": true, "priority": "essential", "source": "..."}],
"preferences": {"esg": {"excluded_tickers": ["XOM"], "excluded_sectors": ["Energy"], "max_lookthrough_excluded_share": 0.05,
                        "prefer_esg_screened_funds": true, "source": "..."}}
```

Recurring goals take `years` or an `end_date` after `start_date`. One-off target dates cannot be in the past. Amounts are >= 0. Other income assigned to goals cannot exceed `other_income_cad_per_year`. The emergency fund is a one-off essential goal due on the decision date: months x monthly expenses, minus savings held outside the portfolio.

**Assumptions** (team assumptions, labeled in the policy):

- Worst past fall and recovery time are historical stress tests, not forecasts.
- 5-year planning horizon, 24-month red window, 2% inflation (the Bank of Canada target), 5% default look-through tolerance.
- ESG screens cover equity exposure only; bond issuers inside bond funds are not looked through.
- Personas are fictional. One persona value was adjusted: Mateo's in-portfolio emergency fund is CAD 2,000 (CAD 9,000 needed, CAD 7,000 held in savings outside the portfolio), so that `mateo ESGV 12000` stays yellow.

**Add a client:** add it under `clients` in `data/profiles.json` (holdings in CAD, `income`, `goals`, `preferences.esg`). Run `uv run --directory second_look python scripts/make_persona_clients.py`, which validates it, then `uv run python scripts/upload_to_s3.py`. No code change is needed.

**Add an asset:** only a ticker that is in the organizers' provided price file. Add its entry to `second_look/config/assets.json`: class, single stock or fund, sector or sourced `sector_weights`, and `esg` only with a real source. Run `uv run python scripts/add_asset_horizons.py` (re-run it for a new decision date too), then `uv run python scripts/provided_universe.py`. No code change is needed.

## Second Look: reproduce output and integrate with AWS

The component is included in [`second_look/`](second_look/README.md) on `ari.dev`.
A teammate can update their checkout and run from the repository root:

```sh
git fetch origin
git switch ari.dev
git pull --ff-only origin ari.dev
uv sync --locked
uv run --directory second_look python -m second_look demo
uv run --directory second_look python -m second_look serve
```

Open http://127.0.0.1:8000 for the dashboard; Ctrl+C stops the server. The demo
prints green/yellow/red checks and writes results to
`second_look/data/runtime/demo_results.json`. Generated outputs and `.venv` are
not committed; raw inputs needed to regenerate them are included.

Run the tests and build an offline AWS handoff bundle from this same directory:

```sh
uv run --directory second_look python -m unittest discover -s tests -v
uv run --directory second_look python scripts/package_aws.py
```

The bundle is generated under `second_look/dist/aws/`. Use its `lambda.zip` for
an existing rule Lambda and its `s3/` inputs in an existing bucket. Handler:
`second_look.lambda_handler.handler`. Configure `DATA_BUCKET`, optional
`DATA_PREFIX` (default `second-look`), and optional `AGENT_FUNCTION_NAME` for the
existing Lambda that runs the team's AWS agent. No AWS resources are created.
The [component README](second_look/README.md#existing-aws-adapter) documents the
JSON contracts, S3 keys and red-reason handoff. An agent explanation cannot
change the numerical verdict. Crypto counts as alternatives and also has its
own cap. AWS calls are locally mocked in tests; live deployment/Bedrock execution
has not been verified.
