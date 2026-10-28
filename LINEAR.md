# Linear evidence adapter

The opt-in adapter combines Git activity, changed repo documents and authenticated
Linear evidence. A major Linear-only update can produce a digest with zero commits.
It never writes to Linear. Personal read-only API keys use Linear's documented
Authorization header at https://api.linear.app/graphql. Do not export desktop
connector OAuth credentials into CI.

## Configuration

Add a private-repository Actions secret named `LINEAR_API_KEY`, scoped to the
intended workspace/team with read-only permissions. Use separate keys for AUC and
PIR. Store workspace/team identifiers in a tracked JSON configuration, not the key:

```json
{"workspace":"workspace-slug","team":"TEAM","team_id":"team-uuid","documents":"workspace"}
```

Pass `linear_config: .github/dev-digest-linear.json`, `cooldown: '24h'` and the
secret through the action's environment. Workspace identity and team ID/key are
checked before collection. `documents: workspace` explicitly opts into documents
throughout a dedicated workspace, including unparented documents. Otherwise only
documents directly attached to the selected team are read. Do not opt a client
digest into an unrelated multi-client workspace. Document and issue content may
be confidential; only use approved private development audiences.

## What counts

- State transitions into active work, completion or cancellation; urgent/high
  priority escalation; owner/due-date changes on active or high-priority work.
- New active/high-priority issues and new adopted decisions. Decisions and plans
  remain distinct from implementation, deployment and live verification.
- Blocker labels/states changing, and substantive issue/document content changes
  against the previous observation. Twenty changed words qualify, as do small
  changes containing numbers or consequential decision/deadline/budget terms.
  This is an explicit heuristic, not a guarantee of editorial significance.

Ignore comment-only updates, routine label/title edits, punctuation/whitespace,
minor typos, link-destination edits, navigation hubs, archived/superseded documents
and import-time Done events. Historical issues can still have real later updates.
No previous document/description baseline means an existing document establishes
a baseline, not invented edit history. New substantive documents can qualify.
The first collection looks back 24 hours rather than replaying all history.

Each issue yields one evidence item, with source links and current status. The
summary instructions merge Git/Linear duplicates using issue IDs and PR links.
This editorial merge is performed by the existing summarizer; the deterministic
dry-run preview verifies collection/filtering, not final LLM prose.

## State and failure behavior

Git delivery state (`last_sha`, `last_at`) and the Linear observation cursor are
independent. The existing private `dev-updates-state` artifact also stores issue
and document baselines. It contains private source content, never API credentials;
retain the repository's private artifact access and 90-day retention. State loss
restarts the bounded lookback and cannot guarantee recovery beyond that window.

Paginate issues, documents and issue history completely. HTTP errors, HTTP-200
GraphQL errors/partial data, pagination truncation, identity mismatch or entities
changing after the collection cutoff fail the job before delivery. No state is
advanced on failures. A fully verified no-op may advance the Linear observation
baseline without changing the Git delivery SHA/time. Successful delivery commits
both cursors. Keep workflow concurrency serialized. Channel partial success can
still cause repeats on retry, as with the existing delivery adapter.

## Dry run

`dry_run: 'true'` performs collection/filtering and writes a GitHub job-summary
preview. It skips the LLM, delivery dispatch, board writes and state upload. It
does not require Claude, Resend or any other delivery secret. It still requires
the read-only Linear key for GitHub-hosted collection.

For local offline evaluation of an authenticated connector snapshot:

```sh
python linear_adapter.py --config .github/dev-digest-linear.json --snapshot /private/snapshot.json --state /private/prior-state.json --output /private/evidence.json --proposed-state /private/proposed.json --preview /private/preview.md
```

The CLI always proposes state to a separate file and never delivers or commits
the source cursor. Keep snapshots/previews outside Git. A connector snapshot must
declare complete pagination and identity, contain full issue/document content,
and disclose its transport and any history fields unavailable to that connection.
Connector-backed evaluation does not verify the GitHub API credential or live
GraphQL transport. Unit tests cover transport failure and pagination with mocks.

Before enabling email, verify an actual GitHub dry run with the configured key.
Auctus has an existing daily email; Pirate Face currently has manual preview
only. No recipients or schedule are inferred for Pirate Face.
