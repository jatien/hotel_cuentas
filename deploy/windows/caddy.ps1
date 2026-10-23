# Arranca Caddy (HTTPS) delante de Django en el PC de la oficina.
#
#   powershell -ExecutionPolicy Bypass -File deploy\windows\caddy.ps1
#
# Requisitos: caddy.exe en el PATH o en deploy\windows\caddy.exe (https://caddyserver.com/download)
# y CUENTAS_HOSTS en el .env o en el entorno, p. ej.  pms.hotel.lan, 192.168.1.20
$ErrorActionPreference = "Stop"
$Raiz = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Raiz

# Lee CUENTAS_HOSTS del .env si no está en el entorno
if (-not $env:CUENTAS_HOSTS -and (Test-Path "$Raiz\.env")) {
    $linea = Get-Content "$Raiz\.env" | Where-Object { $_ -match '^\s*CUENTAS_HOSTS\s*=' } | Select-Object -First 1
    if ($linea) { $env:CUENTAS_HOSTS = ($linea -split '=', 2)[1].Trim().Trim('"') }
}
if (-not $env:CUENTAS_HOSTS) { throw "Falta CUENTAS_HOSTS (p. ej. 'pms.hotel.lan, 192.168.1.20')" }

$env:CUENTAS_UPSTREAM = "127.0.0.1:8001"
$env:CUENTAS_LOG_DIR = "$Raiz\logs"
New-Item -ItemType Directory -Force -Path $env:CUENTAS_LOG_DIR | Out-Null

$caddy = if (Test-Path "$PSScriptRoot\caddy.exe") { "$PSScriptRoot\caddy.exe" } else { "caddy" }
& $caddy run --config "$Raiz\deploy\caddy\Caddyfile" --adapter caddyfile
