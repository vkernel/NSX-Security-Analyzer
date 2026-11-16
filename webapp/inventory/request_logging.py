import logging
import time
import uuid
from django.utils.deprecation import MiddlewareMixin
from .observability import CONTEXT, exception_details
from .audit_events import record
from django.db import connection

LOG = logging.getLogger('inventory.web')


class RequestLoggingMiddleware(MiddlewareMixin):
    def __call__(self, request):
        # Generate IDs locally: never trust unsanitized client-supplied context.
        request.request_id = str(uuid.uuid4())
        token = CONTEXT.set({'request_id': request.request_id, 'service': 'web', 'peer_address': request.META.get('REMOTE_ADDR', '')})
        started = time.monotonic()
        database_seconds = 0.0
        database_queries = 0
        def measured_query(execute, sql, params, many, context):
            nonlocal database_seconds, database_queries
            query_started = time.monotonic()
            try:
                return execute(sql, params, many, context)
            finally:
                database_seconds += time.monotonic()-query_started
                database_queries += 1
        try:
            with connection.execute_wrapper(measured_query):
                response = super().__call__(request)
            duration = time.monotonic()-started
            render_seconds = getattr(request, 'render_seconds', 0)
            analysis_seconds = getattr(request, 'analysis_seconds', 0)
            route = getattr(getattr(request, 'resolver_match', None), 'route', 'unmatched')
            payload = {'route': route, 'method': request.method, 'status': response.status_code,
                       'duration_seconds': round(duration, 4), 'database_seconds': round(database_seconds, 4),
                       'database_queries': database_queries, 'render_seconds': round(render_seconds, 4),
                       'analysis_seconds':round(analysis_seconds,4),
                       'response_bytes':len(response.content) if not response.streaming else None}
            response['Server-Timing'] = f'total;dur={duration*1000:.1f}, db;dur={database_seconds*1000:.1f}, analysis;dur={analysis_seconds*1000:.1f}, render;dur={render_seconds*1000:.1f}'
            if response.status_code >= 400 or route not in ('health/', 'api/jobs/', 'api/notifications/'):
                LOG.log(logging.ERROR if response.status_code >= 500 else logging.WARNING if response.status_code >= 400 else logging.INFO,
                        'web request completed', extra={'details': payload})
            # Successful automatic naming examples are read-only and must not flood the audit table.
            if request.method not in ('GET', 'HEAD', 'OPTIONS') and not (route == 'naming/example/' and response.status_code < 400):
                # HTTP outcome is not a claim that a domain change committed.
                record('http.mutation_attempt', 'Route', route, outcome='failed' if response.status_code >= 400 else 'processed',
                       details=payload, best_effort=True)
            if response.status_code == 403:
                record('access.denied', 'Route', route, outcome='denied', best_effort=True)
            response['X-Request-ID'] = request.request_id
            return response
        finally:
            CONTEXT.reset(token)

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = getattr(request, 'user', None)
        CONTEXT.set({**CONTEXT.get(), 'actor_id': str(user.pk) if user and user.is_authenticated else ''})

    def process_exception(self, request, exception):
        LOG.error('web request failed', extra={'details': {'error': exception_details(exception)}})
