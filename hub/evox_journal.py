"""Append bounded, display-safe events for the local EvoX conversation viewer."""
from datetime import datetime, timezone
import json
from pathlib import Path
import threading

_lock = threading.Lock()
_MAX_BYTES = 512 * 1024
_KEEP_EVENTS = 250


def record(path, role, text, *, session_id=None, input_id=None, status=None):
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    event = {'at': datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds'),
             'role': str(role)[:20], 'text': str(text).replace('\x00', '')[:1200]}
    if session_id:
        event['session_id'] = str(session_id)[:80]
    if input_id:
        event['input_id'] = str(input_id)[:80]
    if status:
        event['status'] = str(status)[:24]
    line = json.dumps(event, ensure_ascii=False) + '\n'
    with _lock:
        if target.exists() and target.stat().st_size + len(line.encode('utf-8')) > _MAX_BYTES:
            try:
                old = target.read_text(encoding='utf-8').splitlines()[-_KEEP_EVENTS + 1:]
                target.write_text('\n'.join(old) + ('\n' if old else ''), encoding='utf-8')
            except OSError:
                return
        with target.open('a', encoding='utf-8', newline='\n') as stream:
            stream.write(line)
