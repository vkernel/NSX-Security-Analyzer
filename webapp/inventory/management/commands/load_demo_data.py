"""Load fully synthetic demonstration data into an empty workspace database."""
from django.core.management.base import BaseCommand, CommandError

from inventory.demo_data.loader import (USERS, Loader, check_credentials_path, demo_environments, foreign_data,
                                        reset, write_credentials)


class Command(BaseCommand):
    help = ('Fill the database with synthetic NSX environments, collections, findings, naming and segmentation '
            'data for demonstrations. Uses the real collector against an in-process synthetic manager; no '
            'network connection is made. Account passwords are written only to --credentials-file.')

    def add_arguments(self, parser):
        parser.add_argument('--credentials-file', required=True,
                            help='New file (outside the source tree) that receives the demonstration account '
                                 'usernames, roles and generated passwords, with mode 600.')
        parser.add_argument('--reset', action='store_true',
                            help='Remove previously loaded demonstration data (environments on '
                                 '*.example.invalid with a demo- ID, demo accounts and Demo naming policies) first.')
        parser.add_argument('--force', action='store_true',
                            help='Load even though other environments or snapshots exist. They are not changed.')

    def handle(self, *args, **options):
        try:
            path = check_credentials_path(options['credentials_file'])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if foreign_data() and not options['force']:
            raise CommandError('This database already contains environments or snapshots that are not demonstration '
                               'data. Use an empty database, or pass --force to add the demonstration data alongside.')
        from django.contrib.auth import get_user_model
        existing_users = get_user_model().objects.filter(username__in=[row[0] for row in USERS]).exists()
        if demo_environments() or existing_users:
            if not options['reset']:
                raise CommandError('Demonstration data is already loaded. Pass --reset to replace it.')
            removed = reset()
            self.stdout.write('Removed previous demonstration data: ' + (', '.join(removed) or 'accounts only'))
        loader = Loader(stdout=self.stdout)
        try:
            results, workflow = loader.run(foreign=foreign_data())
        finally:
            if loader.accounts:
                write_credentials(path, loader.accounts,
                                  'The built-in "admin" account is unchanged by this command.')
        snapshots = sum(len(items) for _, items in results.values())
        self.stdout.write(self.style.SUCCESS(
            'Loaded %d synthetic environments with %d snapshots in %.0fs. Credentials for %d accounts were written '
            'to %s.' % (len(results), snapshots, loader.elapsed, len(loader.accounts), path)))
        self.stdout.write('Browsing indexes, comparisons and segmentation data are prepared; no worker is required '
                          'to browse. Environments are paused and point at unreachable *.example.invalid managers.')
