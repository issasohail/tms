import logging
import re


_REGISTRATION_TOKEN_PATH = re.compile(
    r"(?P<prefix>/registration/)(?P<token>[^/?\s]+)(?P<suffix>(?:/|\?|\s|$))"
)


def _redact(value):
    if not isinstance(value, str):
        return value
    return _REGISTRATION_TOKEN_PATH.sub(
        lambda match: f"{match.group('prefix')}[redacted]{match.group('suffix')}",
        value,
    )


class RedactRegistrationTokenFilter(logging.Filter):
    def filter(self, record):
        record.msg = _redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: _redact(value) for key, value in record.args.items()}
        return True
