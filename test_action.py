"""Check real composite-action gates, including the zero-commit Linear path."""
from pathlib import Path
import re

import pytest
import yaml

ACTION = yaml.safe_load(Path(__file__).with_name('action.yml').read_text(encoding='utf-8'))


def enabled(step, dry='true', send='true', skip='false'):
    expression = step.get('if', 'true')
    values = {'inputs.dry_run': dry, 'inputs.dashboard': 'true',
              'inputs.dashboard_commit': 'true', 'inputs.cooldown': '24h',
              'inputs.linear_config': 'config.json', 'steps.range.outputs.state_missing': 'false',
              'steps.range.outputs.skip': skip, 'steps.activity.outputs.send': send,
              'steps.dispatch.outputs.dispatched': 'true'}
    for key in sorted(values, key=len, reverse=True):
        expression = expression.replace(key, repr(values[key]))
    expression = expression.replace('&&', ' and ').replace('||', ' or ')
    return eval(expression, {'__builtins__': {}}, {'true': True, 'false': False})


@pytest.mark.parametrize('name', ['Detect required modes', 'Check required secrets',
    'Generate summaries', 'Install uv', 'Dispatch to channels', 'Generate board',
    'Commit board', 'Save state', 'Upload state artifact'])
def test_dry_run_cannot_reach_side_effects(name):
    step = next(s for s in ACTION['runs']['steps'] if s.get('name') == name)
    assert not enabled(step)


def test_no_material_activity_cannot_dispatch():
    step = next(s for s in ACTION['runs']['steps'] if s.get('name') == 'Dispatch to channels')
    assert not enabled(step, dry='false', send='false')
    assert enabled(step, dry='false', send='true')


def test_unchanged_git_keeps_linear_collection_reachable():
    step = next(s for s in ACTION['runs']['steps'] if s.get('name') == 'Get commit range')
    unchanged_branch = step['run'].split('elif [[ "$LAST_SHA" == "$HEAD_SHA" ]]; then')[1].split('else')[0]
    assert 'exit' not in unchanged_branch and 'skip=true' not in unchanged_branch
    gate = next(s for s in ACTION['runs']['steps'] if s.get('id') == 'activity')['run']
    assert 'COMMITS > 0 || LINEAR_COUNT > 0' in gate


def test_all_external_action_sources_are_versioned():
    assert all('@' in s['uses'] for s in ACTION['runs']['steps'] if 'uses' in s)
