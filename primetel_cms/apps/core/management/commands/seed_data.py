"""
Seed essential clinical and operational data: ICD-10 (top outpatient subset
relevant to a Tanzanian primary-care clinic), drugs + initial stock, lab tests
with reference ranges, service items for billing, role taxonomy.

Idempotent: run repeatedly without creating duplicates.
"""
from datetime import timedelta
from decimal import Decimal
import random

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role
from apps.billing.models import ServiceItem
from apps.encounters.models import ICD10Code
from apps.lab.models import LabTest
from apps.pharmacy.models import Drug, StockItem


# Expanded outpatient ICD-10 subset (~120 codes) covering the diagnoses most
# commonly seen in Tanzanian primary-care settings: communicable disease,
# maternal/child health, NCDs, MSK, mental health, screening encounters.
ICD10_CODES = [
    # Infectious & parasitic
    ("A00.9", "Cholera, unspecified"),
    ("A01.0", "Typhoid fever"),
    ("A06.0", "Acute amoebic dysentery"),
    ("A09", "Infectious gastroenteritis and colitis, unspecified"),
    ("A15.0", "Tuberculosis of lung, confirmed"),
    ("A41.9", "Sepsis, unspecified organism"),
    ("B05.9", "Measles without complication"),
    ("B20", "HIV disease"),
    ("B24", "Unspecified human immunodeficiency virus disease"),
    ("B50.9", "Plasmodium falciparum malaria, unspecified"),
    ("B51.9", "Plasmodium vivax malaria without complication"),
    ("B54", "Unspecified malaria"),
    ("B74.0", "Filariasis due to Wuchereria bancrofti"),
    ("B76.9", "Hookworm disease, unspecified"),
    ("B82.0", "Intestinal helminthiasis, unspecified"),
    # Respiratory
    ("J00", "Acute nasopharyngitis [common cold]"),
    ("J02.9", "Acute pharyngitis, unspecified"),
    ("J03.90", "Acute tonsillitis, unspecified"),
    ("J04.0", "Acute laryngitis"),
    ("J06.9", "Acute upper respiratory infection, unspecified"),
    ("J11.1", "Influenza with other respiratory manifestations"),
    ("J18.9", "Pneumonia, unspecified organism"),
    ("J20.9", "Acute bronchitis, unspecified"),
    ("J40", "Bronchitis, not specified as acute or chronic"),
    ("J44.9", "Chronic obstructive pulmonary disease, unspecified"),
    ("J45.909", "Unspecified asthma, uncomplicated"),
    # Digestive
    ("K02.9", "Dental caries, unspecified"),
    ("K05.6", "Periodontal disease, unspecified"),
    ("K21.9", "Gastro-oesophageal reflux disease"),
    ("K29.70", "Gastritis, unspecified, without bleeding"),
    ("K30", "Functional dyspepsia"),
    ("K52.9", "Noninfective gastroenteritis and colitis, unspecified"),
    ("K59.0", "Constipation"),
    ("K80.20", "Calculus of gallbladder without obstruction"),
    # GU / OB-GYN
    ("N30.00", "Acute cystitis without haematuria"),
    ("N39.0", "Urinary tract infection, site not specified"),
    ("N72", "Inflammatory disease of cervix uteri"),
    ("N76.0", "Acute vaginitis"),
    ("N91.2", "Amenorrhoea, unspecified"),
    ("N94.6", "Dysmenorrhoea, unspecified"),
    ("O00.9", "Ectopic pregnancy, unspecified"),
    ("O20.0", "Threatened abortion"),
    ("O23.40", "Unspecified infection of urinary tract in pregnancy"),
    ("O36.5990", "Maternal care for poor fetal growth"),
    ("Z34.90", "Encounter for supervision of normal pregnancy, unspecified"),
    ("Z39.2", "Encounter for routine postpartum follow-up"),
    # Skin
    ("L02.91", "Cutaneous abscess, unspecified"),
    ("L03.90", "Cellulitis, unspecified"),
    ("L20.9", "Atopic dermatitis, unspecified"),
    ("L30.9", "Dermatitis, unspecified"),
    ("L50.9", "Urticaria, unspecified"),
    ("L70.9", "Acne, unspecified"),
    ("B35.9", "Dermatophytosis, unspecified"),
    ("B86", "Scabies"),
    # MSK
    ("M25.50", "Pain in unspecified joint"),
    ("M54.2", "Cervicalgia"),
    ("M54.5", "Low back pain"),
    ("M79.1", "Myalgia"),
    ("S93.401A", "Sprain of unspecified ligament of right ankle, initial"),
    # NCDs
    ("E11.9", "Type 2 diabetes mellitus without complications"),
    ("E10.9", "Type 1 diabetes mellitus without complications"),
    ("E03.9", "Hypothyroidism, unspecified"),
    ("E66.9", "Obesity, unspecified"),
    ("E78.5", "Hyperlipidaemia, unspecified"),
    ("I10", "Essential (primary) hypertension"),
    ("I11.9", "Hypertensive heart disease without heart failure"),
    ("I50.9", "Heart failure, unspecified"),
    ("I64", "Stroke, not specified as haemorrhage or infarction"),
    ("D50.9", "Iron deficiency anaemia, unspecified"),
    ("D64.9", "Anaemia, unspecified"),
    # Neurology
    ("G40.909", "Epilepsy, unspecified, not intractable, without status"),
    ("G43.909", "Migraine, unspecified, not intractable, without aura"),
    ("R51", "Headache"),
    # Mental health
    ("F32.9", "Major depressive disorder, single episode, unspecified"),
    ("F33.9", "Major depressive disorder, recurrent, unspecified"),
    ("F41.1", "Generalised anxiety disorder"),
    ("F41.9", "Anxiety disorder, unspecified"),
    ("F43.10", "Post-traumatic stress disorder, unspecified"),
    ("F10.20", "Alcohol dependence, uncomplicated"),
    ("F19.20", "Other psychoactive substance dependence, uncomplicated"),
    # Pediatrics
    ("P07.30", "Preterm newborn, unspecified weeks of gestation"),
    ("R50.9", "Fever, unspecified"),
    ("R56.00", "Simple febrile convulsions"),
    ("E43", "Severe protein-calorie malnutrition, unspecified"),
    ("E44.0", "Moderate protein-calorie malnutrition"),
    ("E46", "Unspecified protein-calorie malnutrition"),
    # Eye / ENT
    ("H10.9", "Unspecified conjunctivitis"),
    ("H66.90", "Otitis media, unspecified"),
    ("H81.10", "Benign paroxysmal vertigo, unspecified"),
    # Injury / trauma
    ("S00.83XA", "Abrasion of other part of head, initial"),
    ("S61.401A", "Unspecified open wound of unspecified hand, initial"),
    ("T14.90", "Injury, unspecified"),
    ("T63.001A", "Toxic effect of unspecified snake venom, accidental, initial"),
    # General symptoms
    ("R05", "Cough"),
    ("R07.9", "Chest pain, unspecified"),
    ("R10.9", "Unspecified abdominal pain"),
    ("R11.2", "Nausea with vomiting, unspecified"),
    ("R19.7", "Diarrhoea, unspecified"),
    ("R21", "Rash and other nonspecific skin eruption"),
    ("R42", "Dizziness and giddiness"),
    ("R53.83", "Other fatigue"),
    ("R63.0", "Anorexia"),
    # Encounters / screening
    ("Z00.00", "Encounter for general adult medical examination without abnormal findings"),
    ("Z00.121", "Encounter for routine child health examination with abnormal findings"),
    ("Z00.129", "Encounter for routine child health examination without abnormal findings"),
    ("Z11.4", "Encounter for screening for HIV"),
    ("Z11.59", "Encounter for screening for other viral diseases"),
    ("Z23", "Encounter for immunisation"),
    ("Z30.09", "Encounter for other general counselling on contraception"),
    ("Z71.3", "Dietary counselling and surveillance"),
    ("Z71.41", "Alcohol abuse counselling and surveillance"),
    ("Z71.6", "Tobacco abuse counselling"),
    ("Z76.89", "Persons encountering health services in other specified circumstances"),
]


