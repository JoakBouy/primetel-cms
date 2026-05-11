"""
Template filters for simplifying audit log display for clinical users.
Converts technical entity types and actions into user-friendly language.
"""
from django import template
from django.utils.translation import gettext_lazy as _

register = template.Library()


# Doctor-friendly entity type mappings
ENTITY_DISPLAY_NAMES = {
    "Encounter": _("Patient Consultation"),
    "Patient": _("Patient File"),
    "Prescription": _("Medication"),
    "LabOrder": _("Lab Test"),
    "LabResult": _("Lab Result"),
    "Diagnosis": _("Diagnosis"),
    "Invoice": _("Invoice"),
    "Payment": _("Payment"),
    "Dispense": _("Medicine Dispense"),
    "Drug": _("Medication"),
    "Vitals": _("Vital Signs"),
    "Appointment": _("Appointment"),
    "Request": _("Record"),
}

# Doctor-friendly action descriptions
ACTION_DISPLAY_NAMES = {
    "CREATE": _("Added"),
    "UPDATE": _("Modified"),
    "DELETE": _("Removed"),
    "READ": _("Viewed"),
    "EXPORT": _("Exported"),
    "LOGIN": _("Logged in"),
    "LOGIN_FAILED": _("Login failed"),
}


@register.filter
def human_entity_type(entity_type):
    """Convert technical entity type to doctor-friendly name."""
    return ENTITY_DISPLAY_NAMES.get(entity_type, entity_type)


@register.filter
def human_action(action):
    """Convert technical action to doctor-friendly description."""
    return ACTION_DISPLAY_NAMES.get(action, action.title())


@register.filter
def activity_summary(audit_log_entry):
    """
    Create a natural-language summary of an audit log entry.
    Returns a string like "Viewed Patient File" or "Added Medication".
    """
    action = human_action(audit_log_entry.action)
    entity = human_entity_type(audit_log_entry.entity_type)
    
    # Add context from metadata if available
    if audit_log_entry.metadata.get("change"):
        return f"{action} {entity} — {audit_log_entry.metadata['change']}"
    return f"{action} {entity}"
