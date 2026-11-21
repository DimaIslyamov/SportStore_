from datetime import timedelta
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
env.read_env(BASE_DIR / ".env", overwrite=False)

CATALOG_S3_BUCKET = env("CATALOG_S3_BUCKET", default="")
CATALOG_S3_REGION = env("CATALOG_S3_REGION", default="")

SECRET_KEY = env("DJANGO_SECRET_KEY")
JWT_SIGNING_KEY = env("JWT_SIGNING_KEY")
for name, value, minimum in (
    ("DJANGO_SECRET_KEY", SECRET_KEY, 50),
    ("JWT_SIGNING_KEY", JWT_SIGNING_KEY, 32),
):
    if (
        len(value.strip()) < minimum
        or len(set(value.strip())) < 5
        or value.lstrip().lower().startswith(("replace-with-", "django-insecure-"))
    ):
        raise ImproperlyConfigured(f"Set {name} to a random, non-placeholder value.")
if SECRET_KEY == JWT_SIGNING_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY and JWT_SIGNING_KEY must differ.")

DEBUG = False
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])
SESSION_COOKIE_SECURE = env.bool("DJANGO_SESSION_COOKIE_SECURE", default=False)
CSRF_COOKIE_SECURE = env.bool("DJANGO_CSRF_COOKIE_SECURE", default=False)
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=False)
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=0)
DATA_UPLOAD_MAX_NUMBER_FILES = 1

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "drf_spectacular",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "accounts.apps.AccountsConfig",
    "catalog.apps.CatalogConfig",
    "cart.apps.CartConfig",
    "orders.apps.OrdersConfig",
    "payments.apps.PaymentsConfig",
    "core.apps.CoreConfig",
]

MIDDLEWARE = [
    "core.middleware.RequestContextMiddleware",
    "django.middleware.security.SecurityMiddleware",
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
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["OPTIONS"] = {
    **DATABASES["default"].get("OPTIONS", {}),
    "connect_timeout": 5,
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
        ),
    },
    {
        "NAME": ("django.contrib.auth.password_validation.MinimumLengthValidator"),
    },
    {
        "NAME": ("django.contrib.auth.password_validation.CommonPasswordValidator"),
    },
    {
        "NAME": ("django.contrib.auth.password_validation.NumericPasswordValidator"),
    },
]

LANGUAGE_CODE = "ru"
TIME_ZONE = env("DJANGO_TIME_ZONE", default="UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "core.errors.exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "NUM_PROXIES": 0,
    "DEFAULT_THROTTLE_RATES": {
        "auth_login": "5/min",
        "auth_register": "5/hour",
        "auth_refresh": "30/min",
        "checkout": "10/min",
        "payment": "10/min",
    },
}

SPECTACULAR_SETTINGS = {
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",
        "core.schema.require_command_fields",
    ],
    "TITLE": "SportStore API",
    "DESCRIPTION": (
        "Diploma MVP for a sporting goods store. "
        "Monetary amounts are decimal strings in PLN. Error responses contain "
        "error.code, error.message, error.details, and error.request_id. "
        "Each response includes X-Request-ID. JWT authenticates the user, "
        "and the API checks roles and resource ownership."
    ),
    "VERSION": "1.0.0",
    "OAS_VERSION": "3.0.3",
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_PUBLIC": True,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
    "SERVE_AUTHENTICATION": [],
    "COMPONENT_SPLIT_REQUEST": True,
    "ENUM_NAME_OVERRIDES": {
        "OrderStatusEnum": "orders.models.Order.Status",
        "PaymentStatusEnum": "payments.models.Payment.Status",
    },
    "TAGS": [
        {"name": "Health", "description": "API and PostgreSQL availability"},
        {"name": "Authentication", "description": "Registration and JWT"},
        {"name": "Users", "description": "Your user profile"},
        {"name": "Addresses", "description": "Customer addresses"},
        {"name": "Catalog", "description": "Catalog browsing and reference data"},
        {"name": "Manager Catalog", "description": "Catalog management"},
        {"name": "Cart", "description": "Customer cart"},
        {"name": "Orders", "description": "Checkout and order lifecycle"},
        {"name": "Payments", "description": "Synchronous simulated payment attempts"},
    ],
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=10),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": False,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": JWT_SIGNING_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "CHECK_USER_IS_ACTIVE": True,
}


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "core.logging.JsonFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        name: {"handlers": ["console"], "level": "INFO", "propagate": False}
        for name in ("sportstore", "django", "django.server")
    },
}
