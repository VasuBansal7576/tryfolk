"""Offline retained-session boundary with real installed TinyPerson memory.

No persona generation, inference, embeddings, browser or provider calls. The
initialization fixture creates genuine SDK objects; the production dispatch,
feedback, snapshots, identity keys and release policy run unchanged.
"""
import ast
import io
import json
import os
from pathlib import Path
import subprocess
import sys

if "--sdk-probe" not in sys.argv:
    import pytest
else:
    from types import SimpleNamespace
    pytest = SimpleNamespace(mark=SimpleNamespace(parametrize=lambda *args: lambda fn: fn))


@pytest.mark.parametrize('count', [5, 10])
def test_actual_sdk_retains_interleaved_owned_persona_memory(count):
    python = Path(os.environ.get('TRYFOLK_TEST_TINY_PYTHON',
                     'integrations/tinytroupe/.venv/bin/python'))
    if not python.exists():
        pytest.skip('Installed TinyTroupe integration interpreter required')
    result = subprocess.run([str(python.absolute()), str(Path(__file__).resolve()),
                             '--sdk-probe', str(count)], capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stderr[-4000:]
    assert json.loads(result.stdout.splitlines()[-1]) == {'retained': count, 'model_calls': 0}


def sdk_probe(count):
    from types import SimpleNamespace
    from tinytroupe import config_manager
    from tinytroupe.agent import TinyPerson
    # A synchronous protocol invocation never calls inference. Disabling the SDK
    # consolidation avoids its unrelated automatic LLM memory housekeeping.
    config_manager.update_multiple({'enable_memory_consolidation': False})
    TinyPerson.all_agents.clear()
    created = {}

    def build(persona):
        person = TinyPerson(persona['name'], mental_faculties=[])
        person.define('name', persona['name'])
        person.episodic_memory.store({'role': 'user', 'content': 'initial-'+persona['id'],
                                     'type': 'CONVERSATION', 'simulation_timestamp': None})
        created[persona['id']] = person
        # Real memory/snapshot objects are exercised; semantic retrieval is outside
        # this capacity-only check and must never start embedding/model work.
        person.retrieve_relevant_memories = lambda *args, **kwargs: ['offline initial retrieval']
        return person, SimpleNamespace(intent=None, allowed=set())

    source = Path(__file__).resolve().parents[1]/'integrations/tinytroupe/adapter.py'
    main = next(n for n in ast.parse(source.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == 'main')
    commands = []
    def cmd(op, index, **kwargs):
        return {'op': 'bulk_'+op, 'run_id': 'offline-run',
                'persona_id': f'person-{index}', **kwargs}
    for i in range(count):
        commands.append(cmd('init', i, persona={'id':f'person-{i}', 'name':f'Offline customer {i}'}))
    if count == 10:
        commands.append(cmd('init', 10, persona={'id':'person-10', 'name':'Offline customer 10'}))
    snapshots = []
    for round_no in range(2):
        for i in reversed(range(count)):
            commands.append(cmd('feedback', i, result={'marker':f'only-{i}-round-{round_no}'}))
        for i in range(count):
            snapshots.append((len(commands), i, round_no))
            commands.append(cmd('snapshot', i))
    if count == 10:
        commands.append(cmd('release', 3))
        commands.append(cmd('init', 10, persona={'id':'person-10', 'name':'Offline customer 10'}))
        commands.append(cmd('snapshot', 3))
        commands.append(cmd('snapshot', 9))
    protocol = io.StringIO()
    namespace = {'sys': SimpleNamespace(stdin=io.StringIO(''.join(json.dumps(c)+'\n' for c in commands)),stderr=sys.stderr),
                 'json': json, 'protocol': protocol, 'TinyPerson':TinyPerson,
                 'EMBEDDING_REVISION':'5c38ec7c405ec4b44b94cc5a9bb96e735b38267a',
                 'build':build,'config_manager':config_manager,
                 'CodexClient':lambda:SimpleNamespace(get_cost_stats=lambda:{})}
    exec(compile(ast.Module(body=[main],type_ignores=[]), str(source), 'exec'), namespace)
    namespace['main']()
    replies = [json.loads(line) for line in protocol.getvalue().splitlines()]
    assert len(replies) == len(commands)
    assert all(r['ok'] for r in replies[:count])
    assert len({id(created[f'person-{i}'].episodic_memory) for i in range(count)})==count
    for offset,i,round_no in snapshots:
        reply=replies[offset]
        assert reply['ok']
        text=json.dumps(reply['data']['snapshot'])
        assert f'initial-person-{i}' in text and f'only-{i}-round-{round_no}' in text
        assert all(f'only-{other}-round-' not in text for other in range(count) if other!=i)
        assert reply['data']['episodic_count']>=round_no+2
    if count == 10:
        assert replies[10]['ok'] is False and 'at-most-ten' in replies[10]['error']
        assert replies[-4]['data']['released'] is True and replies[-3]['ok'] is True
        assert replies[-2]['ok'] is False and 'Unknown run/persona memory' in replies[-2]['error']
        assert replies[-1]['ok'] is True
        assert 'only-9-round-1' in json.dumps(replies[-1]['data']['snapshot'])
    print(json.dumps({'retained':count,'model_calls':0}))


if __name__ == '__main__':
    assert sys.argv[1] == '--sdk-probe'
    sdk_probe(int(sys.argv[2]))
