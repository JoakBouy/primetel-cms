"""
Wipe the drug catalogue and all stock.

Usage:
    python manage.py clear_drugs              # dry-run, prints what would be deleted
    python manage.py clear_drugs --confirm    # actually deletes

WARNING: This is destructive. Existing Prescription rows reference Drug via
PROTECT, so this command will refuse to run if any Prescription exists. To
proceed in that case, you must first cancel/clean up Prescriptions yourself.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.pharmacy.models import Drug, Prescription, StockItem, StockMovement


class Command(BaseCommand):
    help = "Delete all drugs and their stock from the pharmacy catalogue."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Actually perform the deletion. Without this flag, runs as a dry-run.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Also delete Prescription rows (cancels them) instead of refusing. "
                 "Use only on test/staging data.",
        )

    def handle(self, *args, **options):
        confirm = options["confirm"]
        force = options["force"]

        drug_count = Drug.objects.count()
        stock_count = StockItem.objects.count()
        movement_count = StockMovement.objects.count()
        rx_count = Prescription.objects.count()

        self.stdout.write(self.style.NOTICE("Current pharmacy state:"))
        self.stdout.write(f"  Drugs:            {drug_count}")
        self.stdout.write(f"  Stock items:      {stock_count}")
        self.stdout.write(f"  Stock movements:  {movement_count}")
        self.stdout.write(f"  Prescriptions:    {rx_count}")
        self.stdout.write("")

        if rx_count > 0 and not force:
            raise CommandError(
                f"Refusing to delete: {rx_count} Prescription row(s) reference these drugs. "
                "Re-run with --force to cancel them, or clean up prescriptions first."
            )

        if not confirm:
            self.stdout.write(self.style.WARNING("DRY RUN. No changes made. Re-run with --confirm to delete."))
            return

        with transaction.atomic():
            # Order matters: movements → prescriptions (if forced) → stock items → drugs.
            sm_deleted = StockMovement.objects.all().delete()
            rx_deleted = (0, {})
            if force and rx_count > 0:
                rx_deleted = Prescription.objects.all().delete()
            si_deleted = StockItem.objects.all().delete()
            drug_deleted = Drug.objects.all().delete()

        self.stdout.write(self.style.SUCCESS("Cleared:"))
        self.stdout.write(f"  StockMovements:   {sm_deleted[0]}")
        self.stdout.write(f"  Prescriptions:    {rx_deleted[0]}")
        self.stdout.write(f"  StockItems:       {si_deleted[0]}")
        self.stdout.write(f"  Drugs:            {drug_deleted[0]}")
