import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()


class Command(BaseCommand):
    help = (
        "Create a superuser from DJANGO_SUPERUSER_EMAIL, DJANGO_SUPERUSER_PASSWORD and the "
        "optional DJANGO_SUPERUSER_USERNAME (stored as the name). Does nothing if the email "
        "or password is empty, or if the account already exists. Run on every deploy."
    )

    def handle(self, *args, **options):
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")
        name = os.environ.get("DJANGO_SUPERUSER_USERNAME", "").strip()

        if not email or not password:
            self.stdout.write(
                "DJANGO_SUPERUSER_EMAIL and DJANGO_SUPERUSER_PASSWORD are not both set; skipped"
            )
            return

        email = User.objects.normalize_email(email)
        # Existing accounts are never touched, so changing the variables later
        # does not reset a password someone has since changed in the app.
        if User.objects.filter(email__iexact=email).exists():
            self.stdout.write(f"An account for {email} already exists; left unchanged")
            return

        try:
            validate_password(password, User(email=email, name=name))
        except ValidationError as error:
            raise CommandError(
                "DJANGO_SUPERUSER_PASSWORD was refused: " + " ".join(error.messages)
            ) from error

        User.objects.create_superuser(email=email, password=password, name=name)
        self.stdout.write(self.style.SUCCESS(f"Created superuser {email}"))
