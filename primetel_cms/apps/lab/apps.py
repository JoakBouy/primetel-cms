from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class LabConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.lab"
    verbose_name = _("Laboratory")
