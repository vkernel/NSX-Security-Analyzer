"""Demand-driven current group members; NSX cursors never leave the server."""
import time
import uuid
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import (AuditJob, MembershipLookup, MembershipRelation,
                     GroupMembershipSession, GroupMembershipItem, SnapshotRecord)
from .current_membership import TTL, digest, namespace, external_id, manager_lease, save
from .collector import AuditError
from .member_collection_budget import request_deadline

CHUNK_SIZE = 100
UI_PAGE_SIZE = 25
MAX_PAGES_PER_REQUEST = 10
MAX_SESSION_PAGES = 1000


def _error(message, code='paging_changed'):
    raise ValidationError(message, code=code)


def _session(snapshot, owner, lookup, token):
    try:
        token = uuid.UUID(str(token))
    except (ValueError, TypeError, AttributeError):
        _error('This member paging session is invalid. Refresh the group to start again.')
    session = GroupMembershipSession.objects.filter(pk=token, lookup=lookup, snapshot=snapshot).first() if lookup else None
    if session is None:
        _error('This member paging session is unavailable for this group and snapshot. Refresh to start again.')
    return session


def _active(lookup, session):
    return lookup.subject.get('paging_session') == str(session.pk) and not session.invalidated


def _gate(env):
    if AuditJob.objects.filter(environment=env, status__in=['queued', 'running']).exists():
        _error('Collection has priority. Retry the current membership check when it finishes.', 'collection_busy')


def start(snapshot, owner, *, force=True):
    env, ns, key = snapshot.environment, namespace(snapshot.environment), digest(owner['identity'])
    lookup = MembershipLookup.objects.filter(environment=env, namespace=ns, kind='group', key=key).first()
    if not force and lookup and lookup.status == 'complete' and lookup.expires_at and lookup.expires_at > timezone.now():
        return lookup
    with manager_lease(env) as acquired:
        if not acquired:
            _error('Another membership check is in progress. Retry shortly.', 'collection_busy')
        _gate(env)
        stamp = timezone.now()
        with transaction.atomic():
            lookup, _ = MembershipLookup.objects.select_for_update().get_or_create(environment=env, namespace=ns, kind='group', key=key)
            # Prune expired traversals only when a new check is explicitly requested.
            GroupMembershipSession.objects.filter(lookup=lookup, expires_at__lt=stamp).delete()
            session = GroupMembershipSession.objects.create(lookup=lookup, snapshot=snapshot, expires_at=stamp+TTL)
            lookup.subject = {**owner, 'paging_session':str(session.pk)}
            lookup.status, lookup.expires_at = 'partial', session.expires_at
            lookup.notes = ['Partial traversal; missing members remain unknown.']
            lookup.save()
        previous_checked_at = lookup.checked_at
        from .deletion import connection
        try:
            with connection(env) as client:
                failed = _fill(client, snapshot, owner, lookup, session, 0, initial=True)
        except (ValidationError, AuditError):
            if not session.loaded_count:
                _fallback(lookup, session, owner, previous_checked_at, stamp)
            raise
        if failed and not session.loaded_count:
            # A failed fresh check must not hide positive observations from an
            # earlier check. Fall back to the shared cache with explicit staleness.
            _fallback(lookup, session, owner, previous_checked_at, stamp)
        return MembershipLookup.objects.get(pk=lookup.pk)


def _fallback(lookup, session, owner, checked_at, stamp):
    MembershipLookup.objects.filter(pk=lookup.pk, subject__paging_session=str(session.pk)).update(
        subject=owner, status='unavailable', checked_at=checked_at, expires_at=stamp,
        notes=session.notes or ['The new membership check could not start. Earlier positive observations are retained.'])


