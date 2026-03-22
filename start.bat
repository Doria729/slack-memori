@echo off
cd /d "%~dp0"
start "Flask" cmd /k "python app.py"
start "ngrok" cmd /k "ngrok http --domain=glenn-toothier-beulah.ngrok-free.dev 5000"
