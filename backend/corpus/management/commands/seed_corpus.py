from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from corpus.models import (
    FewShotExample,
    Origin,
    ResourceFile,
    ResourceKind,
    Scenario,
    SystemDescription,
)
from corpus.parsers import parse_scenarios

PACK_TITLES = {"oldsmar": "Oldsmar Water Treatment"}


class Command(BaseCommand):
    help = "Load an expert seed pack from resources/seeds/ into the corpus."

    def add_arguments(self, parser):
        parser.add_argument("--pack", default="oldsmar", help="Seed pack directory name.")

    def handle(self, *args, **options):
        pack = options["pack"]
        directory = settings.RESOURCES_DIR / "seeds" / pack
        if not directory.is_dir():
            raise CommandError(f"No seed pack at {directory}")

        system_md = directory / "system_description.md"
        scenarios_md = directory / "scenarios.md"
        for path in (system_md, scenarios_md):
            if not path.exists():
                raise CommandError(f"No seed pack file: {path}")

        system_text = system_md.read_text(encoding="utf-8")
        scenarios_text = scenarios_md.read_text(encoding="utf-8")

        with transaction.atomic():
            system = self._seed_system(pack, system_text)
            self._seed_scenarios(system, scenarios_text)
            self._seed_resources(pack, system_text, scenarios_text)
            self._seed_fewshot(pack)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {pack}: 1 system description, {system.scenarios.count()} scenarios"
            )
        )

    def _seed_system(self, pack, text):
        system, _ = SystemDescription.objects.update_or_create(
            slug=pack,
            defaults={
                "title": PACK_TITLES.get(pack, pack.title()),
                "body": text,
                "origin": Origin.SEED,
            },
        )
        return system

    def _seed_scenarios(self, system, text):
        for index, parsed in enumerate(parse_scenarios(text), start=1):
            # generation_run=None is part of the lookup, not just a default: a
            # generated Phase 2 scenario can share this system (it is often
            # `experiment.target_system`) and this exact scenario_id, and
            # without it get() would match both rows and raise
            # MultipleObjectsReturned instead of updating only the seed one.
            Scenario.objects.update_or_create(
                system_description=system,
                scenario_id=parsed.pop("scenario_id"),
                generation_run=None,
                defaults={**parsed, "origin": Origin.SEED, "order": index},
            )

    def _seed_resources(self, pack, system_text, scenarios_text):
        vocabulary = settings.RESOURCES_DIR / "ontology/vocabulary_2026-08-26.md"
        entries = [
            (f"{pack}-system-description", ResourceKind.SYSTEM_DESCRIPTION, system_text),
            (f"{pack}-scenarios", ResourceKind.SCENARIOS, scenarios_text),
        ]
        if vocabulary.exists():
            entries.append(
                ("hcv-vocabulary", ResourceKind.VOCABULARY, vocabulary.read_text("utf-8"))
            )
        for name, kind, text in entries:
            ResourceFile.objects.update_or_create(name=name, defaults={"kind": kind, "text": text})

    def _seed_fewshot(self, pack):
        FewShotExample.objects.update_or_create(
            phase=2,
            name=pack,
            defaults={
                "input_file": ResourceFile.objects.get(name=f"{pack}-system-description"),
                "output_file": ResourceFile.objects.get(name=f"{pack}-scenarios"),
                "input_text": "",
                "output_text": "",
                "order": 1,
                "is_active": True,
            },
        )
