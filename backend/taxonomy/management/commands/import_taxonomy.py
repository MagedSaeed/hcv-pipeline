import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from taxonomy.models import HCV, Taxonomy

DEFAULT_PATH = "taxonomy/hcv21_v1.1.json"


class Command(BaseCommand):
    help = "Import an HCV taxonomy JSON file into the database."

    def add_arguments(self, parser):
        parser.add_argument(
            "path",
            nargs="?",
            default=None,
            help=f"Path to the taxonomy JSON. Defaults to resources/{DEFAULT_PATH}.",
        )
        parser.add_argument(
            "--activate",
            action="store_true",
            help="Mark this version active and deactivate all others.",
        )

    def handle(self, *args, **options):
        path = Path(options["path"]) if options["path"] else settings.RESOURCES_DIR / DEFAULT_PATH
        if not path.exists():
            raise CommandError(f"Taxonomy file not found: {path}")

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise CommandError(f"{path} is not valid JSON: {error}") from error

        rows = payload.get("rows") or []
        if not rows:
            raise CommandError(f"{path} contains no rows.")

        with transaction.atomic():
            taxonomy, _ = Taxonomy.objects.update_or_create(
                vocabulary_id=payload["vocabulary_id"],
                version=str(payload["version"]),
                defaults={
                    "notes": payload.get("What are these row", ""),
                    "source_json": payload,
                },
            )
            # Update in place rather than replace: a taxonomy is immutable once a
            # run references it, so an HCV must keep its primary key across a
            # re-import. Deleting and re-inserting would silently sever every
            # Scenario.intended_hcvs link and, once Label.hcv exists, cascade
            # away every label ever produced.
            seen = []
            for index, row in enumerate(rows, start=1):
                if not isinstance(row, dict) or not row.get("hcv_id") or not row.get("name"):
                    raise CommandError(
                        f"{path}: row {index} is missing a required 'hcv_id' or 'name' field."
                    )
                # bulk_create used to catch this through the unique constraint;
                # updating in place would quietly let the later row win.
                if row["hcv_id"] in seen:
                    raise CommandError(f"{path}: row {index} repeats hcv_id {row['hcv_id']!r}.")
                HCV.objects.update_or_create(
                    taxonomy=taxonomy,
                    hcv_id=row["hcv_id"],
                    defaults={
                        "name": row["name"],
                        "definition": row.get("definition", ""),
                        "condition_classes": row.get("condition_classes", []),
                        "order": index,
                    },
                )
                seen.append(row["hcv_id"])
            # Rows that left the file leave the database too, so it still mirrors
            # the file exactly.
            taxonomy.hcvs.exclude(hcv_id__in=seen).delete()

            if options["activate"]:
                Taxonomy.objects.exclude(pk=taxonomy.pk).update(is_active=False)
                taxonomy.is_active = True
                taxonomy.save(update_fields=["is_active"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {len(rows)} rows into {taxonomy}"
                + (" (active)" if options["activate"] else "")
            )
        )
