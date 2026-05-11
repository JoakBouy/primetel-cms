"""
Primetel CMS — Appointments Views
Queue management, check-in/out, scheduling.
"""
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.accounts.decorators import requires_role
from apps.patients.models import Patient

from .models import Appointment, AppointmentType

User = get_user_model()


def _queue_status_counts():
    """Single aggregate query returning today's count per status.

    Used by both the main queue page and the HTMX poll endpoint so the
    numbers stay consistent and we don't run 4 separate COUNT queries.
    Excludes CANCELLED appointments.
    """
    from django.db.models import Count, Q
    today = timezone.localdate()
    row = Appointment.objects.filter(scheduled_start__date=today).exclude(status="CANCELLED").aggregate(
        waiting=Count("id", filter=Q(status="SCHEDULED")),
        checked_in=Count("id", filter=Q(status="CHECKED_IN")),
        in_consult=Count("id", filter=Q(status="IN_CONSULT")),
        completed=Count("id", filter=Q(status="COMPLETED")),
    )
    return row


@login_required
def queue_view(request):
    """Today's queue — the receptionist's main screen.

    Each row is annotated with `has_paid_prepay` so the UI can flip the
    nurse's button between 'Send to billing' (no paid invoice yet) and
    'Take Vitals' (consultation has been paid, ready for triage).
    """
    today = timezone.localdate()
    # All users (receptionist, nurse, clinician, admin) see the full queue for today.
    # Access control is enforced at the view level via @login_required.
    queue = list(
        Appointment.objects.filter(scheduled_start__date=today)
        .exclude(status="CANCELLED")
        .select_related("patient", "clinician", "appointment_type")
        .order_by("scheduled_start")
    )

    # One query for paid prepay invoices belonging to the patients in this
    # queue. Batched so we don't N+1.
    if queue:
        from apps.billing.models import Invoice
        patient_ids = {apt.patient_id for apt in queue}
        paid_prepay_patient_ids = set(
            Invoice.objects.filter(
                patient_id__in=patient_ids,
                encounter__isnull=True,
                status="PAID",
            ).values_list("patient_id", flat=True)
        )
        for apt in queue:
            apt.has_paid_prepay = apt.patient_id in paid_prepay_patient_ids

    counts = _queue_status_counts()

    return render(request, "appointments/queue.html", {
        "page_title": _("Today's Queue"),
        "queue": queue,
        "counts": counts,
        "today": today,
    })


@login_required
def queue_summary(request):
    """HTMX endpoint: returns just the 4-tile summary row.

    Polled every 15s by the queue page, and re-triggered after any
    check-in / send-to-billing / check-out action so the user sees
    instantaneous feedback.
    """
    return render(request, "appointments/_queue_summary.html", {
        "counts": _queue_status_counts(),
    })


@requires_role("RECEPTIONIST", "NURSE", "ADMIN")
def appointment_new(request):
    """Book a new appointment or walk-in."""
    patient_pk = request.GET.get("patient")
    patient = get_object_or_404(Patient, pk=patient_pk) if patient_pk else None

    clinicians = User.objects.filter(
        is_active=True, role__code__in=["CLINICIAN", "COUNSELLOR"]
    ).order_by("full_name", "username")
    appointment_types = AppointmentType.objects.filter(is_active=True)

    if request.method == "POST":
        patient_id = request.POST.get("patient_id")
        if not patient_id:
            messages.error(request, _("Please select a patient."))
            return redirect(request.path + (f"?patient={patient_pk}" if patient_pk else ""))
        patient = get_object_or_404(Patient, pk=patient_id)
        is_walk_in = request.POST.get("is_walk_in") == "on"
        now = timezone.now()

        # Parse scheduled start. The HTML datetime-local input gives "YYYY-MM-DDTHH:MM".
        scheduled_start = now
        raw_start = (request.POST.get("scheduled_start") or "").strip()
        if not is_walk_in and raw_start:
            try:
                naive = datetime.strptime(raw_start, "%Y-%m-%dT%H:%M")
                scheduled_start = timezone.make_aware(naive, timezone.get_current_timezone())
            except ValueError:
                messages.error(request, _("Invalid scheduled time."))
                return redirect("appointments:new")

        apt_type = None
        type_pk = request.POST.get("appointment_type") or ""
        if type_pk:
            apt_type = AppointmentType.objects.filter(pk=type_pk, is_active=True).first()
        duration = apt_type.duration_minutes if apt_type else 15

        clinician = None
        clinician_pk = request.POST.get("clinician") or ""
        if clinician_pk:
            clinician = User.objects.filter(pk=clinician_pk, is_active=True).first()

        Appointment.objects.create(
            patient=patient,
            clinician=clinician,
            appointment_type=apt_type,
            scheduled_start=scheduled_start,
            scheduled_end=scheduled_start + timedelta(minutes=duration),
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
        "clinicians": clinicians,
        "appointment_types": appointment_types,
    })


@requires_role("RECEPTIONIST", "NURSE", "ADMIN", "CLINICIAN", "COUNSELLOR")
def patient_picker(request):
    """HTMX patient search — returns clickable rows that fill #patient_id."""
    query = (request.GET.get("q") or "").strip()
    patients = Patient.objects.search(query)[:20] if query else []
    return render(request, "appointments/partials/patient_picker.html", {
        "patients": patients,
        "query": query,
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


@require_POST
@requires_role("RECEPTIONIST", "NURSE", "CLINICIAN", "ADMIN")
def send_to_billing(request, pk):
    """Generate a 'pay-first' consultation invoice for this appointment's patient.

    Used by the pay-before-consult workflow: nurse/reception clicks
    'Send to billing' on a checked-in patient → a draft consultation
    invoice is created (no encounter yet). Reception records the payment;
    the clinician then opens the encounter on the patient chart.
    """
    apt = get_object_or_404(Appointment.objects.select_related("patient"), pk=pk)
    try:
        from apps.billing.consultation import prepay_consultation
        invoice = prepay_consultation(apt.patient, request.user, encounter_type="GENERAL")
        if invoice is None:
            messages.error(request, _("Could not create the consultation invoice."))
            return redirect("appointments:queue")
        # Notify the front desk so they know to collect.
        try:
            from apps.core.notifications import notify_role
            notify_role(
                ["RECEPTIONIST", "FINANCE"],
                exclude_actor=request.user,
                kind="PAYMENT_REQUIRED",
                level="WARNING",
                title=_("Consultation to collect"),
                body=f"{apt.patient.full_name} · {invoice.invoice_number} · {invoice.total_tzs} TZS",
                url=f"/billing/invoices/{invoice.pk}/",
                entity_type="Invoice",
                entity_id=invoice.pk,
            )
        except Exception:
            pass
        messages.success(
            request,
            _("Patient sent to billing. Invoice %(n)s for %(amt)s TZS.")
            % {"n": invoice.invoice_number, "amt": invoice.total_tzs},
        )
    except Exception as exc:
        messages.error(request, _("Could not send to billing: %(e)s") % {"e": exc})
    return redirect("appointments:queue")
