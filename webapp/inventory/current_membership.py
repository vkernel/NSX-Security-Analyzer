"""Explicit, bounded NSX reads cached separately from saved snapshot evidence."""
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
import re
import time
from django.core.exceptions import ValidationError
from django.db import connection as db, transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from .models import AuditJob, Snapshot, SnapshotRecord, MembershipLookup, MembershipRelation
from .collector import AuditError
from .member_collection_budget import request_deadline

GROUP = re.compile(r'/(?:infra|global-infra|orgs/[^/]+/projects/[^/]+/infra)/domains/[^/]+/groups/[^/]+$')
TTL = timedelta(minutes=5)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def namespace(env):
    # Invalidate cached observations when the manager, credentials or trust changes.
    return digest(json.dumps([env.manager, env.username, env.password_ciphertext, env.ca_certificate, env.ca_bundle, env.insecure]))


def external_id(value, *, allow_id=False):
    if not isinstance(value, dict): raise ValueError('Saved VM identity is unavailable.')
    ids = {value['external_id']} if isinstance(value.get('external_id'), str) and value['external_id'] else set()
    ids.update(item[len('externalId:'):] for item in (value.get('compute_ids') or [])
               if isinstance(item, str) and item.startswith('externalId:') and item[len('externalId:'):])
    if len(ids) == 1:
        return next(iter(ids))
    if not ids and allow_id and isinstance(value.get('id'), str):
        return value['id']
    raise ValueError('A unique VM external ID is required; names and IPs cannot identify membership.')


def subject(snapshot, kind, identity):
    if kind == 'vm':
        row = get_object_or_404(SnapshotRecord.objects.values('data__vm_details','compact__path','name'), snapshot=snapshot, view='vms', ordinal=int(identity))
        value = row['data__vm_details'] or {}
        ext = external_id(value)
        path = row['compact__path']
        scope = path.split('/realized-state/', 1)[0] if path and '/realized-state/' in path else '/infra'
        if not re.fullmatch(r'/(?:infra|global-infra|orgs/[^/]+/projects/[^/]+/infra)', scope):
            scope = '/infra'
        return {'identity':ext, 'external_id':ext, 'name':row['name'], 'vm_path':path, 'scope':scope}
    if kind != 'group' or not GROUP.fullmatch(identity):
        raise ValueError('A saved VM or group is required.')
    row = get_object_or_404(SnapshotRecord.objects.values('name'), snapshot=snapshot, view='inventory', compact__path=identity, data__kind='group')
    return {'identity':identity, 'path':identity, 'name':row['name']}


@contextmanager
def manager_lease(env):
    """One enrichment read per manager; no long database transaction around HTTP."""
    if db.vendor != 'postgresql':
        yield True
        return
    dedicated = db.copy(alias='membership_lookup_lease')
    try:
        with dedicated.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s, %s)', [781249317, env.pk])
            acquired = cursor.fetchone()[0]
        yield acquired
    finally:
        dedicated.close()


