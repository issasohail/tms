from django.urls import path
from .views import LoginView, LogoutView
from . import views

urlpatterns = [
    path("login/",  LoginView.as_view(),  name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("signup/", views.signup,         name="signup"),
    path("profile/", views.profile,       name="profile"),
    path("password/", views.auth_views.PasswordChangeView.as_view(
        template_name="accounts/password_change.html",
        success_url="/tms/accounts/profile/",
    ), name="password_change"),
    path("users/", views.user_access_list, name="user_access_list"),
    path("users/new/", views.user_access_create, name="user_access_create"),
    path("users/<int:pk>/", views.user_access_update, name="user_access_update"),
    path("users/<int:pk>/impersonate/", views.impersonate_start, name="impersonate_start"),
    path("impersonate/stop/", views.impersonate_stop, name="impersonate_stop"),
]
