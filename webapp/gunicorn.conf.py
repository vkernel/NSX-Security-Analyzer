"""Container log policy: route context comes from application middleware."""
access_log_format = '%(m)s status=%(s)s duration_seconds=%(L)s'
logconfig_dict = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {'console': {'()': 'inventory.observability.ConsoleFormatter'}},
    'handlers': {'console': {'class': 'logging.StreamHandler', 'stream': 'ext://sys.stdout', 'formatter': 'console'}},
    'loggers': {name: {'handlers': ['console'], 'level': 'INFO', 'propagate': False}
                for name in ('gunicorn.error', 'gunicorn.access')},
}
