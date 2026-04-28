from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class EncountersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.encounters"
    verbose_name = _("Encounters")
