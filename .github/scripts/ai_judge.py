#!/usr/bin/env python3
"""AI Judge — advisory pre-screening for Abu Dhabi AI PropTech Challenge submissions.

For every Issue labeled `submission`, this script:
  1. parses the submission form fields,
  2. gathers evidence from the linked GitHub repo (README, file tree, languages,
     commit activity),
  3. asks Claude to score it against the five public judging criteria with a
     structured-output rubric,
  4. writes `ai-judge-report.md`: ranked tables per track + overall, plus a
     scored card per submission.

It is an ADVISOR, not a judge: it cannot watch demos, so "Demo quality" is
scored as *demo readiness* (working links, run instructions, recorded fallback).
Human judges make every decision. Methodology: docs/ai-judge.md

Optimizations:
  - prompt caching: the rubric system prompt is cached, so every submission
    after the first reads it at ~10% input price,
  - structured outputs: scores arrive as schema-validated JSON, no parsing,
  - evidence is capped (~12k chars/submission) to bound cost and latency.

Usage:
  ANTHROPIC_API_KEY=... python3 ai_judge.py             # full run
  python3 ai_judge.py --dry-run                         # no API calls, show dossiers
  python3 ai_judge.py --include-late --post-feedback    # also comment feedback on Issues
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = os.environ.get("GITHUB_REPOSITORY", "abu-dhabi-ai-proptech-challenge/submissions")
MODEL = os.environ.get("AI_JUDGE_MODEL", "claude-opus-4-8")
TRACKS = ["Land Intelligence", "Investment Intelligence", "Future Communities", "Decision Intelligence"]
EVIDENCE_CAP = 12_000  # chars of repo evidence per submission

FIELDS = ["Team name", "Track", "Project summary", "Problem", "Solution", "Tech stack",
          "GitHub repo link", "Demo link", "Slides link",
          "What was built during the hackathon", "Setup instructions",
          "How did you use Cursor?"]

CRITERIA = ["problem_relevance", "technical_execution", "use_of_ai", "demo_readiness", "potential_impact"]

RUBRIC = """You are the AI pre-screening judge for the Abu Dhabi AI PropTech Challenge \
("Building the Intelligence Layer for Land, Investment and Communities"), a one-day AI \
hackathon. You score project submissions to help human judges prioritize their limited \
review time. You advise; humans decide.

Score each criterion 0-20 using the event's public rubric:

1. problem_relevance — Is this a real, sharply framed problem within the chosen track?
   17-20 specific & well-motivated; 10-16 real but broad; 0-9 vague or solution-in-search-of-problem.
2. technical_execution — Does the evidence suggest a working prototype? Judge from the
   repo (structure, code presence, commits during the event, run instructions).
   17-20 coherent codebase with a credible end-to-end path; 10-16 core present, rough; 0-9 mostly empty or boilerplate.
3. use_of_ai — Is AI doing real work (reasoning, matching, generating, deciding) or decorating?
   17-20 AI is central and better than a rules baseline; 10-16 adds value but replaceable; 0-9 cosmetic or absent.
4. demo_readiness — You CANNOT watch the demo. Score readiness instead: working demo link,
   clear setup instructions, recorded fallback, focused scope.
   17-20 deployed/recorded + reproducible; 10-16 demoable with friction; 0-9 no credible demo path.
5. potential_impact — Could this matter beyond the event?
   17-20 credible users + extensible beyond sample data; 10-16 plausible with open questions; 0-9 unclear who'd use it.

Calibration rules:
- Score ONLY from the evidence given. Claims in the form without support in the repo
  evidence earn the benefit of the doubt at most once — note it in `concerns`.
- A narrow working prototype beats a broad described one.
- Penalize signs the work predates the event (huge codebase, old commit history) in
  technical_execution and flag it in `concerns` — verification is for humans.
