#!/usr/bin/env python3
"""Generate a self-contained kanban board HTML from a plan file's tracker tables.

This is the deterministic companion to the AI digest: no LLM, no hallucinated
cards. It parses the three append-only tracker tables in a plan markdown file
(open questions, action items, decisions log) and renders ONE static HTML file
with no external runtime dependencies. Open it from the filesystem (file://) -
nothing is hosted.

Expected row formats (the canonical dev-updates tracker convention):
    - `Q{id} | addressee | status | date | question (+ answer inline after ->)`
    - `T{id} | owner    | status | date | description (+ outcome inline after ->)`
    - `D{id} | YYYY-MM-DD | decision + rationale`

Questions statuses: open, answered. Task statuses: open, in_progress, blocked,
done. Rows are wrapped in backticks and bulleted ("- `...`"). The "->" marker
(rendered as the arrow glyph in the source) separates the original ask from the
resolution; we split on it to show the outcome as a distinct block.

Theme (colors, fonts, title, logo) is injected via --theme so the generic
action is not branded to any one client. Defaults are a neutral palette.

Usage:
    python board.py --plan PLAN.md --out board.html \
        [--title "Project Board"] [--subtitle "..."] \
        [--repo-url https://github.com/org/repo] [--theme theme.json]
"""

import argparse
import html
import json
import re
import sys
from datetime import date
from pathlib import Path

# --- Section boundaries -----------------------------------------------------
# The whole tracker block is the "## 7 ..." section; it ends at the next
# top-level H2 that isn't a "7.x" sub-heading, or EOF. Within it we track which
# 7.x sub-section we're in (for question grouping) but classify each row purely
# by its id prefix - so a row misfiled under the wrong heading still lands in
# the right lane instead of vanishing.
H_SECTION = re.compile(r"^##\s+7[.\s]", re.I)
H_QUESTIONS = re.compile(r"^#{2,4}\s*7\.1\b", re.I)
H_TASKS = re.compile(r"^#{2,4}\s*7\.2\b", re.I)
H_DECISIONS = re.compile(r"^#{2,4}\s*7\.3\b", re.I)
H_SUB = re.compile(r"^#{3,4}\s*7\.[123]\b", re.I)  # any 7.x sub-heading
H_END = re.compile(r"^##\s+(?!7[.\s])\S")  # next top-level H2 that isn't 7.x

# A tracker row: bulleted, backtick-wrapped, starts with a Q/T/D id. The
# closing backtick is optional so an occasional unterminated row (a real
# formatting slip seen in the wild) is still parsed rather than dropped.
ROW = re.compile(r"^\s*-\s*`([QTD])(\d+)\s*\|\s*(.+?)`?\s*$")
# Group labels inside the questions section (bold line or #### subheading).
GROUP_BOLD = re.compile(r"^\s*\*\*(.+?)\*\*\s*$")
GROUP_SUB = re.compile(r"^####\s+(?!7\.)(.+?)\s*$")

ARROW = "→"  # the resolution marker used in the tracker rows


def inline_md(text: str) -> str:
    """Minimal, safe markdown -> HTML for tracker prose: escape first, then
    re-introduce links, bold and inline code. Escaping before linkifying means
    any "&" inside a URL becomes "&amp;", which is the correct form in an
    href, so the order is deliberate."""
    esc = html.escape(text, quote=False)
    esc = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", esc)
    esc = re.sub(r"`([^`]+)`", r"<code>\1</code>", esc)
    esc = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r'<a href="\2" target="_blank" rel="noopener">\1</a>',
        esc,
    )
    return esc


def slice_section(lines, start_re, *stop_res):
    """Return (start_index, list_of_lines) for the block opened by start_re and
    closed by the first of stop_res (or EOF)."""
    start = None
    for i, ln in enumerate(lines):
        if start_re.match(ln):
            start = i
            break
    if start is None:
        return None, []
    out = []
    for j in range(start + 1, len(lines)):
        if any(s.match(lines[j]) for s in stop_res):
            break
        out.append((j, lines[j]))
    return start, out


