from django.contrib.auth import login, logout, update_session_auth_hash
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import AppSettings
from core.serializers import (
    AppSettingsSerializer,
    ChangePasswordSerializer,
    LoginSerializer,
    UserSerializer,
)


@method_decorator(ensure_csrf_cookie, name="get")
class CSRFView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"detail": "CSRF cookie set."})


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        login(request, user)
        return Response(UserSerializer(user).data)


class LogoutView(APIView):
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    def get(self, request):
        return Response(UserSerializer(request.user).data)


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = request.user
        user.set_password(serializer.validated_data["new_password"])
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password"])
        # Django ties the session to the password hash; without this the
        # user is silently logged out by the very change they just made.
        update_session_auth_hash(request, user)
        return Response(UserSerializer(user).data)


class AppSettingsView(APIView):
    """Read and update application settings. Staff only.

    IsAdminUser checks is_staff, which is the gate the spec puts on the key.
    """

    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response(AppSettingsSerializer(AppSettings.load()).data)

    def patch(self, request):
        instance = AppSettings.load()
        serializer = AppSettingsSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        return Response(AppSettingsSerializer(instance).data)
