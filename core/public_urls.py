from urllib.parse import urlencode, urljoin

from django.conf import settings
from django.urls import reverse


def build_public_path_url(path, query=None):
    """Build a canonical absolute URL for an externally shared path."""
    base_url = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/"
    url = urljoin(base_url, str(path or "").lstrip("/"))
    if query:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlencode(query, doseq=True)}"
    return url


def build_public_url(viewname, kwargs=None, args=None, query=None):
    """Reverse a named Django URL and attach the canonical public origin."""
    path = reverse(viewname, kwargs=kwargs, args=args)
    return build_public_path_url(path, query=query)
