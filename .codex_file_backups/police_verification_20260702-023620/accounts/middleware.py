from django.contrib.auth import get_user_model
from django.apps import apps
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.utils.functional import SimpleLazyObject


def _impersonated_user(request):
    original_user = getattr(request, "real_user", None)
    if original_user is None:
        original_user = getattr(request, "user", None)
    impersonate_id = request.session.get("impersonate_user_id")
    impersonator_id = request.session.get("impersonator_user_id")
    if not impersonate_id or not impersonator_id:
        return original_user

    User = get_user_model()
    try:
        user = User.objects.get(pk=impersonate_id, is_active=True)
    except User.DoesNotExist:
        request.session.pop("impersonate_user_id", None)
        request.session.pop("impersonator_user_id", None)
        return original_user

    user.is_impersonated = True
    user.impersonator_id = impersonator_id
    return user


class ImpersonationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if hasattr(request, "user"):
            request.real_user = request.user
            request.user = SimpleLazyObject(lambda: _impersonated_user(request))
        return self.get_response(request)


class NoCacheAuthenticatedMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        if getattr(user, "is_authenticated", False):
            response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"
        return response


EXEMPT_APP_NAMES = {
    "admin",
    "accounts",
    "dashboard",
}

EXEMPT_URL_NAMES = {
    "login",
    "logout",
    "password_reset",
    "password_reset_done",
    "password_reset_confirm",
    "password_reset_complete",
    "dashboard",
    "suggestion_list",
    "suggestion_create",
    "suggestion_detail",
    "suggestion_status_update",
    "tenant_public_registration",
    "public_lease_family_add",
    "public_lease_family_cnic_check",
    "public_lease_files_share",
    "public_lease_file_share",
    "public_lease_file_download",
    "public_lease_shared_document_download",
    "unit_media_public_share",
    "unit_media_public_file",
    "media_public_share",
    "media_public_file",
    "webhook",
    "whatsapp_webhook",
}

ACTION_WORDS = {
    "delete": "delete",
    "remove": "delete",
    "deactivate": "delete",
    "create": "add",
    "add": "add",
    "new": "add",
    "update": "change",
    "edit": "change",
    "inline": "change",
    "upload": "change",
    "sort": "change",
    "reorder": "change",
    "toggle": "change",
    "assign": "change",
    "install": "change",
    "switch": "change",
    "close": "change",
    "convert": "change",
    "ignore": "change",
    "approve": "change",
    "recharge": "change",
    "refund": "change",
    "cutoff": "change",
    "restore": "change",
    "set": "change",
    "post": "change",
    "distribute": "change",
    "generate": "change",
    "run": "change",
    "bulk": "change",
    "apply": "change",
    "send": "view",
    "pdf": "view",
    "export": "view",
    "download": "view",
    "print": "view",
    "whatsapp": "view",
    "ledger": "view",
    "list": "view",
    "detail": "view",
    "view": "view",
}

APP_MODEL_DEFAULTS = {
    "tenants": "tenant",
    "properties": "property",
    "leases": "lease",
    "payments": "payment",
    "invoices": "invoice",
    "expenses": "expense",
    "utilities": "utility",
    "maintenance": "maintenancerequest",
    "smart_meter": "meter",
    "reports": "report",
    "core": "globalsettings",
    "accounts": "account",
    "whatsapp": "whatsappmessagelog",
}