def parse_qt(kind, num, body, line_no):
    """Parse a Q or T row body: 'addressee | status | date | desc...'."""
    parts = body.split(" | ", 3)
    addressee = parts[0].strip() if len(parts) > 0 else ""
    status = parts[1].strip().lower() if len(parts) > 1 else ""
    date = parts[2].strip() if len(parts) > 2 else ""
    desc = parts[3].strip() if len(parts) > 3 else ""
    ask, _, outcome = desc.partition(ARROW)
    return {
        "type": kind,
        "id": f"{kind}{num}",
        "num": int(num),
        "owner": addressee,
        "status": status,
        "date": date,
        "ask": ask.strip(),
        "outcome": outcome.strip(),
        "line": line_no + 1,
    }


def parse_d(num, body, line_no):
    """Parse a D row body: 'YYYY-MM-DD | decision...'."""
    parts = body.split(" | ", 1)
    date = parts[0].strip() if len(parts) > 0 else ""
    desc = parts[1].strip() if len(parts) > 1 else ""
    return {
        "type": "D",
        "id": f"D{num}",
        "num": int(num),
        "date": date,
        "ask": desc,
        "line": line_no + 1,
    }


def parse(plan_text):
    """Single pass over the '## 7' tracker section. Rows are classified by id
    prefix (Q/T/D), not by which sub-heading they sit under, so a misfiled row
    is still placed correctly. Question grouping uses the most recent bold /
    '####' label within section 7.1."""
    lines = plan_text.splitlines()
    questions, tasks, decisions = [], [], []
    _, body = slice_section(lines, H_SECTION, H_END)

    sub = ""        # "7.1" / "7.2" / "7.3"
    group = ""      # current question group label
    for line_no, ln in body:
        if H_QUESTIONS.match(ln):
            sub, group = "7.1", ""
            continue
        if H_TASKS.match(ln):
            sub, group = "7.2", ""
            continue
        if H_DECISIONS.match(ln):
            sub, group = "7.3", ""
            continue
        gb = GROUP_BOLD.match(ln) or GROUP_SUB.match(ln)
        if gb:
            group = gb.group(1).strip()
            continue
        m = ROW.match(ln)
        if not m:
            continue
        kind, num, rowbody = m.group(1), m.group(2), m.group(3)
        if kind == "Q":
            item = parse_qt("Q", num, rowbody, line_no)
            item["group"] = group if sub != "7.3" else ""
            questions.append(item)
        elif kind == "T":
            tasks.append(parse_qt("T", num, rowbody, line_no))
        elif kind == "D":
            decisions.append(parse_d(num, rowbody, line_no))

    return questions, tasks, decisions


# --- Theme ------------------------------------------------------------------
DEFAULT_THEME = {
    "colors": {
        "paper": "#f7f7f5", "ink": "#16181d", "body": "#33363d",
        "muted": "#6b6e76", "border": "#e3e3df", "panel": "#ffffff",
        "gold": "#8a6d2f", "gold_bright": "#c9a04a", "gold_pale": "#f3ecda",
        "navy": "#13203a", "navy_deep": "#0a1326", "navy_border": "#26344f",
        "up": "#2d7a3d", "down": "#a83232", "open": "#5b6473",
    },
    "fonts": {
        "display": "'Instrument Serif', Georgia, serif",
        "sans": "'IBM Plex Sans', system-ui, sans-serif",
        "mono": "'IBM Plex Mono', ui-monospace, monospace",
    },
    "font_links": [
        "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600;700&family=Instrument+Serif&display=swap",
    ],
    "logo_svg": "",
}


def merge_theme(overrides):
    t = json.loads(json.dumps(DEFAULT_THEME))  # deep copy
    for k, v in (overrides or {}).items():
        if isinstance(v, dict) and isinstance(t.get(k), dict):
            t[k].update(v)
        else:
            t[k] = v
    return t


