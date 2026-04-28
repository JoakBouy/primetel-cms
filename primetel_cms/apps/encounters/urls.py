"""Encounters URLs."""
from django.urls import path
from . import views

app_name = "encounters"

urlpatterns = [
    path("new/", views.encounter_new, name="new"),
    path("new-mh/", views.encounter_new_mh, name="new_mh"),
    path("<uuid:pk>/", views.encounter_detail, name="detail"),
    path("<uuid:pk>/save-draft/", views.encounter_save_draft, name="save_draft"),
    path("<uuid:pk>/vitals/", views.encounter_add_vitals, name="add_vitals"),
    path("<uuid:pk>/diagnosis/", views.encounter_add_diagnosis, name="add_diagnosis"),
    path("<uuid:pk>/finalise/", views.encounter_finalise, name="finalise"),
    path("<uuid:pk>/amend/", views.encounter_amend, name="amend"),
]
