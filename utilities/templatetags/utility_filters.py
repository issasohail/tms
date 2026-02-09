from django import template
import sys
from pathlib import Path
import logging

sys.path.append(str(Path(__file__).parent.parent.parent))
logger = logging.getLogger(__name__)


register = template.Library()


@register.filter(name='divide')
def divide(value, arg):
    try:
        return float(value) / float(arg)
    except (ValueError, ZeroDivisionError):
        return 0.00
