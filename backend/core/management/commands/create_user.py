import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()


class Command(BaseCommand):
    help = "Create an account. The user is forced to change the password on first login."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("--name", default="")
        parser.add_argument(
            "--password",
            default=None,
            help="Password to issue. A random one is generated and printed if omitted.",
        )
        parser.add_argument("--staff", action="store_true", help="Grant is_staff.")

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise CommandError(f"An account for {email} already exists.")

        password = options["password"] or secrets.token_urlsafe(12)
        User.objects.create_user(
            email=email,
            password=password,
            name=options["name"],
            is_staff=options["staff"],
        )
        self.stdout.write(self.style.SUCCESS(f"Created {email}"))
        if not options["password"]:
            self.stdout.write(f"Temporary password: {password}")
