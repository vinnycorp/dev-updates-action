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
# Optional, backward-compatible priority tag leading a description: [P1] high,
# [P2] medium, [P3] low. Absent => unprioritized. Stripped from the title.
PRIORITY = re.compile(r"^\s*\[?[Pp]([123])\]?[.:)\s-]+")


def extract_priority(desc):
    m = PRIORITY.match(desc)
    if m:
        return int(m.group(1)), desc[m.end():]
    return 0, desc


def prio_rank(item):
    # sort order within a column: P1 top, P2, untagged, P3 bottom
    return {1: 0, 2: 1, 0: 2, 3: 3}.get(item.get("priority", 0), 2)


_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def completed_date(item):
    """When the item was completed, used to order the Done column. The first
    ISO date in the resolution text (after the arrow) is the completion date -
    e.g. 'DONE 2026-06-02 ...'. Falls back to the opened date. Deliberately
    NOT the max date in the row, which would be skewed by deadlines or future
    dates mentioned in the prose."""
    m = _DATE.search(item.get("outcome", ""))
    return m.group(0) if m else item.get("date", "")


# Cross-references to other tracker items (e.g. "T42", "Q40", "D7"). Linked
# only when the id actually exists, so there are never dead links. Populated by
# build_html() before any card is rendered.
_VALID_IDS = set()
_XREF = re.compile(r"\b([TQD]\d+)\b")


def _xref_sub(m):
    rid = m.group(1)
    if rid in _VALID_IDS:
        return f'<a class="xref" href="#item-{rid}" data-ref="{rid}">{rid}</a>'
    return rid


# Reverse index: item id -> sorted list of item ids that reference it. Lets each
# card show "Referenced by ..." so a reader can navigate back up the graph.
_BACKLINKS = {}
_TYPE_ORDER = {"T": 0, "Q": 1, "D": 2}


def build_backlinks(items):
    valid = {x["id"] for x in items}
    back = {}
    for x in items:
        text = f'{x.get("ask", "")} {x.get("outcome", "")}'
        refs = set(re.findall(r"\b([TQD]\d+)\b", text)) & valid
        refs.discard(x["id"])  # ignore self-references
        for r in refs:
            back.setdefault(r, set()).add(x["id"])
    keyfn = lambda i: (_TYPE_ORDER.get(i[0], 9), int(i[1:]))
    return {k: sorted(v, key=keyfn) for k, v in back.items()}


def backlinks_html(item_id):
    refs = _BACKLINKS.get(item_id)
    if not refs:
        return ""
    links = " ".join(
        f'<a class="xref" href="#item-{r}" data-ref="{r}">{r}</a>' for r in refs
    )
    return f'<div class="backlinks">&#8617; Referenced by {links}</div>'


def inline_md(text: str) -> str:
    """Minimal, safe markdown -> HTML for tracker prose: escape first, then
    re-introduce links, bold and inline code. Escaping before linkifying means
    any "&" inside a URL becomes "&amp;", which is the correct form in an
    href, so the order is deliberate. Finally, cross-link T/Q/D references to
    their card - only in text segments, never inside an existing tag."""
    esc = html.escape(text, quote=False)
    esc = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", esc)
    esc = re.sub(r"`([^`]+)`", r"<code>\1</code>", esc)
    esc = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r'<a href="\2" target="_blank" rel="noopener">\1</a>',
        esc,
    )
    if _VALID_IDS:
        segs = re.split(r"(<[^>]+>)", esc)
        for i in range(0, len(segs), 2):  # even indices are text outside tags
            segs[i] = _XREF.sub(_xref_sub, segs[i])
        esc = "".join(segs)
    return esc


# Abbreviations whose trailing period must NOT end a sentence.
ABBREV = {"e.g", "i.e", "etc", "vs", "no", "fig", "dr", "mr", "mrs", "ms", "inc",
          "ltd", "co", "st", "approx", "est", "cf", "al", "jr", "sr", "u.s",
          "ph.d", "a.m", "p.m", "mt", "sgd", "usd", "ie", "eg"}
