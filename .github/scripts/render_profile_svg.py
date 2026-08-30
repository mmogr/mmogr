#!/usr/bin/env python3
"""Render the animated profile SVG: a self-playing tour of the contribution
terrain (README <img> tags allow CSS animation but no interactivity, so the
"hover" is choreographed).

Scenes on one looping timeline:
  1. 3D terrain; a cursor tours the months, raising a tooltip card per month
  2. side-on bar chart of contributions by month, sweeping highlight
  3. bar chart by week, sweeping highlight
  4. language share of commits (per-repo commits x repository primary language)

Uses build_payload() from contribs.py (same data as the interactive page).
Env: GH_TOKEN, GH_USERNAME, TZ_NAME; OUT_SVG for the output path.
"""
import json
import math
import os
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from contribs import USERNAME, build_payload, gql  # noqa: E402

OUT_SVG = os.environ.get("OUT_SVG", "profile-terrain/terrain.svg")

W, H = 900, 460
T = 44.0  # loop seconds
RAMP = [(25, 60, 130), (25, 90, 210), (25, 120, 220), (25, 150, 230), (25, 165, 240)]
GOLD = "#ffc837"
INK = "#e8ecff"
MUTED = "#7a86ad"
BG = "#00000f"
MONO = "ui-monospace,'Cascadia Mono','SFMono-Regular',Consolas,monospace"

pct = lambda t: round(t / T * 100, 3)
rgb = lambda c, f=1.0: f"rgb({int(c[0]*f)},{int(c[1]*f)},{int(c[2]*f)})"


def emit_kf(name, pairs, prop="opacity"):
    """pairs: [(seconds, value)] -> @keyframes with sorted, deduped stops."""
    stops, seen = [], set()
    for t, v in pairs:
        p = min(100.0, max(0.0, pct(t)))
        if p in seen:
            continue
        seen.add(p)
        stops.append((p, v))
    stops.sort()
    body = "".join(f"{p}%{{{prop}:{v}}}" for p, v in stops)
    return f"@keyframes {name}{{{body}}}"


def hold(a, b, ramp=0.3, lo=0, hi=1):
    """opacity pairs: lo outside [a,b], hi inside, with short ramps."""
    return [(0, lo), (a - ramp, lo), (a, hi), (b, hi), (b + ramp, lo), (T, lo)]