- Be consistent across submissions: same evidence, same score.
- Write rationales a judge can verify in 30 seconds, citing concrete evidence."""

SCHEMA = {
    "type": "object",
    "properties": {
        **{c: {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "description": "0-20 per the rubric"},
                "rationale": {"type": "string", "description": "1-2 sentences citing concrete evidence"},
            },
            "required": ["score", "rationale"], "additionalProperties": False,
        } for c in CRITERIA},
        "strengths": {"type": "array", "items": {"type": "string"}, "description": "top 2-3"},
        "concerns": {"type": "array", "items": {"type": "string"}, "description": "things human judges should verify"},
        "one_line_verdict": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"],
                       "description": "low when evidence is thin or contradictory"},
    },
    "required": CRITERIA + ["strengths", "concerns", "one_line_verdict", "confidence"],
    "additionalProperties": False,
}


def gh(*args: str) -> str:
    return subprocess.check_output(["gh", *args], text=True, stderr=subprocess.DEVNULL)


def parse_fields(body: str) -> dict:
    out = {}
    for f in FIELDS:
        m = re.search(rf"###\s*{re.escape(f)}\s*\n+(.*?)(?=\n###\s|\Z)", body or "", re.DOTALL)
        v = m.group(1).strip() if m else ""
        out[f] = "" if v == "_No response_" else v
    return out


def repo_evidence(repo_url: str) -> str:
    """Pull verifiable signals from the team's repo. Degrades gracefully."""
    m = re.search(r"github\.com/([\w.-]+/[\w.-]+)", repo_url or "")
    if not m:
        return "No GitHub repo link provided or not a github.com URL."
    slug = m.group(1).removesuffix(".git")
    parts = [f"Repo: {slug}"]
    try:
        readme = gh("api", f"repos/{slug}/readme", "-H", "Accept: application/vnd.github.raw")
        parts.append(f"--- README (truncated) ---\n{readme[:6000]}")
    except Exception:
        parts.append("README: not found or repo not accessible.")
    try:
        langs = gh("api", f"repos/{slug}/languages")
        parts.append(f"Languages: {langs.strip()}")
    except Exception:
        pass
    try:
        tree = json.loads(gh("api", f"repos/{slug}/git/trees/HEAD?recursive=1"))
        paths = [t["path"] for t in tree.get("tree", []) if t["type"] == "blob"][:150]
        parts.append("File tree (first 150 files):\n" + "\n".join(paths))
    except Exception:
        parts.append("File tree: unavailable.")
    try:
        commits = json.loads(gh("api", f"repos/{slug}/commits?per_page=30"))
        dates = [c["commit"]["author"]["date"] for c in commits]
        parts.append(f"Last {len(dates)} commit dates: {dates}")
    except Exception:
        pass
    return "\n\n".join(parts)[:EVIDENCE_CAP]


def build_dossier(sub: dict, evidence: str) -> str:
    form = "\n".join(f"{k}: {sub[k] or '(empty)'}" for k in FIELDS)
    return (f"SUBMISSION #{sub['number']} — evaluate against the rubric.\n\n"
            f"=== Submission form ===\n{form}\n\n=== Repo evidence ===\n{evidence}")


