"""Read-only Linear evidence for dev digests. No delivery or mutations here."""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib import request, error

ENDPOINT = 'https://api.linear.app/graphql'
PAGE = 'pageInfo { hasNextPage endCursor }'
ISSUE_FIELDS = '''id identifier title description url createdAt updatedAt archivedAt
completedAt priority dueDate assignee { id name } state { id name type }
labels(first: 250) { nodes { name } pageInfo { hasNextPage } }'''
HISTORY_FIELDS = '''id createdAt autoClosed autoArchived fromState { name type } toState { name type }
fromPriority toPriority fromAssigneeId toAssigneeId fromDueDate toDueDate
updatedDescription'''
NOISE_TITLE = re.compile(r'^(archived source|historical |superseded)|document index$|meeting register$| - index$', re.I)
IMPORT = re.compile(r'imported source record|historical import|migration source', re.I)


class CoverageError(Exception):
    pass


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path, default=None):
    return json.loads(Path(path).read_text(encoding='utf-8-sig')) if path and Path(path).exists() else default


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def words(value):
    # Link destinations, layout and metadata alone do not constitute product news.
    value = re.sub(r'\[([^]]+)\]\([^)]*\)', r'\1', value or '')
    return re.findall(r'[\w%$]+', value.lower())


def substantive(before, after):
    a, b = words(before), words(after)
    if a == b:
        return False
    changes = [op for op in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op[0] != 'equal']
    changed = sum(max(j-i, l-k) for _, i, j, k, l in changes)
    # Small consequential number/decision edits still count; punctuation and typos do not.
    important = any(re.search(r'\d|approved|blocked|cancelled|canceled|deadline|budget|decision', ' '.join(a[i:j]+b[k:l])) for _, i, j, k, l in changes)
    return changed >= 20 or important


class Client:
    def __init__(self, key):
        if not key or '\n' in key or '\r' in key:
            raise CoverageError('LINEAR_API_KEY is missing or malformed')
        self.key = key.strip()

    def query(self, query, variables=None):
        payload = json.dumps({'query': query, 'variables': variables or {}}).encode()
        for attempt in range(4):
            try:
                req = request.Request(ENDPOINT, payload, {'Authorization': self.key, 'Content-Type': 'application/json'})
                with request.urlopen(req, timeout=45) as response:
                    result = json.load(response)
                if result.get('errors') or not isinstance(result.get('data'), dict):
                    raise CoverageError('Linear returned GraphQL errors; coverage incomplete')
                return result['data']
            except error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise CoverageError(f'Linear HTTP {exc.code}; coverage incomplete') from None
            except (error.URLError, TimeoutError, json.JSONDecodeError):
                raise CoverageError('Linear transport failed; coverage incomplete') from None

    def pages(self, query, variables, field, nested=None):
        nodes, seen = [], set()
        cursor = None
        for _ in range(200):
            data = self.query(query, dict(variables, after=cursor))
            connection = data[field][nested] if nested else data[field]
            nodes.extend(connection['nodes'])
            info = connection['pageInfo']
            if not info['hasNextPage']:
                return nodes
            cursor = info.get('endCursor')
            if not cursor or cursor in seen:
                raise CoverageError('Linear pagination stalled')
            seen.add(cursor)
        raise CoverageError('Linear pagination exceeded safety bound; no cursor saved')


def collect(client, config, since, cutoff):
    identity = client.query('query($id: String!) { organization { urlKey } team(id: $id) { id key } }', {'id': config['team_id']})
    if identity['organization']['urlKey'] != config['workspace'] or identity['team'] != {'id': config['team_id'], 'key': config['team']}:
        raise CoverageError('Linear workspace/team does not match configuration')
    issues = client.pages('query($filter: IssueFilter!, $after: String) { issues(filter: $filter, first: 50, after: $after, includeArchived: true) { nodes { '+ISSUE_FIELDS+' } '+PAGE+' } }',
                          {'filter': {'team': {'id': {'eq': config['team_id']}}, 'updatedAt': {'gte': since}}}, 'issues')
    for issue in issues:
        if issue['labels']['pageInfo']['hasNextPage']:
            raise CoverageError('Issue labels truncated')
        issue['labels'] = [n['name'] for n in issue['labels']['nodes']]
        # No timestamp filter is supported by Issue.history. Paginate fully.
        issue['history'] = client.pages('query($id: String!, $after: String) { issue(id: $id) { history(first: 100, after: $after, includeArchived: true) { nodes { '+HISTORY_FIELDS+' } '+PAGE+' } } }', {'id': issue['id']}, 'issue', 'history')
    # Workspace-wide documents are explicitly opted into by these dedicated workspaces.
    doc_filter = {} if config.get('documents') == 'workspace' else {'team': {'id': {'eq': config['team_id']}}}
    docs = client.pages('query($filter: DocumentFilter!, $after: String) { documents(filter: $filter, first: 50, after: $after, includeArchived: true) { nodes { id title content url createdAt updatedAt archivedAt } '+PAGE+' } }', {'filter': doc_filter}, 'documents')
    return {'workspace': config['workspace'], 'team': config['team'], 'team_id': config['team_id'], 'since': since, 'cutoff': cutoff, 'complete': True, 'transport': 'Linear GraphQL', 'issues': issues, 'documents': docs}


