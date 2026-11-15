# Persistent Second Look conversation

The existing red-only invitation now opens a multi-turn coordinator conversation.
Python owns risk results, account state and execution state. Strands 1.65 runs the
configured coordinator model for prose; no training, model replacement or new AWS
resources are needed. No changes are committed, pushed or deployed by this work.

## Complete demo

From `datathon2026`, use the locked environment:

```sh
uv sync --locked
export AWS_PROFILE=second-look  # the locally configured profile; omit in Lambda
export AWS_REGION=us-east-1
uv run --directory second_look python -m second_look demo
uv run --directory second_look python -m second_look serve --port 8000
```

Use another port if 8000 is occupied. Open the printed localhost URL, choose
**Balanced demo**, then **Buy TSLA · CAD 12,000**, check the trade and select
**Let's talk**. Enter a reason, then ask follow-ups. Enter sends; Shift+Enter
adds a newline. Closing the ticket does not execute a trade. Revise, cancel and
proceed after explicit risk acknowledgement remain available during a reply.

For the complete AI conversation, this process needs the existing AWS credentials
and access to the models and tools Lambda configured in root `config.py`:
`AWS_REGION`, `MODEL_ID`, `MODEL_ID_FAST`, `LAMBDA_NAME`, `DATA_BUCKET`.
The root environment must have Strands and boto3 installed. The standalone
numerical package remains usable without them. `CONVERSATION_KIT_DIR` optionally
locates the same kit elsewhere; the kit's `agent_settings.json` selects its one
canonical system prompt and knowledge file. Application settings are never sent
as Bedrock provider parameters. No temperature or output-token override is added.

Local conversation/session state is under `second_look/data/runtime/`. Local
tools may be selected with `SECOND_LOOK_LOCAL=1 DATA_DIR=/absolute/path/to/data`
only when that directory contains the required original CSVs; **Bedrock inference
still requires AWS access**. Without credentials, send a message to see an honest
saved/unavailable state and retry control. No offline model is substituted.

A contribution example: explain that you expect CAD 50,000 next week, say how
certain it is, and ask to leave it in cash. Supply the missing details when asked.
Then specify any purchases you actually want compared, with CAD amounts. The
tool does not invent purchases. The UI separates **Current trade assessment**
from **Hypothetical assessment after contribution**, with residual breaches and
full-compliance status. The kit's older fixture colors are not promises about
today's policy. Green may indicate improvement while limits remain breached.

The explicit **Confirm simulated cash arrival and recheck** control confirms cash
only at this demo's fixed historical snapshot, creates a new assessment/input
version, and preserves the original. It executes no purchases. This is a fictional
state update, not confirmation of a bank transfer. Running `demo` regenerates
baseline examples without deleting active simulated account updates.

## Flow and persistence

- The first reason runs the same three parallel specialists and initial coordinator.
  Their tools are bound to the actual pinned profile, portfolio, trade and date.
  Historical portfolio risk receives those exact post-trade weights.
- The conversational coordinator restores saved messages, attributed contribution
  details, evidence and reports on every request. It does not rerun the specialists
  on ordinary follow-ups. A dated evidence tool handles new news claims, and
  deterministic tools handle candidate trades, explicit market shocks and cash plans.
- All original assessment inputs are pinned under `assessment_inputs/{trade}.json`.
  Sessions live at `conversations/{client}/{trade}/{input_version}.json` using the
  existing store. In the full AWS API these keys are below
  `s3://<DATA_BUCKET>/second-look/`. There is no process-memory conversation store.
  Explicit simulated cash state is stored under `accounts/{client}.json`.
- S3 conditional writes (ETag / If-Match / If-None-Match) serialize accepted turns;
  local files use atomic writes and file locks. Message IDs deduplicate retries.
  A bounded lease allows retry after an interrupted Lambda. Saved reports and tool
  evidence survive reply failures. Edited trades use fresh IDs; the UI rejects late
  responses for older edits. Late replies cannot reverse cancellation/acknowledgement.
- The source prompt is `second_look_conversation_kit/system_prompt.txt`. The model
  selects zero to two enabled cards semantically using the same configured Claude
  model, and their actual qualified content is supplied before the conversational
  request. A bounded reference tool also permits targeted reads; there is no
  keyword classification or psychological state stored on the profile. Source
  paraphrases remain distinct from Second Look's product adaptations. Development
  dialogues, numerical fixtures, evaluation cases and the source PDF are not loaded
  into live sessions or bundled into the full API deployment.
- Models return text plus evidence IDs. Literal numerical figures are rejected;
  validated placeholders resolve figures/dates from stored evidence. Authoritative
  current/scenario verdicts and execution state come from Python. This validates
  references and numerical formatting. A rejected reply gets up to two live Claude
  correction attempts and is never displayed before validation. Correction starts
  from an independent copy of public history, without replaying SDK thinking/tool
  blocks. News is filtered to items available by the decision date.
- Optional news, scenario or specialist failures are unavailable evidence rather
  than a reason to suppress an otherwise valid Claude reply. Missing model access
  still gives an honest saved/error/retry state. Structured diagnostic logs carry
  application and AWS request IDs, component/error codes, model/region, credential
  provider, profile/prompt hashes and history counts; they do not log credentials.

## Diagnosing a failed message

From the repository root, independently check authentication, the configured
Bedrock model, Strands, and the existing tools Lambda:

