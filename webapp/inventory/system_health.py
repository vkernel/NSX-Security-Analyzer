"""Read-only resource diagnostics; never infer remote disk capacity from local disk."""
import shutil
import time
from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.db import connection, transaction, DatabaseError
from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


def read_number(path):
    try:
        return int(Path(path).read_text().strip())
    except (OSError, ValueError):
        return None


def resources(root='/sys/fs/cgroup', disk_path='/app'):
    root = Path(root)
    v2 = (root / 'memory.current').exists()
    memory = read_number(root / ('memory.current' if v2 else 'memory/memory.usage_in_bytes'))
    limit = read_number(root / ('memory.max' if v2 else 'memory/memory.limit_in_bytes'))
    if limit is not None and limit >= 1 << 60:
        limit = None

    def cpu_seconds():
        if not v2:
            value = read_number(root / 'cpuacct/cpuacct.usage')
            return value / 1e9 if value is not None else None
        try:
            fields = dict(line.split() for line in (root / 'cpu.stat').read_text().splitlines())
            return int(fields['usage_usec']) / 1e6
        except (OSError, ValueError, KeyError):
            return None

    first = cpu_seconds()
    started = time.monotonic()
    cores = None
    if first is not None:
        time.sleep(0.1)
        last = cpu_seconds()
        if last is not None:
            cores = max(0, (last - first) / (time.monotonic() - started))
    try:
        disk = shutil.disk_usage(disk_path)
    except OSError:
        disk = None
    return {'memory': memory, 'memory_limit': limit, 'cpu_cores': cores,
            'disk': disk, 'memory_percent': round(memory / limit * 100, 1) if memory is not None and limit else None}


def database_usage():
    if connection.vendor != 'postgresql':
        return {'error': 'Database storage metrics require PostgreSQL.'}
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = '2000ms'")
                cursor.execute("SET LOCAL lock_timeout = '500ms'")
                cursor.execute('SELECT pg_database_size(current_database())')
                size = cursor.fetchone()[0]
                cursor.execute('''SELECT relname, pg_total_relation_size(relid)
                    FROM pg_catalog.pg_statio_user_tables
                    ORDER BY pg_total_relation_size(relid) DESC LIMIT 10''')
                tables = [{'name': name, 'size': value} for name, value in cursor.fetchall()]
        return {'size': size, 'tables': tables}
    except DatabaseError:
        # Do not disclose database addresses, credentials or SQL error details in the UI.
        return {'error': 'Database measurements unavailable: check connectivity, monitoring permissions and database load.'}


@login_required
@require_GET
@never_cache
def health_page(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden('Administrator access required.')
    from .heartbeats import summary
    return render(request, 'inventory/system_health.html', {
        'services': summary(),
        'metrics': resources(), 'database': database_usage(), 'measured_at': timezone.now(),
    })
