import json

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

# Phase 3 few-shot labels per pack: a starting example for the researcher to
# review, not a gold standard.
LABELLING_EXAMPLES = {
    "oldsmar": {
        "scenario_id": "sc_01",
        "labels": [
            {
                "hcv_id": "HCV-17",
                "evidence": (
                    "The credential is one shared password, set at commissioning in 2013 "
                    "and the one in use."
                ),
                "rationale": "A single shared password left unchanged for years is a weak "
                "password practice the intruder's session relies on.",
            },
            {
                "hcv_id": "HCV-18",
                "evidence": (
                    "The TeamViewer Session accepts connections from any address on the internet."
                ),
                "rationale": "Remote access is not restricted to known parties, so an "
                "unmanaged external party reaches a privileged control path.",
            },
        ],
    }
}


class Command(BaseCommand):
    help = "Load an expert seed pack from resources/seeds/ into the corpus."

    def add_arguments(self, parser):
        parser.add_argument("--pack", default="oldsmar", help="Seed pack directory name.")
        parser.add_argument(
            "--if-missing",
            action="store_true",
            help="Do nothing if the pack is already seeded, so edits made in the app survive.",
        )

    def handle(self, *args, **options):
        pack = options["pack"]
        if options["if_missing"] and SystemDescription.objects.filter(slug=pack).exists():
            self.stdout.write(f"Seed pack {pack} is already loaded; skipped")
            return

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
            self._seed_labelling_fewshot(pack, system)

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

    def _seed_labelling_fewshot(self, pack, system):
        """A phase 3 example: the pack's first scenario and the labels an expert
        gives it. Only seeded where the pack defines them (LABELLING_EXAMPLES)."""
        example = LABELLING_EXAMPLES.get(pack)
        scenario = system.scenarios.filter(
            scenario_id=example["scenario_id"] if example else None, generation_run=None
        ).first()
        if example is None or scenario is None:
            return
        card = "\n".join(
            f"- {field.replace('_', ' ').capitalize()}: {getattr(scenario, field)}"
            for field in Scenario.CARD_FIELDS
        )
        FewShotExample.objects.update_or_create(
            phase=3,
            name=f"{pack}-{scenario.scenario_id}",
            defaults={
                "input_text": f"{card}\n\n{scenario.narrative}".strip(),
                "output_text": json.dumps({"labels": example["labels"]}, indent=2),
                "input_file": None,
                "output_file": None,
                "order": 1,
                "is_active": True,
            },
        )
