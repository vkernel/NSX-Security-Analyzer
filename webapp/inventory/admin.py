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


# One role selector instead of independent staff/superuser flags.
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm
from .models import KeycloakIdentity
from .roles import ROLE_CHOICES, apply_role, role_for


class RoleFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.external_identity = bool(self.instance.pk and KeycloakIdentity.objects.filter(user=self.instance).exists())
        self.fields['role'].initial = role_for(self.instance)
        if self.external_identity:
            self.fields['role'].disabled = True
            self.fields['role'].help_text = 'Managed by Keycloak and updated at sign-in. You can disable this account locally.'

    def save(self, commit=True):
        user = super().save(commit=False)
        if not self.external_identity:
            apply_role(user, self.cleaned_data.get('role') or 'viewer')
        if commit:
            user.save()
            self.save_m2m()
        return user


class LocalUserCreationForm(RoleFormMixin, AdminUserCreationForm):
    role = forms.ChoiceField(choices=ROLE_CHOICES, required=False, initial='viewer',
                             help_text='Viewer reads data; Operator manages environments and collections; Administrator has full access.')


class LocalUserChangeForm(RoleFormMixin, UserChangeForm):
    role = forms.ChoiceField(choices=ROLE_CHOICES,
                             help_text='Viewer reads data; Operator manages environments and collections; Administrator has full access.')


class WorkspaceUserAdmin(UserAdmin):
    add_form = LocalUserCreationForm
    form = LocalUserChangeForm
    list_display = ('username', 'email', 'application_role', 'is_active')
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Profile', {'fields': ('first_name', 'last_name', 'email')}),
        ('Access', {'fields': ('is_active', 'role')}),
        ('Activity', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (('Access', {'fields': ('role',)}),)

    @admin.display(description='Role')
    def application_role(self, obj):
        return dict(ROLE_CHOICES)[role_for(obj)]

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    has_add_permission = has_view_permission
    has_change_permission = has_view_permission
    has_delete_permission = has_view_permission


admin.site.unregister(get_user_model())
admin.site.register(get_user_model(), WorkspaceUserAdmin)
