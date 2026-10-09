"""Heartbeat updates come from progressing supervisor loops, never a timer thread."""
import os
import time
from pathlib import Path
from django.utils import timezone
from .models import ServiceHeartbeat

_last = {}

def beat(name):
    now = time.monotonic()
    if now - _last.get(name, float('-inf')) < 15: return
    ServiceHeartbeat.objects.update_or_create(name=name, defaults={'pod': os.environ.get('HOSTNAME', 'local')[:255], 'seen_at': timezone.now()})
    Path('/tmp/nsxa-heartbeat-' + name).touch()
    _last[name] = now

def summary():
    saved = {row.name: row for row in ServiceHeartbeat.objects.all()}
    result = []
    for name in ('collection', 'recalculation', 'scheduler'):
        row = saved.get(name)
        age = (timezone.now() - row.seen_at).total_seconds() if row else None
        result.append({'name': name, 'pod': row.pod if row else '', 'seen_at': row.seen_at if row else None,
                       'status': 'Reporting' if age is not None and age < 180 else 'Not reporting'})
    return result
