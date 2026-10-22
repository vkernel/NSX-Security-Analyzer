"""Configuration-scoped suppression; membership evidence is never rewritten."""
from django import forms
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from .models import Environment, Finding, Snapshot, TransientEmptinessPolicy


def policies_for(environment_id):
    return list(TransientEmptinessPolicy.objects.filter(enabled=True).filter(
        Q(environment_id=environment_id) | Q(environment__isnull=True)).order_by('pk'))


def condition_scope(row):
    config = row.get('configuration') or {}
    if config.get('extended_expression') or config.get('group_type') not in (None, [], ''):
        return None
    expressions = config.get('expression')
    if not isinstance(expressions, list) or not expressions or len(expressions) % 2 != 1:
        return None
    scopes = set()
    for index, expression in enumerate(expressions):
        if not isinstance(expression, dict): return None
        if index % 2:
            if expression.get('resource_type') != 'ConjunctionOperator' or expression.get('conjunction_operator') not in ('AND', 'OR'): return None
            continue
        if (expression.get('resource_type') != 'Condition' or expression.get('member_type') != 'VirtualMachine'
                or expression.get('key') != 'Tag' or expression.get('operator') != 'EQUALS'
                or expression.get('expressions')): return None
        value = expression.get('value')
        if not isinstance(value, str) or value.count('|') != 1: return None
        scope, tag = value.split('|')
        if not scope or not tag: return None
        scopes.add(scope)
    return next(iter(scopes)) if len(scopes) == 1 else None


def matching(row, policies):
    scope = condition_scope(row)
    return next((p for p in policies if p.enabled and p.tag_scope == scope), None) if scope else None


def apply(finding, row, policies):
    if finding.kind != 'empty_group': return False
    policy = matching(row, policies)
    if not policy: return False
    finding.qualification = 'suppressed'
    finding.qualification_reason = f'{policy.name}: {policy.reason}'[:255]
    finding.observation_started = finding.qualified_at = None
    finding.observation_count = finding.observation_days = 0
    finding.policy_fingerprint = ''
    return True


class PolicyForm(forms.ModelForm):
    class Meta:
        model = TransientEmptinessPolicy
        fields = ['name', 'enabled', 'environment', 'tag_scope', 'reason']
        labels = {'tag_scope':'Membership tag scope'}
        help_texts = {'tag_scope':'Exact, case-sensitive scope in positive VM tag conditions. Mixed and nested definitions are excluded.'}
        widgets = {'tag_scope':forms.TextInput(attrs={'list':'observed-scopes'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['environment'].empty_label = 'All environments'

    def clean_tag_scope(self):
        value = self.cleaned_data['tag_scope']
        if '|' in value: raise forms.ValidationError('Enter the scope only, without a tag value or | separator.')
        return value


@login_required
@require_http_methods(['GET', 'POST'])
def settings_view(request):
    if not request.user.is_superuser: return HttpResponseForbidden('Administrator access is required.')
    selected = request.POST.get('id') or request.GET.get('id')
    instance = get_object_or_404(TransientEmptinessPolicy, pk=selected) if selected else None
    form = PolicyForm(request.POST or None, instance=instance)
    previews, suggestions = [], set()
    total = 0
    valid = request.method == 'POST' and form.is_valid()
    if valid and request.POST.get('action') == 'save':
        from .finding_recalculation import queue
        from .finding_workflow import invalidate, FIELDS
        with transaction.atomic():
            # Serialize against collection/review publication for all affected scopes.
            envs = list(Environment.objects.select_for_update().order_by('pk'))
            form.save()
            for env in envs:
                rules = policies_for(env.pk)
                for finding in Finding.objects.filter(environment=env, kind='empty_group', present=True).iterator(chunk_size=100):
                    if apply(finding, finding.evidence, rules):
                        invalidate(finding, 'Expected transient emptiness policy suppresses this finding.')
                        finding.revision += 1
                        finding.save(update_fields=['qualification','qualification_reason','observation_started','qualified_at','observation_count','observation_days','policy_fingerprint','revision'] + FIELDS)
                queue([env.pk], ['empty_group'])
        return redirect('transient-emptiness')
    candidate = form.save(commit=False) if valid else None
    # Latest full snapshot per environment only; preview is read-only and output bounded.
    for env in Environment.objects.order_by('pk'):
        snapshot = Snapshot.objects.filter(environment=env, testing=False, imported=False).order_by('-generated_at','-created_at').first()
        if not snapshot: continue
        rows = snapshot.report.get('objects', [])
        findings = set(Finding.objects.filter(environment=env, kind='empty_group', present=True).values_list('path', flat=True)) if candidate else set()
        for row in rows:
            scope = condition_scope(row)
            if scope: suggestions.add(scope)
            if candidate and (candidate.environment_id is None or candidate.environment_id == env.pk) and matching(row, [candidate]):
                total += 1
                if len(previews) < 100:
                    previews.append({'environment':env.name, 'name':row.get('name',row['path']), 'path':row['path'],
                                     'membership':row.get('membership'), 'finding':row['path'] in findings})
    return render(request, 'inventory/transient_settings.html', {'form':form, 'editing':instance,
        'policies':TransientEmptinessPolicy.objects.select_related('environment').order_by('name'),
        'suggestions':sorted(suggestions), 'previews':previews, 'previewed':candidate is not None, 'total':total})
