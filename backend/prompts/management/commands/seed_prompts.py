from django.core.management.base import BaseCommand

from prompts.defaults import DEFAULT_TEMPLATES
from prompts.models import PromptTemplate


class Command(BaseCommand):
    help = "Install the default prompt templates (variant A, variant B, labelling) if missing."

    def handle(self, *args, **options):
        for spec in DEFAULT_TEMPLATES:
            if PromptTemplate.objects.filter(phase=spec["phase"], name=spec["name"]).exists():
                self.stdout.write(f"Kept existing {spec['name']}")
                continue
            PromptTemplate.objects.create_version(
                phase=spec["phase"],
                name=spec["name"],
                system_prompt=spec["system_prompt"],
                user_prompt=spec["user_prompt"],
                output_schema={},
                is_active=True,
            )
            self.stdout.write(self.style.SUCCESS(f"Created {spec['name']} v1"))
