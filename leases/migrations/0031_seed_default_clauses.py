from django.db import migrations


HARDCODED_DEFAULT_CLAUSES = [
    "That the rate of rent of the said Premises is hereby agreed at Rs. [MONTHLY_RENT]/- ( [MONTHLY_RENT_IN_WORDS]) Rupees Only per month.",
    "Tenant will also pay Rs. [SOCIETY_MAINTENANCE]/- society maintenance charges along with the rent to the first party. Tenant will pay a total of Rs. [TOTAL_MONTHLY]/monthly ([TOTAL_MONTHLY_IN_WORDS]) Rupees Only).",
    "That one month's advance rent amount Rs. [TOTAL_MONTHLY]/- ([TOTAL_MONTHLY_IN_WORDS]) Rupees Only has been paid by the Tenant and received by the Owner. Thereafter, rent will be payable monthly in advance on or before the [DUE_DATE]th of each month. In case of late payment, Rs. [LATE_FEE]/- will be charged per day as penalty after the [DUE_DATE]th of each month.",
    "That a further sum of Rs. [SECURITY_DEPOSIT]/- ([SECURITY_DEPOSIT_IN_WORDS]) Rupees Only will be paid by the tenant to the Owner as Security before taking possession. If paying in installments, first installment of Rs. [SECURITY_INSTALLMENT_1_AMOUNT]/- is due on [SECURITY_INSTALLMENT_1_DATE], and second installment of Rs. [SECURITY_INSTALLMENT_2_AMOUNT]/- is due on [SECURITY_INSTALLMENT_2_DATE]. The security is refundable at the time of vacation of said premises after deducting breakage, damages, and clearance of all utility bills (Electricity, Sui Gas, Society/Building Maintenance Charges, Telephone, etc.).",
    "That the period of tenancy is hereby agreed as [LEASE_DURATION_MONTHS] months, commencing from [START_DATE] to [END_DATE], with a rent increase of @ [RENT_INCREASE_PERCENT]% after [LEASE_DURATION_MONTHS] months. Renewal is possible with mutual consent of both parties. The Tenant shall vacate peacefully after the lease expires.",
    "That the Tenant is bound not to vacate the premises within [MIN_OCCUPANCY_PERIOD] months. If they choose to vacate earlier, they must pay Rs. [EARLY_TERMINATION_PENALTY]/- per month as penalty.",
    "That [KEYS_ISSUED] keys/keycards will be issued to the Tenant, to be returned upon vacating the premises. If lost, Rs. [KEY_REPLACEMENT_COST]/- per key/keycard will be deducted from the Security Deposit.",
    "That the Tenant shall maintain the premises in good condition, including all fittings and fixtures, and replace any broken items with equal quality. No alterations or wall drilling is allowed without written permission. Subletting is strictly prohibited.",
    "That in case the Owner sells the property, the Tenant shall have no objection and will cooperate in executing a fresh lease agreement with the new Owner for the remaining term.",
    "That the Tenant shall not demand any compensation for decoration or expenses upon vacating the premises. Any legal claims shall be deemed void.",
    "That the said premises will be used strictly for residential purposes only.",
    "That the Tenant is responsible to complete verification from the concerned Police Station.",
    "That the said premises has been handed over in working order, including [INVENTORY_CEILING_FANS] Ceiling Fans, [INVENTORY_LIGHTS] Ceiling Lights, [INVENTORY_EXHAUST_FANS] Exhaust Fans, [INVENTORY_WARDROBE] wardrobes, and [INVENTORY_STOVE] Stove(s). The Tenant shall return all in the same condition upon vacating.",
    "That the Tenant shall pay all utility bills timely and submit copies to the Owner upon request. Electricity bill will be paid at Rs. [ELECTRIC_UNIT_RATE]/- per unit to the Owner along with the rent.",
    "That a 2-month advance written notice is required from either party to vacate the premises. Failure to do so by the Tenant will result in forfeiture of the Security Deposit.",
    "That the Tenant will not rent or sublet the premises to any third party.",
    "That the Owner may visit the premises with reasonable advance notice.",
    "That during the lease period, any complaints from the society against the Tenant shall be the Tenant's responsibility.",
    "That the Electricity Meter reading is [ELECTRICITY_METER_READING] as on [METER_READING_DATE].",
    "That the Security Deposit will not be adjusted against rent under any circumstances.",
    "That smoking is not allowed in the building. Tenant is not allowed to use the common area for drying clothes.",
    "That the Tenant shall not use the terrace, hallway, or any other common areas beyond their own rented portion.",
    "That the Tenant is responsible for cleaning and maintaining common and exterior areas (doors, windows, hallways, walls, stairs, and ceilings) at least three times a week.",
    "That all legal rights regarding the said premises are reserved with the Owner.",
    "That both parties agree to abide by all terms and conditions stated in this agreement.",
    "That the Tenant shall not engage in any illegal or immoral activities on the premises.",
]


def seed_default_clauses(apps, schema_editor):
    DefaultClause = apps.get_model("leases", "DefaultClause")

    for clause_number, body in enumerate(HARDCODED_DEFAULT_CLAUSES, start=1):
        clause = DefaultClause.objects.filter(
            clause_number=clause_number,
            is_active=True,
        ).first()

        if clause:
            if not (clause.body or "").strip():
                clause.body = body
                clause.save(update_fields=["body", "updated_at"])
            continue

        DefaultClause.objects.create(
            clause_number=clause_number,
            body=body,
            is_active=True,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("leases", "0030_backfill_lease_history_agreement_date"),
    ]

    operations = [
        migrations.RunPython(seed_default_clauses, migrations.RunPython.noop),
    ]