```sh
AWS_PROFILE=second-look uv run python scripts/check_chat_runtime.py --tools
```

This performs small live requests. It reports sanitized codes/request IDs and
Lambda role names rather than credential values. Local credentials and a Lambda
execution role are separate: the verified local runtime used the `second-look`
shared profile; the tools function uses `second-look-lambda-role`, and the hosted
API is configured with `second-look-api-role`. No local `AWS_PROFILE` should be
copied into the deployed Lambda environment.

The reproduced failure was after successful model inference: the generated reply
included a literal `30%`, and some numerical references contained an extra `/data`
wrapper. The old validator raised `ValueError: The AI returned a figure without a
tool reference; please retry`, which the endpoint hid behind a generic unavailable
message. Live testing also caught non-JSON tool-turn output. The validator now
normalizes the harmless wrapper and requests bounded model corrections for genuine
formatting errors while retaining numerical grounding. The UI shows diagnostic
references when a reply still cannot be completed. Context audits prove which
profile, prompt, reference content, reports and prior messages reached Claude.

The full AWS packaging script includes the new runtime modules and just the three
production kit files. Deploy through the existing `scripts/deploy_api.py` only
when ready. The standalone rule-Lambda handoff remains a numerical adapter; use
the full API Lambda for hosted chat. This demo's selected client is a simulation
scope, not authentication. A real platform must authorize client/session access
upstream; the existing public demo Function URL has not been converted into an
authenticated brokerage service.

## HTTP contract

`POST /api/evaluate` keeps the existing five-field trade body and adds the
backend-generated `assessment_input_version` to its record.

`GET /api/simulations/{id}/conversation?client_id=...&input_version=...` restores
the corresponding record/history. Send to the same path with:

```json
{"client_id":"demo_balanced","input_version":"<from evaluation>","message_id":"<unique UUID>","expected_revision":0,"message":"What does this flag refer to?"}
```

Responses contain `record`, `conversation`, referenced `evidence`,
`current_assessment_id`, `current_verdict`, `scenario_verdicts` and
`trade_execution_state`. Retry a failed/network-interrupted message with its
**same ID and text**. Refresh after a 409 conflict. New messages require the latest
revision. Failed messages stay visible and no fabricated assistant reply is added.
The demo retains exact wording for up to 40 turns rather than silently summarizing
away identifiers or financial evidence; at the limit it asks for a fresh trade check.

`POST /api/simulations/{id}/confirm-contribution` is an explicit platform control,
absent from model tools. It requires `client_id`, `input_version`, `scenario_id`,
a unique `event_id` and literal `confirmed:true`. Confirmation is deduplicated;
the old result cannot subsequently proceed as the current assessment.

## Validation

```sh
uv run python second_look_conversation_kit/validate_content.py
uv run python -m pytest tests second_look/tests
uv run python scripts/evaluate_conversation.py --live --all
```

The content validator checks content/provenance/arithmetic. Integration tests use
real Python rules, file/S3 contracts, and scripted model responses, including a
real installed-Strands tool loop. The 24 hold-out messages also have contract
coverage; this does not score their conversational quality. Native WebKit checks
cover desktop/mobile controls and unavailable AI behavior.
For a successful scripted UI conversation, run
`../.venv/bin/python tests/serve_scripted_chat.py 8018` from `second_look/`, then
run the native WebKit harness against that URL with `--chat`. Its replies are
explicitly labelled scripted test output; it is never an offline AI fallback.

The optional evaluator calls the **actual configured Bedrock coordinator** with
fictional sessions, cached synthetic specialist context and local tool boundaries.
Without `--all` it runs four small cases. It writes responses/tool traces alongside
the kit rubric for human review; it does not self-certify tone or hard-failure
avoidance. The full 24-case live behavioral suite has not been scored; the suite's
contract/content tests are distinct from the real UI tests below.

Live UI verification on 2026-11-15 used **Claude Sonnet 5.5**, inference profile
`global.anthropic.claude-sonnet-5-5`, in `us-east-1`, unchanged from `config.py`.
Native WebKit drove the ordinary frontend and backend with the actual fictional
Robert profile and a CAD 30,000 Bitcoin proposal. It sent the three requested
messages, reloaded/restored history, and verified the contribution remained
user-stated until its missing intended-use/certainty fields were supplied. A
separate backend process restored the same session and deliberately called a
nonexistent tools Lambda: AWS returned `ResourceNotFoundException`; live Claude
explained the evidence gap and continued. A subsequent complete plan produced a
Python-calculated hypothetical before funding and preserved the original result.

The actual replies, screenshots, context hashes, model request IDs and scenario
checks are under `dist/live-chat/` (generated, ignored by Git). Reproduce the UI
sequence against a running live backend using the native harness's `--live` flag;
`--resume-live <transcript.json>` tests a continued conversation, and
`--complete-plan <transcript.json>` supplies the missing scenario details. Those
modes call real Bedrock, unlike the explicitly scripted `--chat` mode.

No AWS API deployment was performed. The local UI-to-Bedrock path was verified;
redeploy the full API through the existing deployment workflow to update its
hosted copy. No model substitution, prerecorded replies or account execution is
used by the working conversation.

The broad universe scan permits the original `alphaforge_starter/build_dataset.py`
only when it is byte-identical to the already allowed organizer builder. The
conversation code neither changes nor executes either builder. Six existing tools
acceptance cases require root `data/*.csv` files that are absent in this workspace.
