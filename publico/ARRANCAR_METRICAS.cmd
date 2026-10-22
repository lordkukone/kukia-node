@echo off
title KUKIA - publicar metricas en la web
cd /d "%~dp0"
echo ============================================================
echo  KUKIA - publicar metricas en la web
echo  Lee /stats y /saldos del Core local y los manda al rele
echo  publico cada minuto. Cerrar esta ventana SOLO deja de
echo  publicar: el Core y el minero siguen igual.
echo ============================================================
echo.
python -u publicar_metricas.py
echo.
echo [publicacion detenida] La web mostrara el ultimo dato y avisara.
pause
