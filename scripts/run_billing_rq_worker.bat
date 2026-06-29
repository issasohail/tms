@echo off
cd /d "%~dp0\.."
python manage.py billing_rq_worker
