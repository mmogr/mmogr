#!/usr/bin/env python3
"""Generate docs/contribs.json for the interactive contribution terrain.

Pulls the trailing year of contributions from the GitHub GraphQL API (via the
gh CLI, authenticated with GH_TOKEN) and writes one JSON payload:

  {"user": ..., "days": [{"date", "level", "count", "d"?: {...}}, ...]}

Per-day detail "d" itemizes commits per repo ("c"), pull requests ("p"),
issues ("i") and reviews ("r"); "x" is the remainder the API won't itemize
(private/other contributions), computed against the calendar count so private
repo names never appear in the output. When the token can see private repos
(CONTRIB_PAT), "pl" adds per-day {language: commits} buckets for them —
language only, never a name.

Contribution days are bucketed in TZ_NAME — GitHub buckets the profile
calendar in the profile owner's timezone, so this must match it.

"ghosts" holds plain daily-count arrays for up to three prior years (newest
first), for the homepage's hazy background ridges; empty years are dropped.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

USERNAME = os.environ["GH_USERNAME"]
TZ = ZoneInfo(os.environ.get("TZ_NAME", "Australia/Brisbane"))
OUT = os.environ.get("OUT", "docs/contribs.json")

QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!,
      $prCur: String, $isCur: String, $rvCur: String) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        weeks { contributionDays { date contributionCount contributionLevel } }
      }
      commitContributionsByRepository(maxRepositories: 100) {
        repository { name isPrivate primaryLanguage { name } }
        contributions(first: 100) { nodes { occurredAt commitCount } }
      }
      pullRequestContributions(first: 100, after: $prCur) {
        pageInfo { hasNextPage endCursor }
        nodes { occurredAt pullRequest { repository { name isPrivate } } }
      }
      issueContributions(first: 100, after: $isCur) {
        pageInfo { hasNextPage endCursor }
        nodes { occurredAt issue { repository { name isPrivate } } }
      }
      pullRequestReviewContributions(first: 100, after: $rvCur) {
        pageInfo { hasNextPage endCursor }
        nodes { occurredAt pullRequest { repository { name isPrivate } } }
      }
    }
  }
}
"""

EVENT_FIELDS = [
    ("pullRequestContributions", "p", "pullRequest"),
    ("issueContributions", "i", "issue"),
    ("pullRequestReviewContributions", "r", "pullRequest"),
]
LEVEL = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2,
         "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}


