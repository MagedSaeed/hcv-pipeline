from django.urls import path

from core import views

urlpatterns = [
    path("auth/csrf/", views.CSRFView.as_view(), name="csrf"),
    path("auth/login/", views.LoginView.as_view(), name="login"),
    path("auth/logout/", views.LogoutView.as_view(), name="logout"),
    path("auth/me/", views.MeView.as_view(), name="me"),
    path(
        "auth/change-password/",
        views.ChangePasswordView.as_view(),
        name="change-password",
    ),
    path("settings/", views.AppSettingsView.as_view(), name="app-settings"),
]
