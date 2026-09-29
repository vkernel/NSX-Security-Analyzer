"""Explicit IPFIX configuration only. The inventory collector remains GET-only."""
import copy
import hashlib
import json
import re
import uuid
from urllib.parse import quote
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from ..collector import NSXClient, AuditError
from ..credentials import decrypt_password
from ..models import Environment, IPFIXExporter, IPFIXReceiver, IPFIXSetup

COLLECTORS = '/infra/ipfix-dfw-collector-profiles'
PROFILES = '/infra/ipfix-dfw-profiles'
GROUP = r'/infra/domains/[^/]+/groups/[^/]+'
ALLOWED = re.compile(r'^(?:/infra/ipfix-dfw-(?:collector-)?profiles/[^/]+|' + GROUP + r'/group-monitoring-profile-binding-maps/[^/]+)$')


class SetupError(ValueError):
    pass


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def client_for(environment):
    if environment.insecure:
        raise SetupError('Enable TLS verification and approve the Manager certificate before setup.')
    if not environment.password_ciphertext:
        raise SetupError('Save Manager credentials before setup. They must have permission to configure IPFIX.')
    return NSXClient(environment.manager, environment.username, decrypt_password(environment.password_ciphertext),
        timeout=10, retries=0, ca_data=environment.ca_certificate or None,
        ca_bundle=environment.ca_bundle or None)


def read_optional(client, path):
    try:
        return client.get(path)
    except AuditError as exc:
        if exc.status_code == 404:
            return None
        raise


def put(client, path, body):
    if not ALLOWED.fullmatch(path):
        raise SetupError('Setup attempted an unsupported resource path.')
    try:
        response = client.http.request('PUT', client.base_url + quote(path, safe='/'),
            body=json.dumps(body).encode(), headers=dict(client.headers, **{'Content-Type':'application/json'}),
            timeout=10, retries=False, redirect=False)
    except Exception:
        raise SetupError('The write response was lost. Its outcome is unknown; inspect the listed NSX resource before making a new plan.') from None
    try:
        if response.status not in (200, 201):
            raise SetupError('NSX rejected the setup write (HTTP {}). Check IPFIX write permissions or configuration limits; create a fresh preview for revision conflicts.'.format(response.status))
    finally:
        response.release_conn()


def receiver_ready():
    receiver = IPFIXReceiver.objects.filter(pk=1).first()
    if not receiver or not receiver.heartbeat or receiver.heartbeat < timezone.now()-timedelta(seconds=30):
        raise SetupError('Start the IPFIX Docker receiver before applying setup.')


def make_plan(client, environment, destination, port, group='', profile_mode='auto', existing_profile='', profile_name='NSX Security Analyzer', priority=100):
    receiver_ready()
    sources = list(IPFIXExporter.objects.filter(environment=environment,enabled=True).exclude(address=destination).values_list('address',flat=True))
    profiles = list(client.items(PROFILES))
    if len(profiles) > 4:
        raise SetupError('More than four DFW export profiles exist. Review this configuration manually before setup.')
    if profile_mode not in ('auto','existing','new'):
        raise SetupError('Choose an existing profile or create a new profile.')
    warnings = []
    selected = profiles
    if profile_mode == 'existing':
        selected = [p for p in profiles if p['path'] == existing_profile] if existing_profile else profiles
        if len(selected) != 1:
            raise SetupError('Select exactly one existing DFW profile, or choose Create new profile.')
    operations, checks = [], []
    target = {'collector_ip_address': destination, 'collector_port': port}
    if profile_mode != 'new' and profiles:
        paths = sorted({p['ipfix_dfw_collector_profile_path'] for p in selected})
        for path in paths:
            if not re.fullmatch(re.escape(COLLECTORS) + r'/[^/]+', path):
                raise SetupError('Only Local Manager collector profiles are supported.')
            before = client.get(path)
            if before.get('_protection') in ('PROTECTED','REQUIRE_OVERRIDE') or before.get('remote_path'):
                raise SetupError('A collector is protected or federated. Configure it through its owning management system.')
            if type(before.get('_revision')) is not int:
                raise SetupError('The collector has no revision; setup cannot safely update it.')
            collectors = before.get('ipfix_dfw_collectors')
            if not isinstance(collectors,list):
                raise SetupError('NSX returned an invalid collector list.')
            checks.append({'path':path,'before':before})
            if any(c.get('collector_ip_address') == destination and c.get('collector_port') == port for c in collectors):
                continue
            body = {k:copy.deepcopy(before[k]) for k in ('resource_type','display_name','description','tags','_revision','ipfix_dfw_collectors') if k in before}
            body['ipfix_dfw_collectors'].append(target)
            operations.append({'path':path,'body':body,'before':before,'action':('Add destination to system-owned collector; its owner may later reconcile it' if before.get('_system_owned') else 'Add destination; retain every existing collector')})
        mode = 'Use the selected existing export profile; retain current destinations and export interval.'
        if profile_mode == 'existing' and group:
            append_binding(client,group,selected[0]['path'],operations,checks)
            mode += ' Add a binding to the selected group.'
        elif profile_mode == 'existing':
            warnings.append('Existing scope is retained. A non-global profile without an existing binding will not export; provide a group path to activate it for that group.')
        if len(profiles) > len(selected):
            warnings.append('A collector profile may be shared by other export profiles. Adding a destination also affects those consumers.')
    else:
        if not re.fullmatch(GROUP, group):
            raise SetupError('Enter an existing NSX group Policy path to activate the new export profile.')
        identity = 'nsxa-' + uuid.uuid4().hex
        collector_path, profile_path = COLLECTORS+'/'+identity, PROFILES+'/'+identity
        for path, body in [
            (collector_path, {'resource_type':'IPFIXDFWCollectorProfile','display_name':profile_name,'ipfix_dfw_collectors':[target]}),
            (profile_path, {'resource_type':'IPFIXDFWProfile','display_name':profile_name,'ipfix_dfw_collector_profile_path':collector_path,'active_flow_export_timeout':1,'observation_domain_id':int(uuid.uuid4().hex[:4],16),'priority':priority})]:
            if read_optional(client,path) is not None:
                raise SetupError('A generated resource already exists. Create another preview.')
            operations.append({'path':path,'body':body,'before':None,'action':'Create dedicated resource'})
        append_binding(client,group,profile_path,operations,checks)
        for other in profiles:
            warnings.append('Existing profile {} has priority {}. New profile priority: {}. Lower numbers win where scope overlaps; equal priorities are ambiguous. Existing profiles are not disabled.'.format(other.get('display_name',other['path']),other.get('priority',0),priority))
        mode = 'Create dedicated profiles and bind only to the selected group. Export interval: one minute.'
    return {'configuration':fingerprint(environment.collection_config()), 'destination':destination, 'port':port,
        'sources':sources,'group':group,'mode':mode,'operations':operations,'checks':checks,'profiles':profiles,'warnings':warnings,'profile_mode':profile_mode}


