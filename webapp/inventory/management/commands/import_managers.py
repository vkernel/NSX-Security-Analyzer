from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from inventory.models import Environment
from inventory.services import engine


class Command(BaseCommand):
    help = "Import new manager definitions from the existing CLI managers JSON configuration."

    def add_arguments(self, parser):
        parser.add_argument("file", type=Path)

    def handle(self, *args, **options):
        try:
            targets = engine().load_manager_targets(options["file"], "admin")
            with transaction.atomic():
                for target in targets:
                    if Environment.objects.filter(slug=target["id"]).exists():
                        raise CommandError("Environment {} exists; edit it in the workspace.".format(target["id"]))
                    environment = Environment(slug=target["id"], name=target["name"], manager=target["manager"],
                        username=target["username"], username_env=target["username_env"] or "",
                        password_env=target["password_env"])
                    environment.full_clean()
                    environment.save()
            self.stdout.write(self.style.SUCCESS("Imported {} environments.".format(len(targets))))
        except CommandError:
            raise
        except Exception as exc:
            raise CommandError("Manager configuration could not be imported: {}".format(exc)) from exc
