# Second Look

Simulate a cash-funded trade against an investor's **stored** profile and holdings.
The profile comes from a placement questionnaire at onboarding (see
[Placing a client](#placing-a-client)) and is never re-scored by a trade. Crypto is an
**alternative asset**, with a separate crypto cap. The allocation spectrum explains a portfolio's position; deterministic
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

## Placing a client

`second_look/questionnaire.py` turns five answers into one of the five NBF profiles.
Clients are classified by risk tolerance, never by past performance or holdings.

| Question | Answer | Score |
| --- | --- | --- |
| Horizon: years before a significant withdrawal | < 3 / 3-5 / 5-10 / 10-15 / 15+ | 1-5 |
| Capacity: effect of a 20% loss on your plans | severe / significant / moderate / minor / none | 1-5 |
| Willingness: attitude to short-term losses | avoid / small / moderate / large / any | 1-5 |
| Objective | preserve / income / balanced / growth / max_growth | 1-5 |
| Experience | none / basic / intermediate / advanced / professional | 1-5 |

Total 5-25 maps to a band: 5-8 Income, 9-12 Conservative, 13-16 Balanced, 17-20 Growth,
21-25 Maximum growth. Two caps then apply and can only lower the placement: the horizon
cap (< 3 years Income, 3-5 Conservative, 5-10 Balanced, 10-15 Growth) and the capacity cap
(a "severe" answer caps at Income, "significant" at Conservative). The bands and caps are
team assumptions; only the profile names and asset-class ranges are NBF.

`place_investor(answers)` returns the profile with scores, caps and a one-sentence
explanation; `build_client(...)` writes a stored client in this component's format, with a
`placement` block so the dashboard or the Profile agent can explain why the client is
where they are. The three personas' fictional answers reproduce their stored profiles:

```sh
python scripts/place_client.py --persona all      # Robert Conservative, Mateo Balanced, Amira Growth
python scripts/place_client.py --questions        # print the questionnaire
python scripts/place_client.py answers.json --write   # place a new client, write examples/clients/<id>.json
```

## Bias test

`tests/test_bias.py` is the evidence that Second Look does not "always recommend bonds":

- A Growth client parked in bonds (60% fixed income) is **red** when buying more bonds
  (fixed-income maximum worsened) and **green** when buying equities (shortfall reduced).
- Selling equities below a Growth client's 40% minimum is red, exactly like buying above
  a Conservative client's 45% maximum: equal gap, equal severity, same effect label.
- The same bond purchase from the same bond-heavy portfolio is red for Growth and
  Maximum growth, yellow for Balanced and clean for Income and Conservative.
- The same NVDA purchase is red under four profiles and not red under Maximum growth.
- NVDA and JNJ, or QQQ and ESGV, produce identical checks at identical weights: the
  verdict reads weights, never return history.
- A Conservative client already holding 70% equities is still judged as Conservative,
  although the spectrum places the portfolio near Growth.
- `place_investor` has no holdings argument; the lowest and highest answer sets reach
  Income and Maximum growth.

Two properties worth knowing for the pitch, both verified in the tests: a single
cash-funded purchase from a compliant portfolio can move an asset class by at most the
cash cap (20 points for Income/Conservative/Balanced), so it ends at worst 5 points over
and yellow; allocation reds come from sales, pre-existing breaches, or the hard
single-stock and crypto caps (Robert/NVDA is red on the single-stock cap). And because
the HHI counts a whole ETF position as one holding, a portfolio with one large bond ETF
(for example the NBF Income reference itself, 68.5% XBB) is treated as concentrated;
the bias tests use 8+ positions so the allocation behaviour is visible on its own.

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
goal coverage, ESG preferences and liquid cash reserves. Funds are exempt
from single-stock caps; HHI sees whole fund positions and combines cash with ZST.
A reduction cannot cancel a new/worsened breach. New/worsened stock, crypto or
explicit ESG exclusion breaches are red; allocation breaches use the existing
5-point buffer. Existing HHI/watch and strict-improvement precedence is preserved.
All non-allocation limits and color precedence remain labeled team assumptions,
each with a source and rationale in `policy.json` (`threshold_sources`).

### Profiles and limits

- Asset-class ranges come from NBF Fall 2026. Single-stock, crypto, HHI and sector
  caps are team assumptions per profile.
- Goals (`goals.py`) check each client's goals (emergency fund, one-off spending,
  recurring withdrawals such as retirement income) against the assets that would
  still be there in time. An asset counts only if it recovered from its worst past
  fall faster than the time left, and only at its value after such a fall. The fall
  and recovery come from `assets.json` `horizon`, written by
  `scripts/add_asset_horizons.py` from the provided prices. A trade that lowers the
  coverage of an essential goal due within 24 months is red; other drops are
  yellow. Planning horizon 5 years; 2% inflation for indexed goals.
- ESG (`esg.py`): buying an excluded ticker, or a single stock in an excluded
  sector, is red. Buying a fund above the client's look-through tolerance for
  excluded sectors, or pushing the whole portfolio above it, is yellow. Selling
  is never flagged.
- Personas (all fictional): Robert, 63, health care worker retiring 2027-07-01,
  CAD 3,000,000, needs CAD 70,000 a year (indexed) for 30 years. Mateo, 41, no
  fossil fuels, home down payment in 2029. Amira, 26, master's tuition in 2028.

Horizon, objectives and loss tolerance stay agent context. A rule that cannot be
evaluated (no sector breakdown, no price history, an unverified ESG wish) returns
an unresolved item. `unresolved_handling` makes it a yellow note by default, and a
manual review only for stored constraints the engine cannot read.
No sector exposures, ESG certification or numerical horizon rules are invented.

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
