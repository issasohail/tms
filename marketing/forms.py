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

    full_name = forms.CharField(max_length=120)
    business_name = forms.CharField(max_length=160)
    phone = forms.CharField(max_length=32)
    email = forms.EmailField(max_length=254)
    units = forms.ChoiceField(choices=UNIT_CHOICES)
    plan = forms.ChoiceField(choices=PLAN_CHOICES)
    message = forms.CharField(max_length=2000, widget=forms.Textarea)
