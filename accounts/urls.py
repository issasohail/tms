from django.urls import path
from .views import LoginView, LogoutView
from . import views

urlpatterns = [
    path("login/",  LoginView.as_view(),  name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("signup/", views.signup,         name="signup"),
    path("profile/", views.profile,       name="profile"),
]