_SENT_BOUND = re.compile(r"[.?!]\s+(?=[A-Z0-9(])")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def split_sentences(text):
    """Split prose into sentences. Deterministic and conservative: a period only
    ends a sentence when followed by whitespace and a capital/number/paren, and
    not when the preceding token is a known abbreviation. Decimals and URLs
    (period not followed by whitespace) are never split."""
    out, start = [], 0
    for m in _SENT_BOUND.finditer(text):
        end = m.start()
        before = text[:end]
        wm = re.search(r"([A-Za-z][A-Za-z.]*)[)\]\"'’]*$", before)
        word = wm.group(1).lower().rstrip(".") if wm else ""
        if word in ABBREV:
            continue
        seg = text[start:end + 1].strip()
        if seg:
            out.append(seg)
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def make_title(text, limit=90):
    """Derive a concise heading from a row's prose: the first sentence, trimmed
    at the first parenthetical or colon clause, with markdown links flattened to
    their text. Hard-capped at `limit` chars on a word boundary."""
    sents = split_sentences(text)
    head = sents[0] if sents else text
    head = _MD_LINK.sub(r"\1", head)
    head = re.sub(r"\s+", " ", head).strip()
    cuts = [i for sep in (" (", ": ") for i in [head.find(sep)] if i >= 25]
    if cuts:
        head = head[:min(cuts)]
    head = head.strip(" .:;,-")
    if len(head) > limit:
        head = head[:limit].rsplit(" ", 1)[0].rstrip(" .:;,-") + "…"
    return head


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
    prio, desc = extract_priority(desc)
    ask, _, outcome = desc.partition(ARROW)
    return {
        "type": kind,
        "id": f"{kind}{num}",
        "num": int(num),
        "owner": addressee,
        "status": status,
        "date": date,
        "priority": prio,
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


def detail_bullets(text, title=None):
    """Prose rendered one <li> per sentence. If `title` already captures the
    first sentence verbatim (i.e. the title wasn't truncated), drop that first
    bullet so the heading isn't echoed; otherwise keep everything."""
    sents = split_sentences(text)
    if title and sents:
        first = re.sub(r"\s+", " ", _MD_LINK.sub(r"\1", sents[0])).strip().rstrip(" .:;,-")
        if first == title.rstrip("…").strip():
            sents = sents[1:]
    if not sents:
        return ""
    return '<ul class="detail">' + "".join(f"<li>{inline_md(s)}</li>" for s in sents) + "</ul>"


def card_html(item, repo_url, plan_path):
    owner = item.get("owner", "")
    owner_chip = f'<span class="chip owner">{html.escape(owner)}</span>' if owner else ""
    group = item.get("group", "")
    group_chip = f'<span class="chip group">{html.escape(group)}</span>' if group else ""
    prio = item.get("priority", 0)
    prio_chip = f'<span class="chip prio p{prio}">P{prio}</span>' if prio else ""
    deep = f'{repo_url}/blob/main/{plan_path}#L{item["line"]}' if repo_url else ""
    deep_link = (
        f'<a class="src" href="{deep}" target="_blank" rel="noopener" '
        f'title="View in plan">{ARROW}</a>' if deep else ""
    )
    title_text = make_title(item["ask"])
    title = inline_md(title_text)
    detail = detail_bullets(item["ask"], title_text)
    out_bullets = detail_bullets(item["outcome"]) if item.get("outcome") else ""
    outcome_block = (
        f'<div class="outcome"><span class="outcome-tag">'
        f'{"Answered" if item["type"] == "Q" else "Resolved"}</span>{out_bullets}</div>'
        if out_bullets else ""
    )
    bl = backlinks_html(item["id"])
    more = '<div class="more"></div>' if (detail or outcome_block or bl) else ""
    haystack = html.escape(
        f'{item["id"]} {("p"+str(prio)) if prio else ""} {owner} {group} {item["ask"]} {item.get("outcome", "")}'.lower(),
        quote=True,
    )
    return f'''<article id="item-{item['id']}" class="card s-{item['status']}" data-type="{item['type']}" data-status="{item['status']}" data-owner="{html.escape(owner, quote=True)}" data-search="{haystack}">
  <div class="card-top">
    <span class="id">{item['id']}</span>
    {prio_chip}{owner_chip}{group_chip}
    <span class="date">{html.escape(item['date'])}</span>
    {deep_link}
  </div>
  <h3 class="card-title">{title}</h3>
  {detail}
  {outcome_block}
  {bl}
  {more}
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
    title_text = make_title(item["ask"])
    title = inline_md(title_text)
    detail = detail_bullets(item["ask"], title_text)
    bl = backlinks_html(item["id"])
    more = '<div class="more"></div>' if (detail or bl) else ""
    haystack = html.escape(f'{item["id"]} {item["ask"]}'.lower(), quote=True)
    return f'''<article id="item-{item['id']}" class="decision" data-type="D" data-search="{haystack}">
  <div class="d-top"><span class="id">{item['id']}</span><span class="date">{html.escape(item['date'])}</span>{deep_link}</div>
  <h3 class="card-title">{title}</h3>
  {detail}
  {bl}
  {more}
</article>'''


# --- HTML assembly ----------------------------------------------------------
def build_html(questions, tasks, decisions, theme, title, subtitle, repo_url, plan_path, as_of):
    global _VALID_IDS, _BACKLINKS
    _VALID_IDS = {x["id"] for x in (questions + tasks + decisions)}
    _BACKLINKS = build_backlinks(questions + tasks + decisions)
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

    # active task columns: priority first, then most-recent
    by = lambda st: sorted([t for t in tasks if t["status"] == st], key=lambda x: (prio_rank(x), -x["num"]))
    t_open, t_prog, t_block = by("open"), by("in_progress"), by("blocked")
    # Done column: most-recently-completed at the top (priority is moot once done)
    t_done = sorted([t for t in tasks if t["status"] == "done"],
                    key=lambda x: (completed_date(x), x["num"]), reverse=True)
    task_cols = "\n".join([
        column("Open", "open", t_open, repo_url, plan_path),
        column("In progress", "in_progress", t_prog, repo_url, plan_path),
        column("Blocked", "blocked", t_block, repo_url, plan_path),
        column("Done", "done", t_done, repo_url, plan_path),
    ])

    # Open questions: one column per addressee (busiest first), then a single
    # answered/closed column.
    q_open = [q for q in questions if q["status"] == "open"]
    q_ans = sorted([q for q in questions if q["status"] == "answered"], key=lambda x: -x["num"])
    by_owner = {}
    for q in q_open:
        by_owner.setdefault(q["owner"] or "Unassigned", []).append(q)
    owner_order = sorted(by_owner, key=lambda o: (-len(by_owner[o]), o.lower()))
    q_col_parts = [
        column(o, "open", sorted(by_owner[o], key=lambda x: (prio_rank(x), -x["num"])), repo_url, plan_path)
        for o in owner_order
    ]
    q_col_parts.append(column("Answered / closed", "answered", q_ans, repo_url, plan_path, collapsed=True))
    q_cols = "\n".join(q_col_parts)

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

    logo = theme.get("logo_svg", "") or f'<span class="wordmark">{html.escape(title)}</span>'

    return TEMPLATE \
        .replace("%%TITLE%%", html.escape(title)) \
        .replace("%%SUBTITLE%%", html.escape(subtitle or "")) \
        .replace("%%FONT_LINKS%%", font_links) \
        .replace("%%LOGO%%", logo) \
        .replace("%%LAST_UPDATED%%", html.escape(last_updated)) \
        .replace("%%PLAN_LINK%%", f'{repo_url}/blob/main/{plan_path}' if repo_url else "#") \
        .replace("%%N_T%%", str(len(tasks))) \
        .replace("%%N_Q%%", str(len(questions))) \
        .replace("%%N_D%%", str(len(decisions))) \
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

/* toolbar */
.toolbar{position:sticky;top:0;z-index:20;background:var(--paper);border-bottom:1px solid var(--border);padding:12px 24px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.toolbar input[type=search],.toolbar select{font-family:var(--f-sans);font-size:13px;padding:7px 11px;border:1px solid var(--border);border-radius:7px;background:var(--panel);color:var(--ink)}
.toolbar input[type=search]{min-width:230px}
.tabs{display:inline-flex;border:1px solid var(--border);border-radius:8px;overflow:hidden;background:var(--panel)}
.tab{font-family:var(--f-sans);font-size:13px;font-weight:500;padding:8px 17px;border:none;border-right:1px solid var(--border);background:transparent;cursor:pointer;color:var(--muted)}
.tab:last-child{border-right:none}
.tab:hover{color:var(--ink);background:rgba(0,0,0,.025)}
.tab.on{background:var(--navy);color:#fff}
.tab-n{font-family:var(--f-mono);font-size:11px;opacity:.6;margin-left:3px}
.spacer{flex:1}
.count-live{font-size:12px;color:var(--muted);font-family:var(--f-mono)}

/* views (tabbed - one visible at a time) */
main{padding:20px 24px 44px;max-width:1700px;margin:0 auto}
.view{display:none}
.view.on{display:block}
.view-hint{font-size:12px;color:var(--muted);margin:2px 0 16px}

/* kanban */
.board{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;align-items:start}
.board.q{display:flex;gap:14px;overflow-x:auto;padding-bottom:10px;align-items:flex-start}
.board.q .col{flex:1 0 260px}
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
.chip.prio{font-weight:600;letter-spacing:.02em}
.chip.prio.p1{background:rgba(168,50,50,.12);color:var(--down);border-color:rgba(168,50,50,.35)}
.chip.prio.p2{background:rgba(201,160,74,.18);color:#8a6d2f;border-color:rgba(201,160,74,.45)}
.chip.prio.p3{background:var(--paper);color:var(--muted)}
.date{font-family:var(--f-mono);font-size:10.5px;color:var(--muted);margin-left:auto}
.src{font-family:var(--f-mono);text-decoration:none;color:var(--muted);font-size:13px;padding:0 2px}
.src:hover{color:var(--gold)}
.xref{color:var(--gold);font-family:var(--f-mono);font-size:.92em;text-decoration:none;border-bottom:1px dotted;border-radius:2px;cursor:pointer}
.xref:hover{border-bottom-style:solid;background:var(--gold-pale)}
.flash{animation:xflash 1.7s ease-out}
@keyframes xflash{0%,25%{box-shadow:0 0 0 2px var(--gold),0 4px 16px rgba(0,0,0,.14)}100%{box-shadow:0 0 0 0 rgba(0,0,0,0)}}
.card-title{margin:0;font-size:13.5px;font-weight:500;color:var(--body);line-height:1.4;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.open-card .card-title{-webkit-line-clamp:unset;overflow:visible}
.detail{display:none;margin:9px 0 0;padding-left:17px}
.open-card .detail{display:block}
.detail li{font-size:12px;color:var(--body);line-height:1.5;margin:0 0 5px}
.detail li:last-child{margin-bottom:0}
.backlinks{display:none;margin-top:9px;padding-top:8px;border-top:1px solid var(--border);font-size:11px;color:var(--muted)}
.open-card .backlinks{display:block}
.backlinks .xref{margin-right:5px}
.more{margin-top:8px;font-size:10px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;color:var(--gold);opacity:.75}
.more::after{content:"Details +"}
.open-card .more::after{content:"Hide -"}
.outcome{display:none;margin-top:9px;padding:8px 11px;background:rgba(45,122,61,.07);border-radius:6px}
.open-card .outcome{display:block}
.outcome-tag{display:block;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--up);margin-bottom:3px}
.outcome .detail{display:block;margin:0;padding-left:16px}
.outcome .detail li{color:var(--body)}
.empty{color:var(--muted);font-size:12px;font-style:italic;padding:6px 2px;margin:0}

/* decisions */
.decisions{display:flex;flex-direction:column;gap:0;border-left:2px solid var(--border);margin-left:6px}
.decision{position:relative;padding:11px 0 13px 22px;cursor:pointer}
.decision::before{content:"";position:absolute;left:-7px;top:16px;width:10px;height:10px;border-radius:50%;background:var(--gold);border:2px solid var(--paper)}
.d-top{display:flex;align-items:center;gap:10px;margin-bottom:4px}
.d-top .id{background:var(--gold-pale)}
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
</header>

<div class="toolbar">
  <div class="tabs">
    <button class="tab on" data-t="T">Tasks <span class="tab-n">%%N_T%%</span></button>
    <button class="tab" data-t="Q">Questions <span class="tab-n">%%N_Q%%</span></button>
    <button class="tab" data-t="D">Decisions <span class="tab-n">%%N_D%%</span></button>
  </div>
  <input type="search" id="q" placeholder="Search...">
  <select id="owner"><option value="">All owners</option>%%OWNER_OPTS%%</select>
  <div class="spacer"></div>
  <span class="count-live" id="live"></span>
</div>

<main>
  <section class="view on" id="view-T">
    <div class="view-hint">Click a card to expand &middot; click a column header to collapse the column.</div>
    <div class="board" id="tasks">%%TASK_COLS%%</div>
  </section>
  <section class="view" id="view-Q">
    <div class="view-hint">Open questions awaiting an answer from someone.</div>
    <div class="board q" id="questions">%%Q_COLS%%</div>
  </section>
  <section class="view" id="view-D">
    <div class="view-hint">Append-only decision record, newest first.</div>
    <div class="decisions" id="decisions">%%DECISIONS%%</div>
  </section>
</main>

<script>
(function(){
  var q=document.getElementById('q'),owner=document.getElementById('owner'),live=document.getElementById('live');
  var active='T';
  var views={T:document.getElementById('view-T'),Q:document.getElementById('view-Q'),D:document.getElementById('view-D')};

  // cross-reference click -> jump to the referenced card
  // expand/collapse a card or decision; collapse a column from its header
  document.addEventListener('click',function(e){
    var x=e.target.closest('.xref');
    if(x){e.preventDefault();showItem(x.getAttribute('data-ref'));return;}
    var item=e.target.closest('.card,.decision');
    if(item && !e.target.closest('a')){item.classList.toggle('open-card');return;}
    var head=e.target.closest('.col-head');
    if(head){head.parentNode.classList.toggle('collapsed');}
  });

  function showItem(ref){
    var el=document.getElementById('item-'+ref);
    if(!el)return;
    q.value='';owner.value='';                 // clear filters so the target shows
    var type=ref.charAt(0);                     // T / Q / D -> tab
    var tab=document.querySelector('.tab[data-t="'+type+'"]');
    if(tab){
      active=type;
      document.querySelectorAll('.tab').forEach(function(t){t.classList.toggle('on',t===tab);});
      for(var k in views){views[k].classList.toggle('on',k===type);}
      owner.style.display=(type==='D')?'none':'';
    }
    apply();
    var col=el.closest('.col');if(col)col.classList.remove('collapsed');
    el.classList.remove('hidden');el.classList.add('open-card');
    requestAnimationFrame(function(){
      el.scrollIntoView({behavior:'smooth',block:'center',inline:'center'});
      el.classList.add('flash');
      setTimeout(function(){el.classList.remove('flash');},1700);
    });
  }

  // tabs: single-select, one view visible at a time
  Array.prototype.forEach.call(document.querySelectorAll('.tab'),function(t){
    t.addEventListener('click',function(){
      active=t.getAttribute('data-t');
      document.querySelectorAll('.tab').forEach(function(x){x.classList.toggle('on',x===t);});
      for(var k in views){views[k].classList.toggle('on',k===active);}
      owner.style.display=(active==='D')?'none':'';   // decisions have no owner
      apply();
    });
  });
  q.addEventListener('input',apply);
  owner.addEventListener('change',apply);

  function apply(){
    var term=q.value.trim().toLowerCase();
    var own=owner.value;
    var view=views[active];
    var shown=0;
    view.querySelectorAll('.card,.decision').forEach(function(c){
      var ok=true;
      if(term){ok=(c.getAttribute('data-search')||'').indexOf(term)>=0;}
      if(ok && own && active!=='D'){ok=(c.getAttribute('data-owner')||'')===own;}
      c.classList.toggle('hidden',!ok);
      if(ok)shown++;
    });
    view.querySelectorAll('.col').forEach(function(col){
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