def main():
    payload = build_payload()
    days = payload["days"]
    maxc = max(1, max(d["count"] for d in days))

    # ---- grid ----
    bars, col = [], 0
    for d in days:
        row = datetime.fromisoformat(d["date"]).weekday()  # Mon=0
        row = (row + 1) % 7                                # GitHub: Sun=0
        if row == 0 and bars:
            col += 1
        bars.append({**d, "col": col, "row": row})
    COLS = col + 1

    # ---- chronological month groups (SVG paint order must stay by date) ----
    groups = []  # [{key, label, bars, total, top_repo}]
    for b in bars:
        key = b["date"][:7]
        if not groups or groups[-1]["key"] != key:
            groups.append({"key": key, "bars": []})
        groups[-1]["bars"].append(b)
    for g in groups:
        g["label"] = datetime.fromisoformat(g["bars"][0]["date"]).strftime("%b")
        g["total"] = sum(b["count"] for b in g["bars"])
        repo_commits = {}
        for b in g["bars"]:
            for repo, n in (b.get("d", {}).get("c") or []):
                repo_commits[repo] = repo_commits.get(repo, 0) + n
        g["top_repo"] = max(repo_commits.items(), key=lambda x: x[1]) if repo_commits else None

    # merge a leading partial month into its same-name month at the end
    merged = list(range(len(groups)))  # group index -> tour slot index
    slots = groups
    if len(groups) == 13:
        first, last = groups[0], groups[-1]
        last["total"] += first["total"]
        if first["top_repo"] and (not last["top_repo"] or first["top_repo"][1] > last["top_repo"][1]):
            last["top_repo"] = first["top_repo"]
        slots = groups[1:]
        merged = [len(slots) - 1] + list(range(len(slots)))

    # ---- projection (same camera as the interactive page) ----
    yaw, pitch = -0.65, 0.62
    cy, sy = math.cos(yaw), math.sin(yaw)
    sp, cp = math.sin(pitch), math.cos(pitch)
    base = min(W / (COLS + 14), H / ((COLS + 14) * 0.55))
    max_h = base * 8
    ox, oy = W / 2, H * 0.54
    cx0, cy0 = COLS / 2, 3.5

    def p3(x, y, z=0.0):
        xr = x * cy + y * sy
        yr = -x * sy + y * cy
        return (ox + xr * base, oy + yr * base * sp - z * cp, yr)

    half = 0.43

    def bar_faces(b):
        x, y = b["col"] - cx0 + 0.5, b["row"] - cy0
        h = max(0.035, b["count"] / maxc) * max_h
        c0, c1, r0, r1 = x - half, x + half, y - half, y + half
        P = [p3(c0, r0), p3(c1, r0), p3(c1, r1), p3(c0, r1),
             p3(c0, r0, h), p3(c1, r0, h), p3(c1, r1, h), p3(c0, r1, h)]
        depth = (P[0][2] + P[2][2]) / 2
        color = RAMP[b["level"]]
        quads = [((4, 5, 6, 7), 1.0, True), ((0, 1, 5, 4), 0.62, False),
                 ((1, 2, 6, 5), 0.8, False), ((2, 3, 7, 6), 0.62, False),
                 ((3, 0, 4, 7), 0.8, False)]
        out = []
        for idx, f, top in quads:
            p = [P[i] for i in idx]
            area = ((p[1][0] - p[0][0]) * (p[2][1] - p[0][1])
                    - (p[2][0] - p[0][0]) * (p[1][1] - p[0][1]))
            if area >= 0 and not top:
                continue
            pts = " ".join(f"{q[0]:.1f},{q[1]:.1f}" for q in p)
            out.append((depth, f'<polygon points="{pts}" fill="{rgb(color, f)}"/>'))
        return out, P

    css, defs, scenes = [], [], []

    # ---- background stars (seeded) ----
    seed = 20260831
    def rnd():
        nonlocal seed
        seed = (seed * 1664525 + 1013904223) % 2**32
        return seed / 2**32
    star_g = ['<g class="tw1">'], ['<g class="tw2">']
    for i in range(90):
        x, y, r = rnd() * W, rnd() * H * 0.85, 0.5 + rnd()
        star_g[i % 2].append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{r:.1f}" fill="#eeeeff"/>')
    stars = "".join(star_g[0]) + "</g>" + "".join(star_g[1]) + "</g>"
    css.append("@keyframes twk{0%,100%{opacity:.25}50%{opacity:.85}}")
    css.append(f".tw1{{animation:twk 4.6s ease-in-out infinite}}")
    css.append(f".tw2{{animation:twk 6.2s ease-in-out infinite 2s}}")

    # ================= scene 1: terrain + month tour =================
    tour_s, tour_e = 2.0, 20.0
    slot_d = (tour_e - tour_s) / len(slots)
    slot_at = lambda si: (tour_s + si * slot_d, tour_s + (si + 1) * slot_d)

    ground = " ".join(f"{p[0]:.0f},{p[1]:.0f}" for p in
                      [p3(-cx0 - 1.2, -cy0 - 1.2), p3(cx0 + 1.2, -cy0 - 1.2),
                       p3(cx0 + 1.2, cy0 + 1.2), p3(-cx0 - 1.2, cy0 + 1.2)])
    s1 = [f'<polygon points="{ground}" fill="rgba(25,60,130,0.10)" '
          f'stroke="rgba(25,120,220,0.30)"/>']

    anchors = {}  # slot -> (x, y) card anchor
    for gi, g in enumerate(groups):
        faces, tops = [], []
        for b in sorted(g["bars"], key=lambda b: (b["col"], b["row"])):
            fs, P = bar_faces(b)
            faces.extend(fs)
            tops.append(min(p[1] for p in P[4:]))
        faces.sort(key=lambda f: f[0])
        xs = [p3(b["col"] - cx0 + 0.5, b["row"] - cy0)[0] for b in g["bars"]]
        si = merged[gi]
        ax = sum(xs) / len(xs)
        ay = min(tops) - 14
        if si in anchors:  # merged month: keep the larger part's anchor
            if len(g["bars"]) > 7:
                anchors[si] = (ax, ay)
        else:
            anchors[si] = (ax, ay)
        a, b_ = slot_at(si)
        css.append(emit_kf(f"mg{gi}", [(0, 1), (tour_s - 0.3, 1), (tour_s, 0.4),
                                       (a - 0.2, 0.4), (a + 0.02, 1),
                                       (b_, 1), (b_ + 0.25, 0.4),
                                       (tour_e, 0.4), (tour_e + 0.3, 1), (T, 1)]))
        css.append(f".mg{gi}{{animation:mg{gi} {T}s linear infinite}}")
        s1.append(f'<g class="mg{gi}">' + "".join(f[1] for f in faces) + "</g>")

    # month/weekday labels on the ground
    lbl = []
    seen_m = set()
    for b in bars:
        m = b["date"][:7]
        if m not in seen_m and 0 < b["col"] < COLS - 2:
            seen_m.add(m)
            p = p3(b["col"] - cx0 + 0.5, cy0 + 1.7)
            lbl.append(f'<text x="{p[0]:.0f}" y="{p[1]:.0f}" class="lab">'
                       f'{datetime.fromisoformat(b["date"]).strftime("%b")}</text>')
    for row, name in [(1, "Mon"), (3, "Wed"), (5, "Fri")]:
        p = p3(-cx0 - 1.8, row - cy0)
        lbl.append(f'<text x="{p[0]:.0f}" y="{p[1]:.0f}" class="lab">{name}</text>')
    s1.append("".join(lbl))

    # tooltip cards + cursor
    cw, ch = 168, 46
    cur_pairs = []
    for si, g in enumerate(slots):
        a, b_ = slot_at(si)
        ax, ay = anchors[si]
        ax = min(max(ax, 20 + cw / 2), W - 20 - cw / 2)
        ay = max(ay, 70)
        year = g["key"][:4]
        top = (f'{g["top_repo"][0]} · {g["top_repo"][1]} commits'
               if g["top_repo"] else "quiet month")
        s1.append(
            f'<g class="card c{si}">'
            f'<rect x="{ax - cw/2:.0f}" y="{ay - ch - 16:.0f}" width="{cw}" height="{ch}" '
            f'rx="6" fill="rgba(10,16,42,0.92)" stroke="rgba(122,134,173,0.35)"/>'
            f'<text x="{ax:.0f}" y="{ay - ch + 2:.0f}" class="cardt">'
            f'<tspan fill="{GOLD}">{g["total"]}</tspan> in {g["label"]} {year}</text>'
            f'<text x="{ax:.0f}" y="{ay - ch + 20:.0f}" class="cards">{top}</text>'
            f'</g>')
        css.append(emit_kf(f"c{si}", hold(a + 0.1, b_ - 0.05, 0.2)))
        css.append(f".c{si}{{animation:c{si} {T}s linear infinite}}")
        cur_pairs += [(a, f"translate({ax:.0f}px,{ay:.0f}px)"),
                      (b_ - 0.2, f"translate({ax:.0f}px,{ay:.0f}px)")]
    css.append(emit_kf("curm", cur_pairs, prop="transform"))
    css.append(emit_kf("curo", hold(tour_s, tour_e, 0.3)))
    css.append(f".cursor{{animation:curm {T}s ease-in-out infinite,curo {T}s linear infinite}}")
    s1.append(f'<g class="cursor"><path d="M0,0 l5,14 l3,-5 l6,3 z" '
              f'fill="{INK}" stroke="{BG}"/></g>')

    scenes.append(("s1", 0.0, 21.0, "".join(s1)))

    # ================= scene 2: by month =================
    mvals = [g["total"] for g in slots]
    s2, sweep = chart_scene("by month", [g["label"] for g in slots], mvals,
                            22.0, 29.5, "m", css)
    scenes.append(("s2", 21.0, 30.0, s2))

    # ================= scene 3: by week =================
    weeks = {}
    for b in bars:
        weeks.setdefault(b["col"], 0)
        weeks[b["col"]] += b["count"]
    wvals = [weeks[c] for c in sorted(weeks)]
    s3, _ = chart_scene("by week", ["" for _ in wvals], wvals, 31.0, 35.5, "w", css)
    scenes.append(("s3", 30.0, 36.0, s3))

    # ================= scene 4: languages =================
    repo_commits = {}
    for d in days:
        for repo, n in (d.get("d", {}).get("c") or []):
            if repo != "(private)":
                repo_commits[repo] = repo_commits.get(repo, 0) + n
    langs = fetch_languages(repo_commits)
    s4 = lang_scene(langs, 36.0, 43.0, css)
    scenes.append(("s4", 36.0, 43.5, s4))

    # ---- scene visibility ----
    for name, a, b, _ in scenes:
        if a == 0.0:
            css.append(emit_kf(name, [(0, 1), (b - 0.6, 1), (b, 0), (T - 1.2, 0), (T, 1)]))
        else:
            css.append(emit_kf(name, hold(a, b - 0.5, 0.5)))
        css.append(f".{name}{{animation:{name} {T}s linear infinite}}")

    header = (f'<text x="24" y="34" class="hdr">@{payload["user"]}</text>'
              f'<text x="24" y="52" class="sub">contribution terrain · '
              f'{sum(d["count"] for d in days)} contributions this year · '
              f'click for the interactive version</text>')

    style = (
        f"text{{font-family:{MONO}}}"
        f".hdr{{fill:{INK};font-size:17px;font-weight:600;letter-spacing:.5px}}"
        f".sub{{fill:{MUTED};font-size:10px;letter-spacing:.5px}}"
        f".lab{{fill:{MUTED};font-size:10px;text-anchor:middle}}"
        f".cardt{{fill:{INK};font-size:12px;text-anchor:middle;font-weight:600}}"
        f".cards{{fill:{MUTED};font-size:9.5px;text-anchor:middle}}"
        f".card,.cursor{{opacity:0}}"
        f".s2,.s3,.s4{{opacity:0}}"
        + "".join(css)
        + "@media (prefers-reduced-motion:reduce){*{animation:none!important}"
          ".s2,.s3,.s4,.card,.cursor{display:none}"
          + "".join(f".mg{gi}{{opacity:1}}" for gi in range(len(groups))) + "}"
    )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'font-family="{MONO}" role="img" '
        f'aria-label="Animated tour of {payload["user"]}\'s GitHub contributions">'
        f"<style>{style}</style>"
        f'<rect width="{W}" height="{H}" fill="{BG}"/>'
        + stars + header
        + "".join(f'<g class="{n}">{body}</g>' for n, _, _, body in scenes)
        + "</svg>"
    )

    os.makedirs(os.path.dirname(OUT_SVG) or ".", exist_ok=True)
    with open(OUT_SVG, "w") as f:
        f.write(svg)
    print(f"wrote {OUT_SVG}: {len(svg)} bytes, {len(slots)} month slots, "
          f"{len(wvals)} weeks, {len(langs)} languages")


