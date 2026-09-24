from django import forms
from .models import Environment
from .credentials import encrypt_password


class EnvironmentForm(forms.ModelForm):
    ca_upload = forms.FileField(label="CA certificate file", required=False,
        widget=forms.FileInput(attrs={"accept": ".pem,.crt,.cer"}),
        help_text="Optional PEM certificate or bundle, up to 1 MB. Stored with this environment; no worker path needed.")
    remove_ca = forms.BooleanField(label="Remove saved CA certificate", required=False,
        help_text="Use the system trust store instead.")

    def clean_ca_upload(self):
        import re
        import ssl
        upload = self.cleaned_data.get("ca_upload")
        if upload is None:
            return None
        if upload.size > 1024 * 1024:
            raise forms.ValidationError("CA file must be at most 1 MB.")
        try:
            pem = upload.read().decode("ascii")
            certificates = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", pem, re.S)
            remainder = re.sub(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", "", pem, flags=re.S)
            if not certificates or remainder.strip():
                raise ValueError()
            ssl.create_default_context(cadata=pem)
        except (UnicodeError, ValueError, ssl.SSLError):
            raise forms.ValidationError("Upload a valid PEM certificate bundle containing certificates only, without private keys.")
        return {"pem": pem, "name": upload.name[:255]}

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("ca_upload") and cleaned.get("remove_ca"):
            self.add_error("remove_ca", "Choose either a replacement file or removal, not both.")
        return cleaned

    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"autocomplete": "username"}))
    password = forms.CharField(strip=False, required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.original_interval = self.instance.sync_interval_minutes
        self.original_enabled = self.instance.enabled
        if self.instance.ca_certificate:
            self.fields["ca_upload"].help_text = "Saved file: {}. Leave empty to keep it, or upload a replacement (PEM, up to 1 MB).".format(self.instance.ca_filename or "CA bundle")
        elif self.instance.ca_bundle:
            self.fields["ca_upload"].help_text = "A legacy CA configuration is saved. Leave empty to keep it, or upload a PEM replacement (up to 1 MB)."
        if not (self.instance.ca_certificate or self.instance.ca_bundle):
            self.fields["remove_ca"].widget = forms.HiddenInput()
        saved = bool(self.instance.password_ciphertext)
        self.fields["password"].required = not saved
        self.fields["password"].help_text = "Leave blank to keep the saved password." if saved else "Enter the NSX Manager password."

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.cleaned_data["password"]:
            instance.password_ciphertext = encrypt_password(self.cleaned_data["password"])
        from datetime import timedelta
        from django.utils import timezone
        if (not instance.pk or instance.sync_interval_minutes != self.original_interval
                or instance.enabled != self.original_enabled or not instance.next_sync_at):
            instance.next_sync_at = (timezone.now() + timedelta(minutes=instance.sync_interval_minutes)
                                     if instance.enabled and instance.sync_interval_minutes else None)
        upload = self.cleaned_data.get("ca_upload")
        if upload:
            instance.ca_certificate = upload["pem"]
            instance.ca_filename = upload["name"]
            instance.ca_bundle = ""
        elif self.cleaned_data.get("remove_ca"):
            instance.ca_certificate = instance.ca_filename = instance.ca_bundle = ""
        instance.username_env = ""
        instance.password_env = ""
        if commit:
            instance.save()
            self.save_m2m()
        return instance

    class Meta:
        model = Environment
        fields = ["name", "slug", "manager", "username", "password",
                  "ca_upload", "remove_ca", "insecure", "timeout", "retries", "enabled", "sync_interval_minutes"]
        labels = {"sync_interval_minutes": "Automatic sync", "slug": "Environment ID", "manager": "NSX Manager",
                  "insecure": "Disable TLS certificate validation"}
        help_texts = {"insecure": "Leave unchecked to verify the manager using your uploaded CA or the system trust store.", "sync_interval_minutes": "Full inventory syncs run automatically at this interval. The first sync runs after one interval. Paused environments do not sync.", "slug": "A stable identifier, for example east-datacenter."}


class PreferencesForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from zoneinfo import available_timezones
        self.fields['timezone'] = forms.ChoiceField(choices=[(zone, zone.replace('_', ' ')) for zone in sorted(available_timezones())])
        self.fields['remember_tables'].help_text = 'Save report searches, filters, sorting and column layouts to your account.'
        self.fields['remember_menus'].help_text = 'Restore sidebar sections across visits and devices.'

    def clean(self):
        values = super().clean()
        if values.get('landing_page') in ('environment', 'activity') and not values.get('preferred_environment'):
            self.add_error('preferred_environment', 'Choose an environment for this landing page.')
        return values

    class Meta:
        from .models import UserPreferences
        model = UserPreferences
        fields = ['density', 'page_size', 'report_page_size', 'history_days', 'refresh_seconds', 'timezone', 'date_format', 'theme', 'text_size', 'high_contrast', 'reduced_motion', 'remember_tables', 'remember_menus', 'landing_page', 'preferred_environment']
        labels = {'density': 'Display density', 'page_size': 'History rows per page',
                  'report_page_size': 'Default report table rows', 'history_days': 'Default rule history period',
                  'refresh_seconds': 'Collection status refresh'}
        help_texts = {'page_size': 'Applies to snapshot history, collection history and rule history.',
                      'report_page_size': 'Starting row count when opening a report. You can still change it in each table.',
                      'refresh_seconds': 'How often open pages check collection progress. This does not change the automatic sync schedule.'}


class RetentionForm(forms.ModelForm):
    class Meta:
        from .models import RetentionPolicy
        model = RetentionPolicy
        fields = ['enabled', 'snapshot_days', 'testing_days', 'collection_days']
        labels = {'enabled': 'Enable automatic cleanup', 'snapshot_days': 'Full snapshot retention',
                  'testing_days': 'Testing snapshot retention', 'collection_days': 'Collection history retention'}
        help_texts = {'enabled': 'When enabled, eligible records are permanently deleted by the hourly cleanup.',
                      'snapshot_days': 'At least 91 days to preserve the 90-day activity window and its baseline.',
                      'collection_days': 'At least 91 days so failed collections remain visible to historical analysis.'}


class WorkspacePolicyForm(forms.ModelForm):
    class Meta:
        from .models import WorkspacePolicy
        model = WorkspacePolicy
        fields = ['stale_hours', 'notify_failed', 'notify_completed', 'notify_coverage']
        labels = {'stale_hours': 'Stale data after (hours)', 'notify_failed': 'Notify about failed collections', 'notify_completed': 'Notify about completed audits', 'notify_coverage': 'Notify about new coverage issues'}
        help_texts = {'stale_hours': 'Measured from the latest successful full collection. Imported and testing snapshots do not refresh this clock. Paused environments are shown separately.'}


class SnapshotComparisonForm(forms.Form):
    before = forms.ModelChoiceField(queryset=None, label='Earlier snapshot')
    after = forms.ModelChoiceField(queryset=None, label='Later snapshot')

    def __init__(self, *args, environment, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.queryset = environment.snapshots.filter(testing=False, imported=False).defer('report', 'html')
            field.label_from_instance = lambda snapshot: snapshot.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC') + ' · ' + str(snapshot.pk)[:8]

    def clean(self):
        data = super().clean()
        before, after = data.get('before'), data.get('after')
        if before and after and (before.pk == after.pk or before.generated_at >= after.generated_at):
            raise forms.ValidationError('Choose two distinct snapshots, with the earlier snapshot first.')
        return data


class FindingReviewForm(forms.Form):
    status = forms.ChoiceField(choices=[('open', 'Open'), ('acknowledged', 'Acknowledged')])
    owner = forms.ModelChoiceField(queryset=None, required=False)
    review_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    note = forms.CharField(required=False, max_length=5000, widget=forms.Textarea(attrs={'rows': 4}), label='Add a note')
    revision = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        from django.contrib.auth import get_user_model
        super().__init__(*args, **kwargs)
        self.fields['owner'].queryset = get_user_model().objects.filter(is_active=True).order_by('username')
