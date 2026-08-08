"""Dispatch summaries to configured channels.

Forked from alphakek-ai/dev-updates-action on 2026-04-28 to add an `email` channel type.

Supported channel types: telegram, discord, slack, twitter, email.

Email backend: Resend (https://resend.com). Requires RESEND_API_KEY env var
(or override via per-channel `api_key_env`). No extra Python deps - urllib stdlib only.

Email digest visuals (read from env vars set by the workflow before running):
  - DIGEST_METRIC_DONE / DIGEST_METRIC_OPEN / DIGEST_METRIC_QUESTIONS render
    a 3-up dashboard card strip at the top of the email body.
  - DIGEST_SPARKLINE (comma-separated counts, oldest-to-newest) renders an
    inline SVG bar chart of recent activity next to the metric strip.
  - Status pills (DONE / IN PROGRESS / BLOCKED) are auto-inserted by regex
    over the rendered HTML; no env var needed.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

_MODE_ALIASES = {"private": "dev", "public": "community"}


def _normalize_mode(mode: str) -> str:
    return _MODE_ALIASES.get(mode, mode)


def parse_channels(yaml_text: str) -> list[dict]:
    channels = []
    current: dict[str, str] = {}
    for line in yaml_text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- "):
            if current:
                channels.append(current)
            current = {}
            line = line[2:]
        if ":" in line:
            key, val = line.split(":", 1)
            current[key.strip()] = val.strip().strip("\"'")
    if current:
        channels.append(current)
    return channels


def load_summary(mode: str) -> str:
    path = f"/tmp/summary_{mode}.md"
    try:
        return open(path).read().strip()
    except FileNotFoundError:
        return ""


def send_telegram(ch, content, repo, repo_name, commits, files):
    from telegramify_markdown import markdownify

    chat_id = ch.get("chat_id", "")
    thread_id = ch.get("thread_id")
    bot_token_env = ch.get("bot_token_env", "TELEGRAM_BOT_TOKEN")
    token = os.environ.get(bot_token_env, "")

    if not token:
        raise RuntimeError(f"{bot_token_env} not set")

    mode = _normalize_mode(ch.get("mode", "dev"))
    if mode == "community":
        footer = f"{repo_name} - {commits} commit(s) - {files} file(s)"
    else:
        footer = f"[{repo_name} - {commits} commit(s) - {files} file(s)](https://github.com/{repo})"
    md_text = f"{content}\n\n{footer}"
    text = markdownify(md_text)

    if len(text) > 4000:
        text = text[:3997] + "..."

    payload = {
        "chat_id": chat_id,
        "parse_mode": "MarkdownV2",
        "text": text,
        "disable_web_page_preview": True,
    }
    if thread_id:
        payload["message_thread_id"] = int(thread_id)

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "dev-updates-action/1.0"},
    )
    urllib.request.urlopen(req)


def send_discord(ch, content, repo, repo_name, commits, files):
    webhook_url = ch.get("webhook_url") or os.environ.get(ch.get("webhook_url_env", ""), "")
    if not webhook_url:
        raise RuntimeError("No webhook URL configured")
    text = f"{content}\n\n[{repo_name}](https://github.com/{repo}) - {commits} commit(s) - {files} file(s)"
    payload = {"content": text[:2000]}
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "dev-updates-action/1.0"},
    )
    urllib.request.urlopen(req)


def send_slack(ch, content, repo, repo_name, commits, files):
    webhook_url = ch.get("webhook_url") or os.environ.get(ch.get("webhook_url_env", ""), "")
    if not webhook_url:
        raise RuntimeError("No webhook URL configured")
    text = f"{content}\n\n<https://github.com/{repo}|{repo_name}> - {commits} commit(s) - {files} file(s)"
    payload = {"text": text[:3000]}
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "dev-updates-action/1.0"},
    )
    urllib.request.urlopen(req)


def send_twitter(ch, content, repo, repo_name, commits, files):
    import tweepy

    api_key = os.environ.get(ch.get("api_key_env", "TWITTER_API_KEY"), "")
    api_secret = os.environ.get(ch.get("api_secret_env", "TWITTER_API_SECRET"), "")
    access_token = os.environ.get(ch.get("access_token_env", "TWITTER_ACCESS_TOKEN"), "")
    access_token_secret = os.environ.get(ch.get("access_token_secret_env", "TWITTER_ACCESS_TOKEN_SECRET"), "")

    if not all([api_key, api_secret, access_token, access_token_secret]):
        missing = [n for n, v in [
            ("TWITTER_API_KEY", api_key), ("TWITTER_API_SECRET", api_secret),
            ("TWITTER_ACCESS_TOKEN", access_token), ("TWITTER_ACCESS_TOKEN_SECRET", access_token_secret),
        ] if not v]
        raise RuntimeError(f"Twitter credentials missing: {', '.join(missing)}")

    client = tweepy.Client(
        consumer_key=api_key, consumer_secret=api_secret,
        access_token=access_token, access_token_secret=access_token_secret,
    )

    plain = content
    plain = re.sub(r"```.*?```", "", plain, flags=re.DOTALL)
    plain = re.sub(r"`([^`]+)`", r"\1", plain)
    plain = re.sub(r"\*\*(.+?)\*\*", r"\1", plain)
    plain = re.sub(r"\*(.+?)\*", r"\1", plain)
    plain = re.sub(r"_(.+?)_", r"\1", plain)
    plain = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", plain)
    plain = re.sub(r"\n{3,}", "\n\n", plain).strip()

    mode = _normalize_mode(ch.get("mode", "dev"))
    max_length = int(ch.get("max_length", "0"))
    footer = f"{repo_name} - {commits} commit(s) - {files} file(s)"
    link = f"https://github.com/{repo}" if mode == "dev" else ""

    parts = [plain, footer]
    if link:
        parts.append(link)
    tweet = "\n\n".join(parts)

    if max_length > 0 and len(tweet) > max_length:
        suffix = f"\n\n{footer}"
        if link:
            suffix += f"\n{link}"
        available = max_length - len(suffix)
        text = plain[:available].rsplit("\n", 1)[0] if len(plain) > available else plain
        tweet = text + suffix

    client.create_tweet(text=tweet)


def _parse_recipient_list(raw):
    raw = raw.strip()
    if not raw:
        return []
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1]
        items = [s.strip().strip('"').strip("'") for s in inner.split(",")]
    else:
        items = [s.strip() for s in raw.split(",")]
    return [s for s in items if s]


# --- Theme ------------------------------------------------------------------
# Email colour roles. Every default here is the literal this fork shipped before
# theming existed, so a repo that passes no digest_theme renders byte-identically
# to before. Do not "modernise" these values - an existing client's digest
# silently changing appearance is a regression, not an improvement.
DEFAULT_EMAIL_COLORS = {
    "accent": "#7e6717",       # links, metric values, most-recent sparkline bar
    "accent_soft": "#c9a04a",  # earlier sparkline bars
    "ink": "#14110a",          # headings
    "body": "#3b3736",         # body copy
    "muted": "#6b6359",        # captions, footer, metric labels
    "border": "#e8e2d4",       # horizontal rules, card and header-bar borders
    "surface": "#faf7f0",      # header bar, metric cards
    "surface_alt": "#fbf6e6",  # sparkline panel
    "rule": "#c9bfa8",         # the "|" separators inside the header bar
    "code_bg": "#f5f5f5",      # <pre> and inline <code> background
}

# Status chips stay semantic even under a brand palette: a brand-blue "Blocked"
# chip loses the meaning the chip exists to carry. Themeable, but deliberately
# not swept up by the colour mapping below - a client whose palette genuinely
# collides can override these explicitly via theme["status"].
DEFAULT_STATUS_COLORS = {
    "done":        {"label": "Done",        "bg": "#e2efe5", "fg": "#1f5b2c"},
    "in progress": {"label": "In Progress", "bg": "#f5e9c8", "fg": "#7e5a17"},
    "blocked":     {"label": "Blocked",     "bg": "#f4d4d4", "fg": "#7e1f1f"},
    "new":         {"label": "New",         "bg": "#dfe7f2", "fg": "#2a4a72"},
    "updated":     {"label": "Updated",     "bg": "#e8e2d4", "fg": "#5c5348"},
}

# board.py's palette vocabulary -> email colour roles, so a single board-theme
# JSON themes both the board and the digest and a client keeps one source of
# truth for its brand. The board's key names are historical ("gold" holds
# whatever the brand's primary accent is, blue included).
_BOARD_TO_EMAIL = {
    "gold": "accent", "gold_bright": "accent_soft", "ink": "ink",
    "body": "body", "muted": "muted", "border": "border",
    "paper": "surface", "gold_pale": "surface_alt",
}


def load_email_theme(raw=None):
    """Resolve the email palette from a theme JSON string (the digest_theme
    input, arriving as $DIGEST_THEME). Accepts a board-theme.json unchanged.
    Returns {"colors": {...}, "status": {...}} with defaults for anything the
    theme does not specify."""
    colors = dict(DEFAULT_EMAIL_COLORS)
    status = {k: dict(v) for k, v in DEFAULT_STATUS_COLORS.items()}
    raw = os.environ.get("DIGEST_THEME", "") if raw is None else raw
    if not (raw or "").strip():
        return {"colors": colors, "status": status}

    try:
        theme = json.loads(raw)
    except json.JSONDecodeError as e:
        # A malformed theme must not take the digest down: the update is worth
        # more than the branding. Warn into the Actions log and fall back.
        print(f"::warning::digest_theme is not valid JSON ({e}); using default palette")
        return {"colors": colors, "status": status}
    if not isinstance(theme, dict):
        print("::warning::digest_theme is not a JSON object; using default palette")
        return {"colors": colors, "status": status}

    supplied = theme.get("colors") or {}
    if isinstance(supplied, dict):
        for board_key, role in _BOARD_TO_EMAIL.items():
            if supplied.get(board_key):
                colors[role] = supplied[board_key]
        # A board theme carries no separator colour, and the default is a warm
        # tan that clashes with any non-gold brand. Its border reads correctly
        # on the themed header bar, so inherit that instead of leaving it stale.
        if supplied.get("border") and not supplied.get("rule"):
            colors["rule"] = supplied["border"]
        # Direct email-role keys win over the board aliases, so a client can
        # tune the digest without disturbing its board.
        for role in DEFAULT_EMAIL_COLORS:
            if supplied.get(role):
                colors[role] = supplied[role]

    overrides = theme.get("status") or {}
    if isinstance(overrides, dict):
        for key, override in overrides.items():
            k = str(key).strip().lower()
            if k in status and isinstance(override, dict):
                for field in ("label", "bg", "fg"):
                    if override.get(field):
                        status[k][field] = override[field]

    return {"colors": colors, "status": status}


def _markdown_to_html(md, theme=None):
    """Markdown -> HTML for digest emails. Tight spacing tuned for Gmail rendering.
    `theme` is a load_email_theme() result; omitted, the palette comes from
    $DIGEST_THEME (and is the shipped default when that is unset)."""
    theme = theme or load_email_theme()
    c = theme["colors"]
    out = md

    def code_block(m):
        return (
            f'<pre style="background:{c["code_bg"]};padding:10px;border-radius:4px;'
            f'overflow-x:auto;font-size:14px;margin:8px 0;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;">'
            f'<code>{m.group(1)}</code></pre>'
        )

    out = re.sub(r"```(?:\w+)?\n?(.*?)```", code_block, out, flags=re.DOTALL)
    out = re.sub(
        r"`([^`]+)`",
        rf'<code style="background:{c["code_bg"]};padding:2px 5px;border-radius:3px;font-size:14px;'
        r'font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;">\1</code>',
        out,
    )
    out = re.sub(
        r"^### (.+)$",
        rf'<h3 style="margin:14px 0 4px;font-size:17px;line-height:1.3;color:{c["ink"]};">\1</h3>',
        out, flags=re.MULTILINE,
    )
    out = re.sub(
        r"^## (.+)$",
        rf'<h2 style="margin:20px 0 6px;font-size:22px;line-height:1.25;color:{c["ink"]};'
        rf'border-bottom:1px solid {c["border"]};padding-bottom:4px;">\1</h2>',
        out, flags=re.MULTILINE,
    )
    out = re.sub(
        r"^# (.+)$",
        rf'<h1 style="margin:8px 0 4px;font-size:26px;line-height:1.2;color:{c["ink"]};">\1</h1>',
        out, flags=re.MULTILINE,
    )
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", out)
    out = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        rf'<a href="\2" style="color:{c["accent"]};text-decoration:underline;">\1</a>',
        out,
    )

    lines = out.splitlines()
    in_ul = False
    in_ol = False
    rendered = []
    for line in lines:
        m_ol = re.match(r"^\s*\d+\.\s+(.*)$", line)
        m_ul = re.match(r"^\s*[-*]\s+(.*)$", line)
        if m_ol:
            if in_ul:
                rendered.append("</ul>")
                in_ul = False
            if not in_ol:
                rendered.append('<ol style="margin:4px 0 8px;padding-left:24px;font-size:16px;line-height:1.5;">')
                in_ol = True
            rendered.append(f'<li style="margin:0 0 2px;">{m_ol.group(1)}</li>')
        elif m_ul:
            if in_ol:
                rendered.append("</ol>")
                in_ol = False
            if not in_ul:
                rendered.append('<ul style="margin:4px 0 8px;padding-left:22px;font-size:16px;line-height:1.5;">')
                in_ul = True
            rendered.append(f'<li style="margin:0 0 2px;">{m_ul.group(1)}</li>')
        else:
            if in_ul:
                rendered.append("</ul>")
                in_ul = False
            if in_ol:
                rendered.append("</ol>")
                in_ol = False
            rendered.append(line)
    if in_ul:
        rendered.append("</ul>")
    if in_ol:
        rendered.append("</ol>")
    out = "\n".join(rendered)
    out = re.sub(r"\n{2,}", "\n", out)

    # Status pills - inline colored chips on every list item that carries a
    # known status keyword. Applied AFTER list rendering so the regex can see
    # the rendered <li>...</li> structure.
    st = theme["status"]
    # The inline markers keep their own literal label text ("DONE" here vs "Done"
    # on the leading-token chip below); both render uppercase via CSS, and the
    # distinction is preserved so themed output stays diffable against untouched.
    pill_done = (
        f'<span style="display:inline-block;padding:1px 7px;margin-right:6px;'
        f'border-radius:3px;background:{st["done"]["bg"]};color:{st["done"]["fg"]};font-size:11px;'
        f'font-weight:600;letter-spacing:0.04em;text-transform:uppercase;">DONE</span>'
    )
    pill_in_progress = (
        f'<span style="display:inline-block;padding:1px 7px;margin-left:4px;'
        f'border-radius:3px;background:{st["in progress"]["bg"]};color:{st["in progress"]["fg"]};font-size:11px;'
        f'font-weight:600;letter-spacing:0.04em;text-transform:uppercase;">In Progress</span>'
    )
    pill_blocked = (
        f'<span style="display:inline-block;padding:1px 7px;margin-left:4px;'
        f'border-radius:3px;background:{st["blocked"]["bg"]};color:{st["blocked"]["fg"]};font-size:11px;'
        f'font-weight:600;letter-spacing:0.04em;text-transform:uppercase;">Blocked</span>'
    )
    # Lambda replacements, not template strings: a theme-supplied colour is
    # arbitrary text, and a stray backslash in one would otherwise be parsed as
    # a group reference and raise mid-digest.
    out = re.sub(r"✅ Shipped:\s*", lambda _m: pill_done, out)
    out = re.sub(r"\(in progress\)", lambda _m: pill_in_progress, out, flags=re.IGNORECASE)

    # Leading status tokens on task-list bullets ("NEW - T202: ...") render as bare
    # text otherwise. The <li\b[^>]*> anchor is deliberately loose: pinning it to the
    # literal style attribute emitted above would silently stop chipping the moment
    # that markup changes by a character. The separator class covers hyphen/en dash/
    # em dash because the summary is model-written and varies.
    def _lead_chip(m):
        # Collapse whitespace so "IN  PROGRESS" still keys into the status map.
        s = st[" ".join(m.group(2).split()).lower()]
        chip = (
            f'<span style="display:inline-block;padding:1px 7px;margin-right:6px;'
            f'border-radius:3px;background:{s["bg"]};color:{s["fg"]};font-size:11px;'
            f'font-weight:600;letter-spacing:0.04em;text-transform:uppercase;">{s["label"]}</span>'
        )
        return m.group(1) + chip

    out = re.sub(
        r"(<li\b[^>]*>)\s*(?:<strong>)?\s*"
        r"(NEW|IN PROGRESS|DONE|BLOCKED|UPDATED)(?:</strong>)?\s*[-–—]\s+",
        _lead_chip,
        out,
        flags=re.IGNORECASE,
    )

    def _waiting_on(m):
        # Each <li> occupies its own line here, so the line prefix is the right scope
        # to ask "does this bullet already carry a Blocked chip?". If it does (the
        # bullet led with a BLOCKED token), emit only the grey annotation rather than
        # a second identical pill. Lines with no leading Blocked chip are unchanged.
        # The sentinel is the *themed* Blocked background, not a literal: pinning
        # it to #f4d4d4 would silently stop de-duplicating the moment a client
        # overrides the Blocked chip colour.
        src = m.string
        line_start = src.rfind("\n", 0, m.start()) + 1
        grey = f'<span style="color:{c["muted"]};">waiting on {m.group(1)}</span>'
        if f'background:{st["blocked"]["bg"]}' in src[line_start:m.start()]:
            return grey
        return pill_blocked + " " + grey

    out = re.sub(r"\(waiting on ([^)]+)\)", _waiting_on, out)
    return out


def _render_metric_strip(theme=None):
    """Build the 3-up dashboard card row + sparkline. Returns HTML string, or
    empty string if no metric env vars are set (graceful no-op for forks that
    don't compute metrics)."""
    theme = theme or load_email_theme()
    c = theme["colors"]
    done = os.environ.get("DIGEST_METRIC_DONE", "").strip()
    open_ = os.environ.get("DIGEST_METRIC_OPEN", "").strip()
    qs = os.environ.get("DIGEST_METRIC_QUESTIONS", "").strip()
    if not (done or open_ or qs):
        return ""

    def card(value, label):
        return (
            f'<td align="center" style="padding:14px 8px;background:{c["surface"]};'
            f'border:1px solid {c["border"]};border-radius:6px;width:33%;">'
            f'<div style="font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;'
            f'font-size:30px;font-weight:500;color:{c["accent"]};line-height:1;letter-spacing:-0.01em;">{value or "0"}</div>'
            f'<div style="margin-top:6px;font-family:system-ui,sans-serif;font-size:11px;'
            f'color:{c["muted"]};letter-spacing:0.08em;text-transform:uppercase;">{label}</div>'
            f'</td>'
        )

    cards_html = (
        '<table role="presentation" cellpadding="0" cellspacing="6" border="0" '
        'style="width:100%;margin:0 0 12px;border-collapse:separate;">'
        '<tr>'
        + card(done, "Shipped")
        + card(open_, "Open tasks")
        + card(qs, "Open questions")
        + '</tr></table>'
    )

    spark_html = _render_sparkline(theme)
    return cards_html + spark_html


def _render_sparkline(theme=None):
    theme = theme or load_email_theme()
    c = theme["colors"]
    raw = os.environ.get("DIGEST_SPARKLINE", "").strip()
    if not raw:
        return ""
    try:
        vals = [int(v.strip()) for v in raw.split(",") if v.strip().isdigit() or v.strip() == "0"]
    except ValueError:
        return ""
    if not vals:
        return ""

    n = len(vals)
    width = 560  # fits inside 680px wrapper with padding
    height = 36
    bar_w = max(4, (width - (n - 1) * 4) // n)
    gap = 4
    max_v = max(vals) or 1

    bars = []
    for i, v in enumerate(vals):
        x = i * (bar_w + gap)
        h = int((v / max_v) * (height - 4)) if v else 2
        y = height - h
        # Today (last bar) renders in the primary accent; rest in the soft accent.
        fill = c["accent"] if i == n - 1 else c["accent_soft"]
        bars.append(
            f'<rect x="{x}" y="{y}" width="{bar_w}" height="{h}" rx="1" fill="{fill}" />'
        )
    bars_str = "".join(bars)

    total = sum(vals)
    return (
        f'<div style="margin:0 0 18px;padding:10px 14px;background:{c["surface_alt"]};'
        f'border:1px solid {c["border"]};border-radius:6px;">'
        f'<div style="display:block;font-family:system-ui,sans-serif;font-size:11px;'
        f'color:{c["muted"]};letter-spacing:0.08em;text-transform:uppercase;margin-bottom:6px;">'
        f'Activity, last 14 days &middot; {total} commit(s)</div>'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="14-day commit activity">'
        f'{bars_str}'
        f'</svg>'
        f'</div>'
    )


def send_email(ch, content, repo, repo_name, commits, files):
    """Send digest email via Resend API."""
    api_key_env = ch.get("api_key_env", "RESEND_API_KEY")
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        raise RuntimeError(f"{api_key_env} not set")

    to_list = _parse_recipient_list(ch.get("to", ""))
    if not to_list:
        raise RuntimeError("No 'to' recipients configured for email channel")

    sender = ch.get("from", "Dev Updates <onboarding@resend.dev>")
    subject_prefix = ch.get("subject_prefix", "[Dev]")

    first_line = content.strip().splitlines()[0] if content.strip() else ""
    title = re.sub(r"^[*#\s>]+", "", first_line).rstrip("*").strip() or repo_name or "Dev Update"
    # If the subject_prefix is like '[Project]', the LLM often also titles the
    # digest 'Project Dev Updates - {date}', producing a doubled subject
    # '[Project] Project Dev Updates ...'. Strip a leading copy of the prefix
    # word from the title so the bracket only appears once.
    prefix_match = re.match(r"^\s*\[([^\]]+)\]", subject_prefix)
    if prefix_match:
        prefix_word = prefix_match.group(1).strip()
        if prefix_word:
            title = re.sub(rf"^{re.escape(prefix_word)}\s+", "", title, flags=re.IGNORECASE)
    subject = f"{subject_prefix} {title}"[:200]

    # One palette resolved per send and threaded through every renderer, so the
    # digest cannot end up half-branded if a theme is partially specified.
    theme = load_email_theme()
    c = theme["colors"]

    html_body = _markdown_to_html(content, theme)

    preview_url = ch.get("preview_url", "")
    preview_label = ch.get("preview_label", "View live preview")
    preview_anchor = (
        f'<a href="{preview_url}" style="color:{c["accent"]};text-decoration:none;font-weight:600;">'
        f'{preview_label}</a>'
        f'<span style="margin:0 8px;color:{c["rule"]};">|</span>'
    ) if preview_url else ""
    header_html = (
        f'<div style="background:{c["surface"]};border:1px solid {c["border"]};border-radius:6px;'
        f'padding:14px 18px;margin-bottom:18px;font-size:14px;line-height:1.5;'
        f'color:{c["muted"]};font-family:system-ui,sans-serif;">'
        f'{preview_anchor}'
        f'<a href="https://github.com/{repo}" style="color:{c["accent"]};text-decoration:none;font-weight:600;">'
        f'View {repo_name} on GitHub</a>'
        f'<span style="margin:0 8px;color:{c["rule"]};">|</span>'
        f'{commits} commit(s) in this push'
        f'<span style="margin:0 8px;color:{c["rule"]};">|</span>'
        f'{files} file(s) changed'
        f'</div>'
    )

    metric_strip_html = _render_metric_strip(theme)

    footer_html = (
        f'<hr style="border:none;border-top:1px solid {c["border"]};margin:32px 0 12px;">'
        f'<p style="color:{c["muted"]};font-size:13px;font-family:system-ui,sans-serif;line-height:1.5;">'
        f'You are receiving this digest because you are on the project distribution list. '
        f'Reply directly to this email to discuss anything in the digest. '
        f'Source: <a href="https://github.com/{repo}" style="color:{c["accent"]};text-decoration:none;">{repo_name}</a>'
        f'</p>'
    )

    html = (
        f'<div style="font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;'
        f'max-width:680px;margin:0 auto;padding:20px 20px 24px;color:{c["body"]};'
        f'font-size:16px;line-height:1.5;">'
        f'{header_html}{metric_strip_html}{html_body}{footer_html}</div>'
    )

    payload = {
        "from": sender,
        "to": to_list,
        "subject": subject,
        "html": html,
        "text": content + f"\n\n{repo_name} - {commits} commit(s) - {files} file(s)\nhttps://github.com/{repo}",
    }
    if reply_to := ch.get("reply_to"):
        payload["reply_to"] = reply_to

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "dev-updates-action/1.0 (+https://github.com/vinnycorp/dev-updates-action)",
        },
    )
    try:
        urllib.request.urlopen(req)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Resend API error {e.code}: {body}") from e


