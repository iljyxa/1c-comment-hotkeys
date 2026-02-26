@echo off
setlocal

cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo python not found in PATH
  exit /b 1
)

python -m pip install --upgrade pip
if errorlevel 1 exit /b 1

python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1

python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --onefile ^
  --name "1c-comment-hotkeys" ^
  --paths "src" ^
  --icon "src/resources/icon.ico" ^
  "src/app.py"
if errorlevel 1 exit /b 1

echo Build complete: %CD%\dist\1c-comment-hotkeys.exe
exit /b 0
