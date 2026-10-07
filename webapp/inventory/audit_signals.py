"""Allowlisted change audit; never serialize models or submitted request bodies."""
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import pre_save, post_save, post_delete, m2m_changed
from django.dispatch import receiver
from .audit_events import record

FIELDS = {
    'KeycloakConfiguration': ('enabled', 'issuer', 'client_id', 'viewer_role', 'operator_role', 'admin_role'),
    'Environment': ('name', 'slug', 'manager', 'enabled', 'sync_interval_minutes', 'insecure', 'timeout', 'retries'),
    'RetentionPolicy': ('enabled', 'snapshot_days', 'testing_days', 'collection_days'),
    'FindingPolicy': ('scope', 'zero_hits_days', 'empty_group_days', 'unused_days', 'empty_policy_days', 'disabled_days', 'minimum_observations', 'maximum_gap_hours'),
    'WorkspacePolicy': ('stale_hours', 'notify_failed', 'notify_completed', 'notify_coverage'),
    'UserPreferences': ('page_size', 'report_page_size', 'history_days', 'landing_page', 'remember_tables', 'remember_menus', 'density', 'refresh_seconds', 'timezone', 'date_format', 'theme', 'text_size', 'high_contrast', 'reduced_motion', 'preferred_environment_id'),
    'User': ('is_active', 'is_staff', 'is_superuser'),
    'Group': ('name',),
    'AuditJob': ('status', 'scheduled', 'testing'),
}
PRIVATE = {'KeycloakConfiguration': ('secret_ciphertext', 'ca_certificate', 'ca_bundle'), 'Environment': ('username', 'password_ciphertext', 'ca_certificate'), 'User': ('password',)}


def values(instance):
    return {name: getattr(instance, name) for name in FIELDS.get(type(instance).__name__, ()) if hasattr(instance, name)}


@receiver(pre_save)
def before_save(sender, instance, raw=False, **kwargs):
    if raw or sender._meta.apps is not apps or sender.__name__ not in FIELDS:
        return
    old = sender.objects.filter(pk=instance.pk).first() if instance.pk else None
    instance._audit_before = values(old) if old else {}
    instance._audit_private_changed = bool(old and any(getattr(old, f) != getattr(instance, f) for f in PRIVATE.get(sender.__name__, ())))


@receiver(post_save)
def after_save(sender, instance, created, raw=False, **kwargs):
    if raw or sender._meta.apps is not apps or sender.__name__ not in FIELDS:
        return
    before, after = getattr(instance, '_audit_before', {}), values(instance)
    changes = {k: {'before': before.get(k), 'after': v} for k,v in after.items() if created or before.get(k) != v}
    private_changed = getattr(instance, '_audit_private_changed', False)
    if created or changes or private_changed:
        record(sender.__name__.lower() + ('.created' if created else '.updated'), sender.__name__, instance.pk,
               details={'changes': changes, 'credentials_or_trust_changed': private_changed})


@receiver(post_delete)
def after_delete(sender, instance, **kwargs):
    if sender._meta.apps is apps and sender.__name__ in ('Environment', 'User', 'Group', 'FindingPolicy'):
        record(sender.__name__.lower()+'.deleted', sender.__name__, instance.pk)


def auth_event(action, user=None, request=None, outcome='success'):
    # Do not include credentials from user_login_failed.
    record(action, 'User', getattr(user, 'pk', ''), outcome=outcome,
           actor=getattr(user, 'pk', None), best_effort=True)


@receiver(user_logged_in)
def logged_in(sender, user, request, **kwargs):
    auth_event('auth.login', user, request)


@receiver(user_logged_out)
def logged_out(sender, user, request, **kwargs):
    auth_event('auth.logout', user, request)


@receiver(user_login_failed)
def login_failed(sender, credentials, request, **kwargs):
    auth_event('auth.login', request=request, outcome='failed')


@receiver(m2m_changed, sender=get_user_model().groups.through)
@receiver(m2m_changed, sender=get_user_model().user_permissions.through)
@receiver(m2m_changed, sender=Group.permissions.through)
def permissions_changed(sender, instance, action, pk_set, reverse, **kwargs):
    if action.startswith('post_'):
        record('access.membership_changed', type(instance).__name__, instance.pk,
               details={'operation': action, 'relation': sender.__name__, 'reverse': reverse,
                        'related_ids': sorted(pk_set or [])})
