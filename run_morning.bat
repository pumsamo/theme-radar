@echo off
REM ASCII only. cmd.exe reads .bat in the system codepage (cp949 here),
REM so any Korean text in this file would be mangled and break parsing.
chcp 65001 >nul
setlocal
pushd "%~dp0"

set PY=C:\Users\pumsa_yvvjwu4\AppData\Local\Programs\Python\Python312\python.exe
if not exist logs mkdir logs

REM Seed xlsx only needs re-importing when new files are added, so skip it on
REM the daily run. After adding a new xlsx, run: python run_morning.py
"%PY%" run_morning.py --skip-seed >> "logs\run_%date:~0,4%%date:~5,2%.log" 2>&1
set CODE=%ERRORLEVEL%

if not "%CODE%"=="0" echo [%date% %time%] FAILED code=%CODE% >> "logs\errors.log"

popd
exit /b %CODE%
