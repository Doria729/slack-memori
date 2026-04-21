@echo off
cd /d "%~dp0"
start "Flask" cmd /k "python app.py"
start "ngrok" cmd /k "set http_proxy=&& set https_proxy=&& set HTTP_PROXY=&& set HTTPS_PROXY=&& ngrok http --url=glenn-toothier-beulah.ngrok-free.dev 5000"