# Lab tests with reference ranges where applicable.
# (code, name, specimen, ref_min, ref_max, unit, price_tzs)
LAB_TESTS = [
    ("mRDT", "Malaria Rapid Diagnostic Test", "BLOOD", None, None, "", "2000"),
    ("FBP", "Full Blood Picture", "BLOOD", None, None, "", "10000"),
    ("HGB", "Haemoglobin", "BLOOD", "12.0", "16.0", "g/dL", "3000"),
    ("WBC", "White Blood Cell Count", "BLOOD", "4.0", "11.0", "x10^9/L", "3000"),
    ("PLT", "Platelet Count", "BLOOD", "150", "450", "x10^9/L", "3000"),
    ("RBS", "Random Blood Sugar", "BLOOD", "70", "140", "mg/dL", "3000"),
    ("FBS", "Fasting Blood Sugar", "BLOOD", "70", "100", "mg/dL", "3500"),
    ("HBA1C", "Glycated Haemoglobin", "BLOOD", "4.0", "5.6", "%", "15000"),
    ("CREAT", "Serum Creatinine", "BLOOD", "0.6", "1.2", "mg/dL", "5000"),
    ("UrineRE", "Urine Routine Examination", "URINE", None, None, "", "3000"),
    ("StoolRE", "Stool Routine Examination", "STOOL", None, None, "", "3000"),
    ("Widal", "Widal Test (Typhoid)", "BLOOD", None, None, "", "5000"),
    ("HIV", "HIV Rapid Test", "BLOOD", None, None, "", "0"),
    ("UPT", "Urine Pregnancy Test", "URINE", None, None, "", "2000"),
    ("HEPB", "Hepatitis B Surface Antigen", "BLOOD", None, None, "", "5000"),
    ("VDRL", "Syphilis Screening (VDRL)", "BLOOD", None, None, "", "3000"),
]


