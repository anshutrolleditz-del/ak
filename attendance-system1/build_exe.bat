@echo off
setlocal

echo ============================================
echo  Building Attendance System.exe
echo ============================================
echo.
echo Run this from your project's virtual environment (the one where
echo requirements.txt is already installed and "python gui_app.py"
echo already works). If you haven't confirmed gui_app.py runs correctly
echo yet, do that first -- packaging a broken app just gives you a
echo broken exe.
echo.
pause

python -m pip install pyinstaller --quiet
if errorlevel 1 (
    echo.
    echo pyinstaller failed to install -- see the error above.
    pause
    exit /b 1
)

echo.
echo Building... this can take several minutes, especially the first
echo time (the face recognition model data alone is roughly 100MB).
echo.

python -m PyInstaller --windowed --name AttendanceSystem ^
    --collect-all face_recognition_models ^
    --collect-all face_recognition ^
    --hidden-import=PIL._tkinter_finder ^
    gui_app.py

if errorlevel 1 (
    echo.
    echo Build failed -- see the error above. Common causes:
    echo   - This venv doesn't have gui_app.py's own dependencies fully
    echo     installed yet -- confirm "python gui_app.py" works first.
    echo   - A stale build\ or dist\ folder from a previous attempt --
    echo     delete both and try again.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Done.
echo  AttendanceSystem.exe is in: dist\AttendanceSystem\
echo.
echo  That .exe needs the rest of that folder next to it to run --
echo  copy or share the WHOLE "AttendanceSystem" folder, not just
echo  the .exe file by itself.
echo ============================================
pause
