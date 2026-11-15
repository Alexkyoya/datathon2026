# Second Look conversation content kit

Use this kit to configure the existing conversational coordinator. It translates
the supplied **Behavioral Patterns and Pitfalls of U.S. Investors** (Library of
Congress, August 2010) into source-linked knowledge, application instructions,
examples and evaluation cases. It does not train a model, deploy an agent or
replace the project's financial rules.

The central instruction is: help the investor understand the decision while
remaining open to a valid explanation. A red flag is a portfolio-rule result,
not a psychological diagnosis. A good explanation does not erase a numerical
breach, and a deliberate choice can still breach a limit.

## What to load

| File | Use |
|---|---|
| `system_prompt.txt` | Permanent behavioral instructions for the chat role |
| `behavioral_knowledge.json` | Canonical reference cards; select only relevant cards |
| `agent_settings.json` | Proposed application settings, context fields and tool boundaries |
| `conversation_examples.json` | Twelve fictional dialogue examples for prompt development |
| `evaluation_cases.json` | Twenty-four hold-out cases and a review rubric |
| `numeric_fixtures.json` | Computed fictional tool outputs for tests only |
| `source_provenance.json` | Report metadata, page mapping, caveats and provenance |
| `source/locinvestorbehaviorreport.pdf` | Unmodified supplied source document |
| `CODEX_HANDOFF.txt` | Instructions for integrating the content into your repository |
| `validate_content.py` | Local content, reference and arithmetic checks |
| `VALIDATION.json` | Results of those checks, not an LLM performance report |

The `.json` files are content/configuration specifications, not ready-to-send
Bedrock API payloads. Reuse the installed Strands SDK and existing coordinator
configuration. Do not hardcode a model from this document, migrate the SDK or
forward these application settings as unsupported provider parameters.

## Separate knowledge from product policy

Each reference card has a `report_paraphrase` and explicit printed/PDF page
references. These are summaries of the supplied report, not independent reviews
of the studies it cites. The questions, alternative explanations and limits on
inference are **Second Look adaptations**, clearly marked as such.

The autonomy policy, friendly tone, cash-contribution workflow and evaluation
rubric are also project design choices. They are not interventions proven by
this report. E01 is explicitly a project extension with no report source claim.
The guide is not a validated bias detector.

The report is a 2010 review, not a current-market forecast or regulatory rulebook.
Its cover states that the author's findings need not represent SEC views.
Do not claim that the SEC endorses this agent or its thresholds.

## Reference coverage

Printed page numbers are shown below. Add three to obtain the PDF viewer page.

| Card | Topic | Printed pages | Conversational use |
|---|---|---|---|
| C01 | Reference dependence and loss aversion | 2-3 | Separate the purchase price from today's decision |
| C02 | Overconfidence | 3, 8 | Explore evidence and conditions that would change the thesis |
| C03 | Short-term loss focus | 8-9 | Ask whether goals or cash needs have changed |
| C04 | Frequent trading | 7-8 | Understand what the trade is intended to accomplish |
| C05 | Disposition effect | 8-9 | Distinguish gain/loss framing from rebalancing or other reasons |
| C06 | Past returns and costs | 9 | Check product fit and actual costs without inventing fees |
| C07 | Familiarity and employer exposure | 10-11 | Explore purpose, restrictions and account scope |
| C08 | Collective excitement and fear | 3-4, 11-12 | Explore urgency without asserting FOMO |
| C09 | Momentum | 12 | Distinguish a documented strategy from unsupported extrapolation |
| C10 | Equal weighting | 12-13 | Check exposures rather than judging equal weights |
| C11 | Headlines and attention | 13 | Verify the specific claim and its date |
| C12 | Concentration | 13-14 | Recognize intentional exposure while checking limits |
| C13 | Understanding and trust | 5-6 | Answer plainly without stereotyping the investor |
| C14 | Immediate needs, future goals and defaults | 4-5, 7 | Clarify a real change in circumstances |
| C15 | Growth investing | 15-16 | Separate style, thesis and concentration |
| C16 | Framing and annuities | 14-15 | Disabled by default: outside starter product coverage |
| E01 | Planned contributions | Project extension | Simulate a plan without pretending it is funded |

