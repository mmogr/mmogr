#!/usr/bin/env python3
"""Generate the profile README and its card images (profile-cards/*.svg).

The profile is a set of night-palette cards with NATIVE hover tooltips:
GitHub's sanitizer keeps `title` attributes, so each card (or slice of a
card) is an <a title="..."><img></a> whose tooltip carries the data detail.

  - language calendar: each day tinted by that day's top commit language,
    sliced into 12 month images -> per-month tooltips
  - activity card: gold radar + counts (one tooltip)
  - languages card: donut + legend (one tooltip)
  - recents line: quiet, titles-only links to recently updated uninotes
    pages, scraped from the public site; course/date live in the tooltips

Tooltip texts change with the data, so this script rewrites README.md too.
Env: GH_TOKEN, GH_USERNAME, TZ_NAME. Run from the repo root.
"""
import html as htmlmod
import json
import math
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from contribs import USERNAME, build_payload  # noqa: E402

OUT_DIR = "profile-cards"
RAW = f"https://raw.githubusercontent.com/{USERNAME}/{USERNAME}/main/{OUT_DIR}"
TERRAIN_URL = "https://mattogrady.com/terrain/"
GRAPH_URL = "https://mattogrady.com/graph/"
NOTES_URL = f"https://{USERNAME}.github.io/uninotes/"

MUTED, INK, CARD, GOLD = "#7a86ad", "#e8ecff", "#00000f", "#ffc837"
EMPTY, NEUTRAL = "#101d40", "#5a6ea8"
MONO = "ui-monospace,'Cascadia Mono','SFMono-Regular',Consolas,monospace"
MIXW = [0, 0.4, 0.6, 0.8, 1.0]

CAL_W, CAL_H = 780, 138
ACT_W, ACT_GUT, ACT_H = 400, 14, 216
DON_W = 396


def esc(s):
    return htmlmod.escape(htmlmod.unescape(s), quote=True)


def hex2rgb(h):
    return (int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16))


def rgb2hex(c):
    return f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}"


def lighten(hexc, f=0.5):
    c = hex2rgb(hexc)
    if 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2] < 95:
        c = tuple(int(v + (255 - v) * f) for v in c)
    return rgb2hex(c)


def mix(a, b, w):
    a, b = hex2rgb(a), hex2rgb(b)
    return rgb2hex(tuple(int(a[i] + (b[i] - a[i]) * w) for i in range(3)))


def svg_file(name, viewbox, body):
    with open(f"{OUT_DIR}/{name}", "w") as f:
        f.write(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}" '
                f'font-family="{MONO}">{body}</svg>')


def word(n, s):
    return f"{n} {s}" + ("" if n == 1 else "s")


def repo_languages():
    q = ('query($login:String!){user(login:$login){repositories(first:100,'
         'ownerAffiliations:OWNER){nodes{name primaryLanguage{name color}}}}}')
    out = subprocess.run(["gh", "api", "graphql", "-f", f"query={q}",
                          "-f", f"login={USERNAME}"], capture_output=True, text=True)
    if out.returncode:
        sys.exit(f"language query failed: {out.stderr[:500]}")
    nodes = json.loads(out.stdout)["data"]["user"]["repositories"]["nodes"]
    return {n["name"]: (n["primaryLanguage"]["name"], n["primaryLanguage"]["color"] or "#8b949e")
            for n in nodes if n["primaryLanguage"]}