def chart_scene(title, labels, vals, sweep_s, sweep_e, tag, css):
    """Side-on bar chart with a sweeping gold highlight and value labels."""
    n = len(vals)
    vmax = max(1, max(vals))
    left, right, base_y, ph = 80, 80, 380, 230
    bw = (W - left - right) / n
    slot = (sweep_e - sweep_s) / n
    parts = [f'<text x="24" y="86" class="hdr" font-size="14">{title}</text>',
             f'<line x1="{left}" y1="{base_y}" x2="{W - right}" y2="{base_y}" '
             f'stroke="rgba(122,134,173,0.4)"/>']
    for i, v in enumerate(vals):
        h = max(2, v / vmax * ph)
        x = left + i * bw
        a, b = sweep_s + i * slot, sweep_s + (i + 1) * slot
        css.append(emit_kf(f"{tag}b{i}",
                           [(0, "0.55"), (a - 0.1, "0.55"), (a, "1"), (b, "1"),
                            (b + 0.1, "0.55"), (T, "0.55")]))
        css.append(f".{tag}b{i}{{animation:{tag}b{i} {T}s linear infinite}}")
        css.append(emit_kf(f"{tag}v{i}", hold(a, b, 0.08)))
        css.append(f".{tag}v{i}{{animation:{tag}v{i} {T}s linear infinite;opacity:0}}")
        fill = rgb(RAMP[3])
        parts.append(
            f'<rect class="{tag}b{i}" x="{x + bw*0.12:.1f}" y="{base_y - h:.1f}" '
            f'width="{bw*0.76:.1f}" height="{h:.1f}" rx="2" fill="{fill}"/>')
        parts.append(
            f'<rect class="{tag}v{i}" x="{x + bw*0.12:.1f}" y="{base_y - h:.1f}" '
            f'width="{bw*0.76:.1f}" height="{h:.1f}" rx="2" fill="{GOLD}"/>')
        parts.append(f'<text class="{tag}v{i} lab" x="{x + bw/2:.1f}" '
                     f'y="{base_y - h - 8:.1f}" fill="{INK}" font-size="12">{v}</text>')
        if labels[i]:
            parts.append(f'<text class="lab" x="{x + bw/2:.1f}" y="{base_y + 18}">'
                         f'{labels[i]}</text>')
    return "".join(parts), None


