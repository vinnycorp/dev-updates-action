# Setting up `dev-updates-action` in a new repo

A complete, copy-pasteable ritual for adopting this action in a fresh
project. Designed to be handed to a coding agent (Claude Code, Cursor,
etc.) as a single self-contained brief.

The action itself is just one piece. The full workflow needs three
layers to be useful:

1. **The GitHub Action** (this repo) — composes the digest, dispatches
   to channels.
2. **A plan file** in your repo — single source of truth that the
   digest reads from. Conventionally `{project}-plan.md`.
3. **A `CLAUDE.md` (or equivalent agent contract)** — tells every
   working session to keep the plan's tracker tables current as work
   proceeds. Without this rule the plan rots and the digest stops
   being useful.

This doc walks through all three. If you already have an `AGENTS.md`,
`Claude.md`, or `.cursor/rules` file, adapt the "Trackers" rule below
into it; the file name doesn't matter, the contract does.

## Why three layers

A simpler "summarise the git diff and email it" tool was always going
to plateau at the same place: pure-diff digests miss the half of the
work that lives in tracker rows (decisions made, questions still open,
tasks in flight). The plan file captures that. The agent contract
keeps the plan honest. The action stitches the two together and
delivers the result somewhere the team reads.

## What you'll have at the end

- A markdown plan file with three tracker tables (open questions,
  action items, decisions log)
- A CLAUDE.md (or equivalent) rule that every session updates those
  tables as it works
- A GitHub Actions workflow that fires on push + once a day, with a
  24h cooldown, sanitised secrets, and a digest tuned for your
  audience
- Secrets configured in the repo
- A first digest in your team's inbox

## Drop-in prompt for a new repo's coding session

Paste this verbatim into a fresh Claude Code (or other agent) session
in the target repo. The agent will ask you a few questions, then
execute the four pieces below.

---

**Set up the `vinnycorp/dev-updates-action` workflow for this repo.**

Reference implementation: <https://github.com/vinnycorp/dev-updates-action>
(see `SETUP_NEW_REPO.md` and the example workflow in the README's
Quickstart). Do the four pieces below; ask me clarifying questions
before each piece if the answer isn't obvious from the repo context.

### 1. Create the plan file

`{project-slug}-plan.md` at the repo root (e.g. `acme-plan.md`).
Include a Section 7 with three sub-tables. Use these exact row
formats so the digest parser can read them:

- `### 7.1 Open questions`
  - Format: `` `Q{id} | addressee | status | date opened | question (+ answer inline when answered)` ``
  - Statuses: `open`, `answered`
- `### 7.2 Action items`
  - Format: `` `T{id} | owner | status | date opened | description (+ outcome inline when done)` ``
  - Statuses: `open`, `in_progress`, `done`
- `### 7.3 Decisions log`
  - Format: `` `D{id} | YYYY-MM-DD | decision + rationale` ``

Append-only is the rule. Never delete rows. Flip status in place and
append the outcome / answer inline after a `→` separator.

Seed each table with one or two real rows so the agent contract has
something concrete to maintain.

### 2. Add a `CLAUDE.md` (or equivalent agent contract)

At the repo root. Include a "Trackers" section that says, verbatim:

> Every session — whether started from the command line, the IDE, or a
> coworker handoff — reads the tracker tables in `{project}-plan.md`
> Section 7 at the start of the session, and keeps them current as
> work proceeds. When a question gets answered, flip `Q{id}` to
> `answered` and append the answer inline. When a task gets done,
> flip `T{id}` to `done` and append the outcome inline. When a
> decision is made, add a `D{id}` row. Never delete rows.

If your project uses Cursor (`.cursorrules`) or another agent, port
the same rule into its conventions file.

### 3. Drop the workflow at `.github/workflows/dev-updates.yml`

Start from the Quickstart in the action's README and customise. Keep
these defences in place — both have bitten real deployments:

- The **Sanitize secrets** step. Strips `\r\n` and surrounding
  whitespace from pasted-in tokens before they reach HTTP headers.
- The **cooldown** of `24h` so push triggers and the daily cron don't
  double-fire.
- The **schedule cron** at a UTC time that's morning local time for
  the recipient (e.g. `0 20 * * *` = 20:00 UTC = early-morning Asia).
- The **`workflow_dispatch`** trigger so you can fire manual runs from
  the Actions tab while iterating on `dev_rules`.

Tune the `dev_rules` block for the audience:

- Reading frame: engineering team / non-technical management / public
  changelog. Each one needs different vocabulary.
- House-style rules: bullets vs prose, em-dash vs regular dash,
  jargon policy, hyperlink policy.
- Section ordering: if revenue-driving work or customer-facing
  changes should surface first, state it explicitly.
- Sub-grouping threshold: when the model should switch from a flat
  list to H3 topic-groups (typical: more than 6 items per section).
- Good / bad examples: pin one or two before/after pairs in the
  rules. Highest-leverage thing you can add.

