"""
Primetel CMS — Encounters Views
SOAP notes, vitals, diagnoses, prescriptions, lab orders, finalise/amend.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.db.models import Prefetch

from apps.accounts.decorators import requires_role
from apps.core.models import AuditLog
from apps.patients.models import Patient
from apps.lab.models import LabOrder

from .models import Encounter, Vitals, Diagnosis


def _audit(request, action, obj, **metadata):
    """Record an entry in AuditLog for amendment-style changes."""
    try:
        AuditLog.objects.create(
            actor=request.user,
            action=action,
            entity_type=obj.__class__.__name__,
            entity_id=getattr(obj, "pk", None),
            metadata=metadata,
            ip_address=(request.META.get("HTTP_X_FORWARDED_FOR") or request.META.get("REMOTE_ADDR") or "").split(",")[0].strip() or None,
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
        )
    except Exception:
        # Audit failures must never block the clinical action.
        pass


def _get_encounter_for_user(user, pk):
    """
    Resolve an encounter respecting mental-health access rules.
    Unauthorised users get a 404 (not 403), so the existence of MH
    encounters is not leaked through ID enumeration.
    """
    qs = Encounter.objects.for_user(user)
    try:
        return qs.get(pk=pk)
    except Encounter.DoesNotExist:
        raise Http404("Encounter not found")


def _ensure_editable(encounter):
    """Raise if the encounter is locked. Use before any write."""
    if encounter.status == "FINALISED":
        from django.core.exceptions import ValidationError
        raise ValidationError(_("Encounter is finalised. Use Amend to reopen it."))


def _consultation_paid(encounter) -> bool:
    """True if the consultation invoice for this encounter is fully paid.

    The clinical "hard gate": no SOAP/vitals/diagnosis/Rx/lab work is allowed
    until the front desk has recorded the consultation payment. Encounters
    without an invoice (legacy / manually created) are treated as paid so we
    don't lock anyone out by accident.
    """
    from apps.billing.models import Invoice
    inv = Invoice.objects.filter(encounter=encounter).first()
    if inv is None:
        return True
    return inv.status in ("PAID", "WAIVED") or inv.balance_tzs <= 0


def _ensure_paid(request, encounter):
    """Raise (return False + flash) if the consultation hasn't been paid yet.

    Returns True if clinical work can proceed.
    """
    if _consultation_paid(encounter):
        return True
    messages.error(
        request,
        _("Awaiting payment confirmation. The receptionist must record the consultation payment before clinical work can begin.")
    )
    return False


def _notify_billing_for_new_invoice(invoice, actor):
    """Tell the front desk a new consultation invoice needs to be paid."""
    try:
        from apps.core.notifications import notify_role
        notify_role(
            ["RECEPTIONIST", "FINANCE"],
            exclude_actor=actor,
            kind="PAYMENT_REQUIRED",
            level="WARNING",
            title=_("New consultation to collect"),
            body=f"{invoice.patient.full_name} · {invoice.invoice_number} · {invoice.total_tzs} TZS",
            url=f"/billing/invoices/{invoice.pk}/",
            entity_type="Invoice",
            entity_id=invoice.pk,
        )
    except Exception:
        pass


def _claim_nurse_started_encounter(encounter, user):
    """Assign clinical ownership when a clinician continues nurse intake."""
    if (
        getattr(user, "role_code", None) == "CLINICIAN"
        and getattr(getattr(encounter, "clinician", None), "role_code", None) == "NURSE"
        and encounter.status == "DRAFT"
    ):
        encounter.clinician = user
        encounter.updated_by = user
        encounter.save(update_fields=["clinician", "updated_by", "updated_at"])


@requires_role("NURSE", "CLINICIAN", "COUNSELLOR", "ADMIN")
def follow_up_search(request):
    """Returning-patient landing page.

    Clinician types a name / phone / patient number; HTMX hits
    `follow_up_picker` for live results. Clicking a result lands the user on
    that patient's chart with a 'Start follow-up consult' banner. The chart
    is the safer landing than an immediate new encounter because the
    clinician sees prior history before committing to a new visit.
    """
    return render(request, "encounters/follow_up.html", {
        "page_title": _("Start follow-up consult"),
    })


@requires_role("NURSE", "CLINICIAN", "COUNSELLOR", "ADMIN")
def follow_up_picker(request):
    """HTMX search endpoint for the follow-up landing page.

    Returns clickable result rows. Only surfaces patients with at least one
    finalised encounter (i.e. "returning") — patients with no history go
    through the normal Register / Anza Consult flow.
    """
    query = (request.GET.get("q") or "").strip()
    patients = []
    if query:
        # Reuse the patient model's fuzzy search, then filter to those who
        # have a finalised encounter on record. We cap to 20 for the dropdown.
        candidates = Patient.objects.search(query)[:50]
        # Prefetch the existence test in one query: ids of patients with any
        # FINALISED encounter visible to this user.
        with_history_ids = set(
            Encounter.objects.for_user(request.user)
            .filter(patient_id__in=[p.pk for p in candidates], status="FINALISED")
            .values_list("patient_id", flat=True).distinct()
        )
        patients = [p for p in candidates if p.pk in with_history_ids][:20]
    return render(request, "encounters/_follow_up_picker.html", {
        "patients": patients,
        "query": query,
    })


@requires_role("CLINICIAN", "ADMIN")
def encounter_new(request):
    """Start a new encounter for a patient — or resume an open draft.

    Nurses cannot initiate encounters: the patient must first be billed at
    reception. After payment clears, the clinician opens the encounter and
    sees the patient.
    """
    patient_pk = request.GET.get("patient")
    patient = get_object_or_404(Patient, pk=patient_pk)
    # If the patient already has an open (DRAFT) general/follow-up encounter, resume it
    # instead of silently creating a duplicate. This is what makes "Anza Consult" idempotent.
    existing = (
        Encounter.objects.for_user(request.user)
        .filter(patient=patient, status="DRAFT")
        .exclude(encounter_type="MENTAL_HEALTH")
        .order_by("-started_at")
        .first()
    )
    if existing and request.method != "POST":
        return redirect("encounters:detail", pk=existing.pk)
    if request.method == "POST":
        if existing:
            return redirect("encounters:detail", pk=existing.pk)
        encounter = Encounter.objects.create(
            patient=patient,
            clinician=request.user,
            encounter_type=request.POST.get("encounter_type", "GENERAL"),
            chief_complaint=request.POST.get("chief_complaint", ""),
            created_by=request.user,
        )
        # Auto-charge the consultation as a draft invoice (5000 new / 3000 follow-up).
        try:
            from apps.billing.consultation import auto_charge
            inv = auto_charge(encounter, request.user)
            if inv is not None:
                _notify_billing_for_new_invoice(inv, request.user)
        except Exception:
            # Never block clinical work on a billing hiccup.
            pass
        return redirect("encounters:detail", pk=encounter.pk)
    return render(request, "encounters/new.html", {
        "page_title": _("New Encounter"),
        "patient": patient,
    })


@require_POST
@requires_role("RECEPTIONIST", "NURSE", "CLINICIAN", "ADMIN")
def refer_to_psychologist(request, patient_pk):
    """Refer a patient to mental health.

    Creates a prepay MH consultation invoice (5000 TZS) for this patient.
    Notifies counsellors that a new MH referral is pending payment, and
    notifies reception that a new bill needs to be collected. The MH
    encounter itself is created later, by the counsellor, after payment.

    Same endpoint serves both the reception 'direct booking' path and the
    clinician 'mid-encounter referral' path — they only differ in who
    clicks the button.
    """
    patient = get_object_or_404(Patient, pk=patient_pk)
    try:
        from apps.billing.consultation import prepay_consultation
        invoice = prepay_consultation(patient, request.user, encounter_type="MENTAL_HEALTH")
    except Exception as exc:
        messages.error(request, _("Could not refer to mental health: %(e)s") % {"e": exc})
        return redirect(request.META.get("HTTP_REFERER") or "/")
    if invoice is None:
        messages.error(request, _("Could not create the MH consultation invoice."))
        return redirect(request.META.get("HTTP_REFERER") or "/")
    # Notify reception so they can collect; notify counsellors so they know
    # there's an MH referral on its way after payment.
    try:
        from apps.core.notifications import notify_role
        notify_role(
            ["RECEPTIONIST", "FINANCE"],
            exclude_actor=request.user,
            kind="PAYMENT_REQUIRED",
            level="WARNING",
            title=_("MH consultation to collect"),
            body=f"{patient.full_name} · {invoice.invoice_number} · {invoice.total_tzs} TZS",
            url=f"/billing/invoices/{invoice.pk}/",
            entity_type="Invoice",
            entity_id=invoice.pk,
        )
        notify_role(
            ["COUNSELLOR"],
            exclude_actor=request.user,
            kind="INFO",
            level="INFO",
            title=_("New MH referral"),
            body=f"{patient.full_name} · awaiting payment",
            url=f"/patients/{patient.pk}/",
            entity_type="Patient",
            entity_id=patient.pk,
        )
    except Exception:
        pass
    messages.success(
        request,
        _("Referred %(p)s to mental health. Invoice %(n)s for %(amt)s TZS.")
        % {"p": patient.full_name, "n": invoice.invoice_number, "amt": invoice.total_tzs},
    )
    # Receptionist goes to the invoice; everyone else returns where they were.
    if request.user.has_role("RECEPTIONIST"):
        return redirect("billing:invoice_detail", pk=invoice.pk)
    return redirect(request.META.get("HTTP_REFERER") or f"/patients/{patient.pk}/")


@requires_role("COUNSELLOR", "ADMIN")
def encounter_new_mh(request):
    """Start a new mental health encounter — or resume an open draft."""
    patient_pk = request.GET.get("patient")
    patient = get_object_or_404(Patient, pk=patient_pk)
    existing = (
        Encounter.mental_health.for_user(request.user)
        .filter(patient=patient, status="DRAFT")
        .order_by("-started_at")
        .first()
    )
    if existing and request.method != "POST":
        return redirect("encounters:detail", pk=existing.pk)
    if request.method == "POST":
        if existing:
            return redirect("encounters:detail", pk=existing.pk)
        encounter = Encounter.objects.create(
            patient=patient, clinician=request.user,
            encounter_type="MENTAL_HEALTH",
            chief_complaint=request.POST.get("chief_complaint", ""),
            created_by=request.user,
        )
        try:
            from apps.billing.consultation import auto_charge
            inv = auto_charge(encounter, request.user)
            if inv is not None:
                _notify_billing_for_new_invoice(inv, request.user)
        except Exception:
            pass
        return redirect("encounters:detail", pk=encounter.pk)
    return render(request, "encounters/new.html", {
        "page_title": _("New Mental Health Encounter"),
        "patient": patient,
        "is_mental_health": True,
    })


@login_required
def encounter_detail(request, pk):
    """Encounter detail — the main clinician screen (SOAP form)."""
    try:
        encounter = (
            Encounter.objects.for_user(request.user)
            .select_related("patient", "clinician")
            .prefetch_related(
                "vitals",
                "diagnoses",
                "prescriptions__drug",
                Prefetch(
                    "lab_orders",
                    queryset=LabOrder.objects.select_related("test", "result").order_by("-ordered_at"),
                ),
            )
            .get(pk=pk)
        )
    except Encounter.DoesNotExist:
        raise Http404("Encounter not found")
    _claim_nurse_started_encounter(encounter, request.user)
    mh_assessment = getattr(encounter, "mental_health_assessment", None)
    # Invoice for this encounter (created automatically on encounter open).
    from apps.billing.models import Invoice
    invoice = Invoice.objects.filter(encounter=encounter).first()
    consultation_paid = _consultation_paid(encounter)
    return render(request, "encounters/detail.html", {
        "page_title": f"{_('Encounter')} — {encounter.patient.full_name}",
        "encounter": encounter,
        "patient": encounter.patient,
        "vitals": encounter.vitals.all(),
        "diagnoses": encounter.diagnoses.all(),
        "prescriptions": encounter.prescriptions.all() if hasattr(encounter, 'prescriptions') else [],
        "lab_orders": encounter.lab_orders.all() if hasattr(encounter, 'lab_orders') else [],
        "invoice": invoice,
        "consultation_paid": consultation_paid,
        "mh_assessment": mh_assessment,
        "phq9_q9_red_flag": (
            mh_assessment is not None
            and mh_assessment.instrument == "PHQ9"
            and isinstance(mh_assessment.responses, list)
            and len(mh_assessment.responses) >= 9
            and mh_assessment.responses[8] >= 1
        ),
    })


@require_POST
@requires_role("CLINICIAN", "COUNSELLOR", "ADMIN")
def encounter_save_draft(request, pk):
    """Autosave SOAP note — HTMX endpoint."""
    encounter = _get_encounter_for_user(request.user, pk)
    if encounter.status == "FINALISED":
        return JsonResponse({"error": "Encounter is finalised"}, status=400)
    if not _consultation_paid(encounter):
        return JsonResponse({"error": "Awaiting payment confirmation."}, status=402)
    for field in ["chief_complaint", "history_of_presenting_illness", "subjective", "objective", "assessment", "plan"]:
        val = request.POST.get(field)
        if val is not None:
            setattr(encounter, field, val)
    encounter.updated_by = request.user
    encounter.save()
    if request.headers.get("HX-Request"):
        return render(request, "encounters/partials/save_indicator.html", {"saved_at": timezone.now()})
    return JsonResponse({"status": "saved"})


@require_POST
@requires_role("NURSE", "CLINICIAN", "ADMIN")
def encounter_add_vitals(request, pk):
    """Add vitals to an encounter."""
    encounter = _get_encounter_for_user(request.user, pk)
    try:
        _ensure_editable(encounter)
    except Exception as e:
        messages.error(request, str(e))
        return redirect("encounters:detail", pk=pk)
    if not _ensure_paid(request, encounter):
        return redirect("encounters:detail", pk=pk)
    Vitals.objects.create(
        encounter=encounter, recorded_by=request.user, created_by=request.user,
        blood_pressure_systolic=request.POST.get("bp_systolic") or None,
        blood_pressure_diastolic=request.POST.get("bp_diastolic") or None,
        temperature=request.POST.get("temperature") or None,
        pulse=request.POST.get("pulse") or None,
        respiratory_rate=request.POST.get("respiratory_rate") or None,
        spo2=request.POST.get("spo2") or None,
        weight_kg=request.POST.get("weight_kg") or None,
        height_cm=request.POST.get("height_cm") or None,
    )
    if request.headers.get("HX-Request"):
        return render(request, "encounters/partials/vitals_list.html", {"vitals": encounter.vitals.all()})
    return redirect("encounters:detail", pk=pk)


@require_POST
@requires_role("CLINICIAN", "ADMIN")
def encounter_add_diagnosis(request, pk):
    """Add a diagnosis to an encounter."""
    encounter = _get_encounter_for_user(request.user, pk)
    try:
        _ensure_editable(encounter)
    except Exception as e:
        messages.error(request, str(e))
        return redirect("encounters:detail", pk=pk)
    if not _ensure_paid(request, encounter):
        return redirect("encounters:detail", pk=pk)
    Diagnosis.objects.create(
        encounter=encounter, created_by=request.user,
        icd10_code=request.POST.get("icd10_code", ""),
        description=request.POST.get("description", ""),
        is_primary=request.POST.get("is_primary") == "on",
    )
    if request.headers.get("HX-Request"):
        return render(request, "encounters/partials/diagnosis_list.html", {"diagnoses": encounter.diagnoses.all()})
    return redirect("encounters:detail", pk=pk)


@require_POST
@requires_role("CLINICIAN", "ADMIN")
def encounter_finalise(request, pk):
    """Finalise an encounter — lock it."""
    encounter = _get_encounter_for_user(request.user, pk)
    try:
        encounter.finalise(user=request.user)
        messages.success(request, _("Encounter finalised successfully."))
    except Exception as e:
        messages.error(request, str(e))
    return redirect("encounters:detail", pk=pk)


@require_POST
@requires_role("CLINICIAN", "ADMIN")
def encounter_amend(request, pk):
    """Amend a finalised encounter — requires a documented reason."""
    encounter = _get_encounter_for_user(request.user, pk)
    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        messages.error(request, _("A reason is required to amend a finalised encounter."))
        return redirect("encounters:detail", pk=pk)
    try:
        encounter.amend(user=request.user)
        _audit(request, "UPDATE", encounter, change="amend_finalised_encounter", reason=reason)
        messages.success(request, _("Encounter reopened for amendment. Reason recorded."))
    except Exception as e:
        messages.error(request, str(e))
    return redirect("encounters:detail", pk=pk)


@require_POST
@requires_role("NURSE", "CLINICIAN", "ADMIN")
def vitals_edit(request, pk):
    """Edit a vitals row — only on a non-finalised encounter."""
    vitals = get_object_or_404(Vitals.objects.select_related("encounter"), pk=pk)
    encounter = _get_encounter_for_user(request.user, vitals.encounter_id)
    try:
        _ensure_editable(encounter)
    except Exception as e:
        messages.error(request, str(e))
        return redirect("encounters:detail", pk=encounter.pk)
    field_map = {
        "blood_pressure_systolic": "bp_systolic",
        "blood_pressure_diastolic": "bp_diastolic",
        "temperature": "temperature",
        "pulse": "pulse",
        "respiratory_rate": "respiratory_rate",
        "spo2": "spo2",
        "weight_kg": "weight_kg",
        "height_cm": "height_cm",
    }
    for model_field, post_key in field_map.items():
        raw = (request.POST.get(post_key) or "").strip()
        setattr(vitals, model_field, raw or None)
    vitals.updated_by = request.user
    vitals.save()
    if request.headers.get("HX-Request"):
        return render(request, "encounters/partials/vitals_list.html", {"vitals": encounter.vitals.all(), "encounter": encounter})
    return redirect("encounters:detail", pk=encounter.pk)


@require_POST
@requires_role("NURSE", "CLINICIAN", "ADMIN")
def vitals_delete(request, pk):
    """Delete a vitals row — only on a non-finalised encounter."""
    vitals = get_object_or_404(Vitals.objects.select_related("encounter"), pk=pk)
    encounter = _get_encounter_for_user(request.user, vitals.encounter_id)
    try:
        _ensure_editable(encounter)
    except Exception as e:
        messages.error(request, str(e))
        return redirect("encounters:detail", pk=encounter.pk)
    vitals.delete()
    if request.headers.get("HX-Request"):
        return render(request, "encounters/partials/vitals_list.html", {"vitals": encounter.vitals.all(), "encounter": encounter})
    return redirect("encounters:detail", pk=encounter.pk)


@require_POST
@requires_role("CLINICIAN", "ADMIN")
def diagnosis_edit(request, pk):
    """Edit a diagnosis — only on a non-finalised encounter."""
    dx = get_object_or_404(Diagnosis.objects.select_related("encounter"), pk=pk)
    encounter = _get_encounter_for_user(request.user, dx.encounter_id)
    try:
        _ensure_editable(encounter)
    except Exception as e:
        messages.error(request, str(e))
        return redirect("encounters:detail", pk=encounter.pk)
    dx.icd10_code = (request.POST.get("icd10_code") or "").strip()
    dx.description = (request.POST.get("description") or "").strip()
    dx.is_primary = request.POST.get("is_primary") == "on"
    dx.updated_by = request.user
    dx.save()
    if request.headers.get("HX-Request"):
        return render(request, "encounters/partials/diagnosis_list.html", {"diagnoses": encounter.diagnoses.all(), "encounter": encounter})
    return redirect("encounters:detail", pk=encounter.pk)


@require_POST
@requires_role("CLINICIAN", "ADMIN")
def diagnosis_delete(request, pk):
    """Delete a diagnosis — only on a non-finalised encounter."""
    dx = get_object_or_404(Diagnosis.objects.select_related("encounter"), pk=pk)
    encounter = _get_encounter_for_user(request.user, dx.encounter_id)
    try:
        _ensure_editable(encounter)
    except Exception as e:
        messages.error(request, str(e))
        return redirect("encounters:detail", pk=encounter.pk)
    dx.delete()
    if request.headers.get("HX-Request"):
        return render(request, "encounters/partials/diagnosis_list.html", {"diagnoses": encounter.diagnoses.all(), "encounter": encounter})
    return redirect("encounters:detail", pk=encounter.pk)