def fetch_languages(repo_commits):
    """[(lang, color, share)] by commit share across the owner's repos."""
    q = ('query($login:String!){user(login:$login){'
         'repositories(first:100,ownerAffiliations:OWNER){'
         'nodes{name primaryLanguage{name color}}}}}')
    out = subprocess.run(["gh", "api", "graphql", "-f", f"query={q}",
                          "-f", f"login={USERNAME}"],
                         capture_output=True, text=True)
    if out.returncode:
        sys.exit(f"language query failed: {out.stderr[:500]}")
    nodes = json.loads(out.stdout)["data"]["user"]["repositories"]["nodes"]
    lang_of = {n["name"]: n["primaryLanguage"] for n in nodes if n["primaryLanguage"]}
    per = {}
    for repo, n in repo_commits.items():
        pl = lang_of.get(repo)
        if pl:
            key = (pl["name"], pl["color"] or "#8b949e")
            per[key] = per.get(key, 0) + n
    total = sum(per.values()) or 1
    ranked = sorted(per.items(), key=lambda x: -x[1])[:5]
    return [(name, color, n / total) for (name, color), n in ranked]


def lang_scene(langs, a, b, css):
    parts = [f'<text x="24" y="86" class="hdr" font-size="14">languages · '
             f'share of this year\'s commits</text>']
    y0, rh = 130, 52
    bar_w = W - 340
    for i, (name, color, share) in enumerate(langs):
        y = y0 + i * rh
        w = max(4, share * bar_w)
        d = a + 0.4 + i * 0.35
        css.append(emit_kf(f"lg{i}", [(0, "scaleX(0)"), (d, "scaleX(0)"),
                                      (d + 0.8, "scaleX(1)"), (T, "scaleX(1)")],
                           prop="transform"))
        css.append(f".lg{i}{{animation:lg{i} {T}s cubic-bezier(.2,.7,.3,1) infinite;"
                   f"transform-origin:170px {y}px}}")
        parts.append(f'<text x="160" y="{y + 15}" fill="{INK}" font-size="13" '
                     f'text-anchor="end">{name}</text>')
        parts.append(f'<rect class="lg{i}" x="170" y="{y}" width="{w:.0f}" height="20" '
                     f'rx="3" fill="{color}"/>')
        parts.append(f'<text x="{178 + w:.0f}" y="{y + 15}" fill="{MUTED}" '
                     f'font-size="12">{share * 100:.0f}%</text>')
    return "".join(parts)


if __name__ == "__main__":
    main()
