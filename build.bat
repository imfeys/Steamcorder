@echo off
rem Builds dist\Steamcorder.exe. Needs Python 3.9+ installed.
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller || exit /b 1
python -m PyInstaller --noconfirm --onefile --windowed --name Steamcorder ^
  --icon icon.ico --add-data "icon.ico;." --add-data "icon.png;." Steamcorder.pyw || exit /b 1
echo.
echo Done: dist\Steamcorder.exe
