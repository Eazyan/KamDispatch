"""Read-only hooks: no transcripts, credentials, network, mutations or retries."""
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/agent_memory'))
from memory import stale, choose

LIMIT = 2800

def head(name,limit):
    with (ROOT/f'docs/agent-memory/{name}.md').open() as stream:
        return stream.read(limit)

def focus():
    """One line naming the task a new session should continue.

    The queue line is built here from the same selector the agents use, so the
    hook never restates TASKS.md by hand. A queue error (for example two
    in_progress tasks) is reported verbatim, never resolved by guessing.
    """
    try:
        task = choose(ROOT)
    except ValueError as exc:
        return 'Очередь: '+str(exc)+' — спроси пользователя, не переключай трек молча.'
    if not task:
        return 'Очередь: доступных задач нет; см. TASKS.md.'
    return f"Продолжение: {task['id']} ({task['track']}, {task['priority']}, {task['status']}) — {task['ready'].split(';')[0][:200]}"

def output(event):
    if event == 'SessionStart':
        # STATE and HANDOFF can each exceed the budget on their own, so both get
        # a share of what the focus line leaves; the queue always fits.
        first, last = head('STATE',900), head('HANDOFF',700)
        context = ('Камчатка: проверь git status, затем STATE/HANDOFF/TASKS и нужный источник INDEX.\n'
                   + focus() + '\nSTATE\n' + first + '\nHANDOFF\n' + last)[:LIMIT]
        return {'hookSpecificOutput': {'hookEventName': event,'additionalContext': context}}
    warnings=stale(ROOT)
    if not warnings: return {}
    return {'systemMessage': 'Checkpoint требует проверки: '+'; '.join(warnings)+'. Обнови STATE/TASKS/HANDOFF/LOG; запусти memory.py checkpoint и verify.py. Не делай скрытый коммит.'}

if __name__=='__main__':
    try: print(json.dumps(output(sys.argv[1]),ensure_ascii=False))
    except (OSError,ValueError,KeyError,IndexError) as exc:
        print(json.dumps({'systemMessage': 'Memory hook unavailable; read AGENTS.md and checkpoint manually. '+type(exc).__name__}))
