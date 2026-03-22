@echo off
cd /d "%~dp0"
start cmd /k "cd /d \"%~dp0\" && python app.py"
start cmd /k "cd /d \"%~dp0\" && ngrok http --domain=glenn-toothier-beulah.ngrok-free.dev 5000"