def advance(snapshot, owner, token, page):
    env = snapshot.environment
    lookup = MembershipLookup.objects.filter(environment=env, namespace=namespace(env), kind='group', key=digest(owner['identity'])).first()
    session = _session(snapshot, owner, lookup, token)
    with manager_lease(env) as acquired:
        if not acquired:
            _error('Another membership check is in progress. Retry shortly.', 'collection_busy')
        _gate(env)
        lookup.refresh_from_db(); session.refresh_from_db()
        if not _active(lookup, session):
            _error('Membership changed or this group was refreshed elsewhere. Refresh to start a new member list.')
        if session.expires_at <= timezone.now():
            _error('This member paging session has expired. Loaded pages remain available; refresh before loading more.', 'paging_expired')
        if page > session.loaded_count // UI_PAGE_SIZE:
            _error('Open member pages in order. Previously loaded pages remain available.')
        # Another request may already have loaded this window; never replay its cursor.
        if session.loaded_count >= (page+1)*UI_PAGE_SIZE or not session.cursor:
            return lookup
        from .deletion import connection
        with connection(env) as client:
            failed = _fill(client, snapshot, owner, lookup, session, page)
        if failed:
            _error('The next member page could not be verified. Loaded pages remain available; retry or refresh the group.', 'paging_incomplete')
        return MembershipLookup.objects.get(pk=lookup.pk)


def _fill(client, snapshot, owner, lookup, session, page, *, initial=False):
    """Usually one 100-member read; tolerate short NSX pages within one deadline."""
    failed = False
    try:
        with request_deadline(client, time.monotonic()+8):
            for attempt in range(MAX_PAGES_PER_REQUEST):
                if not (initial and attempt == 0) and (not session.cursor or session.loaded_count >= (page+1)*UI_PAGE_SIZE):
                    break
                if len(session.cursor_history) >= MAX_SESSION_PAGES:
                    raise ValueError('Member traversal page limit reached; refresh required.')
                _gate(snapshot.environment)
                params = {'page_size':CHUNK_SIZE}
                if session.cursor:
                    params['cursor'] = session.cursor
                response = client.get(owner['path']+'/members/virtual-machines', params)
                _consume(snapshot, owner, lookup, session, response)
                if session.invalidated:
                    failed = True
                    break
            else:
                # A sequence of empty/very short pages must not monopolize a web request.
                if session.cursor and session.loaded_count < (page+1)*UI_PAGE_SIZE:
                    failed = True
                    _note(lookup, session, 'More NSX pages are required; retry to continue the same member list.')
    except (AuditError, TimeoutError, ValueError) as exc:
        failed = True
        code = getattr(exc, 'status_code', None)
        message = 'Member page read incomplete'+(f' (HTTP {code})' if code else '')+'. Loaded members are retained; missing members remain unknown.'
        # Invalid data cannot be resumed safely. Transient transport errors retain
        # the unconsumed server cursor, so Retry does not skip a page.
        if isinstance(exc, ValueError):
            session.invalidated = True
            message = 'Membership pagination changed or returned invalid data. Refresh before loading more; loaded members are retained.'
        _note(lookup, session, message)
    return failed


def _note(lookup, session, message):
    session.notes = list(dict.fromkeys([*session.notes, message]))
    session.status = 'partial' if session.loaded_count else 'unavailable'
    session.save(update_fields=['notes','status','invalidated'])
    lookup.status, lookup.notes = session.status, session.notes
    lookup.save(update_fields=['status','notes'])


