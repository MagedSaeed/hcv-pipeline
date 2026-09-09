import pytest
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.mark.django_db
def test_create_user_uses_email_as_identifier():
    user = User.objects.create_user(email="A@Example.COM", password="pw-123456")
    assert user.email == "A@example.com"
    assert user.check_password("pw-123456")
    assert user.is_staff is False
    assert User.USERNAME_FIELD == "email"
    assert User.REQUIRED_FIELDS == []


@pytest.mark.django_db
def test_admin_created_user_must_change_password():
    user = User.objects.create_user(email="r@example.com", password="pw-123456")
    assert user.must_change_password is True


@pytest.mark.django_db
def test_superuser_is_staff_and_keeps_its_password():
    user = User.objects.create_superuser(email="a@example.com", password="pw-123456")
    assert user.is_staff is True
    assert user.is_superuser is True
    assert user.must_change_password is False


@pytest.mark.django_db
def test_email_is_required():
    with pytest.raises(ValueError, match="Email is required"):
        User.objects.create_user(email="", password="pw-123456")


@pytest.mark.django_db
def test_create_superuser_rejects_is_staff_false():
    with pytest.raises(ValueError, match="Superuser must have is_staff=True"):
        User.objects.create_superuser(email="a@example.com", password="pw-123456", is_staff=False)


@pytest.mark.django_db
def test_create_superuser_rejects_is_superuser_false():
    with pytest.raises(ValueError, match="Superuser must have is_superuser=True"):
        User.objects.create_superuser(
            email="a@example.com", password="pw-123456", is_superuser=False
        )