def evaluate(snapshot, config, previous=None):
    previous = previous or {}
    for field in ('workspace', 'team', 'team_id'):
        if snapshot.get(field) != config[field]:
            raise CoverageError('Snapshot identity mismatch')
    if snapshot.get('complete') is not True:
        raise CoverageError('Snapshot coverage incomplete')
    if previous and any(previous.get(k) != config[k] for k in ('workspace', 'team', 'team_id')):
        raise CoverageError('Saved cursor belongs to another workspace/team')
    since, cutoff = stamp(snapshot['since']), stamp(snapshot['cutoff'])
    if previous.get('cursor') and stamp(previous['cursor']) != since:
        raise CoverageError('Snapshot does not start at saved cursor')
    if cutoff < since:
        raise CoverageError('Invalid coverage window')
    entries, filtered = [], []
    next_state = dict(previous, **{k: config[k] for k in ('workspace', 'team', 'team_id')})
    next_state.update(cursor=snapshot['cutoff'], issues=dict(previous.get('issues', {})), documents=dict(previous.get('documents', {})))
    for issue in snapshot['issues']:
        updated = stamp(issue['updatedAt'])
        if updated > cutoff:
            # Do not consume a future update or advance over its earliest unseen event.
            raise CoverageError('Issue changed during collection; retry a fresh snapshot')
        if updated <= since:
            continue
        labels = [x.lower() for x in issue.get('labels', [])]
        old = previous.get('issues', {}).get(issue['id'])
        reasons = []
        imported = bool(IMPORT.search(issue.get('description') or '') or 'historical' in labels)
        created = stamp(issue['createdAt'])
        for event in issue.get('history', []):
            at = stamp(event['createdAt'])
            if not since < at <= cutoff or event.get('autoClosed') or event.get('autoArchived'):
                continue
            # Initial imported Done timestamps are migration dates, not delivery evidence.
            if imported and abs((at-created).total_seconds()) < 300:
                continue
            target = event.get('toState') or {}
            source = event.get('fromState') or {}
            if ('block' in target.get('name', '').lower()) != ('block' in source.get('name', '').lower()):
                reasons.append('blocker raised' if 'block' in target.get('name', '').lower() else 'blocker cleared')
            if target and source != target and target.get('type') in ('started', 'completed', 'canceled'):
                reasons.append('state: '+target['name'])
            if event.get('toPriority') in (1, 2) and event.get('fromPriority') != event['toPriority']:
                reasons.append('priority raised to urgent/high')
            active = issue.get('state', {}).get('type') == 'started' or issue.get('priority') in (1, 2)
            for field, title in [('AssigneeId', 'owner'), ('DueDate', 'due date')]:
                if active and event.get('from'+field) != event.get('to'+field):
                    reasons.append(title+' changed on active/high-priority work')
        if since < created <= cutoff and not imported:
            if 'decision' in labels and issue['state']['type'] == 'completed':
                reasons.append('adopted decision')
            elif issue['state']['type'] == 'started' or issue.get('priority') in (1, 2):
                reasons.append('new active/high-priority work')
        if old:
            old_blocked = any('block' in x.lower() for x in old.get('labels', [])) or 'block' in old.get('state', {}).get('name', '').lower()
            blocked = any('block' in x for x in labels) or 'block' in issue['state']['name'].lower()
            if old_blocked != blocked:
                reasons.append('blocker raised' if blocked else 'blocker cleared')
            if substantive(old.get('description'), issue.get('description')):
                reasons.append('substantive issue update')
        next_state['issues'][issue['id']] = {k: issue.get(k) for k in ('description', 'labels', 'state')}
        if reasons:
            entries.append({'kind': 'issue', 'id': issue.get('identifier', issue['id']), 'title': issue['title'], 'url': issue['url'], 'reasons': sorted(set(reasons)), 'status': issue['state']['name'], 'evidence': (issue.get('description') or '')[-6000:]})
        else:
            filtered.append({'id': issue.get('identifier', issue['id']), 'reason': 'housekeeping, comment-only, import, or no evidenced major change'})
    for doc in snapshot['documents']:
        if stamp(doc['updatedAt']) > cutoff:
            raise CoverageError('Document changed during collection; retry a fresh snapshot')
        old = previous.get('documents', {}).get(doc['id'])
        content = doc.get('content') or ''
        next_state['documents'][doc['id']] = {'content': content, 'hash': hashlib.sha256(content.encode()).hexdigest()}
        recent = since < stamp(doc['updatedAt']) <= cutoff
        excluded = doc.get('archivedAt') or NOISE_TITLE.search(doc['title'])
        major = old is not None and substantive(old['content'], content)
        fresh = since < stamp(doc['createdAt']) <= cutoff and len(words(content)) >= 40
        if recent and not excluded and (major or fresh):
            excerpt = '\n'.join(difflib.unified_diff((old or {}).get('content', '').splitlines(), content.splitlines(), n=2))[:6000]
            entries.append({'kind': 'document', 'id': doc['id'], 'title': doc['title'], 'url': doc['url'], 'reasons': ['new substantive document' if fresh and old is None else 'substantive document edit'], 'evidence': excerpt})
        elif recent:
            filtered.append({'id': doc['id'], 'reason': 'navigation/archive, minor edit, or first observation establishes baseline'})
    return {'complete': True, 'workspace': config['workspace'], 'team': config['team'], 'since': snapshot['since'], 'cutoff': snapshot['cutoff'], 'transport': snapshot['transport'], 'entries': entries, 'filtered': filtered}, next_state


