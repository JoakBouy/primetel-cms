"""
Primetel CMS — Appointments Views
Queue management, check-in/out, scheduling.
"""
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.accounts.decorators import requires_role
from apps.patients.models import Patient

from .models import Appointment


@login_required
def queue_view(request):
    """Today's queue — the receptionist's main screen."""
    today = timezone.localdate()
    queue = Appointment.objects.filter(
        scheduled_start__date=today
    ).exclude(status="CANCELLED").select_related("patient", "clinician", "appointment_type").order_by("scheduled_start")

    # Group by status
    checked_in = queue.filter(status="CHECKED_IN")
    in_consult = queue.filter(status="IN_CONSULT")
    waiting = queue.filter(status="SCHEDULED")
    completed = queue.filter(status="COMPLETED")

    return render(request, "appointments/queue.html", {
        "page_title": _("Today's Queue"),
        "queue": queue,
        "checked_in": checked_in,
        "in_consult": in_consult,
        "waiting": waiting,
        "completed": completed,
        "today": today,
    })


@requires_role("RECEPTIONIST", "NURSE", "ADMIN")
def appointment_new(request):
    """Book a new appointment or walk-in."""
    patient_pk = request.GET.get("patient")
    patient = get_object_or_404(Patient, pk=patient_pk) if patient_pk else None

    if request.method == "POST":
        patient = get_object_or_404(Patient, pk=request.POST.get("patient_id"))
        is_walk_in = request.POST.get("is_walk_in") == "on"
        now = timezone.now()
        apt = Appointment.objects.create(
            patient=patient,
            scheduled_start=now if is_walk_in else request.POST.get("scheduled_start", now),
            scheduled_end=now + timedelta(minutes=15),
            is_walk_in=is_walk_in,
            status="CHECKED_IN" if is_walk_in else "SCHEDULED",
            check_in_at=now if is_walk_in else None,
            notes=request.POST.get("notes", ""),
            created_by=request.user,
        )
        messages.success(request, _("Appointment created."))
        return redirect("appointments:queue")

    return render(request, "appointments/new.html", {
        "page_title": _("New Appointment"),
        "patient": patient,
    })


@require_POST
@requires_role("RECEPTIONIST", "NURSE", "ADMIN")
def check_in(request, pk):
    """Check in a scheduled patient."""
    apt = get_object_or_404(Appointment, pk=pk)
    apt.check_in(user=request.user)
    messages.success(request, _("Patient checked in."))
    return redirect("appointments:queue")


@require_POST
@requires_role("RECEPTIONIST", "NURSE", "CLINICIAN", "ADMIN")
def check_out(request, pk):
    """Complete / check out a patient."""
    apt = get_object_or_404(Appointment, pk=pk)
    apt.check_out(user=request.user)
    messages.success(request, _("Patient checked out."))
    return redirect("appointments:queue")
