import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.licensing import codec, keys


class Command(BaseCommand):
    help = "Check a license file the way the database does: signature against the known public keys."

    def add_arguments(self, parser):
        parser.add_argument("file", help="Path to a cassilion.lic file")

    def handle(self, *args, **options):
        try:
            payload = codec.verify(Path(options["file"]).read_text(encoding="utf-8"), keys.public_keys())
        except (OSError, codec.LicenseError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS("Signature valid."))
        self.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False))
