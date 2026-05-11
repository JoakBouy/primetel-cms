"""Encounters URLs."""
from django.urls import path
from django.views.generic import RedirectView
from . import views

app_name = "encounters"

urlpatterns = [
    path("", RedirectView.as_view(url="/dashboard/", permanent=False), name="index"),
    path("new/", views.encounter_new, name="new"),
    path("new-mh/", views.encounter_new_mh, name="new_mh"),
    path("follow-up/", views.follow_up_search, name="follow_up"),
    path("follow-up/picker/", views.follow_up_picker, name="follow_up_picker"),
    path("refer-mh/<uuid:patient_pk>/", views.refer_to_psychologist, name="refer_mh"),
    path("<uuid:pk>/", views.encounter_detail, name="detail"),
    path("<uuid:pk>/save-draft/", views.encounter_save_draft, name="save_draft"),
    path("<uuid:pk>/vitals/", views.encounter_add_vitals, name="add_vitals"),
    path("<uuid:pk>/diagnosis/", views.encounter_add_diagnosis, name="add_diagnosis"),
    path("<uuid:pk>/finalise/", views.encounter_finalise, name="finalise"),
    path("<uuid:pk>/amend/", views.encounter_amend, name="amend"),
    path("vitals/<uuid:pk>/edit/", views.vitals_edit, name="vitals_edit"),
    path("vitals/<uuid:pk>/delete/", views.vitals_delete, name="vitals_delete"),
    path("diagnosis/<uuid:pk>/edit/", views.diagnosis_edit, name="diagnosis_edit"),
    path("diagnosis/<uuid:pk>/delete/", views.diagnosis_delete, name="diagnosis_delete"),
    path("<uuid:pk>/toggle-no-prescription/", views.encounter_toggle_no_prescription, name="toggle_no_prescription"),
]
