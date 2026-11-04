from django import forms
from django.forms import formset_factory, BaseFormSet
from .models import Environment


class PolicyForm(forms.Form):
    name = forms.CharField(max_length=120)
    environment = forms.ModelChoiceField(Environment.objects.all(), required=False, empty_label='Global default — all environments')
    group_type = forms.ChoiceField(choices=[('all', 'All group types'), ('dynamic', 'Dynamic'), ('static', 'Static'), ('mixed', 'Mixed / nested')], initial='all')
    separator = forms.ChoiceField(choices=[('-', 'Hyphen (-)'), ('_', 'Underscore (_)'), ('.', 'Dot (.)'), ('', 'No separator')], required=False)
    max_length = forms.IntegerField(min_value=1, max_value=255, initial=63, label='Maximum name length')
    match_mode = forms.ChoiceField(choices=[('exact', 'Exact category value'), ('exists', 'Scope exists — any value'), ('single_host_ip', 'Exactly one explicit host IP')], required=False, initial='exact', label='Match condition', help_text='Single host IP matches one IPAddressExpression containing one host address (including /32 or /128). Subnets, ranges, multiple members and nested or dynamic definitions are excluded. Category fields are not used. For a general fallback, choose Exact category value and leave both category fields blank.')
    match_field = forms.CharField(max_length=64, required=False, label='Category field', help_text='Choose the tag component whose scope determines which groups match. Required for Scope exists.')
    match_value = forms.CharField(max_length=256, required=False, label='Category tag value', help_text='Exact observed value, before value mapping.')
    revision = forms.IntegerField(widget=forms.HiddenInput, initial=0)


class ComponentForm(forms.Form):
    field = forms.RegexField(r'^[A-Za-z][A-Za-z0-9 _-]{0,63}$', label='Logical field', max_length=64)
    source = forms.ChoiceField(choices=[('tag', 'Definition tag / reviewed scope value'), ('fixed', 'Fixed text'), ('identity', 'Group path suffix'), ('type', 'Group type (map to your prefixes)'), ('single_host_ip', 'Explicit single host IP')], help_text='Explicit single host IP reads saved IP membership automatically; no component proposal is needed. Use Static group type and the Exactly one explicit host IP match condition.')
    scope = forms.CharField(max_length=128, required=False, label='Default tag scope')
    literal = forms.CharField(max_length=128, required=False, label='Fixed text')
    required = forms.BooleanField(required=False, initial=True)
    case = forms.ChoiceField(choices=[('upper', 'UPPERCASE'), ('lower', 'lowercase'), ('keep', 'Keep case')])
    format = forms.ChoiceField(choices=[('alphanumeric', 'Letters and digits'), ('digits', 'Digits only'), ('extended', 'Letters, digits, hyphens, dots and underscores'), ('ip_address', 'IP address (IPv4 / IPv6)')])
    max_length = forms.IntegerField(min_value=1, max_value=128, initial=32)
    mapping_text = forms.CharField(required=False, max_length=8000, label='Value mappings', widget=forms.Textarea(attrs={'rows': 3, 'placeholder': 'Production=PRD\nRotterdam=RTM'}), help_text='One observed value=output per line. If mappings are set, unmapped values are invalid.')

    def clean(self):
        data = super().clean()
        if data.get('source') == 'tag' and not data.get('scope'): self.add_error('scope', 'A tag scope is required.')
        if data.get('source') == 'fixed' and not data.get('literal'): self.add_error('literal', 'Enter fixed text.')
        mappings = {}
        for line in data.get('mapping_text', '').splitlines():
            if not line.strip(): continue
            before, sep, after = line.partition('=')
            before, after = before.strip(), after.strip()
            if not sep or not before or not after or len(before) > 256 or len(after) > 128 or before in mappings:
                self.add_error('mapping_text', 'Use unique nonempty values in observed=output format.')
                break
            mappings[before] = after
        data['mapping'] = mappings
        if data.get('source') == 'single_host_ip':
            if not data.get('required'): self.add_error('required', 'The explicit host IP must be required.')
            if data.get('case') != 'keep': self.add_error('case', 'Use Keep case for the canonical host address.')
            if data.get('format') != 'ip_address': self.add_error('format', 'Select IP address (IPv4 / IPv6).')
            if mappings: self.add_error('mapping_text', 'Host addresses come directly from membership; leave value mappings empty.')
        return data


class Components(BaseFormSet):
    def clean(self):
        if any(self.errors): return
        rows = [f.cleaned_data for f in self.forms if f.cleaned_data and not f.cleaned_data.get('DELETE')]
        names = [r['field'] for r in rows]
        if len(names) != len(set(names)): raise forms.ValidationError('Each logical field must be unique.')


ComponentSet = formset_factory(ComponentForm, formset=Components, extra=0, can_order=True, can_delete=True,
                               min_num=1, max_num=16, validate_min=True, validate_max=True, absolute_max=32)


def configuration(form, formset):
    rows = [{key: value for key, value in f.cleaned_data.items() if key not in ('ORDER', 'DELETE', 'mapping_text')} for f in formset.ordered_forms]
    field = form.cleaned_data.get('match_field', '')
    if field and not any(r['field'] == field and r['source'] == 'tag' for r in rows):
        form.add_error('match_field', 'Choose the logical name of a tag component below.')
    mode = form.cleaned_data.get('match_mode') or 'exact'
    if mode == 'single_host_ip':
        if form.cleaned_data['group_type'] != 'static':
            form.add_error('group_type', 'Select Static for the single host IP condition.')
        if field or form.cleaned_data.get('match_value'):
            form.add_error('match_field', 'Leave category fields blank for the single host IP condition.')
    if any(row['source'] == 'single_host_ip' for row in rows) and mode != 'single_host_ip':
        form.add_error('match_mode', 'Choose Exactly one explicit host IP for an automatic IP component.')
    if mode == 'exists' and not field:
        form.add_error('match_field', 'Choose a tag component logical field for Scope exists.')
    if mode == 'exists' and form.cleaned_data.get('match_value'):
        form.add_error('match_value', 'Leave category value blank when matching any value in a scope.')
    if mode == 'exact' and bool(field) != bool(form.cleaned_data.get('match_value')):
        form.add_error('match_value', 'Provide both category field and value, or leave both blank.')
    return {'target': 'group', 'group_type': form.cleaned_data.get('group_type', ''), 'separator': form.cleaned_data['separator'], 'max_length': form.cleaned_data['max_length'],
            'match_mode': mode, 'match_field': field, 'match_value': form.cleaned_data.get('match_value', ''), 'components': rows}


class BindingForm(forms.Form):
    environment = forms.ModelChoiceField(Environment.objects.all())
    field = forms.CharField(max_length=64, label='Logical field')
    scope = forms.CharField(max_length=128, label='Environment tag scope')
    revision = forms.IntegerField(widget=forms.HiddenInput)


class ProposalForm(forms.Form):
    ticket = forms.CharField(max_length=120, required=False, label='Change ticket')
    progress = forms.ChoiceField(required=False, choices=[('in_review','In review'),('prepared','Change prepared'),('awaiting','Awaiting verification')])
    expires_at = forms.DateField(required=False, widget=forms.DateInput(attrs={'type':'date'}), label='Exception expiry date')
    scope = forms.CharField(max_length=128, required=False)
    value = forms.CharField(max_length=256, required=False)
    note = forms.CharField(max_length=500, required=False, widget=forms.Textarea(attrs={'rows': 2}))
    csv_file = forms.FileField(required=False, label='Import CSV proposals', help_text='UTF-8 CSV: group_path,scope,value. Maximum 1 MB and 1,000 rows.')
