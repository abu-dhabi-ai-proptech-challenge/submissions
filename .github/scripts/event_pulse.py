#!/usr/bin/env python3
"""Event Pulse — the AI narrates the event, live.

Every hour during the event, this script reads the real signals (submissions
by track, Discord activity across public channels) and has Claude write a
short, punchy pulse. It goes out to two screens at once:
  - Discord #announcements (via webhook)
  - the venue projector — /live on the website reads pulse.json from this repo

Outside event hours it exits silently (override with FORCE=true for testing).

Env: ANTHROPIC_API_KEY, GH_TOKEN, DISCORD_BOT_TOKEN, DISCORD_GUILD_ID,
ANNOUNCE_WEBHOOK, FORCE (optional), GITHUB_REPOSITORY.
"""

import base64
import json
import os
import re
import subprocess
import urllib.request
from datetime import datetime, timezone, timedelta

REPO = os.environ.get("GITHUB_REPOSITORY", "abu-dhabi-ai-proptech-challenge/submissions")
MODEL = os.environ.get("AI_BOT_MODEL", "claude-opus-4-8")
EVENT_DATE = "2026-06-26"
EVENT_HOURS_UTC = range(8, 15)  # 12:00-18:00 GST
TRACKS = ["Land Intelligence", "Investment Intelligence", "Future Communities", "Decision Intelligence"]
PULSE_CHANNELS = ["general", "team-formation", "help-desk", "land-intelligence",
                  "investment-intelligence", "future-communities", "decision-intelligence",
                  "cursor-corner", "introductions"]

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "<=110 chars, punchy, emoji welcome, projector-worthy"},
        "body": {"type": "string", "description": "2-3 sentences narrating the moment with the real numbers; energetic but honest, no invention"},
    },
    "required": ["headline", "body"],
    "additionalProperties": False,
}


def discord(path: str):
    req = urllib.request.Request(f"https://discord.com/api/v10{path}",
        headers={"Authorization": f"Bot {os.environ['DISCORD_BOT_TOKEN']}",
                 "User-Agent": "adcc-pulse/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def collect() -> dict:
    issues = json.loads(subprocess.check_output(
        ["gh", "issue", "list", "--repo", REPO, "--label", "submission", "--state", "all",
         "--limit", "500", "--json", "title,body,labels,createdAt"], text=True))
    by_track = {t: 0 for t in TRACKS}
    teams = []
    for issue in issues:
        if any(l["name"] == "late" for l in issue["labels"]):
            continue
        m = re.search(r"###\s*Track\s*\n+\s*(.+)", issue["body"] or "")
        track = m.group(1).strip() if m else ""
        if track in by_track:
            by_track[track] += 1
        tm = re.search(r"###\s*Team name\s*\n+\s*(.+)", issue["body"] or "")
        teams.append(tm.group(1).strip() if tm else issue["title"])

    activity = {}
    try:
        guild = os.environ["DISCORD_GUILD_ID"]
        chans = {c["name"]: c["id"] for c in discord(f"/guilds/{guild}/channels") if c["type"] == 0}
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        for name in PULSE_CHANNELS:
            if name not in chans:
                continue
            msgs = discord(f"/channels/{chans[name]}/messages?limit=100")
            recent = [m for m in msgs
                      if datetime.fromisoformat(m["timestamp"]).astimezone(timezone.utc) > cutoff
                      and not m["author"].get("bot")]
            if recent:
                activity[name] = len(recent)
    except Exception as err:
        activity = {"_unavailable": str(err)[:60]}

    return {"submissions_on_time": sum(by_track.values()), "by_track": by_track,
            "recent_teams": teams[:5], "discord_messages_last_hour": activity,
            "time_gst": (datetime.now(timezone.utc) + timedelta(hours=4)).strftime("%H:%M")}


def main() -> None:
    force = os.environ.get("FORCE", "").lower() == "true"
    now = datetime.now(timezone.utc)
    if not force and (now.strftime("%Y-%m-%d") != EVENT_DATE or now.hour not in EVENT_HOURS_UTC):
        print("outside event hours — nothing to do")
        return

    data = collect()
    import anthropic
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL, max_tokens=500,
        system=("You write the hourly Event Pulse for the Abu Dhabi AI PropTech Challenge "
                "(one-day AI hackathon, 4 tracks, submissions close 17:00 GST, judging 17:00–17:45). "
                "It shows on the venue's big screen and in Discord. Style: ambitious, premium, "
                "builder-first, future-city; never overhyped, never bureaucratic. Use ONLY the "
                "real numbers given — zero submissions early in the day is normal (teams submit "
                "near the deadline), so narrate momentum from Discord activity then. "
                + ("THIS IS A TEST RUN before the event: make it a pre-event pulse — anticipation, "
                   "what's coming on 26 June." if force and now.strftime("%Y-%m-%d") != EVENT_DATE else "")),
        messages=[{"role": "user", "content": json.dumps(data)}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )
    pulse = json.loads(next(b.text for b in response.content if b.type == "text"))
    pulse.update(generated_at=now.isoformat(timespec="seconds"),
                 totals=data["by_track"], total=data["submissions_on_time"])

    # 1. Discord #announcements
    body = json.dumps({"content": f"🧠 **EVENT PULSE · {data['time_gst']} GST**\n**{pulse['headline']}**\n{pulse['body']}",
                       "username": "Event Pulse"}).encode()
    urllib.request.urlopen(urllib.request.Request(
        os.environ["ANNOUNCE_WEBHOOK"], data=body,
        headers={"Content-Type": "application/json", "User-Agent": "adcc-pulse/1.0"}))

    # 2. pulse.json in the repo → the /live dashboard picks it up within 60s
    content = base64.b64encode(json.dumps(pulse, indent=2).encode()).decode()
    sha = subprocess.run(["gh", "api", f"repos/{REPO}/contents/pulse.json", "--jq", ".sha"],
                         capture_output=True, text=True).stdout.strip()
    args = ["gh", "api", "-X", "PUT", f"repos/{REPO}/contents/pulse.json",
            "-f", "message=Event Pulse update [skip ci]", "-f", f"content={content}"]
    if sha:
        args += ["-f", f"sha={sha}"]
    subprocess.run(args, check=True, capture_output=True)
    print(f"pulse published: {pulse['headline']}")


if __name__ == "__main__":
    main()