# --- Card rendering ---------------------------------------------------------
STATUS_LABEL = {
    "open": "Open", "in_progress": "In progress", "blocked": "Blocked",
    "done": "Done", "answered": "Answered",
}


def card_html(item, repo_url, plan_path):
    ask_html = inline_md(item["ask"])
    out_html = inline_md(item["outcome"]) if item.get("outcome") else ""
    owner = item.get("owner", "")
    owner_chip = (
        f'<span class="chip owner">{html.escape(owner)}</span>' if owner else ""
    )
    group = item.get("group", "")
    group_chip = (
        f'<span class="chip group">{html.escape(group)}</span>' if group else ""
    )
    deep = (
        f'{repo_url}/blob/main/{plan_path}#L{item["line"]}' if repo_url else ""
    )
    deep_link = (
        f'<a class="src" href="{deep}" target="_blank" rel="noopener" '
        f'title="View in plan">{ARROW}</a>' if deep else ""
    )
    outcome_block = (
        f'<div class="outcome"><span class="outcome-tag">'
        f'{"Answered" if item["type"] == "Q" else "Resolved"}</span>{out_html}</div>'
        if out_html else ""
    )
    # data-search lowercased haystack for the filter box
    haystack = html.escape(
        f'{item["id"]} {owner} {group} {item["ask"]} {item.get("outcome", "")}'.lower(),
        quote=True,
    )
    return f'''<article class="card s-{item['status']}" data-type="{item['type']}" data-status="{item['status']}" data-owner="{html.escape(owner, quote=True)}" data-search="{haystack}">
  <div class="card-top">
    <span class="id">{item['id']}</span>
    {owner_chip}{group_chip}
    <span class="date">{html.escape(item['date'])}</span>
    {deep_link}
  </div>
  <div class="card-body">{ask_html}</div>
  {outcome_block}
</article>'''


def column(title, status_key, items, repo_url, plan_path, collapsed=False):
    cards = "\n".join(
        card_html(i, repo_url, plan_path) for i in items
    ) or '<p class="empty">Nothing here.</p>'
    cls = "col collapsed" if collapsed else "col"
    return f'''<section class="{cls}" data-col="{status_key}">
  <header class="col-head"><span class="col-title">{title}</span><span class="col-count">{len(items)}</span></header>
  <div class="col-cards">{cards}</div>
</section>'''


def decision_html(item, repo_url, plan_path):
    deep = f'{repo_url}/blob/main/{plan_path}#L{item["line"]}' if repo_url else ""
    deep_link = (
        f'<a class="src" href="{deep}" target="_blank" rel="noopener" title="View in plan">{ARROW}</a>'
        if deep else ""
    )
    haystack = html.escape(f'{item["id"]} {item["ask"]}'.lower(), quote=True)
    return f'''<article class="decision" data-type="D" data-search="{haystack}">
  <div class="d-top"><span class="id">{item['id']}</span><span class="date">{html.escape(item['date'])}</span>{deep_link}</div>
  <div class="d-body">{inline_md(item['ask'])}</div>
</article>'''


