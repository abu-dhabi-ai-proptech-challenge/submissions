# The AI Judge — How It Works

The Abu Dhabi AI PropTech Challenge is about building the intelligence layer for cities — so we built one for the event itself. Every submission is pre-screened by an **AI Judge** powered by Claude. This page explains exactly what it does, how it scores, and what it will never do. It was built by the organizers at [eVoost AI](https://evoost.ai) — the event runs on its own intelligence layer ([the full story](https://github.com/abu-dhabi-ai-proptech-challenge/starter-kit/blob/main/docs/how-this-event-runs-on-ai.md)). The full source is public: [`.github/scripts/ai_judge.py`](../.github/scripts/ai_judge.py).

## The one rule that matters

> **The AI Judge advises. Human judges decide.**
> AI scores are never announced, never published, and never override a human judgment. They exist to help judges spend their limited review time well — and to give every team structured feedback.

## What it does

When organizers trigger it at the deadline freeze, the AI Judge:

1. **Reads your submission** — every field of your Project Submission Issue.
2. **Inspects your repo** — README, file tree, languages, and commit timestamps (public evidence only; it never executes your code).
3. **Scores against the public rubric** — the same five criteria human judges use, 0–20 each, with a written rationale per criterion citing concrete evidence.
4. **Produces a report for judges** — ranked per track and overall, with per-team strengths and a "verify this" list of claims humans should check.
5. **Optionally sends you feedback** — a comment on your Issue with strengths and things worth tightening before the deadline. Never scores.

## How it scores

The five public criteria, with one honest adaptation:

| Human criterion | AI Judge scores | Why |
|---|---|---|
| Problem & relevance | Same | Fully assessable from the submission |
| Technical execution | Same, from repo evidence | Code structure, commit activity, run path |
| Use of AI | Same | Is AI doing real work or decorating? |
| Demo quality | **Demo readiness** | The AI can't try your tool for you — it scores whether your demo *can* land: working link, instructions, recorded video |
| Potential impact | Same | Fully assessable from the submission |

Calibration rules baked into the prompt:

- **Evidence over claims.** Form claims unsupported by the repo get the benefit of the doubt at most once, and are flagged for human verification.
- **Narrow and working beats broad and described.**
- **Pre-event work is flagged**, not silently scored — old commit histories and suspiciously large codebases go on the "verify" list.
- **Consistency:** same evidence, same score, for every team.

## What's under the hood (for the curious)

- **Model:** Claude Opus 4.8 via the Anthropic API.
- **Structured outputs:** scores arrive as schema-validated JSON — the model literally cannot return a malformed verdict.
- **Prompt caching:** the rubric is cached, so each submission after the first is scored at ~10% of the input cost. A full event's judging run costs a few dollars.
- **Bounded evidence:** ~12k characters per submission, so every team gets the same evidence budget.
- **Confidence flag:** every verdict self-reports `high/medium/low` confidence; thin evidence means low confidence, which tells judges to look closer rather than trust the number.

## What it will never do

- ❌ Decide winners, or any placement
- ❌ Publish or announce scores
- ❌ Execute your code or open your demo with credentials
- ❌ Penalize you for a private repo it can't read (it flags "no evidence" for humans instead)
- ❌ Replace the judges' own review of your submission

## For organizers

Everything runs automatically once the `ANTHROPIC_API_KEY` repo secret is set:

- **Live, per submission** (`ai-judge-live.yml`) — every new submission is evaluated within minutes of arriving: the team gets its feedback comment, organizers get the scored card in the private Discord ops channel.
- **Full report** (`ai-judge.yml`) — a cron watches the `DEADLINE_UTC` repo variable; once the deadline passes, the complete ranked report is generated exactly once and delivered to the ops channel. It can also be run manually from the Actions tab anytime (e.g. a mid-day preview).
- Scores are never published — reports go to the private ops channel only, not to public artifacts.

Local run for debugging: `ANTHROPIC_API_KEY=... python3 .github/scripts/ai_judge.py [--issue N] [--dry-run]` from the repo root.

---

*Questions or concerns about AI-assisted judging? Open a [Question issue](../../issues/new?template=question.yml) — we'd genuinely like to hear them.*
