import logging

from django.shortcuts import render


logger = logging.getLogger(__name__)


def _reason_category(reason):
    value = str(reason or "").lower()
    for needle, category in (
        ("csrf cookie not set", "missing_cookie"),
        ("csrf token missing", "missing_token"),
        ("incorrect length", "invalid_length"),
        ("invalid characters", "invalid_characters"),
        ("origin checking failed", "origin_check_failed"),
        ("referer checking failed", "referer_check_failed"),
        ("csrf token from post", "token_mismatch"),
        ("csrf token from the 'x-csrftoken'", "token_mismatch"),
    ):
        if needle in value:
            return category
    return "other"


def csrf_failure(request, reason=""):
    logger.warning("CSRF validation rejected a request reason=%s", _reason_category(reason))
    return render(
        request,
        "tenants/csrf_failure.html",
        {"reload_url": request.get_full_path()},
        status=403,
    )