# --- HTML assembly ----------------------------------------------------------
def build_html(questions, tasks, decisions, theme, title, subtitle, repo_url, plan_path, as_of):
    c = theme["colors"]
    f = theme["fonts"]
    font_links = "\n  ".join(
        f'<link rel="stylesheet" href="{html.escape(u)}">' for u in theme.get("font_links", [])
    )
    if theme.get("font_links"):
        font_links = (
            '<link rel="preconnect" href="https://fonts.googleapis.com">\n  '
            '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n  '
            + font_links
        )

    # task columns
    by = lambda st: sorted([t for t in tasks if t["status"] == st], key=lambda x: -x["num"])
    t_open, t_prog = by("open"), by("in_progress")
    t_block, t_done = by("blocked"), by("done")
    task_cols = "\n".join([
        column("Open", "open", t_open, repo_url, plan_path),
        column("In progress", "in_progress", t_prog, repo_url, plan_path),
        column("Blocked", "blocked", t_block, repo_url, plan_path),
        column("Done", "done", t_done, repo_url, plan_path, collapsed=True),
    ])

    q_open = sorted([q for q in questions if q["status"] == "open"], key=lambda x: -x["num"])
    q_ans = sorted([q for q in questions if q["status"] == "answered"], key=lambda x: -x["num"])
    q_cols = "\n".join([
        column("Open questions", "open", q_open, repo_url, plan_path),
        column("Answered", "answered", q_ans, repo_url, plan_path, collapsed=True),
    ])

    d_sorted = sorted(decisions, key=lambda x: -x["num"])
    decisions_html = "\n".join(decision_html(d, repo_url, plan_path) for d in d_sorted) \
        or '<p class="empty">No decisions logged.</p>'

    # "Updated" stamp. The caller supplies as_of (in CI: the plan file's last
    # commit date - stable per content, never a future scheduling date). We do
    # NOT use max(row date) because tracker rows legitimately carry future
    # "revisit on" dates that would misreport freshness.
    last_updated = as_of

    owners = sorted({t["owner"] for t in tasks if t["owner"]}
                    | {q["owner"] for q in questions if q["owner"]})
    owner_opts = "\n".join(f'<option value="{html.escape(o, quote=True)}">{html.escape(o)}</option>' for o in owners)

    kpis = [
        ("Open tasks", len(t_open), "open"),
        ("In progress", len(t_prog), "in_progress"),
        ("Blocked", len(t_block), "blocked"),
        ("Done", len(t_done), "done"),
        ("Open questions", len(q_open), "qopen"),
        ("Decisions", len(decisions), "dec"),
    ]
    kpi_html = "\n".join(
        f'<div class="kpi k-{k}"><span class="kpi-n">{n}</span><span class="kpi-l">{lbl}</span></div>'
        for lbl, n, k in kpis
    )

    logo = theme.get("logo_svg", "") or f'<span class="wordmark">{html.escape(title)}</span>'

    return TEMPLATE \
        .replace("%%TITLE%%", html.escape(title)) \
        .replace("%%SUBTITLE%%", html.escape(subtitle or "")) \
        .replace("%%FONT_LINKS%%", font_links) \
        .replace("%%LOGO%%", logo) \
        .replace("%%LAST_UPDATED%%", html.escape(last_updated)) \
        .replace("%%PLAN_LINK%%", f'{repo_url}/blob/main/{plan_path}' if repo_url else "#") \
        .replace("%%KPIS%%", kpi_html) \
        .replace("%%OWNER_OPTS%%", owner_opts) \
        .replace("%%TASK_COLS%%", task_cols) \
        .replace("%%Q_COLS%%", q_cols) \
        .replace("%%DECISIONS%%", decisions_html) \
        .replace("/*%%VARS%%*/", "".join([
            f"--paper:{c['paper']};--ink:{c['ink']};--body:{c['body']};",
            f"--muted:{c['muted']};--border:{c['border']};--panel:{c['panel']};",
            f"--gold:{c['gold']};--gold-bright:{c['gold_bright']};--gold-pale:{c['gold_pale']};",
            f"--navy:{c['navy']};--navy-deep:{c['navy_deep']};--navy-border:{c['navy_border']};",
            f"--up:{c['up']};--down:{c['down']};--open:{c['open']};",
            f"--f-display:{f['display']};--f-sans:{f['sans']};--f-mono:{f['mono']};",
        ]))


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>%%TITLE%%</title>
%%FONT_LINKS%%
<style>
:root{/*%%VARS%%*/}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--paper);color:var(--body);font-family:var(--f-sans);font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased}
a{color:var(--gold)}
code{font-family:var(--f-mono);font-size:.86em;background:rgba(0,0,0,.05);padding:.05em .35em;border-radius:3px}