def fetch_recents(limit=4):
    try:
        page = urllib.request.urlopen(NOTES_URL, timeout=30).read().decode()
    except Exception as e:
        print(f"recents fetch failed ({e}); omitting recents line")
        return []
    rows = []
    for m in re.finditer(r"<tr[^>]*>.*?</tr>", page[page.find("Recently updated"):], re.S):
        r = m.group(0)
        href = re.search(r'href="\./(courses/[^"]+\.html)"', r)
        cells = [re.sub(r"<[^>]+>", "", c).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
        if href and len(cells) >= 4 and cells[1]:
            rows.append({"date": cells[0], "title": htmlmod.unescape(cells[1]),
                         "course": cells[2], "change": cells[3],
                         "url": NOTES_URL + href.group(1)})
    return rows[:limit]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    payload = build_payload()
    days = payload["days"]
    i0 = next(i for i, d in enumerate(days) if d["date"].endswith("-01"))
    days = days[i0:]
    rl = repo_languages()

    def day_lang(d):
        per = {}
        for repo, n in d.get("d", {}).get("c", []):
            if repo in rl:
                per[rl[repo]] = per.get(rl[repo], 0) + n
        return max(per.items(), key=lambda x: x[1])[0] if per else None

    bars, col, prev = [], 0, None
    for d in days:
        row = (datetime.fromisoformat(d["date"]).weekday() + 1) % 7
        if row == 0 and prev is not None:
            col += 1
        prev = row
        bars.append({**d, "col": col, "row": row})

    months = []
    for b in bars:
        k = b["date"][:7]
        if not months or months[-1]["key"] != k:
            months.append({"key": k, "c0": b["col"], "c1": b["col"], "total": 0,
                           "langs": {}, "repos": {}, "best": b})
        m = months[-1]
        m["c1"] = max(m["c1"], b["col"])
        m["total"] += b["count"]
        if b["count"] > m["best"]["count"]:
            m["best"] = b
        for repo, n in b.get("d", {}).get("c", []):
            if repo in rl:
                m["langs"][rl[repo]] = m["langs"].get(rl[repo], 0) + n
            if repo != "(private)":
                m["repos"][repo] = m["repos"].get(repo, 0) + n

    # ---------- calendar card, sliced per month ----------
    CELL, CGAP, MGAP = 9, 1.6, 9
    week_w = CELL + CGAP
    colx, x = {}, 0
    starts = {m["c0"] for m in months}
    for c in range(max(b["col"] for b in bars) + 1):
        if c in starts and c != 0:
            x += MGAP
        colx[c] = x
        x += week_w
    pad = (CAL_W - (x - CGAP)) / 2

    content = [f'<rect x="0" y="0" width="{CAL_W}" height="{CAL_H}" rx="8" fill="{CARD}"/>']
    gy = 12
    for b in bars:
        cx, cy = pad + colx[b["col"]], gy + b["row"] * week_w
        if b["count"] == 0:
            fill = EMPTY
        else:
            dl = day_lang(b)
            fill = mix(EMPTY, lighten(dl[1]) if dl else NEUTRAL, MIXW[b["level"]])
        content.append(f'<rect x="{cx:.1f}" y="{cy:.1f}" width="{CELL}" '
                       f'height="{CELL}" rx="2" fill="{fill}"/>')
    for m in months:
        x0, x1 = pad + colx[m["c0"]], pad + colx[m["c1"]] + CELL
        lbl = datetime.fromisoformat(m["key"] + "-01").strftime("%b")
        content.append(f'<text x="{(x0 + x1) / 2:.0f}" y="{gy + 7 * week_w + 13:.0f}" '
                       f'fill="{MUTED}" font-size="10.5" text-anchor="middle">{lbl} '
                       f'<tspan fill="{INK}">{m["total"]}</tspan></text>')
    cal_body = "".join(content)

    cuts = [0]
    for i in range(1, len(months)):
        left = pad + colx[months[i - 1]["c1"]] + CELL
        right = pad + colx[months[i]["c0"]]
        cuts.append(round((left + right) / 2))
    cuts.append(CAL_W)

    month_tags = []
    for i, m in enumerate(months):
        x0, w = cuts[i], cuts[i + 1] - cuts[i]
        svg_file(f"m{i:02d}.svg", f"{x0} 0 {w} {CAL_H}", cal_body)
        mdt = datetime.fromisoformat(m["key"] + "-01")
        lines = [f"{mdt:%B %Y} · {word(m['total'], 'contribution')}"]
        if m["langs"]:
            tot = sum(m["langs"].values())
            lines.append(" · ".join(f"{lang} {n / tot * 100:.0f}%" for (lang, _), n in
                                    sorted(m["langs"].items(), key=lambda i: -i[1])[:4]))
        if m["repos"]:
            top = max(m["repos"].items(), key=lambda x: x[1])
            lines.append(f"top: {top[0]} ({word(top[1], 'commit')})")
        if m["best"]["count"]:
            bd = datetime.fromisoformat(m["best"]["date"])
            lines.append(f"best day {bd.day} {bd:%b} · {m['best']['count']}")
        month_tags.append(f'<a href="{TERRAIN_URL}" title="{"&#10;".join(esc(l) for l in lines)}">'
                          f'<img src="{RAW}/m{i:02d}.svg" width="{w}" alt="{mdt:%B}"></a>')

    # ---------- activity card ----------
    agg = subprocess.run(
        ["gh", "api", "graphql", "-f", "query="
         'query($login:String!){user(login:$login){contributionsCollection{'
         "totalCommitContributions totalPullRequestContributions "
         "totalIssueContributions totalPullRequestReviewContributions "
         "totalRepositoryContributions}}}", "-f", f"login={USERNAME}"],
        capture_output=True, text=True)
    cc = json.loads(agg.stdout)["data"]["user"]["contributionsCollection"]
    AX = [("commits", cc["totalCommitContributions"]),
          ("pull requests", cc["totalPullRequestContributions"]),
          ("issues", cc["totalIssueContributions"]),
          ("reviews", cc["totalPullRequestReviewContributions"]),
          ("repos created", cc["totalRepositoryContributions"])]

    a = [f'<rect x="0" y="0" width="{ACT_W}" height="{ACT_H}" rx="8" fill="{CARD}"/>',
         f'<text x="16" y="26" fill="{MUTED}" font-size="10" letter-spacing="1.5">ACTIVITY</text>']
    rcx, rcy, R = 128, 124, 66
    pts5 = [(rcx + math.sin(i * 2 * math.pi / 5) * R,
             rcy - math.cos(i * 2 * math.pi / 5) * R) for i in range(5)]
    for frac in (1 / 3, 2 / 3, 1.0):
        ring = " ".join(f"{rcx + (px - rcx) * frac:.1f},{rcy + (py - rcy) * frac:.1f}"
                        for px, py in pts5)
        a.append(f'<polygon points="{ring}" fill="none" '
                 f'stroke="rgba(122,134,173,0.25)" stroke-dasharray="3 3"/>')
    poly = []
    for i, (_, v) in enumerate(AX):
        f = math.log10(v + 1) / 4
        poly.append(f"{rcx + math.sin(i * 2 * math.pi / 5) * R * f:.1f},"
                    f"{rcy - math.cos(i * 2 * math.pi / 5) * R * f:.1f}")
    a.append(f'<polygon points="{" ".join(poly)}" fill="rgba(255,200,55,0.22)" '
             f'stroke="{GOLD}" stroke-width="1.6"/>')
    for i, (name, v) in enumerate(AX):
        yy = 62 + i * 29
        a.append(f'<text x="290" y="{yy}" fill="{INK}" font-size="13" '
                 f'text-anchor="end">{v:,}</text>')
        a.append(f'<text x="300" y="{yy}" fill="{MUTED}" font-size="10.5">{name}</text>')
    svg_file("activity.svg", f"0 0 {ACT_W + ACT_GUT} {ACT_H}", "".join(a))
    act_tip = ("past year on GitHub&#10;" + " · ".join(f"{v:,} {n}" for n, v in AX)
               + "&#10;click → the constellation")

    # ---------- languages donut card ----------
    top_langs = {}
    for m in months:
        for k, n in m["langs"].items():
            top_langs[k] = top_langs.get(k, 0) + n
    ranked = sorted(top_langs.items(), key=lambda i: -i[1])
    tot = sum(top_langs.values()) or 1
    shown = [(lang, colr, n / tot) for (lang, colr), n in ranked[:5] if n / tot >= 0.005]

    d = [f'<rect x="0" y="0" width="{DON_W}" height="{ACT_H}" rx="8" fill="{CARD}"/>',
         f'<text x="16" y="26" fill="{MUTED}" font-size="10" letter-spacing="1.5">LANGUAGES</text>']
    dcx, dcy, r1, r2 = 105, 128, 44, 68
    a0 = -math.pi / 2
    for lang, colr, share in shown:
        a1 = a0 + share * 2 * math.pi
        large = 1 if (a1 - a0) > math.pi else 0
        p = lambda ang, r: (dcx + math.cos(ang) * r, dcy + math.sin(ang) * r)
        x1, y1 = p(a0, r2); x2, y2 = p(a1, r2)
        x3, y3 = p(a1, r1); x4, y4 = p(a0, r1)
        d.append(f'<path d="M{x1:.1f},{y1:.1f} A{r2},{r2} 0 {large} 1 {x2:.1f},{y2:.1f} '
                 f'L{x3:.1f},{y3:.1f} A{r1},{r1} 0 {large} 0 {x4:.1f},{y4:.1f} Z" '
                 f'fill="{lighten(colr)}" stroke="{CARD}" stroke-width="1.5"/>')
        a0 = a1
    d.append(f'<text x="{dcx}" y="{dcy + 5}" fill="{INK}" font-size="14" '
             f'font-weight="600" text-anchor="middle">{shown[0][0] if shown else ""}</text>')
    for i, (lang, colr, share) in enumerate(shown[:4]):
        yy = 66 + i * 30
        d.append(f'<rect x="222" y="{yy - 10}" width="10" height="10" rx="2" '
                 f'fill="{lighten(colr)}"/>')
        d.append(f'<text x="240" y="{yy}" fill="{INK}" font-size="12">{lang} '
                 f'<tspan fill="{MUTED}">{share * 100:.0f}%</tspan></text>')
    svg_file("langs.svg", f"0 0 {DON_W} {ACT_H}", "".join(d))
    lang_tip = ("share of this year's commits&#10;"
                + " · ".join(f"{lang} {share * 100:.0f}%" for lang, colr, share in shown))

    # ---------- quiet recents line ----------
    recents = fetch_recents()
    rec_tags = []
    if recents:
        def text_img(name, text, color, size=11):
            wpx = round(len(text) * size * 0.62) + 6
            svg_file(name, f"0 0 {wpx} 18",
                     f'<text x="3" y="13" fill="{color}" font-size="{size}" '
                     f'textLength="{wpx - 6}" lengthAdjust="spacingAndGlyphs">'
                     f'{htmlmod.escape(text)}</text>')
            return wpx
        w0 = text_img("r-label.svg", "recent notes:", MUTED)
        rec_tags.append(f'<img src="{RAW}/r-label.svg" width="{w0}" alt="recent notes:">')
        for i, r in enumerate(recents):
            t = ("· " if i else "") + r["title"]
            wpx = text_img(f"r{i}.svg", t, "#aab4d4")
            tip = esc(f"{r['course']} · {r['change'].lower()} · {r['date']}")
            rec_tags.append(f'<a href="{r["url"]}" title="{tip}">'
                            f'<img src="{RAW}/r{i}.svg" width="{wpx}" alt="{esc(r["title"])}"></a>')

    # ---------- README ----------
    readme = (
        "<p>" + "".join(month_tags) + "</p>\n"
        f'<p><a href="{GRAPH_URL}" title="{act_tip}">'
        f'<img src="{RAW}/activity.svg" width="{ACT_W + ACT_GUT}" alt="activity"></a>'
        f'<a href="{GRAPH_URL}" title="{lang_tip}">'
        f'<img src="{RAW}/langs.svg" width="{DON_W}" alt="languages"></a></p>\n'
        + ("<p>" + "".join(rec_tags) + "</p>\n" if rec_tags else "")
    )
    with open("README.md", "w") as f:
        f.write(readme)
    print(f"wrote README.md + {OUT_DIR}: {len(months)} month slices, "
          f"{len(shown)} languages, {len(recents)} recents")


if __name__ == "__main__":
    main()
