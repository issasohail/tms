import re


PRESERVED_UPPERCASE_WORDS = {"USA", "UAE", "UK", "USD", "CAD", "EUR", "PKR"}

TITLECASE_FIELD_NAMES = {
    "first_name",
    "middle_name",
    "last_name",
    "full_name",
    "tenant_name",
    "owner_name",
    "owner_father_name",
    "caretaker_name",
    "caretaker_father_name",
    "contact_name",
    "emergency_contact_name",
    "family_member_name",
    "reference_name",
    "reference_name_1",
    "reference_name_2",
    "occupant_name",
    "employer_name",
    "property_name",
    "unit_name",
    "building_name",
    "city",
    "province",
    "country",
    "nationality",
    "site_name",
    "name",
    "label",
    "category",
    "title",
}

_WORD_RE = re.compile(r"[A-Za-z]+")


def _title_word(match):
    word = match.group(0)
    upper = word.upper()
    if upper in PRESERVED_UPPERCASE_WORDS:
        return upper

    lowered = word.lower()
    if lowered.startswith("mc") and len(lowered) > 2:
        return "Mc" + lowered[2:3].upper() + lowered[3:]
    return lowered[:1].upper() + lowered[1:]


def smart_title(value):
    """
    Title-case human names/places while preserving common acronyms and prefixes.
    Leaves blank/None values untouched and avoids changing non-string values.
    """
    if value is None or not isinstance(value, str):
        return value

    stripped = " ".join(value.strip().split())
    if not stripped:
        return stripped
    return _WORD_RE.sub(_title_word, stripped)


def normalize_title_fields(instance, field_names):
    for field_name in field_names:
        if hasattr(instance, field_name):
            value = getattr(instance, field_name)
            if isinstance(value, str):
                setattr(instance, field_name, smart_title(value))


def add_auto_titlecase_class(fields, field_names=None):
    names = set(field_names or TITLECASE_FIELD_NAMES)
    for name, field in fields.items():
        if name not in names:
            continue
        widget = field.widget
        current_class = widget.attrs.get("class", "")
        classes = current_class.split()
        if "auto-titlecase" not in classes:
            classes.append("auto-titlecase")
        widget.attrs["class"] = " ".join(classes).strip()
