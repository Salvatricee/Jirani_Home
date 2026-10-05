# Jirani Home: Support Draft Assistant (classroom prototype)

An AI-assisted **draft-reply tool** for support agents. Fictional data only. No real customers, payments or order changes.

**What it does:** for each ticket it decides *what must happen* using business rules, asks a model to *write the reply*, checks the reply for unsafe or unsupported claims, and lets a human inspect / edit / approve / reject. Approved or rejected outcomes are sent to an n8n workflow that creates a hand-off task record. Everything is logged in SQLite.

## Why it is built this way (use this for Task 6 and your demo)

| Decision | Reason (evidence from the case) |
|---|---|
| Rules engine decides route, policy, day-count, order match, safety | E04: 6 of 30 drafts used an outdated policy. Calculations and source selection must be *deterministic and testable*, not left to a model. |
| Model only writes wording | Policy lookup (6 min) and drafting (4 min) are 10 of 16 active minutes (E02). Wording is what a model does well. |
| Archived (A01) and unapproved (U01) texts are never sent to the model | Model cannot quote what it never sees. Tests D02 (old 30-day page). |
| Validator re-runs on the reviewer's edited text | A human edit can also introduce an unsafe promise. Approval is blocked until fixed. |
| Customer text is wrapped as data | D07 tries to make the system approve a refund. Customer text is evidence, not permission (P05). |
| Human approves every draft; n8n creates records only | P05: AI output is a draft; refunds/replacements need an authorised human and are out of scope. |
| Template fallback, always labelled | Model outage must be visible, not silent (P05 logging). |

## Quick start (Linux/macOS, no sudo needed)

```bash
cd jirani-prototype
bash jirani.sh setup        # creates the environment and installs packages (1-3 min)
bash jirani.sh key          # optional: paste your Gemini key (hidden, saved to .env)
bash jirani.sh all          # tests + evaluation + starts the app + a MOCK n8n
```

Every command (`bash jirani.sh help` shows this list):

| Command | What it does |
|---|---|
| `all` | setup + tests + evaluation + start the app with a mock n8n (stopped when you quit) |
| `setup` | create the environment and install packages. Tries `venv`, then `uv`, then `virtualenv`, so no admin rights are needed |
| `key` | save your Gemini API key privately into `.env` |
| `run` | start the app at http://localhost:8501 |
| `test` | run the 55 automated tests |
| `eval [auto\|llm\|template]` | evaluate the 8 dev tickets, writing `evaluation-record-dev.csv` and `dev-drafts.txt` |
| `mock [--fail]` | start a MOCK n8n webhook (`--fail` makes it always error) |
| `n8n` | start real n8n (needs Node.js) |
| `reset` | delete all saved records (asks first) |

Without a key the app runs in **TEMPLATE MODE** (the sidebar says so). That is *not* AI output. For the required
"task runs through a model" evidence, save a key first. Get one free at https://aistudio.google.com/app/apikey
(billing may be required in some regions). The model is `JIRANI_MODEL` (default `gemini-3.8-flash`); check the current name at
https://ai.google.dev/gemini-api/docs/models. If the main model is overloaded (503/429), times out, or isn't found, the app tries backup models in order (`JIRANI_FALLBACK_MODELS`, default `gemini-3.5-flash-lite,gemini-2.5-flash`) and records the model that actually wrote the draft. A bad key is not retried. If every model fails, the draft is labelled `template-fallback`, and
`eval` scores it `NOT-MODEL-OUTPUT` so it can never pass as model evidence.

## n8n workflow

1. Start n8n (`npx n8n` or Docker).
2. Workflows → Import from file → `n8n/jirani-review-outcome-workflow.json`.
3. Open the workflow, click **Execute workflow** (test URL) or toggle **Active** (production URL).
4. Set `N8N_WEBHOOK_URL` if it differs from the default. Note: n8n's *test* URL contains `/webhook-test/`, the production URL `/webhook/`.
5. Approve a draft in the app. The Review tab shows the workflow result, and the Records tab stores it.

No n8n handy? `bash jirani.sh mock` serves the same contract (add `--fail` to test failures). It is **not n8n**, so say so in your evaluation. The sidebar "Simulate n8n" box records outcomes as `SIMULATED`.

## Testing and evaluation

```bash
bash jirani.sh test                         # 55 automated tests (rules, validator, workflow, failures)
bash jirani.sh eval template                # offline check of all 8 dev tickets
bash jirani.sh eval llm                     # real model run (needs a key)
```
`run_eval.py` writes the case's `evaluation-record.csv` format. The `reviewer` column says `auto-eval`. **You still need to read the drafts and confirm each result yourself.** The `cost` column holds token counts; add prices yourself.

### Before the six final tickets are released
1. Commit and tag: `git tag v0.1.0` (and keep `APP_VERSION` in `config.py` matching). Do not change rules/prompts afterwards.
2. Ask the trainer for the six tickets **and** expected outcomes. Write the expected outcomes in a CSV like `data/dev_expected.csv`.
3. `python run_eval.py --tickets final.csv --expected final_expected.csv --out evaluation-record-final.csv`

## Requirement map

| Prototype requirement | Where |
|---|---|
| AI functionality on fictional data | `drafting.py` (model call), `rules.py` (context) |
| Inspect / edit / accept / reject | Review tab in `app.py` |
| Results and decisions persist after reopening | `db.py` (SQLite `jirani.db`), Records tab |
| Automation executes, outcome recorded | `workflow.py` + `n8n/…json` → `workflow_events` table |
| Test normal, missing info, incorrect output, workflow failure | `test_app_logic.py`, `run_eval.py`, sidebar "simulate model outage" |
| Boundaries: test systems only, label simulations | Banner, sidebar labels, `SIMULATED` / template labels |

## Known limitations (put these in your evaluation)
- The validator is **pattern-based**. It catches the failure modes listed in `validator.py`, not every possible bad sentence. Human review is still the real control.
- The 8 dev tickets are only for development; they say nothing about business-wide accuracy (the case says the same about the 30-ticket sample).
- Rules cover the five ticket topics; `account` queries route to `OUT_OF_SCOPE` with a holding reply.
- Message classification for safety/injection uses keyword patterns (see `rules.py`) and will miss unusual wording.
- Elapsed time in `run_eval.py` measures drafting only, not the review step, so it is not comparable to the 16-minute human baseline until you time a real review session.
- The n8n export and live model calls were not run in the environment where this was written (see chat notes). Test both on your machine.
