@echo off
call .venv\Scripts\activate.bat
echo Open http://127.0.0.1:8000 in your browser (Ctrl+C to stop)
python manage.py runserver 127.0.0.1:8000
