@echo off
SETLOCAL
SET PROJECT_PATH=C:\tenant_management_system
SET VENV_PATH=%PROJECT_PATH%\venv
SET PYTHON_PATH=C:\Users\k\AppData\Local\Programs\Python\Python313

:: Cleanup phase
echo [1/6] Cleaning up old files...
del /q /s "%PROJECT_PATH%\*migrations\00*.py" 2>nul
del "%PROJECT_PATH%\db.sqlite3" 2>nul
rmdir /s /q "%PROJECT_PATH%\__pycache__" 2>nul

:: Virtual environment creation with retry logic
echo [2/6] Creating virtual environment...
rmdir /s /q "%VENV_PATH%" 2>nul

:: First try with default python
python -m venv "%VENV_PATH%" || (
    echo First attempt failed, trying with explicit Python path...
    "%PYTHON_PATH%\python.exe" -m venv "%VENV_PATH%" || (
        echo Failed to create virtual environment
        exit /b 1
    )
)

:: Installation phase
echo [3/6] Installing requirements...
call "%VENV_PATH%\Scripts\activate.bat"
pip install --disable-pip-version-check -r "%PROJECT_PATH%\requirements.txt"

:: Database setup
echo [4/6] Running migrations...
python manage.py makemigrations
python manage.py migrate

:: Superuser creation
echo [5/6] Creating superuser...
echo from django.contrib.auth import get_user_model; User = get_user_model(); User.objects.filter(username='user').exists() or User.objects.create_superuser('user', 'user@example.com', 'abcd7861') | python manage.py shell

:: Finalization
echo [6/6] Setup complete! Starting server...
python manage.py runserver
pause
ENDLOCAL