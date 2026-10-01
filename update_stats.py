#!/usr/bin/env python3
"""Builds dark_mode.svg and light_mode.svg for the GitHub profile README.

The GitHub Action in .github/workflows/update-card.yml runs this once a day.
To run it yourself:

    GH_TOKEN=<personal access token> python update_stats.py

Without GH_TOKEN it skips the API calls and reuses the last numbers saved in
cache/stats.json, so you can tweak the layout offline.

Edit USER, BIRTHDAY, INFO and ART below to change what the card shows.
"""

import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

USER = "o-faruk"
BIRTHDAY = ""  # YYYY-MM-DD, drives the Uptime line
TIMEZONE = "America/New_York"

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache" / "stats.json"

# ---------------------------------------------------------------- content --

# Each row is one line of the info panel on the right.
#   ("header", text)       -> "omar@faruk ─────"
#   ("section", title)     -> "- Contact ───────"
#   ("kv", key, value)     -> ". Key: ...... value"   ({uptime} is filled in)
#   ("blank",)             -> empty line
#   ("stats_repos",) / ("stats_commits",) / ("stats_loc",) -> live GitHub rows
INFO = [
    ("header", "omar@faruk"),
    ("kv", "OS", "macOS, Arch Linux"),
    ("kv", "Uptime", "{uptime}"),
    ("kv", "Host", "UConn, Class of 2028"),
    ("kv", "Kernel", "Computer Science, AI/ML"),
    ("kv", "WM", "bspwm"),
    ("kv", "Terminal", "Ghostty, Alacritty"),
    ("kv", "Theme", "Catppuccin Mocha"),
    ("blank",),
    ("kv", "Languages.Programming", "Python, Go, TypeScript, C++, Java"),
    ("kv", "Languages.Computer", "HTML, CSS, SQL, YAML, LaTeX"),
    ("kv", "Languages.Real", "English"),
    ("blank",),
    ("kv", "Projects.Current", "mcp-x-ray, Chronos, Prometheus"),
    ("kv", "Hobbies.Software", "Linux ricing, Roblox game dev"),
    ("kv", "Hobbies.Other", "Cars, streetwear, VALORANT"),
    ("section", "Contact"),
    ("kv", "Website", "ofaruk.dev"),
    ("kv", "Email", "omarfarukk108@gmail.com"),
    ("kv", "LinkedIn", "omar-faruko"),
    ("section", "GitHub Stats"),
    ("stats_repos",),
    ("stats_commits",),
    ("stats_loc",),
]

ART = r"""
     .             +        .
 *         .           .        *
       +                  *
   *           \   /         .
       .        \_/       .
                 |
    _________   _|_   _________
   |_|_|_|_|_|=|   |=|_|_|_|_|_|
   |_|_|_|_|_|=|[o]|=|_|_|_|_|_|
   |_|_|_|_|_|=|___|=|_|_|_|_|_|
  +             /|\           .
 .                               .
  '.       *                   .'
    ' .                     . '
       ' - . _ _ _ _ _ . - '
   +          .              *
           _.-'''''''-._
        .-'   .:::.  .  '-.
      .'  .::::::::.  ':.  '.
    /  .::::::::::'   ':::.   \
   /   ':::::::::'     .:::::' \
  |     ':::::'     ..  '::::'  |
  |       ':.     ':::.         |
  |  .::.          '::.     .   |
""".strip("\n").split("\n")

# ----------------------------------------------------------------- layout --

WIDTH_CHARS = 62  # info panel width in characters
LEFT_CHARS = 36  # where the " | " sits on the two-column stats rows
FONT_SIZE = 16
LINE_HEIGHT = 20
TOP = 30
ART_X = 15
INFO_X = 378
SVG_W = 1010
SVG_H = TOP + LINE_HEIGHT * (max(len(INFO), len(ART)) - 1) + 20
FONT = ("'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, "
        "'DejaVu Sans Mono', 'Liberation Mono', monospace")

# Catppuccin Mocha (dark) and Latte (light)
PALETTES = {
    "dark": dict(bg="#1e1e2e", border="#313244", text="#cdd6f4", art="#bac2de",
                 user="#cba6f7", key="#fab387", value="#89b4fa", dots="#6c7086",
                 add="#a6e3a1", delete="#f38ba8"),
    "light": dict(bg="#eff1f5", border="#ccd0da", text="#4c4f69", art="#5c5f77",
                  user="#8839ef", key="#fe640b", value="#1e66f5", dots="#9ca0b0",
                  add="#40a02b", delete="#d20f39"),
}

