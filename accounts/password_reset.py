from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.forms import PasswordResetForm


class PublicPasswordResetForm(PasswordResetForm):
    """Force password-reset emails to use the canonical public origin."""

    def save(self, *args, **kwargs):
        public_origin = urlsplit(settings.PUBLIC_BASE_URL)
        kwargs["domain_override"] = public_origin.netloc
        kwargs["use_https"] = public_origin.scheme == "https"
        return super().save(*args, **kwargs)
