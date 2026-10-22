from django.apps import AppConfig


class CopilotPmsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "copilot_pms"
    verbose_name = "PMS Copilot"

    def ready(self):
        # Registra las herramientas (cada módulo se registra al importarse).
        from copilot_pms import tools  # noqa: F401
