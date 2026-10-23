# Copia de seguridad cifrada (base de datos + ficheros subidos). Pensado para el Programador de
# tareas, cada noche. Si falla termina con código 1 y la tarea aparece como error.
#
#   powershell -ExecutionPolicy Bypass -File deploy\windows\copia.ps1
#
# Necesita en el .env: BACKUP_AGE_RECIPIENTS (clave pública) y, si pg_dump no está en el PATH,
# BACKUP_PG_BIN_DIR=C:\Program Files\PostgreSQL\17\bin. Ver docs/security.md §5.
$ErrorActionPreference = "Stop"
$Raiz = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Raiz
& "$Raiz\venv\Scripts\Activate.ps1"
python manage.py copia_seguridad
exit $LASTEXITCODE
