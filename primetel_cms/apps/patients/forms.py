"""
Primetel CMS — Patient Forms
Grouped registration and edit forms per §6.2.
"""
from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Patient

INPUT_CLASS = (
    "w-full px-4 py-3 bg-white border border-slate-200 rounded-xl text-sm "
    "text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 "
    "focus:ring-primary-500/30 focus:border-primary-500 transition-all duration-200"
)

SELECT_CLASS = (
    "w-full px-4 py-3 bg-white border border-slate-200 rounded-xl text-sm "
    "text-slate-800 focus:outline-none focus:ring-2 focus:ring-primary-500/30 "
    "focus:border-primary-500 transition-all duration-200"
)

TEXTAREA_CLASS = (
    "w-full px-4 py-3 bg-white border border-slate-200 rounded-xl text-sm "
    "text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 "
    "focus:ring-primary-500/30 focus:border-primary-500 transition-all duration-200 resize-none"
)


class PatientRegistrationForm(forms.ModelForm):
    """
    Patient registration form — grouped into 5 sections per §6.2.
    """

    patient_number = forms.CharField(
        required=False,
        label=_("Patient Number"),
        widget=forms.TextInput(attrs={
            "class": INPUT_CLASS,
            "placeholder": _("Existing number or leave blank to auto-generate"),
            "id": "id_patient_number",
        }),
    )

    # Toggle: age-only vs DOB
    age_only = forms.BooleanField(
        required=False,
        label=_("Date of birth unknown — use estimated age only"),
        widget=forms.CheckboxInput(attrs={"class": "rounded text-primary-500 focus:ring-primary-500", "x-model": "ageOnly"}),
    )

    class Meta:
        model = Patient
        fields = [
            "patient_number", "full_name", "sex", "date_of_birth", "estimated_age", "national_id", "photo",
            "phone", "location", "village", "ward", "district", "region",
            "next_of_kin_name", "next_of_kin_phone", "next_of_kin_relationship",
            "language_preference", "is_pregnant", "chronic_conditions", "allergies",
            "notes",
        ]
        widgets = {
            "full_name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("Full name of patient"), "id": "id_full_name"}),
            "sex": forms.Select(attrs={"class": SELECT_CLASS, "id": "id_sex"}),
            "date_of_birth": forms.DateInput(
                attrs={"class": INPUT_CLASS, "type": "date", "id": "id_date_of_birth", "x-show": "!ageOnly"},
                format="%Y-%m-%d",
            ),
            "estimated_age": forms.NumberInput(
                attrs={"class": INPUT_CLASS, "placeholder": "e.g. 35", "min": 0, "max": 120,
                       "id": "id_estimated_age", "x-show": "ageOnly"}
            ),
            "national_id": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("National ID (optional)"), "id": "id_national_id"}),
            "phone": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "+255712345678", "id": "id_phone"}),
            "location": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("Location, landmark, or directions"), "id": "id_location"}),
            "village": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("Village name"), "id": "id_village"}),
            "ward": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("Ward name"), "id": "id_ward"}),
            "district": forms.TextInput(attrs={"class": INPUT_CLASS, "id": "id_district"}),
            "region": forms.TextInput(attrs={"class": INPUT_CLASS, "id": "id_region"}),
            "next_of_kin_name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("Full name"), "id": "id_kin_name"}),
            "next_of_kin_phone": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "+255...", "id": "id_kin_phone"}),
            "next_of_kin_relationship": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": _("e.g. Spouse, Parent"), "id": "id_kin_rel"}),
            "language_preference": forms.RadioSelect(attrs={"class": "text-primary-500 focus:ring-primary-500"}),
            "is_pregnant": forms.CheckboxInput(attrs={"class": "rounded text-primary-500 focus:ring-primary-500", "id": "id_is_pregnant"}),
            "chronic_conditions": forms.Textarea(attrs={"class": TEXTAREA_CLASS, "rows": 2, "placeholder": _("e.g. Diabetes, Hypertension"), "id": "id_chronic"}),
            "allergies": forms.Textarea(attrs={"class": TEXTAREA_CLASS, "rows": 2, "placeholder": _("e.g. Penicillin, Sulfa drugs"), "id": "id_allergies"}),
            "notes": forms.Textarea(attrs={"class": TEXTAREA_CLASS, "rows": 3, "placeholder": _("Additional clinical notes"), "id": "id_notes"}),
        }

    def clean(self):
        cleaned = super().clean()
        dob = cleaned.get("date_of_birth")
        estimated_age = cleaned.get("estimated_age")
        age_only = cleaned.get("age_only")

        if age_only and not estimated_age:
            self.add_error("estimated_age", _("Please enter an estimated age."))
        if not age_only and not dob and not estimated_age:
            self.add_error("date_of_birth", _("Please enter date of birth or estimated age."))

        return cleaned

    def clean_patient_number(self):
        patient_number = (self.cleaned_data.get("patient_number") or "").strip()
        if not patient_number:
            return ""
        existing = Patient.objects.filter(patient_number__iexact=patient_number)
        if self.instance and self.instance.pk:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise forms.ValidationError(_("A patient with this number already exists. Search for the patient instead of registering again."))
        return patient_number


class PatientSearchForm(forms.Form):
    """Quick search form for patient list."""
    q = forms.CharField(
        required=False,
        label=_("Search"),
        widget=forms.TextInput(attrs={
            "class": INPUT_CLASS,
            "placeholder": _("Search by name, phone, patient number, or location…"),
            "id": "search-input",
            "hx-get": "/patients/",
            "hx-trigger": "keyup changed delay:300ms",
            "hx-target": "#patient-results",
            "hx-indicator": "#search-spinner",
        }),
    )