SERVICE_ITEMS = [
    ("CONS-NEW", "New patient consultation", "CONSULT", "5000"),
    ("CONS-FU", "Follow-up consultation", "CONSULT", "3000"),
    ("CONS-MH", "Mental health consultation", "CONSULT", "10000"),
    ("CONS-ANC", "First antenatal consultation", "CONSULT", "5000"),
    ("PROC-DRESS", "Wound Dressing", "PROCEDURE", "3000"),
    ("PROC-SUTURE", "Suturing (small)", "PROCEDURE", "10000"),
    ("PROC-INJ", "Injection administration", "PROCEDURE", "1000"),
    ("PROC-NEB", "Nebulisation", "PROCEDURE", "5000"),
    ("OTHER-CARD", "Patient card / file", "OTHER", "1000"),
]


class Command(BaseCommand):
    help = "Seed clinical and operational lookup tables (idempotent)."

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write("Starting database seeding...")
        self.seed_roles()
        self.seed_icd10()
        self.seed_drugs()
        self.seed_lab_tests()
        self.seed_service_items()
        self.stdout.write(self.style.SUCCESS("Seeding complete."))

    def seed_roles(self):
        for code, label in Role.ROLE_CHOICES:
            Role.objects.get_or_create(code=code, defaults={"display_name": str(label)})
        self.stdout.write(self.style.SUCCESS(f"Roles: {Role.objects.count()}"))

    def seed_icd10(self):
        before = ICD10Code.objects.count()
        for code, description in ICD10_CODES:
            ICD10Code.objects.get_or_create(code=code, defaults={"description": description})
        self.stdout.write(self.style.SUCCESS(
            f"ICD-10: {ICD10Code.objects.count() - before} added (total {ICD10Code.objects.count()})"
        ))

    def seed_drugs(self):
        drugs_data = [
            {"generic_name": "Paracetamol", "strength": "500mg", "form": "TABLET", "unit_price_tzs": Decimal("100"), "low_stock_threshold": 500},
            {"generic_name": "Amoxicillin", "strength": "500mg", "form": "CAPSULE", "unit_price_tzs": Decimal("200"), "low_stock_threshold": 200},
            {"generic_name": "Artemether/Lumefantrine", "strength": "20/120mg", "form": "TABLET", "unit_price_tzs": Decimal("1500"), "low_stock_threshold": 100},
            {"generic_name": "Ibuprofen", "strength": "400mg", "form": "TABLET", "unit_price_tzs": Decimal("150"), "low_stock_threshold": 300},
            {"generic_name": "Ciprofloxacin", "strength": "500mg", "form": "TABLET", "unit_price_tzs": Decimal("300"), "low_stock_threshold": 150},
            {"generic_name": "Metronidazole", "strength": "400mg", "form": "TABLET", "unit_price_tzs": Decimal("100"), "low_stock_threshold": 200},
            {"generic_name": "Oral Rehydration Salts (ORS)", "strength": "20.5g", "form": "OTHER", "unit_price_tzs": Decimal("500"), "low_stock_threshold": 100},
            {"generic_name": "Salbutamol", "strength": "100mcg/dose", "form": "OTHER", "unit_price_tzs": Decimal("5000"), "low_stock_threshold": 50},
            {"generic_name": "Omeprazole", "strength": "20mg", "form": "CAPSULE", "unit_price_tzs": Decimal("200"), "low_stock_threshold": 150},
            {"generic_name": "Ceftriaxone", "strength": "1g", "form": "INJECTION", "unit_price_tzs": Decimal("2000"), "low_stock_threshold": 100},
            {"generic_name": "Amlodipine", "strength": "5mg", "form": "TABLET", "unit_price_tzs": Decimal("150"), "low_stock_threshold": 200},
            {"generic_name": "Metformin", "strength": "500mg", "form": "TABLET", "unit_price_tzs": Decimal("100"), "low_stock_threshold": 200},
        ]

        today = timezone.now().date()
        for d in drugs_data:
            drug, created = Drug.objects.get_or_create(
                generic_name=d["generic_name"],
                strength=d["strength"],
                defaults={
                    "form": d["form"],
                    "unit_price_tzs": d["unit_price_tzs"],
                    "low_stock_threshold": d["low_stock_threshold"],
                },
            )
            if created or not StockItem.objects.filter(drug=drug).exists():
                qty = random.randint(d["low_stock_threshold"], d["low_stock_threshold"] * 3)
                expiry = today + timedelta(days=random.randint(180, 730))
                StockItem.objects.create(
                    drug=drug,
                    batch_number=f"BATCH-{random.randint(1000, 9999)}",
                    quantity_on_hand=qty,
                    expiry_date=expiry,
                )
        self.stdout.write(self.style.SUCCESS(f"Drugs: {Drug.objects.count()}"))

    def seed_lab_tests(self):
        for code, name, spec, lo, hi, unit, price in LAB_TESTS:
            LabTest.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "specimen_type": spec,
                    "reference_range_min": Decimal(lo) if lo else None,
                    "reference_range_max": Decimal(hi) if hi else None,
                    "reference_unit": unit,
                    "price_tzs": Decimal(price),
                    "is_active": True,
                },
            )
        self.stdout.write(self.style.SUCCESS(f"Lab tests: {LabTest.objects.count()}"))

    def seed_service_items(self):
        for code, name, category, price in SERVICE_ITEMS:
            ServiceItem.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "category": category,
                    "unit_price_tzs": Decimal(price),
                    "is_active": True,
                },
            )
        self.stdout.write(self.style.SUCCESS(f"Service items: {ServiceItem.objects.count()}"))
