"""Application roles shared by local and federated accounts."""
ROLE_CHOICES = (("viewer", "Viewer"), ("operator", "Operator"), ("admin", "Administrator"))


def role_for(user):
    return "admin" if user.is_superuser else "operator" if user.is_staff else "viewer"


def apply_role(user, role):
    if role not in dict(ROLE_CHOICES):
        raise ValueError("Unknown application role")
    user.is_superuser = role == "admin"
    user.is_staff = role in ("admin", "operator")