def fetch(client, kind, value, *, seconds=8, max_pages=10):
    """Read all available association pages, retaining positive partial evidence."""
    path = (value.get('scope', '/infra') + '/virtual-machine-group-associations' if kind == 'vm'
            else value['path'] + '/members/virtual-machines')
    params = {'page_size':1000}
    if kind == 'vm': params['vm_external_id'] = value['external_id']
    rows, cursors, expected, notes = {}, set(), None, []
    complete = False
    try:
        with request_deadline(client, time.monotonic()+seconds):
            for _ in range(max_pages):
                page = client.get(path, params)
                if not isinstance(page, dict) or not isinstance(page.get('results'), list):
                    raise ValueError('Malformed NSX membership page.')
                count = page.get('result_count')
                if count is not None:
                    if type(count) is not int or count < 0: raise ValueError('Invalid member total.')
                    if expected is not None and expected != count: notes.append('NSX total changed while paging.')
                    if expected is None: expected = count
                for row in page['results']:
                    if not isinstance(row, dict): raise ValueError('Malformed member identity.')
                    if kind == 'vm':
                        identity = row.get('path')
                        if not isinstance(identity, str) or not GROUP.fullmatch(identity) or row.get('is_valid') in (False, 'false'):
                            raise ValueError('Invalid group association.')
                        member = {'identity':identity, 'path':identity, 'name':row.get('target_display_name') or identity.rsplit('/', 1)[-1]}
                    else:
                        identity = external_id(row, allow_id=True)
                        member = {'identity':identity, 'external_id':identity, 'name':row.get('display_name') or identity}
                    if identity in rows: notes.append('Duplicate member returned during pagination.')
                    rows[identity] = member
                cursor = page.get('cursor')
                if not cursor:
                    if expected is not None and expected != len(rows): notes.append('Member total differs from saved identities.')
                    if expected is None and len(page['results']) >= params['page_size']: notes.append('Full page without continuation or total.')
                    complete = not notes
                    break
                if not isinstance(cursor, str) or cursor in cursors: raise ValueError('Invalid or repeated continuation.')
                cursors.add(cursor); params['cursor'] = cursor
            else: notes.append('Membership page limit reached.')
    except (AuditError, TimeoutError, ValueError) as exc:
        # Do not expose server response bodies, credentials or URLs in user errors.
        code = getattr(exc, 'status_code', None)
        notes.append('Membership read incomplete'+(f' (HTTP {code})' if code else '')+'. Missing memberships remain unknown.')
    return list(rows.values()), 'complete' if complete else 'partial' if rows else 'unavailable', notes


def save(env, kind, owner, items, status, notes):
    stamp, ns, key = timezone.now(), namespace(env), digest(owner['identity'])
    with transaction.atomic():
        lookup, _ = MembershipLookup.objects.select_for_update().get_or_create(
            environment=env, namespace=ns, kind=kind, key=key)
        existing = MembershipRelation.objects.filter(environment=env, namespace=ns, **{kind+'_key':key})
        previous = {getattr(edge, ('group' if kind == 'vm' else 'vm')+'_key'):edge for edge in existing}
        seen, changed = set(), set()
        pending = []
        for item in items:
            other = digest(item['identity']); seen.add(other)
            pair = {'vm_key':key, 'group_key':other} if kind == 'vm' else {'vm_key':other, 'group_key':key}
            old = previous.get(other)
            if old is None or not old.present: changed.add(other)
            pending.append(MembershipRelation(environment=env, namespace=ns, **pair,
                vm=owner if kind == 'vm' else {**(old.vm if old else {}), **item}, group=item if kind == 'vm' else owner,
                checked_at=stamp, present=True))
        MembershipRelation.objects.bulk_create(pending, batch_size=500, update_conflicts=True,
            unique_fields=['environment','namespace','vm_key','group_key'], update_fields=['vm','group','checked_at','present'])
        if status == 'complete':
            missing = set(previous)-seen
            changed.update(other for other in missing if previous[other].present)
            existing.exclude(**{('group' if kind == 'vm' else 'vm')+'_key__in':seen}).update(present=False, checked_at=stamp)
        # An observation in the opposite direction may now be older/incomplete.
        MembershipLookup.objects.filter(environment=env, namespace=ns, kind='group' if kind == 'vm' else 'vm',
            key__in=changed).update(status='partial', expires_at=stamp,
                notes=['Membership changed in a newer related-object check; refresh to establish the full list.'])
        lookup.subject, lookup.status, lookup.checked_at = owner, status, stamp
        lookup.expires_at, lookup.notes = stamp+TTL, notes
        lookup.save()
    return lookup


