# Second Look

Simulate a cash-funded trade against an investor's **stored** profile and holdings.
There is no questionnaire. Crypto is an **alternative asset**, with a separate
crypto cap. The allocation spectrum explains a portfolio's position; deterministic
limits decide green/yellow/red. Nothing executes an order or changes the profile.

## Run

From this `second_look` directory, reuse the existing team environment:

```sh
../.venv/bin/python -m second_look demo
../.venv/bin/python -m second_look serve
# Open http://127.0.0.1:8000
../.venv/bin/python -m unittest discover -s tests -v
```

Or use Python 3.11+ in your own environment and install `requirements.txt`.
`python -m second_look clean` rebuilds only the data. `demo` builds the snapshot
and evaluates SPY $4,000 (green), GLD $14,000 (yellow), TSLA $12,000 (red) against
the same fictional stored Balanced portfolio. Trades are independent proposals;
confirming one does not change the stored investor's holdings. `serve --port 8001`
selects another loopback port. All local calculations work offline.

With the server running, `python tests/verify_http.py` checks the HTTP workflow.
If that checker environment has `jsonschema`, it also validates response records
against `contracts/simulation.schema.json`. No third-party library is needed in
Lambda's rules/service/application imports; local cleaning uses pandas/numpy.

## One implementation per responsibility

- `second_look/cleaning.py`: validates raw prices/FX, aligns and converts to CAD.
- `config/assets.json`: authoritative mapping of all 31 assets, including ETF
  classification and unknown sector look-through. `CASH_CAD` is a cash ledger.
- `config/policy.json`: existing profile ranges, limits, precedence and data ages.
- `config/reference_portfolios.json` + `second_look/spectrum.py`: versioned
  reference construction and deterministic allocation projection.
- `second_look/rules.py` / `service.py`: trade rules / dated unit valuation.
- `second_look/application.py`: shared stored-input and decision workflow;
  `web/` and `lambda_handler.py` are adapters to the same application.
- `examples/client.json`: fictional stored investor, including holdings in units,
  cash, profile, horizon, objectives, loss tolerance and explicit preferences.
- `data/raw/`: preserved source data, provenance and original generator/docs.
  `data/clean/` and `data/runtime/` are regenerable outputs, ignored by Git.

This component lives at `datathon2026/second_look` on branch `ari.dev` and uses
the repository's shared environment. The root README has clone/pull commands.
Obsolete `examples/snapshot.json`, `examples/demo_results.json` and the old static
`VALIDATION.txt` were removed. Current generated inputs/results have one location
under `data/runtime/`; validation lives in executable tests. Original raw datasets,
source documentation, the stored client and existing tests are retained.

## Reference construction

The supplied [FBN model sheet](../strategie-placement-profil.pdf),
**Stratégie de placement – Automne 2026**, page 1, was visually checked. We use its
**Pondération recommandée** column, rather than the separate Repère benchmarks.
The source hash and original regional weights are recorded in the reference config.

| Reference | Cash | Fixed income | Total equities | Alternatives |
| --- | ---: | ---: | ---: | ---: |
| Income | 4% | 68.5% | 22.5% | 5% |
| Conservative | 4% | 53.5% | 37.5% | 5% |
| Balanced | 4% | 38% | 48% | 10% |
| Growth | 4% | 28% | 58% | 10% |
| Maximum growth | 3% | 14% | 68% | 15% |

Each reference specifies full-precision ticker weights totaling 100%, checked
against its profile's class ranges. These **ETF substitutions are configurable
team assumptions**, not official FBN portfolios or investment recommendations:
ZST.TO proxies cash, XBB.TO fixed income, XIU.TO Canadian equities, SPY US equities,
and GLD alternatives. No international equity fund is supplied: its original
weight is redistributed in proportion to the documented Canada/US allocations.
For example, XIU weight = total equity weight × Canada / (Canada + US).
This preserves class totals without pretending to replicate international exposure.
Gold does not replicate a diversified alternatives sleeve, ZST is not cash, and
XBB does not replicate the sheet's suggested duration. References contain no
crypto; actual BTC/ETH positions still count toward alternatives **and** crypto.
These are allocation anchors, not HHI- or sector-screened portfolios.

## Spectrum methodology

Use the same vector `(cash, fixed_income, equities, alternatives)` for references
and the portfolio. Join adjacent reference vectors in the order above. For every
segment from `a` to `b`, calculate
`t = clip(dot(w-a, b-a) / dot(b-a, b-a), 0, 1)`. Choose the projected point
`a + t(b-a)` with smallest unweighted Euclidean distance from `w`.
Squared distances within `1e-12` tie; choose the earliest segment. Adjacent
identical references are rejected. Endpoints have 100% endpoint membership;
internal reference vertices use the earlier segment with one zero membership.

Return the adjacent categories, memberships `(1-t, t)`, score `segment_index+t`
from 0 to 4, projected class weights, residual vector and Euclidean distance.
No match-quality cutoff is invented: **a portfolio far from every reference still
has a score and must show its mismatch distance**. Memberships are interpolation
weights, not probabilities or a complete risk measure. The result includes both
positions, movement and distance along the score axis from the stored target.
Historical performance never assigns an investor or reference category.

## Data and rules

USD prices × BoC `usdcad` give CAD. Closing prices value holdings; adjusted CAD
prices calculate returns. Do not apply splits again. Daily snapshots are at
00:00 UTC: a completed source day D first enters D+1. Both price and FX joins
select only earlier available observations, with source dates, carried/missing/
stale flags and source hashes. No future backfill or interpolation occurs.
Calendar-day adjusted returns use 365 for annualization, not 252. Price-age caps
(7 days securities, 2 crypto) and FX age (7) are existing team assumptions now
shared through policy. Missing/stale required valuation data rejects a proposal.

