# Arranca la app en producción en el PC de la oficina (Windows): Waitress en 127.0.0.1:8001.
# A la app se entra por Caddy (caddy.ps1), nunca directamente.
#
#   powershell -ExecutionPolicy Bypass -File deploy\windows\servir.ps1
#
# Para que arranque solo con el PC, ver docs/security.md §4 (Programador de tareas).
$ErrorActionPreference = "Stop"
$Raiz = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Raiz

& "$Raiz\venv\Scripts\Activate.ps1"
$env:DJANGO_ENV = "prod"

python manage.py collectstatic --noinput -v 0
python manage.py migrate --check | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Hay migraciones sin aplicar: python manage.py migrate"
}
python manage.py servir
