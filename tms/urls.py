from dashboard.views import dashboard
from importlib import import_module
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.staticfiles.storage import staticfiles_storage
from django.templatetags.static import static
from django.urls import path, include
from django.views.generic import RedirectView

from django.conf import settings
from django.conf.urls.static import static

from leases.views import UnitAutocomplete
from leases import views_lease_files
from core.views import SettingsView
from accounts.views import LogoutView as AccountsLogoutView
from accounts.password_reset import PublicPasswordResetForm
from whatsapp.views import webhook as whatsapp_webhook


def plain_include(module_path):
    return include(import_module(module_path).urlpatterns)


# -----------------------------
# Namespaced App URLs
# -----------------------------
app_patterns = [
    path('', include('core.urls')),

    path('dashboard/', include(('dashboard.urls', 'dashboard'),
         namespace='dashboard')),

    path(
        "favicon.ico",
        RedirectView.as_view(
            url=staticfiles_storage.url("images/favicon.ico"),
            permanent=True,
        ),
    ),

    path('tenants/', include(
        ('tenants.urls', 'tenants'),
        namespace='tenants'
    )),

    path('payments/', include(
        ('payments.urls', 'payments'),
        namespace='payments'
    )),

    path('expenses/', include(
        ('expenses.urls', 'expenses'),
        namespace='expenses'
    )),

    path('documents/', include(
        ('documents.urls', 'documents'),
        namespace='documents'
    )),

    path('notifications/', include(
        ('notifications.urls', 'notifications'),
        namespace='notifications'
    )),

    path('reports/', include(
        ('reports.urls', 'reports'),
        namespace='reports'
    )),

    path('utilities/', include('utilities.urls')),

    path('properties/', include(
        ('properties.urls', 'properties'),
        namespace='properties'
    )),

    path('accounts/', include(
        ('accounts.urls', 'accounts'),
        namespace='accounts'
    )),

    path('leases/', include('leases.urls')),

    path('invoices/', include(
        ('invoices.urls', 'invoices'),
        namespace='invoices'
    )),

    path('maintenance/', include(
        ('maintenance.urls', 'maintenance'),
        namespace='maintenance'
    )),

    path('handyman/', include(
        ('handyman.urls', 'handyman'),
        namespace='handyman'
    )),

    path(
        "whatsapp/webhook/",
        whatsapp_webhook,
        name="whatsapp_webhook",
    ),

    path('whatsapp/', include(
        ('whatsapp.urls', 'whatsapp'),
        namespace='whatsapp'
    )),

    path(
        'unit-autocomplete/',
        UnitAutocomplete.as_view(),
        name='unit-autocomplete'
    ),

    path("smart-meter/", include("smart_meter.urls")),
    path("api/", include("leases.urls_pcr")),

    path("accounts/", include("accounts.urls")),
    path("accounts/", include("django.contrib.auth.urls")),
]


# Legacy `/tms/` compatibility URLs are intentionally not namespaced. Canonical
# namespaced reversing uses the root application routes in `app_patterns`.
root_app_patterns = [
    path('', plain_include('core.urls')),
    path('', plain_include('dashboard.urls')),

    path(
        "favicon.ico",
        RedirectView.as_view(
            url=staticfiles_storage.url("images/favicon.ico"),
            permanent=True,
        ),
    ),

    path('tenants/', plain_include('tenants.urls')),
    path('payments/', plain_include('payments.urls')),
    path('expenses/', plain_include('expenses.urls')),
    path('documents/', plain_include('documents.urls')),
    path('notifications/', plain_include('notifications.urls')),
    path('reports/', plain_include('reports.urls')),
    path('utilities/', plain_include('utilities.urls')),
    path('properties/', plain_include('properties.urls')),
    path('accounts/', plain_include('accounts.urls')),
    path('leases/', plain_include('leases.urls')),
    path('invoices/', plain_include('invoices.urls')),
    path('maintenance/', plain_include('maintenance.urls')),
    path('handyman/', plain_include('handyman.urls')),
    path("whatsapp/webhook/", whatsapp_webhook, name="whatsapp_webhook"),
    path('whatsapp/', plain_include('whatsapp.urls')),

    path(
        'unit-autocomplete/',
        UnitAutocomplete.as_view(),
        name='unit-autocomplete'
    ),

    path("smart-meter/", plain_include("smart_meter.urls")),
    path("api/", plain_include("leases.urls_pcr")),

    path("accounts/", plain_include("accounts.urls")),
    path("accounts/", include("django.contrib.auth.urls")),
]


urlpatterns = [
    path('admin/', admin.site.urls),

    # Login / Logout
    path(
        'login/',
        auth_views.LoginView.as_view(
            template_name='login.html'
        ),
        name='login'
    ),

    path(
        'logout/',
        AccountsLogoutView.as_view(),
        name='logout'
    ),

    # Password Reset URLs
    path(
        'accounts/password_reset/',
        auth_views.PasswordResetView.as_view(
            template_name='accounts/password_reset.html',
            form_class=PublicPasswordResetForm,
        ),
        name='password_reset'
    ),

    path(
        'accounts/password_reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='accounts/password_reset_done.html'
        ),
        name='password_reset_done'
    ),

    path(
        'accounts/reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='accounts/password_reset_confirm.html'
        ),
        name='password_reset_confirm'
    ),

    path(
        'accounts/reset/done/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='accounts/password_reset_complete.html'
        ),
        name='password_reset_complete'
    ),

    # Always show the marketing website at the site root.
    path('', include('marketing.urls')),

    # Canonical application URLs at the Kirayas domain root.
    *app_patterns,

    path(
        "public/files/<str:token>/",
        views_lease_files.public_lease_file_share,
        name="public_file_share_root",
    ),
    path(
        "public/files/<str:token>/download/",
        views_lease_files.public_lease_file_download,
        name="public_file_share_download_root",
    ),
    path(
        "public/lease-files/<str:token>/",
        views_lease_files.public_lease_files_share,
        name="public_lease_files_share_root",
    ),
    path(
        "public/lease-files/<str:token>/<int:document_id>/download/",
        views_lease_files.public_lease_shared_document_download,
        name="public_lease_files_download_root",
    ),

    # Legacy unnamespaced compatibility URLs.
    path('tms/', include(root_app_patterns)),
]


urlpatterns += static(
    settings.MEDIA_URL,
    document_root=settings.MEDIA_ROOT
)

if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT
    )

if getattr(settings, "ENABLE_LOCAL_DEBUG_TOOLBAR", False):
    urlpatterns += [
        path("__debug__/", include("debug_toolbar.urls")),
    ]
