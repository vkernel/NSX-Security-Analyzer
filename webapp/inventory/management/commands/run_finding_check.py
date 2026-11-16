from django.core.management.base import BaseCommand
from inventory.approval_live import run

class Command(BaseCommand):
    help = 'Run one read-only finding evidence check.'
    def add_arguments(self, parser): parser.add_argument('check_id', type=int)
    def handle(self, *args, **options): run(options['check_id'])