DISPATCHERS = {
    "telegram": send_telegram,
    "discord": send_discord,
    "slack": send_slack,
    "twitter": send_twitter,
    "email": send_email,
}


def main():
    channels = parse_channels(os.environ.get("CHANNELS", ""))
    repo = os.environ.get("REPO", "")
    repo_name = repo.split("/")[-1] if repo else ""
    commits = os.environ.get("COMMITS", "0")
    files = os.environ.get("FILES", "0")

    if not channels:
        print("ERROR: No channels configured")
        sys.exit(1)

    successes = 0
    failures = 0

    for ch in channels:
        name = ch.get("name", ch.get("type", "unknown"))
        ch_type = ch.get("type", "telegram")
        mode = _normalize_mode(ch.get("mode", "dev"))

        content = load_summary(mode)
        if not content:
            print(f"ERROR: No {mode} summary generated for {name}")
            failures += 1
            continue

        dispatcher = DISPATCHERS.get(ch_type)
        if not dispatcher:
            print(f"ERROR: Unknown channel type '{ch_type}' for {name}")
            failures += 1
            continue

        try:
            dispatcher(ch, content, repo, repo_name, commits, files)
            print(f"OK: {name} ({ch_type}, {mode})")
            successes += 1
        except Exception as e:
            print(f"ERROR: {name} ({ch_type}): {e}")
            failures += 1

    if successes == 0 and failures > 0:
        print(f"FATAL: All {failures} channel(s) failed")
        sys.exit(1)
    elif failures > 0:
        print(f"ERROR: {failures}/{successes + failures} channel(s) failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