Tune the `channels` block for the delivery target. Pick from email,
Slack, Discord, Telegram, Twitter. See the README's Channel
reference for required and optional fields per type.

### 4. Configure GitHub Actions secrets

In the repo's `Settings → Secrets and variables → Actions`:

- `CLAUDE_CODE_OAUTH_TOKEN` — run `claude setup-token` locally and
  paste the long-lived OAuth value.
- One of: `RESEND_API_KEY` (email) / `SLACK_WEBHOOK_URL` (Slack) /
  `DISCORD_WEBHOOK_URL` (Discord) / `TELEGRAM_BOT_TOKEN` (Telegram).
- If email: verify the sending domain in Resend with SPF + DKIM +
  DMARC at the DNS provider. Until verified, Resend rejects sends
  with a 403.

### Sharp edges to defend against

Both have bitten real deployments:

- **`dev_rules` must not contain unescaped double quotes or backticks.**
  The action templates rules into a bash `TASK="..."` assignment;
  inner double quotes break it. Use single quotes or rephrase.
- **Secrets pasted from a terminal often arrive with trailing CR/LF.**
  This silently breaks HTTP `Authorization` headers — the request
  looks valid in the workflow log but the upstream API returns a 401
  or 403 with an opaque error. The Sanitize-secrets step in the
  Quickstart workflow strips this before use.

### Verify

When you're done, push a no-op commit (or click "Run workflow" on
`workflow_dispatch`) and watch the Actions tab. The first digest
should land at the configured delivery target within 30 seconds of
the run completing.

If it doesn't:

- Check that the Sanitize step printed non-zero lengths for the
  cleaned tokens.
- Check that `dev_rules` has no unescaped double quotes / backticks.
- For email: check that the sending domain shows as "verified" in
  Resend.
- For webhooks: check that the URL is correct (no path typos) and
  the channel is alive.

---

## Anatomy of a working digest

For reference, here's what a tuned digest looks like in production
(from `vinnycorp/auctus`, an audience-focused engagement repo where
the recipient is a non-technical management team):

```
# Dev Updates - 13 May 2026

## What is new in this push
- 🎨 How-we-work cards now read as a clean visual progression.
- 📊 New "metals YTD divergence" chart on /which-precious-metals.
- 🛠 Daily economic ingest no longer breaks on a single upstream flake.
- ✅ Shipped: T44 - How-we-work cards now read as a clean visual progression.
- ✅ Shipped: T68 - Patrick's "different dogs" YTD divergence chart is live.

## Decisions made
- D24 - Drop the 1M/3M/6M/YTD selector on the YTD chart - the values
  don't recompute on zoom, so the buttons were decorative.

## Task-list changes
- T71 - Build a Month-to-date toggle for the YTD chart (open).
- T70 - Build social-media-friendly chart PNG export (open).

## Still open questions
- Q15 - Confirm preferred AUM-style metric to surface on the
  homepage. (assigned to Justin)
```

Each bullet is one short line. Each item carries its cross-reference
(`T44`, `Q15`, `D24`) so the team can jump back to the plan file for
the long form. The tone matches the audience.

The same digest for a developer audience would be three paragraphs of
implementation detail. Same workflow, same plan file, different
`dev_rules`. That's the customisation knob.

## Common follow-ups

- **Metric strip on email digests.** Compute `DIGEST_METRIC_DONE` /
  `DIGEST_METRIC_OPEN` / `DIGEST_METRIC_QUESTIONS` in a workflow step
  before the action runs. The renderer drops them into a 3-up
  dashboard card row at the top of the email. See the README's "Email
  visuals (optional)" section for the exact bash.
- **Activity sparkline.** Same idea, with a 14-day commit-bar SVG.
- **Status pills.** No setup needed. Items prefixed `✅ Shipped:` or
  suffixed `(in progress)` / `(waiting on X)` get auto-styled as
  coloured chips in the email.
- **Static kanban board.** The digest tells the team what *changed*;
  a board shows the *whole state at a glance*. Set `dashboard: 'true'`
  plus `dashboard_plan_file` on the action step and the run renders the
  Section 7 tables into a single self-contained, themeable HTML file
  (`board.html`) and commits it back to the repo - a four-column task
  kanban, a questions lane, and a decisions timeline, with search,
  filters, and per-card deep links to the plan. Deterministic (no LLM),
  independent of the digest cooldown, and the commit-back uses
  `GITHUB_TOKEN` so it never re-triggers the workflow. Grant the job
  `contents: write`. Full input table in the README's "Static board"
  section.

## Updating the digest behaviour later

The `dev_rules` block is the customisation knob. Edit it in place,
push, and the next digest follows the new rules. There's no separate
configuration service to keep in sync — the rules live next to the
workflow that uses them.

If you want to test a change without firing a real digest, set the
cooldown briefly to `0`, run `workflow_dispatch`, watch the run log,
restore the cooldown. The action prints the rendered output to the
workflow log even if it doesn't dispatch.
