@echo off
chcp 65001 > nul
echo.
echo ╔══════════════════════════════════════════════════════╗
echo ║       AI Resume Analyzer — Démarrage Backend        ║
echo ║              Circet Morocco PFA 2026                ║
echo ╚══════════════════════════════════════════════════════╝
echo.

cd /d "%~dp0"

REM Vérifier si Python est installé
python --version > nul 2>&1
if errorlevel 1 (
    echo [ERREUR] Python n'est pas installé ou pas dans le PATH.
    echo         Téléchargez Python 3.10+ sur https://python.org
    pause
    exit /b 1
)

echo [1/2] Installation des dépendances...
pip install -r requirements.txt --quiet

echo [2/2] Démarrage du serveur backend...
echo.
echo  Serveur Backend : http://127.0.0.1:8000
echo  Frontend        : ouvrez frontend/index.html dans votre navigateur
echo.
echo  Appuyez sur Ctrl+C pour arrêter le serveur.
echo ──────────────────────────────────────────────────────
python main.py

pause
