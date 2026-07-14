import re


CNIC_RE = re.compile(r"\b\d{5}[- ]?\d{7}[- ]?\d\b")
LONG_NUMBER_RE = re.compile(r"\b\d{12,24}\b")


def mask_sensitive_text(value):
    text = str(value or "")
    text = CNIC_RE.sub("[CNIC MASKED]", text)
    return LONG_NUMBER_RE.sub("[NUMBER MASKED]", text)


def safe_summary(value, limit=500):
    return mask_sensitive_text(value).strip()[:limit]


def sanitize_tool_arguments(arguments, allowed_fields):
    arguments = arguments if isinstance(arguments, dict) else {}
    return {key: value for key, value in arguments.items() if key in allowed_fields}
