"""Bounded per-process cache of immutable snapshot presentation, never full pages."""
from collections import OrderedDict
from threading import Lock
from time import monotonic

_MAX_BYTES = 64 * 1024 * 1024
_TTL = 300
_entries = OrderedDict()
_lock = Lock()
_size = 0


def clear():
    global _size
    with _lock:
        _entries.clear()
        _size = 0


def presentation(snapshot, renderer):
    """Caller must first load snapshot metadata under normal access checks."""
    global _size
    key = (str(snapshot.pk), snapshot.generated_at, snapshot.created_at)
    now = monotonic()
    with _lock:
        for old_key, (expires, size, _) in list(_entries.items()):
            if expires <= now:
                _size -= size
                del _entries[old_key]
        if key in _entries:
            _entries.move_to_end(key)
            return _entries[key][2]
    # Do not serialize unrelated report renders behind a global lock.
    data = snapshot.report
    fragments = renderer(data)
    value = (fragments, {
        'dfw': {'collection_diagnostics': data.get('dfw', {}).get('collection_diagnostics')},
        'performance': {'concurrency': {'endpoints': data.get('performance', {}).get('concurrency', {}).get('endpoints')}},
    })
    # Include diagnostics and use a conservative estimate of Python string storage.
    size = 4 * sum(len(v) for v in fragments.values()) + 4 * len(str(value[1])) + 4096
    if size <= _MAX_BYTES:
        with _lock:
            if key in _entries:
                _size -= _entries.pop(key)[1]
            while _entries and _size + size > _MAX_BYTES:
                _, (_, old_size, _) = _entries.popitem(last=False)
                _size -= old_size
            _entries[key] = (monotonic() + _TTL, size, value)
            _size += size
    return value