def _consume(snapshot, owner, lookup, session, response):
    if not isinstance(response, dict) or not isinstance(response.get('results'), list) or len(response['results']) > CHUNK_SIZE:
        raise ValueError('Invalid NSX member page.')
    count, cursor = response.get('result_count'), response.get('cursor')
    if count is not None and (type(count) is not int or count < 0):
        raise ValueError('Invalid NSX member total.')
    if count is not None and session.reported_count is not None and count != session.reported_count:
        raise ValueError('NSX member total changed.')
    if cursor is not None and not isinstance(cursor, str):
        raise ValueError('Invalid NSX continuation.')
    if cursor and digest(cursor) in session.cursor_history:
        raise ValueError('Repeated NSX continuation.')
    members = []
    for raw in response['results']:
        identity = external_id(raw, allow_id=True)
        members.append({'identity':identity, 'external_id':identity, 'name':raw.get('display_name') or identity})
    keys = [digest(member['identity']) for member in members]
    if len(set(keys)) != len(keys) or session.items.filter(key__in=keys).exists():
        raise ValueError('Duplicate member while paging.')
    stamp = timezone.now()
    with transaction.atomic():
        lookup.refresh_from_db(); session.refresh_from_db()
        if not _active(lookup, session):
            _error('Membership changed while the page was loading. Refresh the group.')
        GroupMembershipItem.objects.bulk_create([
            GroupMembershipItem(session=session, key=key, position=session.loaded_count+i, member=member, checked_at=stamp)
            for i,(key,member) in enumerate(zip(keys,members))], batch_size=CHUNK_SIZE)
        session.loaded_count += len(members)
        if session.reported_count is None:
            session.reported_count = count
        session.checked_at = stamp
        session.cursor = cursor or ''
        if cursor:
            session.cursor_history = [*session.cursor_history, digest(cursor)]
        terminal_error = (not cursor and ((session.reported_count is not None and session.reported_count != session.loaded_count)
                                         or (session.reported_count is None and len(members) >= CHUNK_SIZE)))
        if session.reported_count is not None and session.loaded_count > session.reported_count:
            terminal_error = True
        session.invalidated = bool(terminal_error)
        if terminal_error:
            session.notes = ['NSX totals or continuation do not establish a complete member list. Refresh required; missing members remain unknown.']
        session.status = 'complete' if not cursor and not terminal_error else 'partial'
        # Publish positives in both directions immediately, without removing
        # members from earlier observations during an unfinished traversal.
        save(snapshot.environment, 'group', owner, members, 'partial', session.notes)
        lookup.subject = {**owner, 'paging_session':str(session.pk)}
        lookup.checked_at, lookup.expires_at = stamp, session.expires_at
        lookup.status, lookup.notes = session.status, session.notes
        if session.status == 'complete':
            existing = MembershipRelation.objects.filter(environment=snapshot.environment, namespace=lookup.namespace, group_key=lookup.key, present=True)
            missing = existing.exclude(vm_key__in=session.items.values('key'))
            changed = list(missing.values_list('vm_key', flat=True))
            missing.update(present=False, checked_at=stamp)
            MembershipLookup.objects.filter(environment=snapshot.environment, namespace=lookup.namespace, kind='vm', key__in=changed).update(
                status='partial', expires_at=stamp, notes=['Membership changed in a newer group check; refresh to establish the full VM group list.'])
        session.save()
        lookup.save()


def cached_page(snapshot, owner, lookup, page, token=None):
    """Stable positions within a traversal; never merge pages from other checks."""
    if not token:
        token = lookup.subject.get('paging_session') if lookup else None
        if not token:
            return None
        session = GroupMembershipSession.objects.filter(pk=token, lookup=lookup, snapshot=snapshot).first()
        if session is None:
            return None  # Shared inverse cache from another snapshot is still useful.
    else:
        session = _session(snapshot, owner, lookup, token)
    fresh = session.expires_at > timezone.now() and _active(lookup, session)
    values = list(session.items.order_by('position').values_list('member','checked_at')[page*UI_PAGE_SIZE:page*UI_PAGE_SIZE+UI_PAGE_SIZE])
    items = [{**member, 'observed_at':stamp.isoformat()} for member,stamp in values]
    if items:
        records = SnapshotRecord.objects.filter(snapshot=snapshot, view='vms', data__vm_details__external_id__in=[item['external_id'] for item in items]).values('name','compact__path','data__vm_details__external_id')
        mapping = {row['data__vm_details__external_id']:row for row in records}
        for item in items:
            row = mapping.get(item['external_id'])
            if row:
                item.update(name=row['name'], vm_path=row['compact__path'])
    notes = ['Current NSX group traversal; saved snapshot membership is unchanged.', *session.notes]
    can_continue = bool(session.cursor and fresh)
    if not fresh:
        notes.append('This traversal is stale, changed or superseded. Loaded pages remain available; refresh before loading more.')
    if session.status != 'complete':
        notes.append('Only loaded members are confirmed in this traversal; missing members remain unknown.')
    return {'items':items, 'page':page, 'has_next':session.loaded_count>(page+1)*UI_PAGE_SIZE or can_continue,
        'status':session.status if fresh else 'partial', 'source':'current_nsx', 'stale':not fresh,
        'saved_count':session.loaded_count, 'loaded_count':session.loaded_count, 'reported_count':session.reported_count,
        'checked_at':session.checked_at.isoformat() if session.checked_at else None, 'notes':notes,
        'paging_session':str(session.pk), 'can_continue':can_continue,
        'needs_more':can_continue and session.loaded_count<(page+1)*UI_PAGE_SIZE}
