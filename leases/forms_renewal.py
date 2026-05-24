from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django import forms

from .models_renewal import LeaseRenewal


MONEY_QUANT = Decimal("0.01")


class LeaseRenewalForm(forms.ModelForm):
    class Meta:
        model = LeaseRenewal
        fields = [
            "start_date",
            "end_date",
            "agreement_date",
            "monthly_rent",
            "society_maintenance",
            "water_charges",
            "internet_charges",
            "agreement_charges",
            "security_deposit",
            "rent_increase_percent",
            "witness1_name",
            "witness1_cnic",
            "witness2_name",
            "witness2_cnic",
            "police_verification_status",
            "police_verification_date",
            "police_verification_document",
            "police_verification_remarks",
            "police_verification_follow_up_date",
            "notes",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date", "class": "form-control form-control-sm"}),
            "end_date": forms.DateInput(attrs={"type": "date", "class": "form-control form-control-sm"}),
            "agreement_date": forms.DateInput(attrs={"type": "date", "class": "form-control form-control-sm"}),
            "monthly_rent": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
            "society_maintenance": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
            "water_charges": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
            "internet_charges": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
            "agreement_charges": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
            "security_deposit": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
            "rent_increase_percent": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
            "witness1_name": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "witness1_cnic": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "witness2_name": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "witness2_cnic": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "police_verification_status": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "police_verification_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control form-control-sm"}),
            "police_verification_document": forms.ClearableFileInput(attrs={"class": "form-control form-control-sm"}),
            "police_verification_remarks": forms.Textarea(attrs={"class": "form-control form-control-sm", "rows": 3}),
            "police_verification_follow_up_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control form-control-sm"}),
            "notes": forms.Textarea(attrs={"class": "form-control form-control-sm", "rows": 3}),
        }

    def __init__(self, *args, lease=None, **kwargs):
        self.lease = lease
        super().__init__(*args, **kwargs)

        if lease and not self.is_bound:
            start_date = lease.end_date + timedelta(days=1)
            end_date = start_date + timedelta(days=365) - timedelta(days=1)
            increase = lease.rent_increase_percent or Decimal("10.00")
            current_rent = lease.monthly_rent or Decimal("0.00")
            proposed_rent = (
                current_rent * (Decimal("1.00") + Decimal(increase) / Decimal("100.00"))
            ).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)

            self.initial.update({
                "start_date": start_date,
                "end_date": end_date,
                "agreement_date": start_date,
                "monthly_rent": proposed_rent,
                "society_maintenance": lease.society_maintenance or Decimal("0.00"),
                "water_charges": lease.water_charges or Decimal("0.00"),
                "internet_charges": lease.internet_charges or Decimal("0.00"),
                "agreement_charges": lease.agreement_charges or Decimal("0.00"),
                "security_deposit": lease.security_deposit or Decimal("0.00"),
                "rent_increase_percent": increase,
                "witness1_name": lease.witness1_name,
                "witness1_cnic": lease.witness1_cnic,
                "witness2_name": lease.witness2_name,
                "witness2_cnic": lease.witness2_cnic,
            })

    def clean(self):
        cleaned = super().clean()
        lease = self.lease
        start_date = cleaned.get("start_date")
        end_date = cleaned.get("end_date")

        if start_date and end_date and end_date <= start_date:
            self.add_error("end_date", "End date must be after start date.")

        if start_date and not cleaned.get("agreement_date"):
            cleaned["agreement_date"] = start_date

        if lease and start_date and start_date <= lease.start_date:
            self.add_error(
                "start_date",
                "Renewal start date must be after the current lease start date.",
            )

        if lease and start_date and end_date:
            overlap = lease.renewals.filter(
                start_date__lte=end_date,
                end_date__gte=start_date,
            )
            if self.instance and self.instance.pk:
                overlap = overlap.exclude(pk=self.instance.pk)
            if overlap.exists():
                self.add_error(
                    "start_date",
                    "This renewal period overlaps an existing renewal for this lease.",
                )

        return cleaned


class LeaseHistoryEditForm(forms.ModelForm):
    class Meta:
        model = LeaseRenewal
        fields = [
            "start_date",
            "end_date",
            "agreement_date",
            "monthly_rent",
            "society_maintenance",
            "water_charges",
            "internet_charges",
            "agreement_charges",
            "security_deposit",
            "witness1_name",
            "witness1_cnic",
            "witness2_name",
            "witness2_cnic",
            "notes",
        ]
        widgets = {
            "start_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control form-control-sm"}),
            "end_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control form-control-sm"}),
            "agreement_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control form-control-sm"}),
            "monthly_rent": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
            "society_maintenance": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
            "water_charges": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
            "internet_charges": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
            "agreement_charges": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
            "security_deposit": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01"}),
            "witness1_name": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "witness1_cnic": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "witness2_name": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "witness2_cnic": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "notes": forms.Textarea(attrs={"class": "form-control form-control-sm", "rows": 3}),
        }

    def clean(self):
        cleaned = super().clean()
        start_date = cleaned.get("start_date")
        end_date = cleaned.get("end_date")
        if start_date and end_date and end_date <= start_date:
            self.add_error("end_date", "End date must be after start date.")
        if start_date and not cleaned.get("agreement_date"):
            cleaned["agreement_date"] = start_date
        return cleaned
