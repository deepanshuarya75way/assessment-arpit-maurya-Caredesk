@echo off
REM Creates the .env file on the first run, installs packages and starts the server
if not exist .env copy .env.example .env
pip install -r requirements.txt
uvicorn main:app --reload
