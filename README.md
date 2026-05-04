# dev-updates-action

A GitHub Action that turns every push into a written summary and dispatches it to the channels your team actually reads. Summaries are produced by [Claude Code](https://claude.com/claude-code) using a prompt you control, so the digest reads in your team's voice, at the right level of detail for the audience.

Supported channels: **Telegram**, **Discord**, **Slack**, **Twitter**, **Email**.

This is a fork of [alphakek-ai/dev-updates-action](https://github.com/alphakek-ai/dev-updates-action) maintained by [@vinnycorp](https://github.com/vinnycorp). See [What's different in this fork](#whats-different-in-this-fork) below.

## Why use it

Most dev-update tooling is either too noisy (raw commit lists) or too heavy (project-management dashboards nobody opens). This action sits in the middle:

- Runs on every push (and on a schedule, with cooldown so you don't double-fire)
- Lets you write the digest rules in plain English, in your workflow file, no separate config service
- Sends to wherever the team already lives - chat, email, social
- Costs effectively nothing per run (a few seconds of Claude Code + one HTTP call per channel)

Good fits include shipping changelogs, internal dev updates, weekly digests for non-technical stakeholders, and customer-facing release notes.

## Quickstart

```yaml
# .github/workflows/dev-updates.yml
name: Dev Updates

on:
  push:
    branches: [main]
  workflow_dispatch:
  schedule:
    - cron: '0 20 * * *'   # daily safety net at 20:00 UTC

jobs:
  notify:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write
      actions: read
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 50

      - uses: vinnycorp/dev-updates-action@v1
        with:
          cooldown: '24h'
          dev_rules: |
            Audience: the engineering team. Be terse, factual.
            One bullet per meaningful change, leading with what shipped.
          channels: |
            - name: team-slack
              type: slack
              webhook_url_env: SLACK_WEBHOOK_URL
        env:
          CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
```

That's it. Push to `main` and a Slack message lands describing what changed.

## What's different in this fork

Compared to the upstream `alphakek-ai/dev-updates-action`:

1. **Email channel** added (Resend backend). See [Email channel](#email-channel) below.
2. **Gmail-tuned HTML renderer** for digest emails. Tight margins, no `<br><br>` voids between blocks, brand-coloured links, monospace code spans.
3. **Optional metric strip** - if you set `DIGEST_METRIC_DONE`, `DIGEST_METRIC_OPEN`, `DIGEST_METRIC_QUESTIONS` env vars in a workflow step before the action runs, a 3-up dashboard card row renders at the top of the email.
4. **Optional activity sparkline** - if you set `DIGEST_SPARKLINE` to a comma-separated list of recent daily commit counts, an inline SVG bar chart renders next to the metric strip.
5. **Auto-status pills** in the email - lines containing `(in progress)`, `(waiting on ...)`, or a `✅ Shipped:` prefix are auto-styled as colored chips so the digest scans like a status board.
6. **Auto subject de-doubling** - if your `subject_prefix` is `[Project]` and the LLM titles the digest "Project Dev Updates ...", the leading "Project" is stripped from the title so the subject only carries the bracket once.
7. **`preview_url` field** on the email channel - if you point this at a staging/preview URL, a "View live preview" link renders in the email header bar (useful for design or marketing repos that ship to a Vercel/Netlify preview).

Everything else is upstream-compatible. Existing telegram/discord/slack/twitter configs continue to work unchanged.

## Required secrets

| Secret | Where to get it | Used by |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | Run `claude setup-token` in your terminal (long-lived OAuth) | All channels (summarization) |
| `RESEND_API_KEY` | https://resend.com/api-keys | Email channel |
| `TELEGRAM_BOT_TOKEN` | @BotFather on Telegram | Telegram channel |
| Discord/Slack webhook URLs | Server / workspace settings | Discord/Slack channels |
| Twitter API keys (4) | https://developer.twitter.com | Twitter channel |

Configure only the secrets for channels you actually use.

> **Sharp edge:** secrets pasted from a terminal sometimes carry a trailing `\r\n`, which silently breaks HTTP `Authorization` headers. If you see 401s with otherwise-valid keys, sanitize before passing to the action:
>
> ```yaml
> - name: Sanitize secrets
>   shell: bash
>   env:
>     RAW_OAUTH: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
>     RAW_RESEND: ${{ secrets.RESEND_API_KEY }}
>   run: |
>     CLEAN_OAUTH="$(printf '%s' "$RAW_OAUTH" | tr -d '\r\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
>     CLEAN_RESEND="$(printf '%s' "$RAW_RESEND" | tr -d '\r\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
>     echo "::add-mask::$CLEAN_OAUTH"
>     echo "::add-mask::$CLEAN_RESEND"
>     echo "CLAUDE_CODE_OAUTH_TOKEN=$CLEAN_OAUTH" >> "$GITHUB_ENV"
>     echo "RESEND_API_KEY=$CLEAN_RESEND" >> "$GITHUB_ENV"
> ```

## Channel reference

Channels are inline YAML in the `channels` input. Each entry needs a `type`. Other fields depend on the type.

### Email channel

| Field | Required | Description |
|---|---|---|
| `type` | yes | `email` |
| `to` | yes | Recipients. Inline-array YAML (`["a@x", "b@y"]`) or comma-separated string. |
| `from` | yes | Sender. Format: `"Name <addr@domain>"`. The domain must be verified in Resend. |
| `subject_prefix` | no | Prepended to the auto-generated subject. Default: `"[Dev]"`. Use `"[Project]"` style; if the LLM also titles the digest "Project ...", the duplicate is stripped automatically. |
| `reply_to` | no | Reply-to address. |
| `preview_url` | no | URL to a live preview/staging site. If set, a "View live preview" link renders ahead of the GitHub link in the email header. Useful for design or website repos. |
| `api_key_env` | no | Env var holding the Resend API key. Default: `RESEND_API_KEY`. |
| `mode` | no | `dev` (default - includes a github-link footer) or `community` (link omitted). |

Example:

```yaml
- name: team-email
  type: email
  mode: dev
  to: ["alice@example.com", "bob@example.com"]
  from: "Updates <updates@yourdomain.com>"
  subject_prefix: "[ProjectName]"
  reply_to: "alice@example.com"
  preview_url: "https://your-app.vercel.app"
```

#### Sender domain setup (one-time, ~15 minutes)

1. Verify your sending domain at https://resend.com/domains
2. Add the SPF, DKIM, and DMARC DNS records Resend lists for your domain
3. Wait ~5-15 minutes for propagation; Resend will mark the domain "verified"

Until the domain is verified, Resend rejects sends with a 403.

#### Email visuals (optional)

The fork supports three optional visual additions to the email body. Each is opt-in via env var; if you don't set the env var, nothing renders and the digest looks the same as before.

**Metric strip - 3-up dashboard cards.** Set these in a workflow step before the action runs:

```yaml
- name: Compute digest metrics
  shell: bash
  run: |
    # Replace with however you count work in your repo - tracker rows in a
    # markdown file, GitHub issue queries, project-board API calls, etc.
    DONE=$(grep -cE "^- \`T[0-9]+ \| [^|]+ \| done \|" plan.md || echo 0)
    OPEN=$(grep -cE "^- \`T[0-9]+ \| [^|]+ \| (open|in_progress) \|" plan.md || echo 0)
    QS=$(grep -cE "^- \`Q[0-9]+ \| [^|]+ \| open \|" plan.md || echo 0)
    echo "DIGEST_METRIC_DONE=$DONE" >> "$GITHUB_ENV"
    echo "DIGEST_METRIC_OPEN=$OPEN" >> "$GITHUB_ENV"
    echo "DIGEST_METRIC_QUESTIONS=$QS" >> "$GITHUB_ENV"
```

The card labels are fixed ("Shipped", "Open tasks", "Open questions"); fork the renderer in `dispatch.py` (`_render_metric_strip`) if you want different labels.

**Activity sparkline - 14-day commit bars.** Same idea:

```yaml
- name: Compute sparkline
  shell: bash
  run: |
    SPARK=""
    for i in 13 12 11 10 9 8 7 6 5 4 3 2 1 0; do
      DAY=$(date -u -d "$i days ago" +%Y-%m-%d 2>/dev/null || date -u -v-${i}d +%Y-%m-%d)
      CNT=$(git log --since="${DAY} 00:00:00" --until="${DAY} 23:59:59" --oneline | wc -l | tr -d ' ')
      if [ -z "$SPARK" ]; then SPARK="$CNT"; else SPARK="$SPARK,$CNT"; fi
    done
    echo "DIGEST_SPARKLINE=$SPARK" >> "$GITHUB_ENV"
```

The bars render in inline SVG (~560x36px), with the most recent day in primary gold and prior days in muted gold. Total commit count is shown in the strip caption.

**Status pills - inline colored chips.** No env var needed. The renderer scans the rendered HTML and replaces:

- `✅ Shipped:` -> green DONE pill
- `(in progress)` -> amber IN PROGRESS pill
- `(waiting on X)` -> red BLOCKED pill plus "waiting on X" caption

This lines up with a common `dev_rules` convention: have the LLM emit `✅ Shipped: T15 - description` for completed items and append `(in progress)` or `(waiting on Person)` to in-flight items.

#### Email styling notes (Gmail tuned)

The HTML output is tuned for Gmail rendering, which expands blank lines and stacks margins aggressively:

- Heading and list margins cut roughly in half vs. upstream defaults so the digest doesn't read as airy
- Blank-line gaps between blocks are stripped (block-level `<h1>`/`<h2>`/`<ul>`/`<ol>` carry their own margins; doubling them creates visual voids)
- Lists render with 16px body, gold-themed link color, monospace code spans
- Table-based 3-column metric strip uses `cellpadding/cellspacing` rather than CSS gap so Outlook also renders it cleanly

If you're sending to a non-Gmail-heavy audience, fork and re-tune `_markdown_to_html` in `dispatch.py`.

### Telegram channel

| Field | Required | Description |
|---|---|---|
| `type` | yes | `telegram` |
| `chat_id` | yes | Target chat or channel ID |
| `thread_id` | no | Forum thread ID for groups with topics enabled |
| `bot_token_env` | no | Env var holding the bot token. Default: `TELEGRAM_BOT_TOKEN`. |
| `mode` | no | `dev` (links to GitHub) or `community` (no link) |

### Discord channel

| Field | Required | Description |
|---|---|---|
| `type` | yes | `discord` |
| `webhook_url` or `webhook_url_env` | yes | Webhook URL (inline) or env var holding it |

### Slack channel

| Field | Required | Description |
|---|---|---|
| `type` | yes | `slack` |
| `webhook_url` or `webhook_url_env` | yes | Incoming-webhook URL (inline) or env var |

### Twitter channel

| Field | Required | Description |
|---|---|---|
| `type` | yes | `twitter` |
| `api_key_env` | no | Default: `TWITTER_API_KEY` |
| `api_secret_env` | no | Default: `TWITTER_API_SECRET` |
| `access_token_env` | no | Default: `TWITTER_ACCESS_TOKEN` |
| `access_token_secret_env` | no | Default: `TWITTER_ACCESS_TOKEN_SECRET` |
| `max_length` | no | Truncate the tweet to N chars. `0` disables. |
| `mode` | no | `dev` (links to GitHub) or `community` (no link) |

## Writing good `dev_rules`

`dev_rules` is the heart of the customization. It's templated into a `TASK="..."` bash variable and passed to Claude Code, so:

1. **Never use unescaped double quotes or backticks in `dev_rules`.** They break the bash assignment. Use single quotes or rephrase.
2. **Be specific about formatting.** Tell the model:
   - Bulleted vs. numbered lists
   - One-line vs. multi-line items
   - Whether to lead with file paths or business impact
   - Whether to include cross-reference IDs
3. **Provide good/bad examples.** A short before/after pair pinned to the rules is the highest-leverage thing you can add.
4. **Sub-grouping threshold.** Specify when the model should switch from a flat list to H3 sub-groupings (typical: more than 6 items per section).
5. **Section ordering.** If certain topics should surface first (e.g. revenue-driving work for an executive audience), state that explicitly.

Example fragment that works well for non-technical readers:

```text
WRITING STYLE - one short line per item. Lead with business 'why', not
implementation 'how'. Skip file paths in prose. Compare:
  BAD: 'Tightened Gmail rendering in tools/dispatch.py - heading and list
        margins cut, blank-line gaps replaced with single newlines.'
  GOOD: 'Digest email renders cleanly in Gmail, no more excess whitespace.'

LIST FORMAT: bulleted markdown lists. Each item starts with its T-id or
Q-id prefix (e.g. '- T15 - description'). The ID is the cross-reference,
no need for a redundant counter.

For done items, prefix with '✅ Shipped:' so the renderer can promote the
line to a green DONE pill. For in-flight items, append '(in progress)' or
'(waiting on Person)' so the renderer can show an amber or red pill.
```

## Triggering and cooldown

Triggers:

- **`push`** to a tracked branch (typical)
- **`workflow_dispatch`** for manual runs from the Actions tab
- **`schedule`** for a cron safety net (e.g. daily at a fixed time)

Cooldown:

- The action records the last successful send timestamp.
- If `now - last < cooldown`, the run is skipped.
- Useful when you have both push triggers and a daily cron - the cron only fires if no push went out in the last 24h.
- Set `cooldown: 0` to disable.

## Local testing

```bash
export CLAUDE_CODE_OAUTH_TOKEN=...
export RESEND_API_KEY=...
export REPO=your/repo
export COMMITS=1
export FILES=3
export DIGEST_METRIC_DONE=12
export DIGEST_METRIC_OPEN=8
export DIGEST_METRIC_QUESTIONS=3
export DIGEST_SPARKLINE="0,1,0,3,2,1,0,4,2,1,0,5,3,2"
export CHANNELS='- name: test
  type: email
  mode: dev
  to: ["you@example.com"]
  from: "Test <test@yourdomain.com>"
  subject_prefix: "[Test]"
  preview_url: "https://example.com"
'

# Drop a sample summary
mkdir -p /tmp
cat > /tmp/summary_dev.md <<'EOF'
# Dev Updates - Test

## What is new in this push
- ✅ Shipped: T18 - Hover state on the primary CTA polished site-wide.
- T22 - New API endpoint scaffolded (in progress).
- T23 - Auth integration (waiting on vendor sandbox access).
EOF

python3 dispatch.py
```

## Versioning

`v1` is a moving tag pinned to the latest stable commit on `main`. For stricter reproducibility, pin to a specific SHA in your workflow:

```yaml
uses: vinnycorp/dev-updates-action@<commit-sha>
```

Re-tagging `v1` after a backward-compatible change:

```bash
git tag -d v1
git push origin :refs/tags/v1
git tag v1
git push origin v1
```

## Contributing

PRs welcome. Bug fixes for the email renderer or new channel types are especially appreciated. Keep `dispatch.py` dependency-free where practical (stdlib `urllib` over `requests`); the action runs in a `uv run` environment that doesn't pre-install third-party packages, and the email channel uses only stdlib intentionally.

## License

MIT, inheriting the upstream license.

## Credits

- Upstream: [alphakek-ai/dev-updates-action](https://github.com/alphakek-ai/dev-updates-action)
- Email adapter + Gmail-tuned renderer + visuals: this fork
