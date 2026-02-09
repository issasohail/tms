# tms/__init__.py

from __future__ import absolute_import, unicode_literals
import pymysql
pymysql.install_as_MySQLdb()

# This will make sure the app is always imported when
# Django starts so that shared_task will use this app.
from .celery import app as celery_app

import logging
import os
logger = logging.getLogger(__name__)

celery_app = None
if os.environ.get("DJANGO_ENABLE_CELERY", "0") == "1":
    try:
        from .celery import app as celery_app
    except Exception as e:
        logger.warning("Celery disabled: %s", e)

__all__ = ("celery_app",)
