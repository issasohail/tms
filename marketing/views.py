import re
from urllib.parse import quote

from django.conf import settings
from django.db import DatabaseError
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from core.public_urls import build_public_path_url
from .config import FEATURES, PRICING_PLANS
from .forms import ContactForm


PAGE_TEMPLATES = {
    "marketing_home": "marketing/index.html",
    "marketing_features": "marketing/features.html",
    "marketing_how_it_works": "marketing/how_it_works.html",
    "marketing_pricing": "marketing/pricing.html",
    "marketing_faq": "marketing/faq.html",
    "marketing_privacy": "marketing/privacy.html",
    "marketing_terms": "marketing/terms.html",
    "marketing_security": "marketing/security.html",
    "marketing_support": "marketing/support.html",
}


def _tms_url(request, route_name):
    path = reverse(route_name, urlconf="tms.urls")
    host = request.get_host().split(":", 1)[0].lower()
    if host == settings.MARKETING_PUBLIC_HOST:
        return build_public_path_url(f"/tms{path}")
    return request.build_absolute_uri(path)


def _login_url(request):
    host = request.get_host().split(":", 1)[0].lower()
    if host == settings.MARKETING_PUBLIC_HOST:
        return build_public_path_url(
            f"/tms{settings.LOGIN_URL}?next=/tms/"
        )
    login_path = f"{settings.LOGIN_URL}?next=/dashboard/"
    return request.build_absolute_uri(login_path)


def _base_context(request):
    return {
        "features": FEATURES,
        "pricing_plans": PRICING_PLANS,
        "login_url": _login_url(request),
        "register_url": _tms_url(request, "accounts:signup"),
        "marketing_whatsapp_url": reverse("marketing_whatsapp"),
        "marketing_whatsapp_is_direct": True,
    }


def page(request, page_name):
    context = _base_context(request)
    context["page_name"] = page_name
    return render(request, PAGE_TEMPLATES[page_name], context)


def _destination_number():
    configured = getattr(settings, "MARKETING_WHATSAPP_NUMBER", "").strip()
    if not configured:
        try:
            from core.views import _whatsapp_api_display_number

            configured = (_whatsapp_api_display_number() or "").strip()
        except (DatabaseError, AttributeError, ImportError):
            configured = ""

    digits = re.sub(r"\D", "", configured)
    return digits if 8 <= len(digits) <= 15 else ""


def _floating_whatsapp_url():
    destination = _destination_number()
    if not destination:
        return reverse("marketing_contact"), False

    message = quote(
        "Hello Kirayas.com,\n\n"
        "I would like to learn more about your rental management platform.",
        safe="",
    )
    return f"https://wa.me/{destination}?text={message}", True


def _whatsapp_message(cleaned_data):
    plan_label = dict(ContactForm.PLAN_CHOICES).get(
        cleaned_data.get("plan"),
        "Not specified",
    )
    return "\n".join(
        (
            "New Kirayas.com Website Inquiry",
            "",
            f"Name: {cleaned_data.get('full_name') or 'Not provided'}",
            f"Business: {cleaned_data.get('business_name') or 'Not provided'}",
            f"Phone: {cleaned_data.get('phone') or 'Not provided'}",
            f"Email: {cleaned_data.get('email') or 'Not provided'}",
            f"Units Managed: {cleaned_data.get('units') or 'Not specified'}",
            f"Interested Plan: {plan_label}",
            "",
            "Message:",
            cleaned_data["message"],
        )
    )


def whatsapp(request):
    whatsapp_url, _is_direct = _floating_whatsapp_url()
    return HttpResponseRedirect(whatsapp_url)


@require_http_methods(["GET", "POST"])
def contact(request):
    initial = {}
    requested_plan = request.GET.get("plan", "")
    if requested_plan in dict(ContactForm.PLAN_CHOICES):
        initial["plan"] = requested_plan
    form = ContactForm(
        request.POST if request.method == "POST" else None,
        initial=initial,
    )
    context = _base_context(request)
    context["page_name"] = "marketing_contact"
    context["form"] = form

    if request.method == "POST" and form.is_valid():
        destination = _destination_number()
        if not destination:
            form.add_error(
                None,
                "WhatsApp contact is temporarily unavailable. Please try again later.",
            )
        else:
            message = quote(_whatsapp_message(form.cleaned_data), safe="")
            return HttpResponseRedirect(
                f"https://wa.me/{destination}?text={message}"
            )

    return render(request, "marketing/contact.html", context)
