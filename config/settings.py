"""
Django settings for the RHM SMS Transaction Extractor.

All sensitive configuration comes from environment variables (or a local
`.env` file next to manage.py). See `.env.example`.
"""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

try:  # optional .env support
    from dotenv import load_dotenv

    # RHM_ENV_FILE lets servers keep secrets outside the code directory (e.g. /etc/rhm/rhm.env)
    load_dotenv(os.environ.get("RHM_ENV_FILE") or (BASE_DIR / ".env"))
except ImportError:  # pragma: no cover
    pass


def env(name, default=None, cast=str):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    if cast is bool:
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if cast is list:
        return [v.strip() for v in value.split(",") if v.strip()]
    return cast(value)


DEBUG = env("DJANGO_DEBUG", False, bool)
SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-insecure-key-change-me"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DEBUG is off.")

ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS", ["localhost", "127.0.0.1"], list)
CSRF_TRUSTED_ORIGINS = env("DJANGO_CSRF_TRUSTED_ORIGINS", [], list)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "accounts",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.app_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------- Database
# DB_ENGINE: postgresql | mysql | sqlite  (sqlite is for local development only)
DB_ENGINE = env("DB_ENGINE", "sqlite").lower()
if DB_ENGINE in {"postgres", "postgresql"}:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DB_NAME", "rhm_extractor"),
            "USER": env("DB_USER", "postgres"),
            "PASSWORD": env("DB_PASSWORD", ""),
            "HOST": env("DB_HOST", "localhost"),
            "PORT": env("DB_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }
elif DB_ENGINE == "mysql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": env("DB_NAME", "rhm_extractor"),
            "USER": env("DB_USER", "root"),
            "PASSWORD": env("DB_PASSWORD", ""),
            "HOST": env("DB_HOST", "localhost"),
            "PORT": env("DB_PORT", "3306"),
            "OPTIONS": {
                "charset": "utf8mb4",
                "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
                "isolation_level": "read committed",
            },
            "CONN_MAX_AGE": 60,
            "CONN_HEALTH_CHECKS": True,
            "TEST": {"CHARSET": "utf8mb4", "COLLATION": "utf8mb4_unicode_ci"},
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

LANGUAGE_CODE = "en-gb"
TIME_ZONE = env("TIME_ZONE", "Africa/Nairobi")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = Path(env("STATIC_ROOT", str(BASE_DIR / "staticfiles")))

# Private storage: uploaded templates and generated reports are NEVER served
# directly – only through permission-checked views.
PRIVATE_MEDIA_ROOT = Path(env("PRIVATE_MEDIA_ROOT", str(BASE_DIR / "private_media")))
BACKUP_DIR = Path(env("BACKUP_DIR", str(BASE_DIR / "backups")))

# Upload limits
MAX_UPLOAD_MB = env("MAX_UPLOAD_MB", 5, int)
FILE_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_MB * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 20000
MAX_MESSAGES_PER_BATCH = env("MAX_MESSAGES_PER_BATCH", 2000, int)

# Path to the original desktop script. When present its parse_mpesa_message()
# is loaded (safely, without running its Tkinter GUI) and used as the first
# extraction pass. A vendored copy is used when the file is not reachable.
MPESAV3_SCRIPT_PATH = env("MPESAV3_SCRIPT_PATH", str(BASE_DIR / "legacy" / "mpesav3.py"))

# Download-password protection
DOWNLOAD_MAX_ATTEMPTS = env("DOWNLOAD_MAX_ATTEMPTS", 5, int)
DOWNLOAD_LOCKOUT_SECONDS = env("DOWNLOAD_LOCKOUT_SECONDS", 900, int)
# Initial download password used only to seed the hashed value on first run.
INITIAL_DOWNLOAD_PASSWORD = env("INITIAL_DOWNLOAD_PASSWORD", None)

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "rhm_cache",
    }
}

# ---------------------------------------------------------------- Email
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = env("EMAIL_PORT", 587, int)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env("EMAIL_USE_TLS", True, bool)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "RHM Extractor <no-reply@localhost>")

# ---------------------------------------------------------------- Security
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = env("SESSION_COOKIE_AGE", 60 * 60 * 8, int)
CSRF_COOKIE_HTTPONLY = False  # JS reads csrftoken for fetch() calls
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

# HTTPS_ENABLED=False only for internal plain-HTTP deployments (not recommended).
HTTPS_ENABLED = env("HTTPS_ENABLED", not DEBUG, bool)
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env("SECURE_SSL_REDIRECT", False, bool)  # Nginx already redirects HTTP -> HTTPS
    SESSION_COOKIE_SECURE = HTTPS_ENABLED
    CSRF_COOKIE_SECURE = HTTPS_ENABLED
    SESSION_COOKIE_NAME = "__Host-rhm_session" if HTTPS_ENABLED else "rhm_session"
    CSRF_COOKIE_NAME = "csrftoken"
    if HTTPS_ENABLED:
        SECURE_HSTS_SECONDS = env("SECURE_HSTS_SECONDS", 31536000, int)
        SECURE_HSTS_INCLUDE_SUBDOMAINS = env("SECURE_HSTS_INCLUDE_SUBDOMAINS", False, bool)
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }

# Data retention (days). 0 disables purging. Applied by `manage.py purge_old_data`.
RETENTION_DAYS_TRANSACTIONS = env("RETENTION_DAYS_TRANSACTIONS", 0, int)
RETENTION_DAYS_REPORTS = env("RETENTION_DAYS_REPORTS", 365, int)
RETENTION_DAYS_AUDIT = env("RETENTION_DAYS_AUDIT", 730, int)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "loggers": {
        "rhm": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

ADMINS = [("Admin", a) for a in env("DJANGO_ADMINS", [], list)]
SERVER_EMAIL = env("SERVER_EMAIL", DEFAULT_FROM_EMAIL)

# Nginx performs the HTTP->HTTPS redirect; HSTS preload is opt-in.
SILENCED_SYSTEM_CHECKS = ["security.W008", "security.W021"]

# OCR (image → SMS text). Leave TESSERACT_CMD empty to find `tesseract` on PATH.
# Windows example: TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
TESSERACT_CMD = env("TESSERACT_CMD", "")
OCR_LANG = env("OCR_LANG", "eng")
MAX_IMAGES_PER_UPLOAD = env("MAX_IMAGES_PER_UPLOAD", 20, int)
