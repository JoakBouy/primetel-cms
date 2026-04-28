from django.core.management.base import BaseCommand
from django.db import transaction
from decimal import Decimal
import random

from apps.encounters.models import ICD10Code
from apps.pharmacy.models import Drug, StockItem
from apps.lab.models import LabTest
from django.utils import timezone
from datetime import timedelta

class Command(BaseCommand):
    help = 'Seeds the database with essential clinical and operational data'

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write("Starting database seeding...")

        self.seed_icd10()
        self.seed_drugs()
        self.seed_lab_tests()

        self.stdout.write(self.style.SUCCESS("Database seeding completed successfully."))

    def seed_icd10(self):
        self.stdout.write("Seeding ICD-10 codes...")
        codes = [
            ("A09", "Infectious gastroenteritis and colitis, unspecified"),
            ("B50.9", "Plasmodium falciparum malaria, unspecified"),
            ("B54", "Unspecified malaria"),
            ("J00", "Acute nasopharyngitis [common cold]"),
            ("J02.9", "Acute pharyngitis, unspecified"),
            ("J03.90", "Acute tonsillitis, unspecified"),
            ("J06.9", "Acute upper respiratory infection, unspecified"),
            ("J18.9", "Pneumonia, unspecified organism"),
            ("J20.9", "Acute bronchitis, unspecified"),
            ("J45.909", "Unspecified asthma, uncomplicated"),
            ("K29.70", "Gastritis, unspecified, without bleeding"),
            ("M54.5", "Low back pain"),
            ("N39.0", "Urinary tract infection, site not specified"),
            ("I10", "Essential (primary) hypertension"),
            ("E11.9", "Type 2 diabetes mellitus without complications"),
            ("Z00.00", "Encounter for general adult medical examination without abnormal findings"),
            ("Z34.90", "Encounter for supervision of normal pregnancy, unspecified, unspecified trimester"),
        ]

        created_count = 0
        for code, description in codes:
            _, created = ICD10Code.objects.get_or_create(
                code=code,
                defaults={"description": description}
            )
            if created:
                created_count += 1

        self.stdout.write(self.style.SUCCESS(f"Seeded {created_count} new ICD-10 codes."))

    def seed_drugs(self):
        self.stdout.write("Seeding Drugs and Batches...")
        drugs_data = [
            {"generic_name": "Paracetamol", "strength": "500mg", "form": "TABLET", "unit_price_tzs": Decimal("100"), "low_stock_threshold": 500},
            {"generic_name": "Amoxicillin", "strength": "500mg", "form": "CAPSULE", "unit_price_tzs": Decimal("200"), "low_stock_threshold": 200},
            {"generic_name": "Artemether/Lumefantrine", "strength": "20/120mg", "form": "TABLET", "unit_price_tzs": Decimal("1500"), "low_stock_threshold": 100},
            {"generic_name": "Ibuprofen", "strength": "400mg", "form": "TABLET", "unit_price_tzs": Decimal("150"), "low_stock_threshold": 300},
            {"generic_name": "Ciprofloxacin", "strength": "500mg", "form": "TABLET", "unit_price_tzs": Decimal("300"), "low_stock_threshold": 150},
            {"generic_name": "Metronidazole", "strength": "400mg", "form": "TABLET", "unit_price_tzs": Decimal("100"), "low_stock_threshold": 200},
            {"generic_name": "Oral Rehydration Salts (ORS)", "strength": "20.5g", "form": "SACHET", "unit_price_tzs": Decimal("500"), "low_stock_threshold": 100},
            {"generic_name": "Salbutamol", "strength": "100mcg/dose", "form": "INHALER", "unit_price_tzs": Decimal("5000"), "low_stock_threshold": 50},
            {"generic_name": "Omeprazole", "strength": "20mg", "form": "CAPSULE", "unit_price_tzs": Decimal("200"), "low_stock_threshold": 150},
            {"generic_name": "Ceftriaxone", "strength": "1g", "form": "INJECTION", "unit_price_tzs": Decimal("2000"), "low_stock_threshold": 100},
        ]

        today = timezone.now().date()
        for d in drugs_data:
            drug, created = Drug.objects.get_or_create(
                generic_name=d["generic_name"],
                strength=d["strength"],
                defaults={
                    "form": d["form"],
                    "unit_price_tzs": d["unit_price_tzs"],
                    "low_stock_threshold": d["low_stock_threshold"]
                }
            )
            
            # Create some initial stock batches if new or empty
            if created or not StockItem.objects.filter(drug=drug).exists():
                qty = random.randint(d["low_stock_threshold"], d["low_stock_threshold"] * 3)
                # Random expiry 6 to 24 months from now
                expiry = today + timedelta(days=random.randint(180, 730))
                
                StockItem.objects.create(
                    drug=drug,
                    batch_number=f"BATCH-{random.randint(1000, 9999)}",
                    quantity_on_hand=qty,
                    expiry_date=expiry
                )
                
        self.stdout.write(self.style.SUCCESS(f"Seeded drugs and initial stock."))

    def seed_lab_tests(self):
        self.stdout.write("Seeding Lab Tests...")
        tests_data = [
            {"code": "mRDT", "name": "Malaria Rapid Diagnostic Test", "specimen_type": "BLOOD", "price_tzs": Decimal("2000")},
            {"code": "FBP", "name": "Full Blood Picture", "specimen_type": "BLOOD", "price_tzs": Decimal("10000")},
            {"code": "UrineRE", "name": "Urine Routine Examination", "specimen_type": "URINE", "price_tzs": Decimal("3000")},
            {"code": "StoolRE", "name": "Stool Routine Examination", "specimen_type": "STOOL", "price_tzs": Decimal("3000")},
            {"code": "Widal", "name": "Widal Test (Typhoid)", "specimen_type": "BLOOD", "price_tzs": Decimal("5000")},
            {"code": "HIV", "name": "HIV Rapid Test", "specimen_type": "BLOOD", "price_tzs": Decimal("0")},
            {"code": "RBS", "name": "Random Blood Sugar", "specimen_type": "BLOOD", "price_tzs": Decimal("3000")},
            {"code": "UPT", "name": "Urine Pregnancy Test", "specimen_type": "URINE", "price_tzs": Decimal("2000")},
        ]

        created_count = 0
        for t in tests_data:
            _, created = LabTest.objects.get_or_create(
                code=t["code"],
                defaults={
                    "name": t["name"],
                    "specimen_type": t["specimen_type"],
                    "price_tzs": t["price_tzs"],
                    "is_active": True
                }
            )
            if created:
                created_count += 1
                
        self.stdout.write(self.style.SUCCESS(f"Seeded {created_count} new Lab Tests."))