# ------------------------------------------------------------- github api --

def request(url, token, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": f"{USER}-profile-card",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
        return resp.status, (json.loads(body) if body else None)


def graphql(query, variables, token):
    _, body = request("https://api.github.com/graphql", token,
                      {"query": query, "variables": variables})
    if body.get("errors"):
        raise RuntimeError(body["errors"])
    return body["data"]


REPOS_QUERY = """
query($login: String!, $cursor: String) {
  user(login: $login) {
    createdAt
    followers { totalCount }
    contributed: repositories(ownerAffiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER]) {
      totalCount
    }
    repositories(first: 100, after: $cursor, ownerAffiliations: [OWNER]) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes { nameWithOwner stargazerCount isFork }
    }
  }
}
"""

COMMITS_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      restrictedContributionsCount
    }
  }
}
"""


def count_commits(created_at, token):
    """Commit contributions summed one calendar year at a time (API limit)."""
    start = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    now = dt.datetime.now(dt.timezone.utc)
    total = 0
    for year in range(start.year, now.year + 1):
        lo = max(start, dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc))
        hi = min(now, dt.datetime(year, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc))
        cc = graphql(COMMITS_QUERY, {"login": USER, "from": lo.isoformat(),
                                     "to": hi.isoformat()}, token)
        cc = cc["user"]["contributionsCollection"]
        total += cc["totalCommitContributions"] + cc["restrictedContributionsCount"]
    return total


def repo_lines(name, token, tries=6):
    """(added, deleted) by USER in one repo, or None if GitHub is still computing."""
    url = f"https://api.github.com/repos/{name}/stats/contributors"
    for attempt in range(tries):
        try:
            status, body = request(url, token)
        except urllib.error.HTTPError as err:
            print(f"  {name}: HTTP {err.code}, skipping")
            return 0, 0
        if status == 202:  # GitHub builds these stats lazily; ask again shortly
            time.sleep(3 + 2 * attempt)
            continue
        for contributor in body or []:
            login = (contributor.get("author") or {}).get("login", "")
            if login.lower() == USER.lower():
                weeks = contributor["weeks"]
                return sum(w["a"] for w in weeks), sum(w["d"] for w in weeks)
        return 0, 0
    return None


def fetch_stats(token, old_loc):
    repos, cursor, first = [], None, None
    while True:
        data = graphql(REPOS_QUERY, {"login": USER, "cursor": cursor}, token)["user"]
        first = first or data
        page = data["repositories"]
        repos += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    loc = {}
    for repo in repos:
        if repo["isFork"]:
            continue
        name = repo["nameWithOwner"]
        result = repo_lines(name, token)
        if result is None:  # still computing after retries: keep yesterday's numbers
            result = tuple(old_loc.get(name, (0, 0)))
            print(f"  {name}: stats not ready, using cached {result}")
        loc[name] = list(result)

    return {
        "repos": first["repositories"]["totalCount"],
        "contributed": first["contributed"]["totalCount"],
        "stars": sum(r["stargazerCount"] for r in repos),
        "followers": first["followers"]["totalCount"],
        "commits": count_commits(first["createdAt"], token),
        "loc_add": sum(a for a, _ in loc.values()),
        "loc_del": sum(d for _, d in loc.values()),
        "loc_by_repo": loc,
        "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }

# ---------------------------------------------------------------- render --

def uptime(today):
    if not BIRTHDAY:
        return "set BIRTHDAY in update_stats.py"
    born = dt.date.fromisoformat(BIRTHDAY)
    years = today.year - born.year
    months = today.month - born.month
    days = today.day - born.day
    if days < 0:
        months -= 1
        days += (today.replace(day=1) - dt.timedelta(days=1)).day
    if months < 0:
        years -= 1
        months += 12

    def unit(n, word):
        return f"{n} {word}{'' if n == 1 else 's'}"

    return f"{unit(years, 'year')}, {unit(months, 'month')}, {unit(days, 'day')}"


def num(value):
    return f"{value:,}" if isinstance(value, int) else "-"


def kv(key, values, width, lead=". "):
    """'. Key: ....... value' padded with dots to exactly `width` characters."""
    used = len(lead) + len(key) + len(": ") + 1 + sum(len(t) for _, t in values)
    dots = width - used
    if dots < 1:
        print(f"warning: '{key}' row is {1 - dots} chars too long", file=sys.stderr)
        dots = 1
    return [("text", lead), ("key", key), ("text", ":"),
            ("dots", " " + "." * dots + " ")] + values


def rule(segments, width):
    used = sum(len(t) for _, t in segments)
    return segments + [("dots", " " + "─" * (width - used - 1))]


def build_rows(stats, today):
    v = lambda x: [("value", x)]
    rows = []
    for row in INFO:
        kind = row[0]
        if kind == "header":
            rows.append(rule([("user", row[1])], WIDTH_CHARS))
        elif kind == "section":
            rows.append(rule([("text", "- " + row[1])], WIDTH_CHARS))
        elif kind == "kv":
            rows.append(kv(row[1], v(row[2].format(uptime=uptime(today))), WIDTH_CHARS))
        elif kind == "blank":
            rows.append([])
        elif kind == "stats_repos":
            left = kv("Repos", v(num(stats.get("repos"))) + [
                ("text", " {Contributed: "), ("value", num(stats.get("contributed"))),
                ("text", "}")], LEFT_CHARS)
            right = kv("Stars", v(num(stats.get("stars"))),
                       WIDTH_CHARS - LEFT_CHARS, lead=" | ")
            rows.append(left + right)
        elif kind == "stats_commits":
            left = kv("Commits", v(num(stats.get("commits"))), LEFT_CHARS)
            right = kv("Followers", v(num(stats.get("followers"))),
                       WIDTH_CHARS - LEFT_CHARS, lead=" | ")
            rows.append(left + right)
        elif kind == "stats_loc":
            add, dele = stats.get("loc_add"), stats.get("loc_del")
            net = add - dele if isinstance(add, int) and isinstance(dele, int) else None
            rows.append(kv("Lines of Code", v(num(net)) + [
                ("text", " ( "), ("add", num(add) + "++"), ("text", ", "),
                ("delete", num(dele) + "--"), ("text", " )")], WIDTH_CHARS))
    return rows


def render(palette, stats, today):
    p = palette
    css = "".join(f".{name}{{fill:{color}}}" for name, color in p.items()
                  if name not in ("bg", "border"))
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_W}" height="{SVG_H}" '
        f'viewBox="0 0 {SVG_W} {SVG_H}" font-family="{escape(FONT, {chr(34): "&quot;"})}" '
        f'font-size="{FONT_SIZE}px" role="img" aria-label="Omar Faruk, GitHub profile card">',
        f"<style>text,tspan{{white-space:pre}}{css}</style>",
        f'<rect x="0.5" y="0.5" width="{SVG_W - 1}" height="{SVG_H - 1}" rx="14" '
        f'fill="{p["bg"]}" stroke="{p["border"]}"/>',
        f'<text class="art" xml:space="preserve">',
    ]
    for i, line in enumerate(ART):
        out.append(f'<tspan x="{ART_X}" y="{TOP + i * LINE_HEIGHT}">{escape(line)}</tspan>')
    out.append("</text>")
    out.append('<text class="text" xml:space="preserve">')
    for i, segments in enumerate(build_rows(stats, today)):
        if not segments:
            continue
        inner = "".join(f'<tspan class="{cls}">{escape(txt)}</tspan>' for cls, txt in segments)
        out.append(f'<tspan x="{INFO_X}" y="{TOP + i * LINE_HEIGHT}">{inner}</tspan>')
    out.append("</text>")
    out.append("</svg>")
    return "\n".join(out) + "\n"


def main():
    cached = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    token = os.environ.get("GH_TOKEN")
    stats = cached
    if token:
        stats = fetch_stats(token, cached.get("loc_by_repo", {}))
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n")
    else:
        print("GH_TOKEN not set: using cached stats", file=sys.stderr)

    today = dt.datetime.now(ZoneInfo(TIMEZONE)).date()
    for mode, palette in PALETTES.items():
        (ROOT / f"{mode}_mode.svg").write_text(render(palette, stats, today), encoding="utf-8")
    print("wrote dark_mode.svg and light_mode.svg")


if __name__ == "__main__":
    main()
