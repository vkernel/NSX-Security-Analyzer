from django.apps import AppConfig


class InventoryConfig(AppConfig):
    name = 'inventory'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        from . import history_cache, audit_signals  # noqa: F401
