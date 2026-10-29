from django import forms
from django.forms import formset_factory, BaseFormSet
from .models import Environment


class PolicyForm(forms.Form):
    name = forms.CharField(max_length=120)
    environment = forms.ModelChoiceField(Environment.objects.all(), required=False, empty_label='Global default — all environments')
    group_type = forms.ChoiceField(choices=[('all', 'All group types'), ('dynamic', 'Dynamic'), ('static', 'Static'), ('mixed', 'Mixed / nested')], initial='all')
    separator = forms.ChoiceField(choices=[('-', 'Hyphen (-)'), ('_', 'Underscore (_)'), ('.', 'Dot (.)'), ('', 'No separator')], required=False)
    max_length = forms.IntegerField(min_value=1, max_value=255, initial=63, label='Maximum name length')
    match_field = forms.CharField(max_length=64, required=False, label='Category field', help_text='Optional logical field below. Leave blank to cover all groups.')
    match_value = forms.CharField(max_length=256, required=False, label='Category tag value', help_text='Exact observed value, before value mapping.')
    revision = forms.IntegerField(widget=forms.HiddenInput, initial=0)


class ComponentForm(forms.Form):
    field = forms.RegexField(r'^[A-Za-z][A-Za-z0-9 _-]{0,63}$', label='Logical field', max_length=64)
    source = forms.ChoiceField(choices=[('tag', 'Definition tag / reviewed scope value'), ('fixed', 'Fixed text'), ('identity', 'Group path suffix'), ('type', 'Group type (map to your prefixes)')])
    scope = forms.CharField(max_length=128, required=False, label='Default tag scope')
    literal = forms.CharField(max_length=128, required=False, label='Fixed text')
    required = forms.BooleanField(required=False, initial=True)
    case = forms.ChoiceField(choices=[('upper', 'UPPERCASE'), ('lower', 'lowercase'), ('keep', 'Keep case')])
    format = forms.ChoiceField(choices=[('alphanumeric', 'Letters and digits'), ('digits', 'Digits only'), ('extended', 'Letters, digits, hyphens, dots and underscores')])
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
    if bool(field) != bool(form.cleaned_data.get('match_value')):
        form.add_error('match_value', 'Provide both category field and value, or leave both blank.')
    return {'target': 'group', 'group_type': form.cleaned_data['group_type'], 'separator': form.cleaned_data['separator'], 'max_length': form.cleaned_data['max_length'],
            'match_field': field, 'match_value': form.cleaned_data.get('match_value', ''), 'components': rows}


class BindingForm(forms.Form):
    environment = forms.ModelChoiceField(Environment.objects.all())
    field = forms.CharField(max_length=64, label='Logical field')
    scope = forms.CharField(max_length=128, label='Environment tag scope')
    revision = forms.IntegerField(widget=forms.HiddenInput)


class ProposalForm(forms.Form):
    scope = forms.CharField(max_length=128, required=False)
    value = forms.CharField(max_length=256, required=False)
    note = forms.CharField(max_length=500, required=False, widget=forms.Textarea(attrs={'rows': 2}))
    csv_file = forms.FileField(required=False, label='Import CSV proposals', help_text='UTF-8 CSV: group_path,scope,value. Maximum 1 MB and 1,000 rows.')
