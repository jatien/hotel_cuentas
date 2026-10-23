# Reglas del Firewall de Windows para el PC que sirve la app. Ejecutar como administrador:
#
#   powershell -ExecutionPolicy Bypass -File deploy\windows\cortafuegos.ps1
#
# Abre 80/443 (Caddy) solo a la red local y bloquea desde la red los puertos internos:
# 8000 (runserver), 8001 (Waitress) y 5432 (PostgreSQL).
$ErrorActionPreference = "Stop"

$reglas = @(
    @{ Nombre = "Cuentas HTTPS (Caddy)"; Puertos = "80,443"; Accion = "Allow" },
    @{ Nombre = "Cuentas bloquear runserver/Waitress"; Puertos = "8000,8001"; Accion = "Block" },
    @{ Nombre = "Cuentas bloquear PostgreSQL"; Puertos = "5432"; Accion = "Block" }
)

foreach ($r in $reglas) {
    Get-NetFirewallRule -DisplayName $r.Nombre -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    $params = @{
        DisplayName = $r.Nombre
        Direction   = "Inbound"
        Protocol    = "TCP"
        LocalPort   = $r.Puertos -split ","
        Action      = $r.Accion
    }
    if ($r.Accion -eq "Allow") { $params.RemoteAddress = "LocalSubnet" }
    New-NetFirewallRule @params | Out-Null
    Write-Host "OK  $($r.Nombre) ($($r.Accion) $($r.Puertos))"
}