/* header */
.top{position:sticky;top:0;z-index:30;background:var(--navy);color:#fff;padding:18px 24px 14px;border-bottom:1px solid var(--navy-border)}
.top-row{display:flex;align-items:baseline;gap:16px;flex-wrap:wrap}
.wordmark{font-family:var(--f-display);font-size:30px;letter-spacing:.5px;color:#fff}
.top h1{font-family:var(--f-display);font-weight:400;font-size:30px;margin:0;color:#fff}
.top .sub{color:rgba(255,255,255,.62);font-size:13px}
.top .meta{margin-left:auto;display:flex;gap:18px;align-items:baseline;font-size:12px;color:rgba(255,255,255,.6)}
.top .meta a{color:var(--gold-bright)}
.updated{font-family:var(--f-mono)}

/* kpis */
.kpis{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
.kpi{background:rgba(255,255,255,.06);border:1px solid var(--navy-border);border-radius:8px;padding:8px 14px;display:flex;flex-direction:column;min-width:96px}
.kpi-n{font-family:var(--f-mono);font-size:22px;font-weight:600;color:#fff;line-height:1}
.kpi-l{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:rgba(255,255,255,.55);margin-top:4px}
.k-in_progress .kpi-n{color:var(--gold-bright)}
.k-blocked .kpi-n{color:#e98b8b}
.k-done .kpi-n{color:#7fce8f}

/* toolbar */
.toolbar{position:sticky;top:0;z-index:20;background:var(--paper);border-bottom:1px solid var(--border);padding:12px 24px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.toolbar input[type=search],.toolbar select{font-family:var(--f-sans);font-size:13px;padding:7px 11px;border:1px solid var(--border);border-radius:7px;background:var(--panel);color:var(--ink)}
.toolbar input[type=search]{min-width:230px}
.toggles{display:flex;gap:6px}
.toggle{font-size:12px;padding:6px 12px;border:1px solid var(--border);border-radius:999px;background:var(--panel);cursor:pointer;user-select:none;color:var(--muted)}
.toggle.on{background:var(--ink);color:#fff;border-color:var(--ink)}
.toggle.t-Q.on{background:var(--navy);border-color:var(--navy)}
.toggle.t-D.on{background:var(--gold);border-color:var(--gold)}
.spacer{flex:1}
.count-live{font-size:12px;color:var(--muted);font-family:var(--f-mono)}

/* sections */
main{padding:24px;max-width:1700px;margin:0 auto}
.section-head{display:flex;align-items:baseline;gap:12px;margin:30px 0 14px}
.section-head:first-child{margin-top:6px}
.section-head h2{font-family:var(--f-display);font-weight:400;font-size:24px;color:var(--ink);margin:0}
.section-head .hint{font-size:12px;color:var(--muted)}

/* kanban */
.board{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;align-items:start}
.board.q{grid-template-columns:1fr 1fr;max-width:980px}
.col{background:rgba(0,0,0,.018);border:1px solid var(--border);border-radius:10px;min-height:60px}
.col-head{display:flex;align-items:center;justify-content:space-between;padding:11px 14px;border-bottom:1px solid var(--border);cursor:pointer;position:sticky;top:0}
.col-title{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.07em;color:var(--ink)}
.col-count{font-family:var(--f-mono);font-size:12px;color:var(--muted);background:var(--paper);border:1px solid var(--border);border-radius:999px;padding:1px 9px}
.col-cards{padding:12px;display:flex;flex-direction:column;gap:10px}
.col.collapsed .col-cards{display:none}
.col-head::after{content:"\2013";margin-left:8px;color:var(--muted);font-family:var(--f-mono)}
.col.collapsed .col-head::after{content:"+"}
[data-col=open] .col-title{color:var(--open)}
[data-col=in_progress] .col-title{color:var(--gold)}
[data-col=blocked] .col-title{color:var(--down)}
[data-col=done] .col-title,[data-col=answered] .col-title{color:var(--up)}

/* cards */
.card{background:var(--panel);border:1px solid var(--border);border-left:3px solid var(--open);border-radius:8px;padding:11px 12px;cursor:pointer;transition:box-shadow .12s,border-color .12s}
.card:hover{box-shadow:0 2px 10px rgba(0,0,0,.07)}
.card.s-in_progress{border-left-color:var(--gold-bright)}
.card.s-blocked{border-left-color:var(--down)}
.card.s-done,.card.s-answered{border-left-color:var(--up)}
.card[data-type=Q]{border-left-color:var(--navy)}
.card[data-type=Q].s-answered{border-left-color:var(--up)}
.card-top{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-bottom:6px}
.id{font-family:var(--f-mono);font-size:11px;font-weight:600;color:var(--ink);background:var(--gold-pale);border-radius:4px;padding:1px 7px}
.card[data-type=Q] .id{background:#dfe6f3}
.chip{font-size:10.5px;padding:1px 8px;border-radius:999px;border:1px solid var(--border);color:var(--muted);background:var(--paper)}
.chip.group{border-style:dashed}
.date{font-family:var(--f-mono);font-size:10.5px;color:var(--muted);margin-left:auto}
.src{font-family:var(--f-mono);text-decoration:none;color:var(--muted);font-size:13px;padding:0 2px}
.src:hover{color:var(--gold)}
.card-body{font-size:13px;color:var(--body);max-height:4.6em;overflow:hidden;position:relative;-webkit-line-clamp:3}
.card.open-card .card-body{max-height:none}
.card-body::after{content:"";position:absolute;bottom:0;left:0;right:0;height:1.5em;background:linear-gradient(transparent,var(--panel));pointer-events:none}
.card.open-card .card-body::after{display:none}
.outcome{display:none;margin-top:9px;padding:9px 10px;background:rgba(45,122,61,.07);border-radius:6px;font-size:12.5px;color:var(--body)}
.card.open-card .outcome{display:block}
.outcome-tag{display:inline-block;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--up);margin-right:7px}
.empty{color:var(--muted);font-size:12px;font-style:italic;padding:6px 2px;margin:0}

/* decisions */
.decisions{display:flex;flex-direction:column;gap:0;border-left:2px solid var(--border);margin-left:6px}
.decision{position:relative;padding:11px 0 11px 22px}
.decision::before{content:"";position:absolute;left:-7px;top:16px;width:10px;height:10px;border-radius:50%;background:var(--gold);border:2px solid var(--paper)}
.d-top{display:flex;align-items:center;gap:10px;margin-bottom:3px}
.d-top .id{background:var(--gold-pale)}
.d-body{font-size:13px;color:var(--body)}
.decision.clip .d-body{max-height:3em;overflow:hidden}
.hidden{display:none!important}

@media(max-width:1100px){.board{grid-template-columns:repeat(2,1fr)}}
@media(max-width:680px){.board,.board.q{grid-template-columns:1fr}.top .meta{margin-left:0;width:100%}}
</style>
</head>
<body>
<header class="top">
  <div class="top-row">
    %%LOGO%%
    <span class="sub">%%SUBTITLE%%</span>
    <div class="meta">
      <span>Updated <span class="updated">%%LAST_UPDATED%%</span></span>
      <a href="%%PLAN_LINK%%" target="_blank" rel="noopener">View plan &#8599;</a>
    </div>
  </div>
  <div class="kpis">%%KPIS%%</div>
</header>

<div class="toolbar">
  <input type="search" id="q" placeholder="Search id, owner, text...">
  <select id="owner"><option value="">All owners</option>%%OWNER_OPTS%%</select>
  <div class="toggles">
    <span class="toggle t-T on" data-t="T">Tasks</span>
    <span class="toggle t-Q on" data-t="Q">Questions</span>
    <span class="toggle t-D on" data-t="D">Decisions</span>
  </div>
  <div class="spacer"></div>
  <span class="count-live" id="live"></span>
</div>

<main>
  <div class="section-head" data-sect="T"><h2>Tasks</h2><span class="hint">click a card to expand &middot; click a column header to collapse</span></div>
  <div class="board" id="tasks">%%TASK_COLS%%</div>

  <div class="section-head" data-sect="Q"><h2>Open questions</h2><span class="hint">awaiting an answer from someone</span></div>
  <div class="board q" id="questions">%%Q_COLS%%</div>

  <div class="section-head" data-sect="D"><h2>Decisions log</h2><span class="hint">append-only record, newest first</span></div>
  <div class="decisions" id="decisions">%%DECISIONS%%</div>
</main>

<script>
(function(){
  var q=document.getElementById('q'),owner=document.getElementById('owner'),live=document.getElementById('live');
  var types={T:true,Q:true,D:true};
  var cards=Array.prototype.slice.call(document.querySelectorAll('.card,.decision'));

  // expand/collapse a card
  document.addEventListener('click',function(e){
    var card=e.target.closest('.card');
    if(card && !e.target.closest('a')){card.classList.toggle('open-card');return;}
    var head=e.target.closest('.col-head');
    if(head){head.parentNode.classList.toggle('collapsed');}
  });

  // type toggles
  Array.prototype.forEach.call(document.querySelectorAll('.toggle'),function(t){
    t.addEventListener('click',function(){
      var k=t.getAttribute('data-t');types[k]=!types[k];t.classList.toggle('on',types[k]);apply();
    });
  });
  q.addEventListener('input',apply);
  owner.addEventListener('change',apply);

  function apply(){
    var term=q.value.trim().toLowerCase();
    var own=owner.value;
    var shown=0;
    cards.forEach(function(c){
      var ty=c.getAttribute('data-type');
      var ok=types[ty];
      if(ok && term){ok=(c.getAttribute('data-search')||'').indexOf(term)>=0;}
      if(ok && own && ty!=='D'){ok=(c.getAttribute('data-owner')||'')===own;}
      c.classList.toggle('hidden',!ok);
      if(ok)shown++;
    });
    // hide whole sections when their type is off
    ['T','Q','D'].forEach(function(k){
      var on=types[k];
      var sect=document.querySelector('.section-head[data-sect="'+k+'"]');
      var board=k==='T'?document.getElementById('tasks'):k==='Q'?document.getElementById('questions'):document.getElementById('decisions');
      if(sect)sect.classList.toggle('hidden',!on);
      if(board)board.classList.toggle('hidden',!on);
    });
    // recount visible per column
    document.querySelectorAll('.col').forEach(function(col){
      var n=col.querySelectorAll('.card:not(.hidden)').length;
      col.querySelector('.col-count').textContent=n;
    });
    live.textContent=shown+' shown';
  }
  apply();
})();
</script>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser(description="Generate a kanban board HTML from a plan file.")
    ap.add_argument("--plan", required=True, help="Path to the plan markdown file")
    ap.add_argument("--out", required=True, help="Output HTML path")
    ap.add_argument("--title", default="Project Board")
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--repo-url", default="", help="e.g. https://github.com/org/repo (for deep links)")
    ap.add_argument("--plan-path", default="", help="Repo-relative plan path for deep links (defaults to --plan's name)")
    ap.add_argument("--theme", default="", help="Path to a JSON theme override file")
    ap.add_argument("--as-of", default="", help="Date shown in the 'Updated' stamp (YYYY-MM-DD). Defaults to today; in CI pass the plan's last commit date.")
    args = ap.parse_args()

    plan_text = Path(args.plan).read_text(encoding="utf-8")
    overrides = json.loads(Path(args.theme).read_text(encoding="utf-8")) if args.theme else {}
    theme = merge_theme(overrides)
    plan_path = args.plan_path or Path(args.plan).name

    questions, tasks, decisions = parse(plan_text)
    if not (questions or tasks or decisions):
        print("::warning::No tracker rows parsed - check the section headings and row format.", file=sys.stderr)

    as_of = args.as_of.strip() or date.today().isoformat()
    htmlout = build_html(questions, tasks, decisions, theme, args.title, args.subtitle, args.repo_url, plan_path, as_of)
    Path(args.out).write_text(htmlout, encoding="utf-8")
    print(f"Board written to {args.out}: {len(tasks)} tasks, {len(questions)} questions, {len(decisions)} decisions.")


if __name__ == "__main__":
    main()
