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
    path("users/<int:pk>/permissions/", views.user_permission_autosave, name="user_permission_autosave"),
    path("users/<int:pk>/delete/", views.user_access_delete, name="user_access_delete"),
    path("users/<int:pk>/impersonate/", views.impersonate_start, name="impersonate_start"),
    path("groups/", views.group_access_list, name="group_access_list"),
    path("groups/new/", views.group_access_create, name="group_access_create"),
    path("groups/<int:pk>/", views.group_access_update, name="group_access_update"),
    path("groups/<int:pk>/delete/", views.group_access_delete, name="group_access_delete"),
    path("impersonate/stop/", views.impersonate_stop, name="impersonate_stop"),
]