def append_binding(client,group,profile_path,operations,checks):
    if not re.fullmatch(GROUP,group):
        raise SetupError('Enter an existing group Policy path.')
    record=client.get(group)
    if record.get('marked_for_delete') or record.get('resource_type') != 'Group':
        raise SetupError('Choose an existing active NSX group.')
    checks.append({'path':group,'before':record})
    path=group+'/group-monitoring-profile-binding-maps/nsxa-'+uuid.uuid4().hex
    if read_optional(client,path) is not None:
        raise SetupError('Generated binding already exists. Create a new preview.')
    operations.append({'path':path,'body':{'resource_type':'GroupMonitoringProfileBindingMap',
        'display_name':'NSX Security Analyzer','ipfix_dfw_profile_path':profile_path},
        'before':None,'action':'Activate profile for the selected group (create binding)'})


def apply_plan(setup_id, actor):
    # Consume approval before network writes. A crashed attempt cannot be replayed.
    with transaction.atomic():
        setup = IPFIXSetup.objects.select_for_update().get(pk=setup_id,actor=actor)
        if setup.status != 'preview' or setup.created_at < timezone.now()-timedelta(minutes=10):
            raise SetupError('This preview has expired or was already submitted. Create a fresh preview.')
        setup.plan['baseline'] = {e.address:e.messages-e.malformed for e in IPFIXExporter.objects.filter(environment_id=setup.environment_id,enabled=True).exclude(address=setup.plan['destination'])}
        setup.status='applying'
        setup.applied_at=timezone.now()
        setup.save(update_fields=['status','applied_at','plan'])
    client = None
    try:
        with transaction.atomic():
            environment = Environment.objects.select_for_update().get(pk=setup.environment_id)
            # Serialize local setup and mapping updates across environments.
            IPFIXReceiver.objects.select_for_update().get(pk=1)
            plan = setup.plan
            if fingerprint(environment.collection_config()) != plan['configuration']:
                raise SetupError('Environment settings changed. Create a fresh preview.')
            receiver_ready()
            client = client_for(environment)
            if fingerprint(sorted(client.items(PROFILES), key=lambda p:p['path'])) != fingerprint(sorted(plan['profiles'],key=lambda p:p['path'])):
                raise SetupError('DFW profiles changed after preview. Create a fresh preview.')
            for check in plan['checks'] + plan['operations']:
                if read_optional(client,check['path']) != check['before']:
                    raise SetupError('An NSX resource changed after preview. No further changes will be made. Create a fresh preview.')
            # Retain acknowledged paths for the outcome saved after this transaction.
            for operation in plan['operations']:
                put(client,operation['path'],operation['body'])
                setup.outcomes.append(operation['path'])
            IPFIXReceiver.objects.filter(pk=1).update(enabled=True)
        setup.status='applied'
    except Exception as exc:
        setup.status='failed'
        setup.error = str(exc) if isinstance(exc,SetupError) else 'Setup could not complete. Check Manager connectivity, TLS trust and IPFIX permissions. Inspect the resource paths before retrying.'
    finally:
        if client:
            client.http.clear()
        setup.save(update_fields=['status','error','outcomes'])
    return setup
