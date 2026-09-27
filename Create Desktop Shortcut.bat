@echo off
rem Puts a "Level Stats" shortcut on your desktop, with the app icon,
rem pointing at the launcher in this folder. Run once; optional.

title Level Stats - create shortcut
cd /d "%~dp0"

echo.
echo   Creating a desktop shortcut for Level Stats...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "tools\create-shortcut.ps1" -ProjectDir "%CD%"
if errorlevel 1 (
  echo.
  echo   Could not create the shortcut. You can still start the app by
  echo   double-clicking "Start Level Stats.bat" in this folder, or by
  echo   right-clicking it and choosing "Send to" - "Desktop ^(create shortcut^)".
  echo.
)

pause
