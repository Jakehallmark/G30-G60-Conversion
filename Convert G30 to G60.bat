@echo off
:: Drag a G30 XML settings file onto this .bat to convert it.
:: Outputs go to the "Converted" subfolder next to this script.

python "%~dp0convert_g30_to_g60.py" %*
echo.
pause
