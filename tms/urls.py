# builds /static/... URLom dashboard.views import dashboard
from django.templatetags.static import static
from django.views.generic import RedirectView
from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views

from django.conf import settings
from django.conf.urls.static import static
from leases.views import UnitAutocomplete
from django.urls import path, include
from django.urls import path
from django.views.generic import RedirectView
from django.contrib.staticfiles.storage import staticfiles_storage
from core.views import SettingsView

urlpatterns = [
    path('admin/', admin.site.urls),

    # core/urls.py will decide what "" points to
    path('', include('core.urls')),

    path('', include(('dashboard.urls', 'dashboard'), namespace='dashboard')),
    # path('dashboard/', views.dashboard, name='dashboard'),
    # path('', dashboard, name='dashboard'),
    path('login/', auth_views.LoginView.as_view(template_name='login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),

    path(
        "favicon.ico",
        RedirectView.as_view(
            url=staticfiles_storage.url("images/favicon.ico"),  # <-- STRING
            permanent=True,
        ),
    ),
    # App URLs
    path('tenants/', include(('tenants.urls', 'tenants'), namespace='tenants')),
    path('payments/', include(('payments.urls', 'payments'), namespace='payments')),
    path('expenses/', include(('expenses.urls', 'expenses'), namespace='expenses')),
    path('documents/', include(('documents.urls', 'documents'), namespace='documents')),
    path('notifications/', include(('notifications.urls',
         'notifications'), namespace='notifications')),
    path('reports/', include(('reports.urls', 'reports'), namespace='reports')),
    path('utilities/', include('utilities.urls')),  # Add this line
    path('properties/', include(('properties.urls',
         'properties'), namespace='properties')),
    path('accounts/', include(('accounts.urls',
         'accounts'), namespace='accounts')),
    path('leases/', include('leases.urls')),

    path('invoices/', include(('invoices.urls', 'invoices'), namespace='invoices')),

    # Password reset URLs
    path('accounts/password_reset/',
         auth_views.PasswordResetView.as_view(
             template_name='accounts/password_reset.html'
         ),
         name='password_reset'),
    path('accounts/password_reset/done/',
         auth_views.PasswordResetDoneView.as_view(
             template_name='accounts/password_reset_done.html'
         ),
         name='password_reset_done'),
    path('accounts/reset/<uidb64>/<token>/',
         auth_views.PasswordResetConfirmView.as_view(
             template_name='accounts/password_reset_confirm.html'
         ),
         name='password_reset_confirm'),
    path('accounts/reset/done/',
         auth_views.PasswordResetCompleteView.as_view(
             template_name='accounts/password_reset_complete.html'
         ),
         name='password_reset_complete'),

    path('unit-autocomplete/', UnitAutocomplete.as_view(),
         name='unit-autocomplete'),

    path("smart-meter/", include("smart_meter.urls")),

    path("api/", include("leases.urls_pcr")),


    path("accounts/", include("accounts.urls")),           # signup/profile
    path("accounts/", include("django.contrib.auth.urls")),

] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if settings.DEBUG:
    from django.conf.urls.static import static
    urlpatterns += static(settings.MEDIA_URL,
                          document_root=settings.MEDIA_ROOT)
