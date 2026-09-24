from django.contrib import admin
from .models import AuditJob, Snapshot

admin.site.site_header = "NSX Security"
admin.site.site_title = "NSX Security Administration"
admin.site.index_title = "Administration"
admin.site.enable_nav_sidebar = False


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditJob)
class AuditJobAdmin(ReadOnlyAdmin):
    list_display = ("id", "environment", "status", "testing", "created_at", "finished_at")
    list_filter = ("status", "testing")


@admin.register(Snapshot)
class SnapshotAdmin(ReadOnlyAdmin):
    list_display = ("id", "environment", "generated_at", "needs_review", "testing", "imported")
    exclude = ("html", "report")