def commit_state(state, proposed, *, dry_run, dispatched, no_activity, sha=None):
    """Only confirmed no-op or successful delivery may commit an observation cursor."""
    result = dict(state)
    if dry_run or not (dispatched or no_activity):
        return result
    result['linear'] = proposed
    if dispatched:
        result.update(last_sha=sha, last_at=int(time.time()))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--state', default='/tmp/state/dev-updates-state.json')
    parser.add_argument('--snapshot')
    parser.add_argument('--output', default='/tmp/linear-evidence.json')
    parser.add_argument('--proposed-state', default='/tmp/linear-proposed-state.json')
    parser.add_argument('--preview', default='/tmp/linear-preview.md')
    parser.add_argument('--since')
    args = parser.parse_args()
    config = read_json(args.config)
    previous = read_json(args.state, {}).get('linear', {})
    cutoff = now()
    since = previous.get('cursor') or args.since or (stamp(cutoff)-timedelta(hours=24)).isoformat()
    snapshot = read_json(args.snapshot) if args.snapshot else collect(Client(os.environ.get('LINEAR_API_KEY')), config, since, cutoff)
    evidence, proposed = evaluate(snapshot, config, previous)
    write_json(args.output, evidence)
    write_json(args.proposed_state, proposed)
    lines = [f"# {config['team']} Linear dry-run preview", '', f"Coverage: {evidence['since']} to {evidence['cutoff']} ({evidence['transport']}).", '', 'Tracker evidence is not deployment verification. No delivery or cursor writes.', '']
    for item in evidence['entries']:
        lines.append(f"- [{item['title']}]({item['url']}): {', '.join(item['reasons'])}.")
    if not evidence['entries']:
        lines.append('No major Linear changes evidenced in this window.')
    lines.append(f"\nFiltered {len(evidence['filtered'])} housekeeping/baseline observations.")
    Path(args.preview).write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(f"{config['team']}: {len(evidence['entries'])} major entries; {len(evidence['filtered'])} filtered; complete read; no delivery")
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
            stream.write(f"major_count={len(evidence['entries'])}\n")


if __name__ == '__main__':
    try:
        main()
    except CoverageError as exc:
        sys.exit(str(exc))
