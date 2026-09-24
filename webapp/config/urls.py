from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from inventory.views import administration

urlpatterns = [
    path("admin/", administration),
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("inventory.urls")),
]
