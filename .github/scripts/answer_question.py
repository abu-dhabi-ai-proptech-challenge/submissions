#!/usr/bin/env python3
"""AI answer bot for `question` Issues — the Discord helper, on GitHub.

When a participant opens a Question issue, this script answers it within
minutes, grounded in the public event docs. Calibrated honesty: if the answer
isn't in the docs, it says so and flags the issue for a human organizer
(`needs-review` label) instead of inventing rules.

Runs from question-bot.yml. Env: ANTHROPIC_API_KEY, GH_TOKEN, ISSUE_NUMBER,
GITHUB_REPOSITORY.
"""

import json
import os
import subprocess
import urllib.request

REPO = os.environ["GITHUB_REPOSITORY"]
ISSUE = os.environ["ISSUE_NUMBER"]
MODEL = os.environ.get("AI_BOT_MODEL", "claude-opus-4-8")

DOCS = [
    "https://raw.githubusercontent.com/abu-dhabi-ai-proptech-challenge/starter-kit/main/docs/faq.md",
    "https://raw.githubusercontent.com/abu-dhabi-ai-proptech-challenge/starter-kit/main/docs/challenge-brief.md",
    "https://raw.githubusercontent.com/abu-dhabi-ai-proptech-challenge/starter-kit/main/docs/tracks.md",
    "https://raw.githubusercontent.com/abu-dhabi-ai-proptech-challenge/starter-kit/main/docs/cursor-guide.md",
    "https://raw.githubusercontent.com/abu-dhabi-ai-proptech-challenge/submissions/main/docs/submission-guide.md",
    "https://raw.githubusercontent.com/abu-dhabi-ai-proptech-challenge/submissions/main/docs/judging-criteria.md",
]

PERSONA = """You are the official AI assistant of the Abu Dhabi AI PropTech Challenge \
("Building the Intelligence Layer for Land, Investment and Communities", Friday 26 June \
2026, 12:00-18:00 GST at Hub71; submissions close 16:30 GST), answering a participant's \
question on GitHub.

Rules:
- Answer ONLY from the event knowledge below. If the answer isn't covered, set \
confident=false and say plainly that an organizer will confirm — never invent rules, \
deadlines, prizes or logistics.
- GitHub-comment sized: a few sentences or a short list, links where useful.
- For technical questions (Python, pandas, Next.js, LLM APIs, git), help like a good \
hackathon mentor: short explanation, small snippet if useful.
- Never describe "eVoost Brain" or "DMT by eVoost" as an existing product — eVoost is \
defining a City Intelligence Framework; the event prototypes parts of a future system.
- Faster answers live on Discord: https://discord.gg/jy3QDxQ3jK"""

SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "description": "the GitHub comment body, markdown"},
        "confident": {"type": "boolean",
                      "description": "false if the docs don't clearly cover this"},
    },
    "required": ["answer", "confident"],
    "additionalProperties": False,
}


def gh(*args: str) -> str:
    return subprocess.check_output(["gh", *args], text=True)


def fetch(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return r.read().decode()
    except Exception:
        return ""


def main() -> None:
    issue = json.loads(gh("issue", "view", ISSUE, "--repo", REPO, "--json", "title,body"))
    knowledge = "\n\n---\n\n".join(filter(None, (fetch(u) for u in DOCS)))

    import anthropic
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1200,
        system=[{"type": "text", "text": PERSONA},
                {"type": "text", "text": f"EVENT KNOWLEDGE:\n\n{knowledge}"}],
        messages=[{"role": "user",
                   "content": f"Question title: {issue['title']}\n\n{issue['body']}"}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )
    result = json.loads(next(b.text for b in response.content if b.type == "text"))

    footer = ("\n\n---\n*Answered by the event AI assistant "
              "([how it works](https://github.com/abu-dhabi-ai-proptech-challenge/starter-kit/blob/main/docs/how-this-event-runs-on-ai.md)). ")
    if result["confident"]:
        footer += "If this doesn't fully answer it, reply here and an organizer will jump in.*"
    else:
        footer += "**An organizer will confirm this one** — flagged for human review.*"
        gh("issue", "edit", ISSUE, "--repo", REPO, "--add-label", "needs-review")

    gh("issue", "comment", ISSUE, "--repo", REPO, "--body", result["answer"] + footer)
    print(f"answered #{ISSUE} (confident={result['confident']}) | "
          f"tokens in={response.usage.input_tokens} out={response.usage.output_tokens}")


if __name__ == "__main__":
    main()
