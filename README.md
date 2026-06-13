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

> **Adopting this in a new repo?** Read [SETUP_NEW_REPO.md](./SETUP_NEW_REPO.md) for the full ritual — plan file, agent contract, workflow, secrets — as a single self-contained brief you can hand to a coding agent. The Quickstart below covers just the workflow piece.

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

      # Sanitize: strip CR/LF and surrounding whitespace from pasted-in
      # tokens before they reach HTTP headers. Secrets pasted from a
      # terminal often arrive with a trailing \r\n, which silently
      # breaks the Authorization header on Resend / Slack / Telegram
      # / Twitter. Keep this step - it bites real deployments often
      # enough that it's part of the canonical Quickstart.
      - name: Sanitize secrets
        shell: bash
        env:
          RAW_OAUTH: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          RAW_SLACK: ${{ secrets.SLACK_WEBHOOK_URL }}
        run: |
          CLEAN_OAUTH="$(printf '%s' "$RAW_OAUTH" | tr -d '\r\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
          CLEAN_SLACK="$(printf '%s' "$RAW_SLACK" | tr -d '\r\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
          echo "::add-mask::$CLEAN_OAUTH"
          echo "::add-mask::$CLEAN_SLACK"
          echo "CLAUDE_CODE_OAUTH_TOKEN=$CLEAN_OAUTH" >> "$GITHUB_ENV"
          echo "SLACK_WEBHOOK_URL=$CLEAN_SLACK" >> "$GITHUB_ENV"

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
        # Env comes from the Sanitize step via $GITHUB_ENV. Do NOT add
        # a step-level `env:` block here - it would override the
        # cleaned values with the raw secrets.
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
8. **Static kanban board** - an optional, deterministic (no-LLM) render of the plan's Section 7 tracker tables into a single self-contained, themeable HTML file, committed back to the repo on every run. See [Static board](#static-board) below.

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

> **Sharp edge:** secrets pasted from a terminal sometimes carry a trailing `\r\n`, which silently breaks HTTP `Authorization` headers and surfaces as 401s with otherwise-valid keys. The **Sanitize secrets** step in the [Quickstart](#quickstart) workflow strips this — keep that step in your workflow. To sanitize different secrets, edit the `RAW_` env block and the corresponding `$GITHUB_ENV` exports.

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
| `preview_url` | no | URL to a live preview/staging site. If set, a link renders ahead of the GitHub link in the email header. Useful for design or website repos. |
| `preview_label` | no | Text for the `preview_url` link. Default: `"View live preview"`. Set e.g. `"View live tracker"` when pointing at a board file rather than a staging site. |
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

## Static board

The digest answers "what changed?" The board answers "what's the state of everything right now?" It's a **deterministic** render - pure parsing, no LLM, no API cost, no hallucinated cards - of the same three tracker tables the digest reads (Section 7.1 open questions, 7.2 action items, 7.3 decisions log). The output is one self-contained HTML file (inline CSS + a sprinkle of vanilla JS, no build step, no hosting): open it straight off disk.

Layout: a four-column task kanban (Open / In progress / Blocked / Done), a separate two-column lane for questions (Open / Answered), and a reverse-chronological decisions timeline. Global search, an owner filter, type toggles, click-to-expand cards, collapsed-by-default Done/Answered columns, and per-card deep links back to the exact line in the plan on GitHub.

Enable it by adding the `dashboard_*` inputs to the same action step:

```yaml
    - uses: vinnycorp/dev-updates-action@v1
      with:
        # ... your channels / dev_rules ...
        dashboard: 'true'
        dashboard_plan_file: 'project-plan.md'
        dashboard_output: 'board.html'          # committed back to the repo
        dashboard_title: 'Project Board'
        dashboard_subtitle: 'live tracker'
        dashboard_theme: |
          { "colors": { "navy": "#0c1828", "gold": "#7e6717" },
            "logo_svg": "<span class=\"wordmark\">ACME</span>" }
```

and grant the job write access so the refreshed board can be committed:

```yaml
    permissions:
      contents: write   # board commit-back
      id-token: write
      actions: read
```

How it stays fresh without looping: the board step runs on every trigger (independent of the digest cooldown), regenerates the HTML, and commits it **only if it changed**. The commit uses the workflow's `GITHUB_TOKEN`, and GitHub deliberately does **not** start a new workflow run for `GITHUB_TOKEN` pushes - so the board commit never re-triggers the digest, and there is no email storm. The "Updated" stamp is the plan file's last commit date (stable per content, so an unchanged plan never churns a new commit), not wall-clock time.

Board inputs:

| Input | Default | Notes |
|---|---|---|
| `dashboard` | `false` | Master switch. |
| `dashboard_plan_file` | `''` | Path to the plan markdown. Required when `dashboard` is true. |
| `dashboard_output` | `board.html` | Output path, committed back to the repo. Don't gitignore it. |
| `dashboard_title` | `Project Board` | Header title. |
| `dashboard_subtitle` | `''` | Header subtitle. |
| `dashboard_theme` | `''` | JSON overrides: `colors`, `fonts`, `font_links`, `logo_svg`. Empty = neutral palette. Same double-quote sharp edge as `dev_rules` - use a YAML block and escape inner quotes. |
| `dashboard_commit` | `true` | Set false to generate without committing (e.g. to upload as an artifact yourself). |
| `dashboard_commit_message` | `chore(board): refresh engagement board [skip ci]` | |

Parsing is forgiving by design: rows are classified by their `Q`/`T`/`D` id prefix regardless of which sub-heading they sit under (a misfiled row still lands in the right lane), and a row whose trailing backtick is missing is still parsed rather than dropped. Run it locally to preview:

```bash
python3 board.py --plan project-plan.md --out board.html \
  --title 'Project Board' --repo-url https://github.com/org/repo \
  --theme theme.json
```

## Writing good `dev_rules`

> **Sharp edge:** `dev_rules` is templated into a `TASK="..."` bash variable inside the action runner. Unescaped double quotes or backticks anywhere in your rules will break the bash assignment and the workflow fails before Claude Code runs. Use single quotes or rephrase. This bites every new adopter at least once — if your run errors out on parsing before any digest output, this is almost always the cause.

`dev_rules` is the heart of the customization. To get a digest that reads in your team's voice:

1. **Be specific about formatting.** Tell the model:
   - Bulleted vs. numbered lists
   - One-line vs. multi-line items
   - Whether to lead with file paths or business impact
   - Whether to include cross-reference IDs
2. **Provide good/bad examples.** A short before/after pair pinned to the rules is the highest-leverage thing you can add.
3. **Sub-grouping threshold.** Specify when the model should switch from a flat list to H3 sub-groupings (typical: more than 6 items per section).
4. **Section ordering.** If certain topics should surface first (e.g. revenue-driving work for an executive audience), state that explicitly.

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
