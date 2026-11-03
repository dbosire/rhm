@echo off
REM One-time setup on Windows. Run from this folder.
python -m venv .venv || goto :error
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || goto :error
if not exist .env copy .env.example .env
python manage.py migrate || goto :error
python manage.py bootstrap
echo.
echo Setup complete. Start the app with run_windows.bat
goto :eof
:error
echo Setup failed. See the messages above.
exit /b 1