def cached(snapshot, kind, owner, page=0):
    env, key = snapshot.environment, digest(owner['identity'])
    ns = namespace(env)
    lookup = MembershipLookup.objects.filter(environment=env, namespace=ns, kind=kind, key=key).first()
    edges = MembershipRelation.objects.filter(environment=env, namespace=ns, present=True, **{kind+'_key':key})
    count = edges.count()
    status = lookup.status if lookup else 'partial' if count else 'not_collected'
    field = 'group' if kind == 'vm' else 'vm'
    items = [{**value, 'observed_at':stamp.isoformat()} for value,stamp in edges.order_by(field+'__name', field+'__identity').values_list(field,'checked_at')[page*25:page*25+26]]
    if kind == 'group' and items:
        identities = [item.get('external_id') for item in items]
        records = SnapshotRecord.objects.filter(snapshot=snapshot, view='vms', data__vm_details__external_id__in=identities).values('name','compact__path','data__vm_details__external_id')
        mapping = {r['data__vm_details__external_id']:r for r in records}
        for item in items:
            row = mapping.get(item.get('external_id'))
            if row: item.update(name=row['name'], vm_path=row['compact__path'])
    notes = list(lookup.notes) if lookup else ['Observed through related-object checks; this subject has not had a complete check.']
    fresh = bool(lookup and lookup.expires_at and lookup.expires_at > timezone.now())
    if not fresh and lookup: notes.append('Cached observation is stale; check current NSX to refresh.')
    if lookup and status != 'complete' and count: notes.append('Positive observations may come from earlier checks; their individual observation times are retained.')
    return {'items':items[:25], 'page':page, 'has_next':len(items)>25, 'status':status,
        'saved_count':count, 'checked_at':lookup.checked_at.isoformat() if lookup and lookup.checked_at else None,
        'source':'current_nsx', 'stale':not fresh, 'notes':['Current NSX observation; saved snapshot membership is unchanged.']+notes}


def refresh(snapshot, kind, owner, *, force=False):
    env = snapshot.environment
    lookup = MembershipLookup.objects.filter(environment=env, namespace=namespace(env), kind=kind, key=digest(owner['identity'])).first()
    if not force and lookup and lookup.status == 'complete' and lookup.expires_at and lookup.expires_at > timezone.now():
        return lookup
    with manager_lease(env) as acquired:
        if not acquired or AuditJob.objects.filter(environment=env, status__in=['running','queued']).exists():
            raise ValidationError('Collection has priority. Retry the current membership check when it finishes.', code='collection_busy')
        from .deletion import connection
        with connection(env) as client:
            items, status, notes = fetch(client, kind, owner)
        return save(env, kind, owner, items, status, notes)


@login_required
@require_http_methods(['GET','POST'])
def endpoint(request, pk):
    snapshot = get_object_or_404(Snapshot.objects.select_related('environment').defer('report','html','summary'), pk=pk)
    if snapshot.browsing_status != 'ready': return JsonResponse({'error':'Restore saved browsing evidence first.'},status=409)
    params = request.POST if request.method == 'POST' else request.GET
    try:
        kind = params.get('kind', 'vm'); page = int(params.get('page', '0'))
        if not 0 <= page <= 100000: raise ValueError('Invalid page.')
        owner = subject(snapshot, kind, params.get('id' if kind == 'vm' else 'path', ''))
        env = snapshot.environment
        if request.method == 'POST':
            if not request.user.is_staff: return JsonResponse({'error':'Operator access required for current NSX reads.'},status=403)
            # Concurrent drawer openings may already have refreshed this subject.
            # Explicit Refresh still bypasses the freshness cache.
            lookup = refresh(snapshot, kind, owner, force=params.get('refresh_mode') != 'if_needed')
            from .audit_events import record
            record('inventory.membership_checked', 'Environment', env.pk, actor=request.user.pk,
                   details={'kind':kind,'status':lookup.status})
        result = cached(snapshot, kind, owner, page)
        result['can_refresh'] = request.user.is_staff
        if params.get('section') == 'firewall' and kind == 'vm':
            from .confirmed_references import read
            groups = list(MembershipRelation.objects.filter(environment=env, namespace=namespace(env), vm_key=digest(owner['identity']), present=True).values_list('group__path',flat=True))
            result = {**result, **read(snapshot.pk, groups, page), 'notes':result['notes']+['Current VM memberships matched against saved firewall configuration; not live rule state or observed traffic.']}
            result.pop('saved_count',None)
        response = JsonResponse(result); response['Cache-Control']='private, no-store'; return response
    except ValidationError as exc:
        return JsonResponse({'error':' '.join(exc.messages)},status=409 if exc.code == 'collection_busy' else 400)
    except ValueError as exc:
        return JsonResponse({'error':str(exc)},status=400)
    except AuditError:
        return JsonResponse({'error':'NSX membership read failed. Check environment connectivity and account permissions.'},status=502)