def judge(client, dossier: str) -> dict:
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=[{"type": "text", "text": RUBRIC, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": dossier}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )
    text = next(b.text for b in response.content if b.type == "text")
    result = json.loads(text)
    for c in CRITERIA:  # schema can't bound integers — clamp defensively
        result[c]["score"] = max(0, min(20, result[c]["score"]))
    result["_usage"] = {"in": response.usage.input_tokens,
                        "cached": response.usage.cache_read_input_tokens,
                        "out": response.usage.output_tokens}
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="build dossiers, skip Claude")
    ap.add_argument("--issue", type=int, default=None,
                    help="judge a single Issue number (live per-submission mode)")
    ap.add_argument("--include-late", action="store_true")
    ap.add_argument("--post-feedback", action="store_true",
                    help="comment strengths/concerns (never scores) on each Issue")
    ap.add_argument("--out", default="ai-judge-report.md")
    args = ap.parse_args()

    issues = json.loads(gh("issue", "list", "--repo", REPO, "--label", "submission",
                           "--state", "all", "--limit", "500",
                           "--json", "number,title,body,labels,url,createdAt"))
    subs = []
    for issue in issues:
        labels = {l["name"] for l in issue["labels"]}
        if "late" in labels and not args.include_late:
            continue
        subs.append({"number": issue["number"], "url": issue["url"],
                     "late": "late" in labels, **parse_fields(issue["body"])})
    if args.issue is not None:
        subs = [s for s in subs if s["number"] == args.issue]
    if not subs:
        sys.exit("No submissions to judge.")
    print(f"{len(subs)} submissions to evaluate")

    if not args.dry_run:
        import anthropic
        client = anthropic.Anthropic()

    results = []
    for sub in subs:
        evidence = repo_evidence(sub["GitHub repo link"])
        dossier = build_dossier(sub, evidence)
        if args.dry_run:
            print(f"\n#{sub['number']} {sub['Team name']}: dossier {len(dossier)} chars OK")
            continue
        verdict = judge(client, dossier)
        verdict["total"] = sum(verdict[c]["score"] for c in CRITERIA)
        results.append({**sub, "ai": verdict})
        u = verdict["_usage"]
        print(f"#{sub['number']} {sub['Team name']}: {verdict['total']}/100 "
              f"(tokens in={u['in']} cached={u['cached']} out={u['out']})")

    if args.dry_run:
        print("\ndry run complete — no API calls made")
        return

    results.sort(key=lambda r: r["ai"]["total"], reverse=True)
    crit_names = {"problem_relevance": "Problem", "technical_execution": "Execution",
                  "use_of_ai": "Use of AI", "demo_readiness": "Demo readiness",
                  "potential_impact": "Impact"}

    lines = [
        "# AI Judge Report — ADVISORY ONLY",
        "",
        f"_{len(results)} submissions scored by `{MODEL}` against the public rubric "
        "([methodology](docs/ai-judge.md)). The AI cannot watch demos and cannot verify "
        "claims — **human judges make every decision**. Use this to prioritize review time "
        "and as a second opinion, never as a ranking to announce._",
        "",
        "## Overall ranking",
        "",
        "| Rank | Team | Track | " + " | ".join(crit_names.values()) + " | Total | Conf. |",
        "|---|---|---|" + "---|" * (len(CRITERIA) + 2),
    ]
    for i, r in enumerate(results, 1):
        scores = " | ".join(str(r["ai"][c]["score"]) for c in CRITERIA)
        lines.append(f"| {i} | [{r['Team name']}]({r['url']}){' ⏰' if r['late'] else ''} | "
                     f"{r['Track']} | {scores} | **{r['ai']['total']}** | {r['ai']['confidence']} |")

    for track in TRACKS:
        in_track = [r for r in results if r["Track"] == track]
        if not in_track:
            continue
        lines += ["", f"## {track}"]
        for r in in_track:
            ai = r["ai"]
            lines += [
                "", f"### {r['Team name']} — {ai['total']}/100 ([#{r['number']}]({r['url']}))",
                "", f"> {ai['one_line_verdict']}", "",
                *(f"- **{crit_names[c]} ({ai[c]['score']}/20):** {ai[c]['rationale']}" for c in CRITERIA),
                "", "**Strengths:** " + "; ".join(ai["strengths"]),
                "**Verify (humans):** " + ("; ".join(ai["concerns"]) or "—"),
            ]

    Path(args.out).write_text("\n".join(lines) + "\n")
    print(f"\nwrote {args.out}")

    if args.post_feedback:
        for r in results:
            ai = r["ai"]
            body = "\n".join([
                "🤖 **AI feedback on your submission** (advisory — does not affect judging):",
                "",
                "**What stands out:** " + "; ".join(ai["strengths"]),
                "**Worth tightening before demos:** " + ("; ".join(ai["concerns"]) or "looks solid"),
                "",
                "_Generated by the [AI Judge](docs/ai-judge.md), built by [eVoost AI](https://evoost.ai). Human judges score independently._",
            ])
            gh("issue", "comment", str(r["number"]), "--repo", REPO, "--body", body)
            print(f"feedback posted on #{r['number']}")


if __name__ == "__main__":
    main()
