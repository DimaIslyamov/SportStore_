from .base import *

DEBUG = env.bool("DJANGO_DEBUG", default=True)

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"