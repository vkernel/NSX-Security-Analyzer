from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from inventory.views import administration
from inventory import keycloak, ldap_settings
from inventory.initial_password import ChangePasswordView

urlpatterns = [
    path("account/password/", ChangePasswordView.as_view(), name="password-change"),
    path("login/ldap/", ldap_settings.sign_in, name="ldap-login"),
    path("admin/", administration),
    path("admin/", admin.site.urls),
    path("login/", keycloak.WorkspaceLoginView.as_view(), name="login"),
    path("login/keycloak/", keycloak.start, name="keycloak_login"),
    path("login/keycloak/callback/", keycloak.callback, name="keycloak_callback"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("inventory.urls")),
]
