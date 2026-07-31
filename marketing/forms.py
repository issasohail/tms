from django import forms

from .config import PRICING_PLANS


class ContactForm(forms.Form):
    UNIT_CHOICES = (
        ("1–5", "1–5"),
        ("6–10", "6–10"),
        ("11–25", "11–25"),
        ("26–50", "26–50"),
        ("51–100", "51–100"),
        ("100+", "100+"),
    )
    PLAN_CHOICES = (("not-sure", "Not sure"),) + tuple(
        (plan["slug"], plan["name"]) for plan in PRICING_PLANS
    )

    full_name = forms.CharField(max_length=120, required=False)
    business_name = forms.CharField(max_length=160, required=False)
    phone = forms.CharField(max_length=32, required=False)
    email = forms.EmailField(max_length=254, required=False)
    units = forms.ChoiceField(choices=UNIT_CHOICES, required=False)
    plan = forms.ChoiceField(choices=PLAN_CHOICES, required=False)
    message = forms.CharField(max_length=2000, widget=forms.Textarea)
