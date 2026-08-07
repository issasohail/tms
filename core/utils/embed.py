"""Shared helper for Settings-tool views embedded via ?embed=1 iframes.

Any view rendered inside core/templates/core/settings.html's iframes must
preserve the `embed=1` query flag across every redirect it issues (e.g.
after a successful POST), or the iframe reloads as a full page -- complete
with its own navbar/footer -- nested inside the outer Settings page's own
navbar. base.html already suppresses navbar/footer correctly based on the
`embedded_in_settings` context variable (see core.context_processors); the
only thing each view needs to do is not lose that flag on redirect.

Usage, in place of `redirect(...)`:

    from core.utils.embed import embed_redirect

    return embed_redirect(request, "core:backup_center")
    return embed_redirect(request, "core:suggestion_detail", pk=suggestion.pk)
    return embed_redirect(request, f"{request.path}?phone={phone_number}")

The third form accepts a pre-built path/URL directly (with or without an
existing query string) for views that redirect to a dynamic path rather
than a named URL.
"""
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.urls.exceptions import NoReverseMatch


def _append_embed(url: str, request) -> str:
    if request.GET.get("embed") != "1":
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}embed=1"


def embed_redirect(request, to, *args, **kwargs):
    """Redirect to a named URL or literal path, preserving ?embed=1.

    `to` may be a URL name (resolved via reverse()) or a literal path/URL
    (used as-is) if it isn't a valid URL name.
    """
    try:
        url = reverse(to, args=args, kwargs=kwargs)
    except NoReverseMatch:
        url = to
    return HttpResponseRedirect(_append_embed(url, request))