URL_MODEL_HINTS = {
    "properties": {
        "unit": "unit",
        "property": "property",
        "media": "unit",
    },
    "leases": {
        "template": "leasetemplate",
        "renewal": "leaserenewal",
        "history": "leaserenewal",
        "photo": "leasemedia",
        "photos": "leasemedia",
        "media": "leasemedia",
        "pcr": "propertyconditionreport",
        "file": "leasedocument",
        "files": "leasedocument",
        "document": "leasedocument",
        "family": "leasefamilymember",
        "clause": "leaseagreementclause",
    },
    "payments": {
        "allocation": "paymentallocation",
        "allocations": "paymentallocation",
        "cash": "payment",
        "ledger": "payment",
        "payment": "payment",
    },
    "invoices": {
        "category": "itemcategory",
        "categories": "itemcategory",
        "recurring": "recurringcharge",
        "waterbill": "waterbill",
        "water": "waterbill",
        "security": "securitydeposittransaction",
        "item": "invoiceitem",
        "items": "invoiceitem",
    },
    "expenses": {
        "category": "expensecategory",
        "categories": "expensecategory",
        "receipt": "expensereceipt",
        "distribution": "expensedistribution",
        "distributions": "expensedistribution",
    },
    "maintenance": {
        "media": "maintenancerequestmedia",
        "request": "maintenancerequest",
    },
    "smart_meter": {
        "reading": "meterreading",
        "readings": "meterreading",
        "unknown": "unknownmeter",
        "bill": "bill",
        "invoice": "bill",
        "settings": "metersettings",
        "tariff": "tariff",
        "meter": "meter",
        "meters": "meter",
        "live": "meter",
        "energy": "meter",
        "report": "meter",
    },
    "reports": {
        "financial": "financialreport",
        "report": "report",
        "reports": "report",
    },
    "whatsapp": {
        "conversation": "whatsappconversation",
        "conversations": "whatsappconversation",
        "message": "whatsappmessagelog",
        "messages": "whatsappmessagelog",
        "webhook": "whatsappwebhooklog",
        "logs": "whatsappstaffactionlog",
        "action": "whatsappstaffactionlog",
        "actions": "whatsappstaffactionlog",
        "device": "trusteddeviceregistry",
        "devices": "trusteddeviceregistry",
        "external": "whatsappexternallinktoken",
        "link": "whatsappexternallinktoken",
        "links": "whatsappexternallinktoken",
        "property": "whatsappstaffpropertyaccess",
        "access": "whatsappstaffpropertyaccess",
        "payment": "pendingwhatsapppayment",
        "media": "pendingwhatsappmedia",
        "maintenance": "pendingwhatsappmaintenance",
    },
    "core": {
        "payment": "paymentmethod",
        "settings": "globalsettings",
        "backup": "globalsettings",
        "suggestion": "globalsettings",
    },
    "accounts": {
        "user": "account",
        "users": "account",
    },
}


def _request_app_name(match):
    if not match:
        return ""
    if match.app_name:
        return match.app_name
    if match.namespace:
        return match.namespace
    module = getattr(match.func, "__module__", "") or ""
    return module.split(".", 1)[0]


def _action_from_url_name(url_name, method):
    words = (url_name or "").replace("-", "_").split("_")
    for word in words:
        if word in ACTION_WORDS:
            return ACTION_WORDS[word]
    if method and method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        return "change"
    return "view"


def _model_from_url(app_name, url_name):
    words = (url_name or "").replace("-", "_").split("_")
    hints = URL_MODEL_HINTS.get(app_name, {})
    for word in words:
        if word in hints:
            return hints[word]
    return APP_MODEL_DEFAULTS.get(app_name)


def _permission_for_request(request, match):
    url_name = match.url_name or ""
    app_name = _request_app_name(match)
    if not app_name or app_name in EXEMPT_APP_NAMES or url_name in EXEMPT_URL_NAMES:
        return None

    model_name = _model_from_url(app_name, url_name)
    if not model_name:
        return None
    try:
        model = apps.get_model(app_name, model_name)
    except LookupError:
        return None

    action = _action_from_url_name(url_name, request.method)
    codename = f"{action}_{model._meta.model_name}"
    return f"{model._meta.app_label}.{codename}"


class PermissionEnforcementMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = getattr(request, "resolver_match", None)
        required_perm = _permission_for_request(request, match)
        if not required_perm:
            return None

        user = request.user
        if not user.is_authenticated:
            return redirect("login")
        if user.is_superuser or user.has_perm(required_perm):
            return None
        raise PermissionDenied(f"You do not have permission: {required_perm}")
