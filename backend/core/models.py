from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models

from core.managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    # Admins hand out passwords, so a new account changes it on first login.
    must_change_password = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.email


class AppSettings(models.Model):
    """Application-wide configuration. Exactly one row, pk=1.

    The OpenRouter key lives here rather than on the user because every run
    belongs to the same research project and bills to the same account.
    """

    openrouter_key = models.TextField(blank=True, default="")
    updated_by = models.ForeignKey("core.User", null=True, blank=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "app settings"

    def __str__(self):
        return "Application settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("AppSettings is a singleton and cannot be deleted.")

    @classmethod
    def load(cls):
        instance, _ = cls.objects.get_or_create(pk=1)
        return instance
