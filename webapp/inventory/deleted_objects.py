"""Durable, identity-scoped deletion evidence shared by publication and replay."""
from .models import DeletionItem


def identities(environment_id):
    result = {}
    for path, uid, created in DeletionItem.objects.filter(batch__environment_id=environment_id,
            status='deleted').values_list('path', 'approved_evidence__unique_id', 'approved_evidence__created_at'):
        if uid and created is not None:
            result.setdefault(path, set()).add((uid, created))
    return result


def superseded(deleted, path, evidence):
    if path not in deleted:
        return False
    uid, created = evidence.get('unique_id'), evidence.get('created_at')
    # Missing identity cannot establish that this is a new object.
    return not uid or created is None or (uid, created) in deleted[path]
