from django import template
from inventory.user_labels import user_label
register = template.Library()


@register.filter
def review_user(user):
    return user_label(user)


@register.filter
def review_actor(event):
    # Keep historical labels; make legacy machine identifiers readable if the user survives.
    if event.actor_label and not event.actor_label.startswith(('keycloak_', 'ldap_')):
        return event.actor_label
    return user_label(event.actor) if event.actor else event.actor_label or 'System'


@register.filter
def review_name(user):
    if user is None:
        return 'Unassigned'
    return user.get_full_name().strip() or user_label(user).split(' · ', 1)[0]
