@echo off
setlocal

cd /d "%~dp0"

set "PYTHON=python"

echo.
echo ========================================
echo 1. Importing China cost data
echo ========================================
"%PYTHON%" import_china_to_sql.py
if errorlevel 1 goto :error

echo.
echo ========================================
echo 2. Building SKU matches
echo ========================================
"%PYTHON%" build_sku_match.py
if errorlevel 1 goto :error

echo.
echo ========================================
echo 3. Building sales product margins
echo ========================================
"%PYTHON%" build_sales_product_margin.py
if errorlevel 1 goto :error

echo.
echo ========================================
echo PIPELINE COMPLETED SUCCESSFULLY
echo ========================================
pause
exit /b 0

:error
echo.
echo ========================================
echo PIPELINE FAILED
echo ========================================
pause
exit /b 1