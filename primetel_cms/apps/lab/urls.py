"""Lab URLs."""
from django.urls import path
from django.views.generic import RedirectView
from . import views

app_name = "lab"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="lab:queue", permanent=False), name="index"),
    path("queue/", views.lab_queue, name="queue"),
    path("orders/<uuid:pk>/", views.lab_order_detail, name="order_detail"),
    path("orders/<uuid:pk>/collect/", views.lab_collect, name="collect"),
    path("orders/<uuid:pk>/result/", views.lab_result_enter, name="result_enter"),
    path("orders/<uuid:pk>/review/", views.lab_review, name="review"),
    path("orders/<uuid:pk>/print/", views.lab_order_print, name="print"),
    path("encounters/<uuid:encounter_pk>/order/", views.lab_order_new, name="order_new"),
    path("catalogue/", views.lab_test_catalogue, name="catalogue"),
    path("catalogue/new/", views.lab_test_create, name="test_create"),
    path("catalogue/<uuid:pk>/edit/", views.lab_test_edit, name="test_edit"),
    path("catalogue/<uuid:pk>/toggle/", views.lab_test_toggle_active, name="test_toggle"),
]