The engine checks **both** allocation bounds, every single stock, crypto, HHI,
explicit ticker/sector exclusions and liquid cash reserves. Funds are exempt
from single-stock caps; HHI sees whole fund positions and combines cash with ZST.
A reduction cannot cancel a new/worsened breach. New/worsened stock, crypto or
explicit preference breaches are red; allocation breaches use the existing
5-point buffer. Existing HHI/watch and strict-improvement precedence is preserved.
All non-allocation limits and color precedence remain labeled team assumptions.

Horizon, objectives and loss tolerance stay agent context. Unknown stored
preferences, required ESG evidence or ETF sector look-through prevent a complete
green: result is partial/manual review unless a known red breach takes precedence.
No sector exposures, ESG certification or numerical horizon rules are invented.

## Interface and JSON contract v2.0

`POST /api/evaluate` accepts only the existing five trade fields:

```json
{"client_id":"demo_balanced","decision_date":"2026-11-13","ticker":"TSLA","side":"buy","amount_cad":12000}
```

Both HTTP and Lambda return the same record: `schema_version`, `simulation_id`,
`request`, `result`, `stage`, `agent_status`. Consumers read **`result.verdict`**
(previously top-level in v1.1). The result retains all original numerical fields,
adds `reference_portfolio_version` and `classification`, and hashes the request,
stored client, asset mapping, policy, references and snapshot.
The schema also exposes the trade shape as `$defs.trade`.

Green continues the simulation. Yellow waits for confirm/cancel. Red pauses for
a nonempty reason; only then is the agent handoff stored and the configured
service activated. `POST /api/simulations/{id}/decision` accepts
`{"decision":"confirm"}`, `{"decision":"cancel"}`, or
`{"decision":"explain","reason":"..."}`. No reason is a rule input. Agent
return values cannot change the numerical result. Missing required information
is manual review, never green. The UI shows target, positions, memberships,
mismatch, residual allocation, individual checks and provenance.

## Existing AWS adapter

Build the offline handoff bundle from this component directory after `demo`:

```sh
../.venv/bin/python scripts/package_aws.py
```

This creates ignored `dist/aws/lambda.zip`, staged objects under `dist/aws/s3/`,
and a SHA-256 manifest. It does not contact AWS. The ZIP has the importable
`second_look/` package and `config/` at its root, with no virtual environment,
raw data, caches or third-party dependencies. Your teammate can upload that ZIP
to an existing rule Lambda and upload the staged `s3/` contents beneath the
existing bucket/prefix. The manifest describes the exact files to transfer.

Handler: `second_look.lambda_handler.handler`; configure existing `DATA_BUCKET`,
optional `DATA_PREFIX` (default `second-look`), and optionally an **existing**
`AGENT_FUNCTION_NAME`. No resources are provisioned. Beneath that prefix supply:

```text
config/assets.json
config/policy.json
config/reference_portfolios.json
clients/demo_balanced.json          <- examples/client.json
snapshots/2026-11-13.json            <- data/runtime/snapshots/2026-11-13.json
```

Evaluation/decision routes use the same bodies and response record as local HTTP
(the `/api` prefix is optional in the Lambda routing). Results go to `results/`,
agent payloads to `agent_requests/`. A red reason invokes the configured existing
agent Lambda asynchronously. No configured service means `not_configured`, and
failed invocation means `dispatch_failed`; the simulation remains paused.
`AGENT_FUNCTION_NAME` is the name/ARN of an existing **Lambda**, not a Bedrock
agent ID. That Lambda is the bridge to the team's Bedrock/Strands implementation.
It receives `schema_version`, `simulation_id`, `investor_reason`, `request`,
`numerical_result` (including both spectrum positions and provenance), and an
instruction to preserve the fixed verdict. For an agent that calls the numerical
engine as a tool, invoke the rule Lambda with only the five trade fields above;
parse its HTTP envelope `body` and read `record.result`. Agents must get trusted
profiles/holdings from the store and cannot supply a replacement profile.
Returning/persisting the final agent explanation in the main platform is an
integration step for the teammate; this component only dispatches that handoff.
Direct Python callers can import `second_look.service.evaluate_from_snapshot`
from the ZIP's package root and should pass assets, policy and references from
the trusted configuration explicitly.

The existing role needs these S3 reads/writes, CloudWatch logs and permission to
invoke that existing function if used. Logs include stages, verdict, policy/
reference versions and evaluation result hash. Authenticate the platform endpoint
and authorize client/result access upstream; this adapter is not an identity layer.
Concurrent production decision processing needs idempotency/conditional writes;
the current workflow store is for the prototype, not a transaction system.

## Verification and gaps

Local tests cover the rule cases, cash/short rejection, no future data, stale/
missing observations, reference totals/ranges, exact matches, interpolation,
endpoints, deterministic ties, distant portfolios and a perfect allocation match
that remains red for crypto. They cover confirmation, cancellation, immutable
agent results, partial evaluation and mocked S3/Lambda dispatch. HTTP smoke tests
check the same workflow and optional JSON Schema validation. Browser visual QA
could not run because the in-app browser connection failed in this session.
**No live AWS deployment or Bedrock/agent execution was verified.** No existing
agent implementation was supplied; an explanation remains pending until connected.

Real data: starter retrieval 2026-11-13, history through 2026-11-12; clients and
trades are fictional. Limitations include assumed publication availability,
asynchronous equity/crypto/FX closes, carried-return smoothing, survivorship bias,
back-adjusted vendor history, revised macro data, the CNQ 1.021 split caveat and
ZST's retained zero-volume observation. Macro/news/stress-test agents are outside
this data/rule integration. Source terms remain in `data/raw/STARTER_README.md`
and `sources_metadata.csv`. No prediction or financial advice is produced.
