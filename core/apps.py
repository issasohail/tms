from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        # Identity display filters are project-wide builtins so PDF, admin,
        # public and app templates all use the same canonical formatter.
        from django.template import engines
        from core.templatetags.identity_tags import register

        builtins = engines["django"].engine.template_builtins
        if register not in builtins:
            builtins.append(register)
