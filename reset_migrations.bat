@echo off
:: reset_migrations.bat - Windows one-click migration reset script
:: Place this in your Django project root folder

echo ⚠️ WARNING: THIS WILL DELETE ALL DATABASE DATA!
echo ⚠️ Only run this in development environment!
echo.
set /p confirm="Are you sure you want to continue? (y/n) "
if /i not "%confirm%"=="y" (
    echo Operation cancelled
    pause
    exit /b
)

echo.
echo [1/6] Removing old migration files...
for /d %%d in (*) do (
    if exist "%%d\migrations" (
        echo Cleaning %%d migrations...
        del "%%d\migrations\*.py" /q
        del "%%d\migrations\*.pyc" /q
        echo. > "%%d\migrations\__init__.py"
    )
)

echo.
echo [2/6] Deleting database...
del db.sqlite3 2>nul
if exist db.sqlite3 (
    echo ❌ Failed to delete database!
    pause
    exit /b
) else (
    echo ✅ Database deleted
)

echo.
echo [3/6] Creating fresh migrations...
python manage.py makemigrations
if errorlevel 1 (
    echo ❌ Failed to create migrations!
    pause
    exit /b
)

echo.
echo [4/6] Applying migrations...
python manage.py migrate
if errorlevel 1 (
    echo ❌ Failed to apply migrations!
    pause
    exit /b
)

echo.
echo [5/6] Creating superuser...
python manage.py createsuperuser --username=admin --email=admin@example.com --noinput
if errorlevel 1 (
    echo ❌ Failed to create superuser!
    echo Try creating manually with: python manage.py createsuperuser
) else (
    echo ✅ Superuser created:
    echo Username: admin
    echo Password: admin (change this immediately)
)

echo.
echo [6/6] Verification:
python manage.py check
python manage.py showmigrations

echo.
echo ✅ Reset complete!
echo You can now run the development server with: python manage.py runserver
pause