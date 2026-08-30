#!/usr/bin/env python3
"""Build graph-data.json for the constellation page (mattogrady.com/graph).

Output shape:
  {"universe": {"nodes": [...], "edges": [[a, b, w], ...]},
   "dives": {repo: {"nodes": [...], "edges": [[f1, f2, co], ...],
                    "stats": {"commits": n, "prs": n, "issues": n}}}}

Universe: public repos (sized by this year's commits, colored by language),
language nodes, and concept nodes mined from commit messages of the most
active repos. Dives: file co-change networks, mined from a blob-less clone,
for every public non-fork repo with >= DIVE_MIN_COMMITS commits.

Env: GH_TOKEN, GH_USERNAME; CONTRIBS = path to contribs.json (for per-repo
commit counts); OUT = output path.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

USERNAME = os.environ["GH_USERNAME"]
CONTRIBS = os.environ.get("CONTRIBS", "_site/contribs.json")
OUT = os.environ.get("OUT", "_site/graph-data.json")
DIVE_MIN_COMMITS = 150
KW_REPOS = 4          # mine commit messages of this many most-active repos
KW_PER_REPO = 8

LANGC = {"Rust": "#dea584", "Python": "#5a9fd4", "Lua": "#8f8fd9", "C++": "#f34b7d",
         "TeX": "#7fa350", "Jupyter Notebook": "#DA5B0B", "Shell": "#89e051",
         "HTML": "#e34c26", "CSS": "#8f6fd9", "TypeScript": "#3178c6",
         "JavaScript": "#f1e05a", "Java": "#b07219", "Go": "#00add8", "C": "#8f9fbf"}

STOP = set("""a an and or of to the in for on with from into by at as is are was be
been this that it its not no now new use using used via when where which while
after before also only more less all some most other than then so if but out up
down over under can could should would will just do does did done have has had
we i you he she they them our your my dont doesnt cant wont isnt arent very
merge fix fixes fixed bug update updates updated add adds added remove removes
removed bump wip initial commit pr branch main master release version readme
test tests testing ci chore feat refactor docs doc cleanup clean minor small
change changes changed make makes made support improve improved improves better
fixup revert rename renamed move moved file files code work working issue issues
error errors instead still missing correct correctly properly actually handle
handling handles check checks checking set gets get getting keep don part first
second last next per non like need needs allow allows show shows start starts
end run runs running stop try tries real right left two one way own same each
""".split())

SKIP_FILES = re.compile(r"(^\.github/|Cargo\.lock$|package-lock\.json$|\.lock$)")


def gh(args):
    out = subprocess.run(["gh"] + args, capture_output=True, text=True)
    if out.returncode:
        sys.exit(f"gh {' '.join(args[:2])} failed: {out.stderr[:400]}")
    return out.stdout


def keywords(messages, top):
    freq = {}
    for m in messages:
        for w in re.sub(r"[^a-z0-9 ]", " ", m.lower()).split():
            if len(w) >= 3 and w not in STOP and not w.isdigit():
                freq[w] = freq.get(w, 0) + 1
    return dict(sorted(freq.items(), key=lambda x: -x[1])[:top])


def mine_cochange(repo):
    tmp = tempfile.mkdtemp(prefix="cochange-")
    try:
        r = subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout",
                            "--quiet", f"https://github.com/{USERNAME}/{repo}", tmp],
                           capture_output=True, text=True)
        if r.returncode:
            print(f"clone {repo} failed: {r.stderr[:200]}")
            return None
        log = subprocess.run(["git", "-C", tmp, "log", "--name-only",
                              "--pretty=format:@%h"], capture_output=True, text=True).stdout
        counts, pairs = {}, {}
        for block in log.split("@")[1:]:
            fs = [ln for ln in block.splitlines()[1:] if ln and not SKIP_FILES.search(ln)]
            fs = sorted(set(fs))[:30]  # ignore huge refactor commits' tails
            for f in fs:
                counts[f] = counts.get(f, 0) + 1
            for i, a in enumerate(fs):
                for b in fs[i + 1:]:
                    pairs[(a, b)] = pairs.get((a, b), 0) + 1
        top = dict(sorted(counts.items(), key=lambda x: -x[1])[:45])
        edges = [[a, b, c] for (a, b), c in pairs.items()
                 if c >= 4 and a in top and b in top]
        used = {f for e in edges for f in e[:2]}
        nodes = [{"id": f, "kind": "file", "label": f.split("/")[-1], "path": f,
                  "dir": f.split("/")[0] if "/" in f else "(root)", "count": top[f],
                  "url": f"https://github.com/{USERNAME}/{repo}/blob/main/{f}"}
                 for f in top if f in used]
        return {"nodes": nodes, "edges": edges}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def repo_stats(repo):
    q = ('query($o:String!,$r:String!){repository(owner:$o,name:$r){'
         'issues{totalCount} pullRequests{totalCount} '
         'defaultBranchRef{target{... on Commit{history{totalCount}}}}}}')
    d = json.loads(gh(["api", "graphql", "-f", f"query={q}",
                       "-f", f"o={USERNAME}", "-f", f"r={repo}"]))["data"]["repository"]
    hist = ((d.get("defaultBranchRef") or {}).get("target") or {}).get("history", {})
    return {"commits": hist.get("totalCount", 0),
            "prs": d["pullRequests"]["totalCount"],
            "issues": d["issues"]["totalCount"]}


def main():
    repo_commits = {}
    try:
        for d in json.load(open(CONTRIBS))["days"]:
            for repo, n in d.get("d", {}).get("c", []):
                if repo != "(private)":
                    repo_commits[repo] = repo_commits.get(repo, 0) + n
    except FileNotFoundError:
        print(f"warning: {CONTRIBS} not found; repo sizes fall back to 0")

    repos = json.loads(gh(["api", f"users/{USERNAME}/repos", "--paginate"]))
    repos = [r for r in repos if r["name"] != USERNAME]

    nodes, edges = [], []
    for r in repos:
        nodes.append({"id": r["name"], "kind": "repo", "label": r["name"],
                      "desc": r["description"] or "", "lang": r["language"] or "",
                      "commits": repo_commits.get(r["name"], 0),
                      "color": LANGC.get(r["language"] or "", "#4a5a8a"),
                      "url": r["html_url"]})
        if r["language"]:
            lid = "lang:" + r["language"]
            if not any(n["id"] == lid for n in nodes):
                nodes.append({"id": lid, "kind": "lang", "label": r["language"],
                              "color": LANGC.get(r["language"], "#4a5a8a")})
            edges.append([r["name"], lid, 2])

    active = sorted((r for r in repos if not r["fork"]),
                    key=lambda r: -repo_commits.get(r["name"], 0))[:KW_REPOS]
    for r in active:
        if not repo_commits.get(r["name"]):
            continue
        msgs = []
        for page in (1, 2):
            out = subprocess.run(
                ["gh", "api", f"repos/{USERNAME}/{r['name']}/commits?per_page=100&page={page}"],
                capture_output=True, text=True)
            if out.returncode:
                break
            batch = json.loads(out.stdout)
            msgs += [c["commit"]["message"].splitlines()[0] for c in batch]
            if len(batch) < 100:
                break
        for wd, cnt in keywords(msgs, KW_PER_REPO).items():
            kid = "kw:" + wd
            if not any(n["id"] == kid for n in nodes):
                nodes.append({"id": kid, "kind": "kw", "label": wd,
                              "count": cnt, "color": "#3a6fc4"})
            edges.append([r["name"], kid, 1])

    dives = {}
    for r in repos:
        if r["fork"] or r["name"] == USERNAME:
            continue
        stats = repo_stats(r["name"])
        if stats["commits"] < DIVE_MIN_COMMITS:
            continue
        net = mine_cochange(r["name"])
        if net and len(net["nodes"]) >= 10:
            dives[r["name"]] = {**net, "stats": stats}
            print(f"dive {r['name']}: {len(net['nodes'])} files, "
                  f"{len(net['edges'])} pairs, {stats['commits']} commits")

    data = {"universe": {"nodes": nodes, "edges": edges}, "dives": dives}
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    json.dump(data, open(OUT, "w"), separators=(",", ":"))
    print(f"wrote {OUT}: {len(nodes)} universe nodes, {len(edges)} edges, "
          f"{len(dives)} dive(s)")


if __name__ == "__main__":
    main()
