from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction

from payments.models import Payment, PaymentAllocation, PaymentMethod
from invoices.models import SecurityDepositTransaction


def D(v):
    try:
        return Decimal(v or 0)
    except Exception:
        return Decimal("0.00")


def absD(v):
    x = D(v)
    return x if x >= 0 else -x


class Command(BaseCommand):
    help = (
        "Backfill PaymentAllocation for all Payments AND create one Payment+Allocation "
        "for standalone SecurityDepositTransaction rows (PAYMENT/REFUND only)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Do not write changes, only report."
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Limit number of records processed (0 = no limit).",
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        limit = int(opts["limit"] or 0)

        # PaymentMethod: bank_transfer for backfilled SDT-payments (or blank if not found)
        pm_bank = None
        try:
            pm_bank = PaymentMethod.objects.filter(code="bank_transfer").first()
        except Exception:
            pm_bank = None

        created_alloc = 0
        updated_alloc = 0
        linked_sec = 0
        created_sec = 0

        created_pay_from_sdt = 0
        created_alloc_from_sdt = 0
        linked_sdt = 0
        skipped_required = 0
        skipped_already_linked = 0

        # ---------------------------------------------------------------------
        # PART A: Existing Payments -> ensure allocation + link/create SDT per allocation
        # ---------------------------------------------------------------------
        pay_qs = Payment.objects.select_related("lease").order_by("id")
        if limit:
            pay_qs = pay_qs[:limit]

        for p in pay_qs:
            pay_amt = D(p.amount)

            alloc, alloc_created = PaymentAllocation.objects.get_or_create(
                payment=p,
                defaults=dict(
                    lease_amount=pay_amt,
                    security_amount=Decimal("0.00"),
                    security_type="PAYMENT",
                ),
            )

            existing_sec = list(SecurityDepositTransaction.objects.filter(payment=p))

            sec_amt = Decimal("0.00")
            sec_type = "PAYMENT"

            for tx in existing_sec:
                if tx.type == "REQUIRED":
                    continue
                if tx.type == "PAYMENT":
                    sec_amt += D(tx.amount)
                elif tx.type == "REFUND":
                    sec_amt -= D(tx.amount)
                elif tx.type == "ADJUST":
                    sec_amt += D(tx.amount)
                elif tx.type == "DAMAGE":
                    sec_amt -= D(tx.amount)

            if sec_amt < 0:
                sec_type = "REFUND"
                sec_amt = abs(sec_amt)

            if sec_amt > pay_amt:
                sec_amt = pay_amt

            lease_amt = pay_amt - sec_amt

            if (
                alloc.lease_amount != lease_amt
                or alloc.security_amount != sec_amt
                or alloc.security_type != sec_type
            ):
                alloc.lease_amount = lease_amt
                alloc.security_amount = sec_amt
                alloc.security_type = sec_type
                if not dry:
                    alloc.save(
                        update_fields=[
                            "lease_amount",
                            "security_amount",
                            "security_type",
                        ]
                    )
                updated_alloc += 1
            elif alloc_created:
                created_alloc += 1

            if sec_amt > 0:
                notes = (p.notes or "").strip()
                extra = f"Total Payment: Rs. {pay_amt:,.2f} | Lease: Rs. {lease_amt:,.2f} | Security: Rs. {sec_amt:,.2f}"
                merged_notes = (notes + "\n" + extra).strip() if notes else extra

                sdt, sdt_created = SecurityDepositTransaction.objects.update_or_create(
                    allocation=alloc,
                    defaults=dict(
                        lease=p.lease,
                        payment=p,
                        date=p.payment_date,
                        type=sec_type,
                        amount=sec_amt,
                        notes=merged_notes,
                    ),
                )
                if sdt_created:
                    created_sec += 1
                else:
                    linked_sec += 1

                if existing_sec and not dry:
                    SecurityDepositTransaction.objects.filter(payment=p).exclude(
                        allocation=alloc
                    ).delete()
            else:
                if not dry:
                    SecurityDepositTransaction.objects.filter(allocation=alloc).delete()

        # ---------------------------------------------------------------------
        # PART B (NEW): Standalone SDT rows (PAYMENT/REFUND) -> create Payment + Allocation
        # - one Payment per SDT row
        # - do NOT create Payment for REQUIRED
        # - only act when SDT has no allocation
        # ---------------------------------------------------------------------
        sdt_qs = SecurityDepositTransaction.objects.select_related(
            "lease", "payment", "allocation"
        ).order_by("id")
        if limit:
            sdt_qs = sdt_qs[:limit]

        for sdt in sdt_qs:
            if sdt.type == "REQUIRED":
                skipped_required += 1
                continue

            if sdt.type not in ("PAYMENT", "REFUND"):
                continue

            if sdt.allocation is not None:
                skipped_already_linked += 1
                continue

            if sdt.lease is None:
                # Lease is required in your model, but keep safe.
                continue

            amt = absD(sdt.amount)
            if amt <= 0:
                continue

            marker = f"SDBF-SDT-{sdt.pk}"

            pay = None
            if sdt.payment is not None:
                pay = sdt.payment
            else:
                pay = Payment.objects.filter(reference_number=marker).first()

            if not pay:
                # DRY-RUN: do not create unsaved instances (prevents Django ValueError)
                if dry:
                    created_pay_from_sdt += 1
                    created_alloc_from_sdt += 1
                    linked_sdt += 1
                    continue

                pay = Payment(
                    lease=sdt.lease,
                    payment_date=sdt.date,
                    amount=amt,
                    reference_number=marker,
                    notes=(sdt.notes or "").strip(),
                )
                if pm_bank:
                    pay.payment_method = pm_bank
                pay.save()
                created_pay_from_sdt += 1

            alloc, a_created = PaymentAllocation.objects.get_or_create(
                payment=pay,
                defaults=dict(
                    lease_amount=Decimal("0.00"),
                    security_amount=amt,
                    security_type=sdt.type,  # PAYMENT or REFUND
                ),
            )

            if a_created:
                created_alloc_from_sdt += 1
            else:
                needs_update = False
                if alloc.security_amount != amt:
                    alloc.security_amount = amt
                    needs_update = True
                if alloc.lease_amount != Decimal("0.00"):
                    alloc.lease_amount = Decimal("0.00")
                    needs_update = True
                if alloc.security_type != sdt.type:
                    alloc.security_type = sdt.type
                    needs_update = True

                if needs_update and not dry:
                    alloc.save(
                        update_fields=[
                            "lease_amount",
                            "security_amount",
                            "security_type",
                        ]
                    )

            if not dry:
                sdt.payment = pay
                sdt.allocation = alloc
                sdt.amount = amt  # keep positive; direction is in sdt.type
                sdt.save(update_fields=["payment", "allocation", "amount"])

            linked_sdt += 1
