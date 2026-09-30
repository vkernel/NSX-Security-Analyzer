"""Shared, bounded container logging with safe context and exception summaries."""
import contextvars
import json
import logging
import os
import re
import traceback
from datetime import datetime, timezone

CONTEXT = contextvars.ContextVar('diagnostic_context', default={})
# A collector is a dedicated process. This also covers its request/heartbeat threads.
PROCESS_CONTEXT = {}
PROCESS_SECRETS = ()


def sanitize(value):
    from django.conf import settings
    secrets = list(PROCESS_SECRETS) + [os.getenv('DJANGO_SECRET_KEY', ''), os.getenv('POSTGRES_PASSWORD', '')]
    if settings.configured:
        secrets += [settings.SECRET_KEY, settings.DATABASES['default'].get('PASSWORD', '')]
    text = str(value)
    for secret in sorted({str(s) for s in secrets if s}, key=len, reverse=True):
        text = text.replace(secret, '[REDACTED]')
    text = re.sub(r'(?i)(authorization\s*[:=]\s*)(?:basic|bearer)\s+[^\s,;]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)((?:password|passwd|secret|token|cookie|api[_-]?key)\s*[\"\x27]?\s*[:=]\s*)(?:\"[^\"]*\"|\x27[^\x27]*\x27|[^\s,;]+)', r'\1[REDACTED]', text)
    text = re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@', text)
    return text.replace('\r', '\\r').replace('\n', '\\n')[:3000]


def safe_data(value, depth=0):
    if depth > 8:
        return '[truncated]'
    if isinstance(value, dict):
        return {str(k): ('[REDACTED]' if re.search(r'password|secret|token|cookie|authorization|ciphertext', str(k), re.I)
                         else safe_data(v, depth+1)) for k,v in list(value.items())[:50]}
    if isinstance(value, (tuple, list)):
        return [safe_data(v, depth+1) for v in value[:100]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize(value)


def exception_details(exc):
    from django.db import DatabaseError
    chain, seen = [], set()
    current = exc
    database_error = isinstance(exc, DatabaseError)
    while current is not None and id(current) not in seen and len(chain) < 5:
        seen.add(id(current))
        state = getattr(current, 'sqlstate', None) or getattr(current, 'pgcode', None)
        if state and not re.fullmatch('[A-Z0-9]{5}', str(state)):
            state = None
        # Database messages may embed SQL parameters or entire JSON snapshots.
        message = ('Database operation failed; inspect SQLSTATE and server diagnostics.'
                   if database_error or state else sanitize(current))
        chain.append({'type': type(current).__name__, 'message': message, 'sqlstate': state,
                      'frames': [{'file': os.path.basename(f.filename), 'function': f.name, 'line': f.lineno}
                                 for f in traceback.extract_tb(current.__traceback__)[-15:]]})
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    code = 'DATABASE_ERROR' if database_error else 'COLLECTION_ERROR'
    if any(c['sqlstate'] == '55P03' for c in chain): code = 'DATABASE_LOCK_TIMEOUT'
    elif any(c['sqlstate'] == '57014' for c in chain): code = 'DATABASE_QUERY_CANCELED'
    elif any(c['sqlstate'] == '40P01' for c in chain): code = 'DATABASE_DEADLOCK'
    elif isinstance(exc, TimeoutError): code = 'NETWORK_TIMEOUT'
    elif any('SSLError' in c['type'] or 'Certificate' in c['type'] for c in chain): code = 'TLS_ERROR'
    elif any(c['type'] == 'gaierror' or 'NameResolution' in c['type'] for c in chain): code = 'DNS_ERROR'
    return {'code': code, 'chain': chain}


class ConsoleFormatter(logging.Formatter):
    def format(self, record):
        from .version import VERSION, BUILD
        context = {**PROCESS_CONTEXT, **CONTEXT.get()}
        payload = {'timestamp': datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                   'level': record.levelname, 'logger': record.name, 'pid': record.process,
                   'pod': os.getenv('HOSTNAME', ''), 'version': VERSION,
                   'build': BUILD['revision'], **context,
                   'message': sanitize(record.getMessage())}
        payload.update(safe_data(getattr(record, 'details', {})))
        if record.exc_info and record.exc_info[1]:
            payload['error'] = exception_details(record.exc_info[1])
        # Never call the default formatter, which would append unredacted tracebacks.
        if os.getenv('NSX_LOG_FORMAT', 'json').lower() == 'text':
            return ' '.join(f'{key}={json.dumps(value, ensure_ascii=False)}' for key,value in payload.items())
        return json.dumps(payload, ensure_ascii=False)


class ScopedDebug(logging.Filter):
    def __init__(self, until):
        super().__init__()
        self.until = until

    def filter(self, record):
        return record.levelno >= logging.INFO or bool(self.until and datetime.now(timezone.utc) < self.until)
