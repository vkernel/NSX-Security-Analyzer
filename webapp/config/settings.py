"""Configuration shared by the web process and collection workers."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",")
CSRF_TRUSTED_ORIGINS = list(filter(None, os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")))
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "inventory.apps.InventoryConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "inventory.request_logging.RequestLoggingMiddleware", "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates",
              "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True,
              "OPTIONS": {"context_processors": [
                  "django.template.context_processors.request", "django.contrib.auth.context_processors.auth",
                  "django.contrib.messages.context_processors.messages", "inventory.preferences.display_preferences", "inventory.version.application_version"]}}]
DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql", "NAME": os.getenv("POSTGRES_DB", "nsx"),
    "USER": os.getenv("POSTGRES_USER", "nsx"), "PASSWORD": os.getenv("POSTGRES_PASSWORD", ""),
    "HOST": os.getenv("POSTGRES_HOST", "db"), "PORT": os.getenv("POSTGRES_PORT", "5432"),
    "CONN_MAX_AGE": 60,
    "CONN_HEALTH_CHECKS": True,
    "OPTIONS": {"sslmode": os.getenv("POSTGRES_SSLMODE", "prefer"),
                "connect_timeout": int(os.getenv("POSTGRES_CONNECT_TIMEOUT", "10"))},
}}
if os.getenv("POSTGRES_SSLROOTCERT"):
    DATABASES["default"]["OPTIONS"]["sslrootcert"] = os.environ["POSTGRES_SSLROOTCERT"]
# Explicit local development/test option; Compose always uses PostgreSQL.
if os.getenv("NSX_SQLITE_PATH"):
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": os.environ["NSX_SQLITE_PATH"]}}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "landing"
LOGOUT_REDIRECT_URL = "login"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "SAMEORIGIN"
SECURE_SSL_REDIRECT = os.getenv("DJANGO_HTTPS", "0") == "1"
SECURE_REDIRECT_EXEMPT = [r"^health/$"]
SESSION_COOKIE_SECURE = SECURE_SSL_REDIRECT
CSRF_COOKIE_SECURE = SECURE_SSL_REDIRECT
if os.getenv("DJANGO_TRUST_PROXY", "0") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
AUDIT_TIMEOUT = int(os.getenv("NSX_AUDIT_TIMEOUT", "3600"))

# Logs are captured by the container runtime; no file volume is required.
LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "formatters": {"console": {"()": "inventory.observability.ConsoleFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "stream": "ext://sys.stdout", "formatter": "console"}},
    "loggers": {name: {"handlers": ["console"], "level": "INFO", "propagate": False}
                for name in ("inventory", "django.request", "django.security")},
}
# 0 retains audit events indefinitely; independent of snapshot retention.
AUDIT_EVENT_RETENTION_DAYS = max(0, int(os.getenv("NSX_AUDIT_LOG_RETENTION_DAYS", "0")))

# Optional Keycloak sign-in. Local authentication remains available.
KEYCLOAK_ENABLED = os.getenv("NSX_KEYCLOAK_ENABLED", "0") == "1"
KEYCLOAK_ISSUER = os.getenv("NSX_KEYCLOAK_ISSUER", "").rstrip("/")
KEYCLOAK_CLIENT_ID = os.getenv("NSX_KEYCLOAK_CLIENT_ID", "")
KEYCLOAK_CLIENT_SECRET = os.getenv("NSX_KEYCLOAK_CLIENT_SECRET", "")
KEYCLOAK_CA_BUNDLE = os.getenv("NSX_KEYCLOAK_CA_BUNDLE", "")
KEYCLOAK_ROLES = {"viewer": "nsx-analyzer-viewer", "operator": "nsx-analyzer-operator", "admin": "nsx-analyzer-admin"}

AUTHENTICATION_BACKENDS = ['django.contrib.auth.backends.ModelBackend', 'inventory.ldap_auth.LDAPBackend']
