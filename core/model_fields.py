from django import forms
from django.core.exceptions import ValidationError
from django.db import models

from core.utils.identity import normalize_cnic, normalize_phone, validate_cnic


class NormalizedCNICFormField(forms.CharField):
    def to_python(self, value):
        return normalize_cnic(super().to_python(value))


class NormalizedPhoneFormField(forms.CharField):
    def to_python(self, value):
        return normalize_phone(super().to_python(value))


class NormalizedCNICField(models.CharField):
    description = "CNIC stored as thirteen digits"
    default_validators = [validate_cnic]

    def to_python(self, value):
        value = super().to_python(value)
        if value is None:
            return None
        return normalize_cnic(value)

    def pre_save(self, model_instance, add):
        raw_value = getattr(model_instance, self.attname)
        value = self.to_python(raw_value)
        try:
            validate_cnic(value)
        except ValidationError:
            # Existing malformed legacy identifiers must not block an unrelated
            # record update. They remain untouched and are reported by the cleanup
            # command; new or changed malformed values are still rejected.
            if not add and model_instance.pk:
                stored_value = (
                    model_instance.__class__._base_manager.filter(pk=model_instance.pk)
                    .values_list(self.attname, flat=True)
                    .first()
                )
                if stored_value == raw_value:
                    return raw_value
            raise
        setattr(model_instance, self.attname, value)
        return value

    def get_prep_value(self, value):
        # Normal model writes have already passed through pre_save. Avoid a second
        # conversion here so an explicitly preserved malformed legacy value is not
        # silently reduced to whichever digits happened to be embedded in it.
        if value is None:
            return None
        return str(value)

    def formfield(self, **kwargs):
        kwargs.setdefault("form_class", NormalizedCNICFormField)
        field = super().formfield(**kwargs)
        field.widget.attrs.setdefault("inputmode", "numeric")
        field.widget.attrs.setdefault("autocomplete", "off")
        field.widget.attrs.setdefault("placeholder", "XXXXX-XXXXXXX-X")
        return field


class NormalizedPhoneField(models.CharField):
    description = "Phone number with separators removed and optional leading plus preserved"

    def to_python(self, value):
        value = super().to_python(value)
        if value is None:
            return None
        return normalize_phone(value)

    def pre_save(self, model_instance, add):
        value = self.to_python(getattr(model_instance, self.attname))
        setattr(model_instance, self.attname, value)
        return value

    def formfield(self, **kwargs):
        kwargs.setdefault("form_class", NormalizedPhoneFormField)
        field = super().formfield(**kwargs)
        field.widget.input_type = "tel"
        field.widget.attrs.setdefault("inputmode", "tel")
        field.widget.attrs.setdefault("autocomplete", "tel")
        return field
