@echo off
cd /d "%~dp0"
set MOLLIE_API_KEY=test_hSuGbKgKEzbKt5nsUW8yraKPGVxxHd
echo Installeren afhankelijkheden...
python -m pip install -r requirements.txt --quiet
echo.
echo Uniec2Vabi starten op http://127.0.0.1:5002
echo (Ctrl+C om te stoppen)
echo.
python app.py
pause
