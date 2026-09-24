from django.urls import path
from . import views

urlpatterns = [
    path('workspace/<slug:section>/', views.workspace_section, name='workspace-section'),
    path('environments/', views.environment_directory, name='environment-directory'),
    path('collections/', views.all_collections, name='all-collections'),
    path('start/', views.landing, name='landing'),
    path('administration/collection-policy/', views.workspace_policy, name='workspace-policy'),
    path('api/preferences/interface/', views.interface_preferences, name='interface-preferences'),
    path('api/notifications/', views.notifications, name='notifications'),
    path('api/notifications/read/', views.notifications_read, name='notifications-read'),
    path("administration/retention/", views.retention_settings, name="retention-settings"),
    path("settings/", views.website_settings, name="website-settings"),
    path("environments/<int:pk>/collections/", views.collection_history, name="collection-history"),
    path("environments/<int:pk>/rule-history/", views.rule_history, name="rule-history"),
    path("administration/", views.administration, name="administration"),
    path("", views.dashboard, name="dashboard"),
    path("health/", views.health, name="health"),
    path("api/jobs/", views.api_jobs, name="api-jobs"),
    path("environments/new/", views.environment_edit, name="environment-new"),
    path("environments/<int:pk>/", views.environment_detail, name="environment"),
    path("environments/<int:pk>/edit/", views.environment_edit, name="environment-edit"),
    path("environments/<int:pk>/collect/", views.collect, name="collect"),
    path("environments/testing-data/", views.testing_data, name="testing-data"),
    path("snapshots/<uuid:pk>/", views.snapshot_detail, name="snapshot"),
    path("snapshots/<uuid:pk>/report/", views.report_content, name="report-content"),
]
