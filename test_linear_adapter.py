import copy
import json
from unittest.mock import patch

import pytest

from linear_adapter import Client, CoverageError, commit_state, evaluate, substantive

CONFIG = {'workspace': 'test', 'team': 'TST', 'team_id': 'team-1'}
SINCE, CUTOFF = '2026-10-27T00:00:00Z', '2026-10-28T00:00:00Z'


def snapshot(issues=None, docs=None):
    return dict(CONFIG, since=SINCE, cutoff=CUTOFF, complete=True,
                transport='test', issues=issues or [], documents=docs or [])


def issue(**extra):
    return dict(id='id1', identifier='TST-1', title='Fix registration', url='https://linear.app/test/issue/TST-1',
                createdAt='2026-10-01T00:00:00Z', updatedAt='2026-10-27T12:00:00Z',
                description='Registration fix.', labels=[], state={'name': 'Done', 'type': 'completed'},
                priority=0, history=[], **extra)


def event(**extra):
    return dict(id='event1', createdAt='2026-10-27T12:00:00Z', **extra)


def test_linear_only_completion_and_decision():
    a = issue()
    a['history'] = [event(fromState={'name': 'In progress', 'type': 'started'}, toState=a['state'])]
    b = issue()
    b.update(id='id2', identifier='TST-2', labels=['Decision'], createdAt=b['updatedAt'])
    evidence, _ = evaluate(snapshot([a, b]), CONFIG)
    assert len(evidence['entries']) == 2  # independent of any Git range
    assert 'adopted decision' in evidence['entries'][1]['reasons']


def test_comments_and_labels_alone_are_housekeeping():
    a = issue()
    a['history'] = [event(addedLabelIds=['label']), event(updatedDescription=False)]
    evidence, _ = evaluate(snapshot([a]), CONFIG)
    assert not evidence['entries']
    assert len(evidence['filtered']) == 1


def test_import_time_done_is_not_completion_but_later_work_is():
    a = issue()
    a.update(description='Imported source record.', createdAt=a['updatedAt'], labels=['Historical'])
    a['history'] = [event(toState=a['state'])]
    assert not evaluate(snapshot([a]), CONFIG)[0]['entries']
    a['createdAt'] = '2026-10-01T00:00:00Z'
    assert evaluate(snapshot([a]), CONFIG)[0]['entries']


def test_active_owner_due_and_priority_changes():
    a = issue()
    a.update(state={'name': 'In progress', 'type': 'started'})
    a['history'] = [event(fromAssigneeId='a', toAssigneeId='b'), event(fromDueDate=None, toDueDate='2026-11-01'), event(fromPriority=0, toPriority=1)]
    reasons = evaluate(snapshot([a]), CONFIG)[0]['entries'][0]['reasons']
    assert len(reasons) == 3


def test_substantive_and_blocker_changes_from_baseline():
    a = issue()
    old = dict(CONFIG, cursor=SINCE, issues={'id1': {'description': a['description'], 'labels': [], 'state': a['state']}})
    a.update(description='Approved budget is $5000.', labels=['Blocked'])
    reasons = evaluate(snapshot([a]), CONFIG, old)[0]['entries'][0]['reasons']
    assert reasons == ['blocker raised', 'substantive issue update']


def test_layout_typos_and_link_changes_are_not_substantive():
    assert not substantive('Some text', 'Some   text!')
    assert not substantive('[Roadmap](https://old)', '[Roadmap](https://new)')
    assert not substantive('Fix teh form', 'Fix the form')
    assert substantive('Budget is $5000', 'Budget is $6000')
    assert not substantive('Plan unchanged\nLast updated: 2026-10-26', 'Plan unchanged\nLast updated: 2026-10-27')


def test_document_edits_and_navigation_filter():
    doc = dict(id='doc', title='Delivery plan', content='Approved budget is $6000.', url='https://linear.app/doc',
               createdAt='2026-10-01T00:00:00Z', updatedAt='2026-10-27T12:00:00Z')
    old = dict(CONFIG, cursor=SINCE, documents={'doc': {'content': 'Approved budget is $5000.'}})
    assert evaluate(snapshot(docs=[doc]), CONFIG, old)[0]['entries']
    doc['title'] = 'Auctus document index'
    assert not evaluate(snapshot(docs=[doc]), CONFIG, old)[0]['entries']
    doc['title'] = 'Delivery plan'
    assert not evaluate(snapshot(docs=[doc]), CONFIG)[0]['entries']  # unknown history establishes baseline


@pytest.mark.parametrize('change', [{'complete': False}, {'workspace': 'wrong'}, {'team_id': 'wrong'}])
def test_partial_and_wrong_workspace_fail_closed(change):
    value = snapshot()
    value.update(change)
    with pytest.raises(CoverageError):
        evaluate(value, CONFIG)


def test_concurrent_update_does_not_advance_cursor():
    a = issue()
    a['updatedAt'] = '2026-10-28T00:00:01Z'
    with pytest.raises(CoverageError):
        evaluate(snapshot([a]), CONFIG)


@pytest.mark.parametrize('dry,dispatched,no_activity', [(True, True, False), (True, False, True), (False, False, False)])
def test_dry_run_or_failure_never_changes_state(dry, dispatched, no_activity):
    state = {'last_sha': 'git0', 'last_at': 123, 'linear': {'cursor': SINCE}}
    original = copy.deepcopy(state)
    assert commit_state(state, {'cursor': CUTOFF}, dry_run=dry, dispatched=dispatched, no_activity=no_activity, sha='git1') == original
    assert state == original


def test_noop_observation_preserves_delivery_and_pending_git():
    state = {'last_sha': 'git0', 'last_at': 123}
    result = commit_state(state, {'cursor': CUTOFF}, dry_run=False, dispatched=False, no_activity=True)
    assert result == dict(state, linear={'cursor': CUTOFF})


def test_successful_dispatch_commits_both_cursors():
    result = commit_state({'other': 1}, {'cursor': CUTOFF}, dry_run=False, dispatched=True, no_activity=False, sha='git1')
    assert result['last_sha'] == 'git1' and result['linear']['cursor'] == CUTOFF and result['other'] == 1


def test_pagination_collects_every_page_and_rejects_stalls():
    client = Client('not-a-real-key')
    first = {'issues': {'nodes': [1], 'pageInfo': {'hasNextPage': True, 'endCursor': 'a'}}}
    last = {'issues': {'nodes': [2], 'pageInfo': {'hasNextPage': False}}}
    with patch.object(client, 'query', side_effect=[first, last]):
        assert client.pages('query', {}, 'issues') == [1, 2]
    with patch.object(client, 'query', side_effect=[first, first]):
        with pytest.raises(CoverageError):
            client.pages('query', {}, 'issues')


def test_http_200_graphql_partial_data_is_failure():
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps({'data': {'issues': []}, 'errors': [{'message': 'private error'}]}).encode()
    with patch('linear_adapter.request.urlopen', return_value=Response()):
        with pytest.raises(CoverageError, match='coverage incomplete'):
            Client('not-a-real-key').query('query { issues { nodes { id } } }')
