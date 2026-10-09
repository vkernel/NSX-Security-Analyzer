from django.contrib.auth.views import PasswordChangeView
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.urls import reverse_lazy
from .models import InitialPasswordChange

class InitialPasswordMiddleware:
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        if request.user.is_authenticated and request.path not in ('/account/password/', '/logout/', '/health/'):
            if InitialPasswordChange.objects.filter(user_id=request.user.pk).exists():
                return redirect('password-change')
        return self.get_response(request)

class SafePasswordForm(PasswordChangeForm):
    def clean_new_password1(self):
        password = self.cleaned_data.get('new_password1')
        if password == 'NSXSecurityA!' or self.user.check_password(password):
            raise ValidationError('Choose a new password different from the initial and current password.')
        return password

class ChangePasswordView(PasswordChangeView):
    template_name = 'registration/password_change.html'
    form_class = SafePasswordForm
    success_url = reverse_lazy('landing')
    def form_valid(self, form):
        from django.db import transaction
        with transaction.atomic():
            response = super().form_valid(form)
            InitialPasswordChange.objects.filter(user_id=self.request.user.pk).delete()
        return response
