from django.urls import path

from . import views


urlpatterns = [
    path("", views.page, {"page_name": "marketing_home"}, name="marketing_home"),
    path("features/", views.page, {"page_name": "marketing_features"}, name="marketing_features"),
    path("how-it-works/", views.page, {"page_name": "marketing_how_it_works"}, name="marketing_how_it_works"),
    path("pricing/", views.page, {"page_name": "marketing_pricing"}, name="marketing_pricing"),
    path("faq/", views.page, {"page_name": "marketing_faq"}, name="marketing_faq"),
    path("contact/", views.contact, name="marketing_contact"),
    path("whatsapp/", views.whatsapp, name="marketing_whatsapp"),
    path("privacy/", views.page, {"page_name": "marketing_privacy"}, name="marketing_privacy"),
    path("terms/", views.page, {"page_name": "marketing_terms"}, name="marketing_terms"),
    path("security/", views.page, {"page_name": "marketing_security"}, name="marketing_security"),
    path("support/", views.page, {"page_name": "marketing_support"}, name="marketing_support"),
]