Do not flatten the report into rules such as 'all active trading is bad,' 'growth
is irrational,' 'every investor needs 300 stocks' or 'value will outperform.'
Printed page 14 explicitly describes contrary evidence on concentration, and
page 15 explicitly avoids declaring growth investing a mistake. Demographic
comparisons in the review are not used to classify individual clients.

## Runtime flow

1. A red result permits the existing invitation. On the first substantive client
   reason, use the project's specialist/coordinator workflow as already designed.
2. Start or restore the chat session with the actual profile, holdings, proposed
   trade, original assessment, specialist reports and conversation history.
3. Supply the system prompt and zero to two relevant knowledge cards. The model
   should answer the actual question; it need not mention a behavioral concept.
4. Use a targeted tool only when a calculation, changed input or evidence check
   is necessary. Reuse valid results rather than rerunning every specialist.
5. Persist factual conversation state and tool references. Keep user statements
   separate from verified account state. Never store a guessed bias as a client trait.
6. Render natural text while backend code supplies authoritative verdicts,
   scenario results and execution state.

For a hackathon, appending this small guide as clearly delimited reference content
is sufficient to start. Select relevant cards in code or through a bounded
retrieval tool as needed; a new vector database is not required by this kit.
Only load a few relevant examples during development. Keep evaluation cases and
numeric fixtures out of live context. Do not paste the full historical PDF into
every turn or treat retrieved text as system instructions.

## The future-cash example

The numeric fixtures use fictional CAD-valued holdings and the earlier local
Second Look starter rule engine. They are actual outputs from a local calculation,
not live market valuations, the report's findings or your deployed backend.

The example begins with a proposed stock purchase. The client then supplies a
possible contribution and a plan for using it. All future prices and FX are held
fixed, and fees and taxes are excluded.

| Test context | Stock weight | Cash weight | Fixed income | All checked limits met? |
|---|---:|---:|---:|---|
| Current proposed trade | 15% | 10% | 30% | No |
| Hypothetical CAD 50,000 contribution left in cash | 10% | 40% | 20% | No |
| Same contribution plus client-specified CAD 15,000 purchases of each of XBB.TO, AGG and TLT | 10% | 10% | 50% | Yes, under the fixture assumptions |

These are illustrative client-specified actions, not recommended allocations.
Numerical source: `numeric_fixtures.json`, IDs T_CURRENT, T_DEPOSIT_CASH and
T_DEPOSIT_ALLOCATED. The original trade remains red for its original inputs.

**A color is not always a full-compliance statement.** The starter engine allows
green for a trade that reduces an existing deviation. Consequently the cash-only
fixture has a raw green trade verdict despite residual portfolio breaches. The
fixture preserves that verdict and separately computes `portfolio_within_limits`
from all residual gaps. The agent must explain the distinction, not change the
engine result or infer compliance from color. Confirm your current backend's
semantics before integration.

This example teaches the agent to take a valid explanation seriously without
assuming a deposit fixes every limit, inventing a purchase plan, or treating a
future event as current money.

## Validation and evaluation

From this directory, run:

```bash
python validate_content.py
```

This checks JSON parsing, card/fixture references, page mapping, source integrity
and the internal arithmetic of the numeric fixtures. It does not call AWS,
validate actual Strands configuration, execute the full current project, or test
the LLM's conversational behavior.

Before a demo, run the hold-out cases through the actual agent with fake sessions.
Review the answers and actual tool calls against the rubric. Include several
runs for difficult cases. Separately test session isolation, message deduplication,
context restoration and edited-trade invalidation.

Success is grounded information, responsiveness, useful adaptation and respect
for the client's choice. Cancellation rate, agreement with the assistant and
the number of bias labels are not success criteria.
