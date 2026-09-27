@echo off
rem ===================================================================
rem  Level Stats - double-click launcher for Windows
rem
rem  First run: finds Python, builds a private .venv, installs the
rem  dependencies, then starts the app and opens your browser.
rem  Later runs: skips straight to starting the app.
rem
rem  Everything happens in this file's own folder, so it does not matter
rem  where you extracted the project or what folder your terminal is in.
rem ===================================================================

title Level Stats
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"
set "BASE_PY="

echo.
echo   Level Stats
echo   Folder: %CD%
echo.

if not exist "run.py" goto wrongfolder
if not exist "%VENV_PY%" goto bootstrap
goto checkdeps

rem -------------------------------------------------------------------
:bootstrap
echo   First run - setting up. This takes a minute or two.
echo.
call :findpython
if not defined BASE_PY goto nopython

echo   Using Python: %BASE_PY%
echo   Creating a private environment in .venv ...
"%BASE_PY%" -m venv .venv
if errorlevel 1 goto venvfail
if not exist "%VENV_PY%" goto venvfail
goto install

rem -------------------------------------------------------------------
:checkdeps
rem Cheap import probe. Catches a half-built venv and any dependency
rem added in a later version of the project.
"%VENV_PY%" -c "import fastapi, uvicorn, jinja2, statsapi, pandas, multipart" >nul 2>&1
if errorlevel 1 (
  echo   Dependencies missing or out of date - installing.
  echo.
  goto install
)
goto run

rem -------------------------------------------------------------------
:install
"%VENV_PY%" -m pip install --upgrade pip >nul 2>&1
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto installfail
echo.
echo   Setup complete.
echo.
goto run

rem -------------------------------------------------------------------
:run
"%VENV_PY%" run.py %*
if errorlevel 1 goto runfail
goto end

rem ===================================================================
rem  Helpers
rem ===================================================================
:findpython
rem Prefer the Python launcher, newest supported version first.
rem 3.14+ is skipped on purpose: the pinned pandas has no wheel for it.
call :try_py 3.13
if defined BASE_PY exit /b
call :try_py 3.12
if defined BASE_PY exit /b
call :try_py 3.11
if defined BASE_PY exit /b
rem Fall back to whatever "python" resolves to, if the version is usable.
python -c "import sys; sys.exit(0 if (3,11)<=sys.version_info[:2]<(3,14) else 1)" >nul 2>&1
if errorlevel 1 exit /b
for /f "usebackq delims=" %%p in (`python -c "import sys;print(sys.executable)"`) do set "BASE_PY=%%p"
exit /b

:try_py
for /f "usebackq delims=" %%p in (`py -%1 -c "import sys;print(sys.executable)" 2^>nul`) do set "BASE_PY=%%p"
exit /b

rem ===================================================================
rem  Failure paths - all of them keep the window open
rem ===================================================================
:wrongfolder
echo   ERROR: run.py is not next to this file.
echo.
echo   This usually means the zip was extracted one level deeper than
echo   expected. Look for another "mlb-level-stats" folder inside this
echo   one and use the "Start Level Stats.bat" in there instead.
echo.
pause
exit /b 1

:nopython
echo   ERROR: no usable Python found.
echo.
echo   Level Stats needs Python 3.11, 3.12 or 3.13.
echo   Install one from https://www.python.org/downloads/
echo   and tick "Add python.exe to PATH" in the installer.
echo.
echo   Already have Python? Check what Windows sees:
echo       py -0
echo       python --version
echo.
pause
exit /b 1

:venvfail
echo.
echo   ERROR: could not create the .venv folder.
echo.
echo   Common causes:
echo     - this folder is read-only, or syncing (OneDrive/Dropbox) locked it
echo     - antivirus blocked the new python.exe
echo.
echo   Try copying the project somewhere local, e.g. C:\Projects\, and
echo   double-click this file again.
echo.
pause
exit /b 1

:installfail
echo.
echo   ERROR: installing dependencies failed. The pip output above says why.
echo.
echo   Most likely causes:
echo     - no internet connection, or a proxy/VPN blocking pypi.org
echo     - a Python version with no pandas wheel (needs 3.11-3.13)
echo.
echo   To retry from scratch, delete the .venv folder and run this again.
echo.
pause
exit /b 1

:runfail
echo.
echo   The app stopped with an error. The message above says why.
echo.
pause
exit /b 1

:end
echo.
echo   Level Stats has stopped. You can close this window.
echo.
timeout /t 5 /nobreak >nul 2>&1
exit /b 0
