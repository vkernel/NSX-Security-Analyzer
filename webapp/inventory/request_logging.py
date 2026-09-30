import logging
import time
import uuid
from django.utils.deprecation import MiddlewareMixin
from .observability import CONTEXT, exception_details
from .audit_events import record

LOG = logging.getLogger('inventory.web')


class RequestLoggingMiddleware(MiddlewareMixin):
    def __call__(self, request):
        # Generate IDs locally: never trust unsanitized client-supplied context.
        request.request_id = str(uuid.uuid4())
        token = CONTEXT.set({'request_id': request.request_id, 'service': 'web', 'peer_address': request.META.get('REMOTE_ADDR', '')})
        started = time.monotonic()
        try:
            response = super().__call__(request)
            route = getattr(getattr(request, 'resolver_match', None), 'route', 'unmatched')
            payload = {'route': route, 'method': request.method, 'status': response.status_code,
                       'duration_seconds': round(time.monotonic()-started, 4)}
            if response.status_code >= 400 or route not in ('health/', 'api/jobs/', 'api/notifications/'):
                LOG.log(logging.ERROR if response.status_code >= 500 else logging.WARNING if response.status_code >= 400 else logging.INFO,
                        'web request completed', extra={'details': payload})
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
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
