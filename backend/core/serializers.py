from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from core.keys import key_source, mask_key
from core.models import AppSettings

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "name", "is_staff", "must_change_password"]
        read_only_fields = fields


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = authenticate(
            self.context["request"],
            username=attrs["email"],
            password=attrs["password"],
        )
        if user is None:
            raise serializers.ValidationError("Invalid email or password.")
        attrs["user"] = user
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_current_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate_new_password(self, value):
        user = self.context["request"].user
        # Without this, an account can satisfy the forced first-login change by
        # retyping the password it was issued with.
        if user.check_password(value):
            raise serializers.ValidationError(
                "The new password must be different from the current one."
            )
        validate_password(value, user)
        return value


class AppSettingsSerializer(serializers.ModelSerializer):
    openrouter_key = serializers.CharField(
        write_only=True, allow_blank=True, required=False, trim_whitespace=True
    )
    openrouter_key_masked = serializers.SerializerMethodField()
    key_source = serializers.SerializerMethodField()

    class Meta:
        model = AppSettings
        fields = ["openrouter_key", "openrouter_key_masked", "key_source", "updated_at"]
        read_only_fields = ["updated_at"]

    def get_openrouter_key_masked(self, obj):
        return mask_key(obj.openrouter_key)

    def get_key_source(self, _obj):
        return key_source()
