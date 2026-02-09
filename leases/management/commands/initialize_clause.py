from django.core.management.base import BaseCommand
from leases.models import Lease, LeaseAgreementClause
from django.db.models import Count

default_clauses = [
    # Clause 1
    "That the rate of rent of the said Premises is hereby agreed at Rs. [MONTHLY_RENT]/- (Rupees [MONTHLY_RENT_IN_WORDS] Only) per month.",

    # Clause 2
    "Tenant will also pay Rs. [SOCIETY_MAINTENANCE]/- society maintenance charges along with the rent to the first party. Tenant will pay a total of Rs. [TOTAL_MONTHLY]/monthly (Rupees [TOTAL_MONTHLY_IN_WORDS] Only).",

    # Clause 3
    "That one month's advance rent amount Rs. [TOTAL_MONTHLY]/- (Rupees [TOTAL_MONTHLY_IN_WORDS] Only) has been paid by the Tenant and received by the Owner. Thereafter, rent will be payable monthly in advance on or before the 5th of each month. In case of late payment, Rs. [LATE_FEE]/- will be charged per day as penalty after the 10th of each month.",

    # Clause 4
    "That a further sum of Rs. [SECURITY_DEPOSIT]/- (Rupees [SECURITY_DEPOSIT_IN_WORDS] Only) will be paid by the tenant to the Owner as Security. If paying in installments, first installment of Rs. [SECURITY_INSTALLMENT_1_AMOUNT]/- is due on [SECURITY_INSTALLMENT_1_DATE], and second installment of Rs. [SECURITY_INSTALLMENT_2_AMOUNT]/- is due on [SECURITY_INSTALLMENT_2_DATE]. The security is refundable at the time of vacation of said premises after deducting breakage, damages, and clearance of all utility bills (Electricity, Sui Gas, Society/Building Maintenance Charges, Telephone, etc.).",

    # Clause 5
    "That the period of tenancy is hereby agreed as 11 months, commencing from [START_DATE] to [END_DATE], with a rent increase of [RENT_INCREASE_PERCENT]% after 11 months. Renewal is possible with mutual consent of both parties. The Tenant shall vacate peacefully after the lease expires.",

    # Clause 6
    "That the Tenant is bound not to vacate the premises within [MIN_OCCUPANCY_PERIOD] months. If they choose to vacate earlier, they must pay Rs. [EARLY_TERMINATION_PENALTY]/- per month as penalty.",

    # Clause 7
    "That [KEYS_ISSUED] keys/keycards will be issued to the Tenant, to be returned upon vacating the premises. If lost, Rs. [KEY_REPLACEMENT_COST]/- per key/keycard will be deducted from the Security Deposit.",

    # Clause 8
    "That the Tenant shall maintain the premises in good condition, including all fittings and fixtures, and replace any broken items with equal quality. No alterations or wall drilling is allowed without written permission. Subletting is strictly prohibited.",

    # Clause 9
    "That in case the Owner sells the property, the Tenant shall have no objection and will cooperate in executing a fresh lease agreement with the new Owner for the remaining term.",

    # Clause 10
    "That the Tenant shall not demand any compensation for decoration or expenses upon vacating the premises. Any legal claims shall be deemed void.",

    # Clause 11
    "That the said premises will be used strictly for residential purposes only.",

    # Clause 12
    "That the Tenant is responsible to complete verification from the concerned Police Station.",

    # Clause 13
    "That the said premises has been handed over in working order, including [INVENTORY_CEILING_FANS] Ceiling Fans, [INVENTORY_LIGHTS] Ceiling Lights, [INVENTORY_EXHAUST_FANS] Exhaust Fans, [INVENTORY_WARDROBE] wardrobes, and [INVENTORY_STOVE] Stove(s). The Tenant shall return all in the same condition upon vacating.",

    # Clause 14
    "That the Tenant shall pay all utility bills timely and submit copies to the Owner upon request. Electricity bill will be paid at Rs. 50/- per unit to the Owner along with the rent.",

    # Clause 15
    "That a 2-month advance written notice is required from either party to vacate the premises. Failure to do so by the Tenant will result in forfeiture of the Security Deposit.",

    # Clause 16
    "That the Tenant will not rent or sublet the premises to any third party.",

    # Clause 17
    "That the Owner may visit the premises with reasonable advance notice.",

    # Clause 18
    "That during the lease period, any complaints from the society against the Tenant shall be the Tenant's responsibility.",

    # Clause 19
    "That the Electricity Meter reading is [ELECTRICITY_METER_READING] as on [METER_READING_DATE]. Gas Meter Reading is [GAS_METER_READING].",

    # Clause 20
    "That the Security Deposit will not be adjusted against rent under any circumstances.",

    # Clause 21
    "That smoking is not allowed in the building. Tenant is not allowed to use the common area for drying clothes.",

    # Clause 22
    "That the Tenant shall not use the terrace, hallway, or any other common areas beyond their own rented portion.",

    # Clause 23
    "That the Tenant is responsible for cleaning and maintaining common and exterior areas (doors, windows, hallways, walls, stairs, and ceilings) at least three times a week.",

    # Clause 24
    "That all legal rights regarding the said premises are reserved with the Owner.",

    # Clause 25
    "That both parties agree to abide by all terms and conditions stated in this agreement.",

    # Clause 26
    "That the Tenant shall not engage in any illegal or immoral activities on the premises."
]


class Command(BaseCommand):
    help = 'Replace all existing lease clauses with new default clauses'

    def handle(self, *args, **options):
        total_leases = Lease.objects.count()
        if total_leases == 0:
            self.stdout.write(self.style.WARNING(
                "No leases found in the database."))
            return

        self.stdout.write(f"Replacing clauses for {total_leases} leases...")

        for lease in Lease.objects.all():
            # Delete existing clauses for this lease
            deleted_count, _ = LeaseAgreementClause.objects.filter(
                lease=lease).delete()
            self.stdout.write(
                f" - Lease #{lease.id}: Deleted {deleted_count} old clause(s)")

            # Add new default clauses
            for clause_number, clause_text in enumerate(default_clauses, start=1):
                LeaseAgreementClause.objects.create(
                    lease=lease,
                    clause_number=clause_number,
                    template_text=clause_text
                )
            self.stdout.write(
                f" - Lease #{lease.id}: Added {len(default_clauses)} new clauses")

        self.stdout.write(self.style.SUCCESS(
            f"Successfully replaced clauses for {total_leases} leases."
        ))