GHOST_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar { weeks { contributionDays { contributionCount } } }
    }
  }
}
"""


def gql(frm, to, cursors, query=QUERY):
    cmd = ["gh", "api", "graphql",
           "-f", f"query={query}", "-f", f"login={USERNAME}",
           "-f", f"from={frm}", "-f", f"to={to}"]
    for k, v in cursors.items():
        if v:
            cmd += ["-f", f"{k}={v}"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode:
        sys.exit(f"GraphQL query failed: {out.stderr[:1000]}")
    return json.loads(out.stdout)["data"]["user"]["contributionsCollection"]


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_date(occurred_at):
    return (datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
            .astimezone(TZ).strftime("%Y-%m-%d"))


def build_payload():
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=364)

    # calendar for the whole year in one query
    calendar = gql(iso(start), iso(now), {})["contributionCalendar"]
    days = [{"date": d["date"], "level": LEVEL[d["contributionLevel"]],
             "count": d["contributionCount"]}
            for w in calendar["weeks"] for d in w["contributionDays"]]

    # breakdown in <=92-day windows so per-repo commit buckets fit in first:100
    commit_buckets = {}  # (occurredAt, real repo name) -> (max count, private, lang)
    events = set()       # (occurredAt, kind, repo)
    edges = [start + (now - start) * i / 4 for i in range(5)]
    for w in range(4):
        frm, to = iso(edges[w]), iso(edges[w + 1])
        cursors = {"prCur": None, "isCur": None, "rvCur": None}
        done = {f: False for f, _, _ in EVENT_FIELDS}
        first = True
        while True:
            cc = gql(frm, to, cursors)
            if first:
                for rep in cc["commitContributionsByRepository"]:
                    r = rep["repository"]
                    lang = ((r.get("primaryLanguage") or {}).get("name")
                            if r["isPrivate"] else None)
                    for n in rep["contributions"]["nodes"]:
                        key = (n["occurredAt"], r["name"])
                        old = commit_buckets.get(key)
                        if not old or n["commitCount"] > old[0]:
                            commit_buckets[key] = (n["commitCount"],
                                                   r["isPrivate"], lang)
                first = False
            for field, kind, obj in EVENT_FIELDS:
                if done[field]:
                    continue
                for n in cc[field]["nodes"]:
                    repo = ("(private)" if n[obj]["repository"]["isPrivate"]
                            else n[obj]["repository"]["name"])
                    events.add((n["occurredAt"], kind, repo))
                pi = cc[field]["pageInfo"]
                if pi["hasNextPage"]:
                    cursors[{"pullRequestContributions": "prCur",
                             "issueContributions": "isCur",
                             "pullRequestReviewContributions": "rvCur"}[field]] = pi["endCursor"]
                else:
                    done[field] = True
            if all(done.values()):
                break

    detail = {}
    def bump(date, kind, repo, n=1):
        k = detail.setdefault(date, {}).setdefault(kind, {})
        k[repo] = k.get(repo, 0) + n

    plang = {}  # date -> {language: private commits}; anonymous by design
    for (occurred, name), (n, priv, lang) in commit_buckets.items():
        date = local_date(occurred)
        bump(date, "c", "(private)" if priv else name, n)
        if priv and lang:
            pl = plang.setdefault(date, {})
            pl[lang] = pl.get(lang, 0) + n
    for occurred, kind, repo in sorted(events):
        bump(local_date(occurred), kind, repo)

    for d in days:
        det = detail.get(d["date"])
        dd = ({k: sorted(v.items(), key=lambda x: -x[1]) for k, v in det.items()}
              if det else {})
        visible = sum(n for k in dd.values() for _, n in k)
        other = max(0, d["count"] - visible)
        if other:
            dd["x"] = other
        if d["date"] in plang:
            dd["pl"] = plang[d["date"]]
        if dd:
            d["d"] = dd

    payload = {"user": USERNAME, "days": days, "repoLangs": repo_langs()}
    ghosts = ghost_years(now)
    if ghosts:
        payload["ghosts"] = ghosts
    return payload


def ghost_years(now):
    """Daily counts for prior years, newest first; stops at the first empty."""
    ghosts = []
    for k in range(1, 4):
        to = now - timedelta(days=365 * k)
        cal = gql(iso(to - timedelta(days=364)), iso(to), {},
                  GHOST_QUERY)["contributionCalendar"]
        counts = [d["contributionCount"]
                  for w in cal["weeks"] for d in w["contributionDays"]]
        if not any(counts):
            break
        ghosts.append(counts)
    return ghosts


def repo_langs():
    """{repo: primary language} for the terrain's language tinting.

    PUBLIC repos only, filtered explicitly: the output is published, and a
    run with a personal token (unlike the Actions token) can see private
    repos — never let their names leak into the feed.
    """
    q = ('query($login:String!){user(login:$login){repositories(first:100,'
         'ownerAffiliations:OWNER){nodes{name isPrivate primaryLanguage{name}}}}}')
    out = subprocess.run(["gh", "api", "graphql", "-f", f"query={q}",
                          "-f", f"login={USERNAME}"], capture_output=True, text=True)
    if out.returncode:
        print(f"repoLangs query failed ({out.stderr[:200]}); omitting")
        return {}
    nodes = json.loads(out.stdout)["data"]["user"]["repositories"]["nodes"]
    return {n["name"]: n["primaryLanguage"]["name"]
            for n in nodes if n["primaryLanguage"] and not n["isPrivate"]}


def main():
    payload = build_payload()
    with open(OUT, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    days = payload["days"]
    total = sum(d["count"] for d in days)
    print(f"wrote {OUT}: {len(days)} days, {total} contributions, "
          f"{sum(1 for d in days if 'd' in d)} days with detail")


if __name__ == "__main__":
    main()
