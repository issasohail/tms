# somewhere like invoices/aggregates.py
from django.db.models import Aggregate, CharField


class GroupConcat(Aggregate):
    function = 'GROUP_CONCAT'
    template = "%(function)s(%(distinct)s%(expressions)s SEPARATOR '%(separator)s')"
    allow_distinct = True
    output_field = CharField()

    def __init__(self, expression, distinct=False, separator=', ', **extra):
        super().__init__(expression, distinct='DISTINCT ' if distinct else '',
                         separator=separator, **extra)
