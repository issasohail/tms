from django.core.management.base import BaseCommand
from django.urls import get_resolver


class Command(BaseCommand):
    help = 'Display all URLs'

    def handle(self, *args, **options):
        resolver = get_resolver()
        for url_pattern in resolver.url_patterns:
            self.stdout.write(
                f"{pattern.pattern} -> {pattern.callback.__module__}.{pattern.callback.__name__}")
