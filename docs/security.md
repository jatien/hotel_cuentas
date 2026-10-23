# Seguridad de `cuentas`

Diseño y guía de uso de la seguridad del proyecto. Cada capa dice qué está hecho, dónde está y qué
queda pendiente.

```
Navegador / app móvil
   │  LAN, o VPN para acceso remoto (nunca un puerto abierto a Internet)
   ▼
Caddy (proxy)            TLS, redirección HTTP→HTTPS, HSTS, límite de tamaño de peticiones
   ▼
Waitress / Gunicorn      solo escucha en 127.0.0.1
   ▼
Middleware de Django     cabeceras de seguridad, CSP, sesiones, CSRF
   ▼
Vista → servicio → PostgreSQL (rol propio de la app)
                 └→ herramientas del PMS Copilot (mismos permisos que las vistas)
```

| Capa | Estado |
|---|---|
| Ajustes por entorno y secretos fuera de git | ✅ este documento, §1 |
| Cabeceras de seguridad y CSP | ✅ §2 |
| Registro de eventos de seguridad | ✅ §3 |
| Servidor WSGI + proxy + HTTPS | ✅ §4 |
| Cifrado en reposo y copias cifradas | ver §5 (PR aparte) |
| Llamadas a APIs externas y servidor del modelo | ver §6 (PR aparte) |
| Autenticación y autorización de usuarios | **pendiente** (se hará más adelante) |

---

## 1. Ajustes por entorno y secretos

`mysite/settings/` es un paquete:

| Fichero | Uso |
|---|---|
| `base.py` | Común: apps, middleware, cabeceras, CSP, logs, configuración de las apps |
| `dev.py` | Desarrollo: `DEBUG=True`, `runserver` |
| `prod.py` | Producción detrás de HTTPS: `DEBUG=False`, cookies seguras, HSTS, SMTP, estáticos con hash |

Se elige con `DJANGO_ENV=dev|prod` en el `.env`. `manage.py`, `wsgi.py` y `asgi.py` siguen apuntando a
`mysite.settings`, que carga el que toque.

**Ningún secreto vive en el código.** Todo sale del `.env` (plantilla en `.env.example`):
`SECRET_KEY`, `DB_PASSWORD`, `ALLOWED_HOSTS`, claves de los modelos de IA…

`prod.py` se niega a arrancar si:
- falta `SECRET_KEY`, es corta (< 50 caracteres) o es la de desarrollo;
- `ALLOWED_HOSTS` está vacío o contiene `*`;
- falta `DB_PASSWORD`.

Comprobación completa antes de poner en marcha el servidor:

```powershell
$env:DJANGO_ENV="prod"; python manage.py check --deploy
```

Solo debe quedar el aviso `security.W005` (HSTS de subdominios), que no aplica en la LAN.

### ⚠️ Secretos que han estado en git

Hasta este cambio, `mysite/settings.py` tenía la `SECRET_KEY` y la contraseña de PostgreSQL
(`postgres` / `1234`), y `db.sqlite3` estaba versionado. Siguen en el historial del repositorio, así
que hay que darlos por públicos:

1. Generar una `SECRET_KEY` nueva y ponerla en el `.env` de cada PC:
   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(64))"
   ```
   Al cambiarla se cierran las sesiones abiertas y caducan los enlaces firmados (normal).
2. Cambiar la contraseña de PostgreSQL y ponerla en `DB_PASSWORD`. Mejor aún, crear un rol propio
   para la app (ver §5) y dejar `postgres` solo para administración.
3. `db.sqlite3` se ha quitado del repositorio (el proyecto usa PostgreSQL); queda en `.gitignore`.

### Rotar la `SECRET_KEY` sin cerrar sesiones

```
SECRET_KEY=<nueva>
SECRET_KEY_FALLBACKS=<antigua>
```

Pasado un tiempo (cuando caduquen las sesiones), se vacía `SECRET_KEY_FALLBACKS`.

---

## 2. Cabeceras de seguridad y Content Security Policy

En todas las respuestas (`base.py`):

| Cabecera | Valor | Por qué |
|---|---|---|
| `X-Content-Type-Options` | `nosniff` | El navegador no adivina tipos (evita ejecutar un fichero subido como script) |
| `Referrer-Policy` | `same-origin` | Las URLs internas no salen hacia la CDN o Google Fonts |
| `Cross-Origin-Opener-Policy` | `same-origin` | Aísla la ventana de otras páginas |
| `X-Frame-Options` | `SAMEORIGIN` | Nadie de fuera puede meter la app en un iframe; los PDF propios sí |
| `Content-Security-Policy(-Report-Only)` | ver abajo | Lista blanca de orígenes de scripts y estilos |

En producción, además: cookies `Secure`, redirección a HTTPS y HSTS.

### CSP

La política (`CSP_POLICY` en `base.py`) permite scripts solo de la propia app, de
`cdn.jsdelivr.net` (Chart.js) y bloques `<script>` en línea que lleven el nonce de la petición.

- **Scripts en línea:** siempre con `<script nonce="{{ csp_nonce }}">`. Los bloques
  `<script type="application/json">` (datos con `json_script`) no lo necesitan.
- **Nada de `onclick`, `onchange`, `onsubmit`…** en las plantillas: se usan atributos que maneja
  `seguridad/static/seguridad/acciones.js`:

  | Antes | Ahora |
  |---|---|
  | `onchange="this.form.submit()"` | `data-autosubmit` |
  | `onsubmit="return confirm('…')"` (en `<form>`) | `data-confirm="…"` |
  | `onclick="return confirm('…')"` (en `<button>`) | `data-confirm="…"` |
  | `onclick="window.print()"` | `data-print` |

  Los tests de `seguridad` fallan si una plantilla nueva usa `on…=` o un `<script>` sin nonce.

**Despliegue en dos fases:**

1. `CSP_ENFORCE=False` (por defecto): *solo informe*. No bloquea nada; cada infracción aparece en la
   consola del navegador y en `logs/security.log` como `csp_violation`.
2. Cuando el log quede limpio usando la app con normalidad, `CSP_ENFORCE=True`.

---

## 3. Registro de eventos de seguridad

`logs/security.log` (rotativo, 10 × 5 MB; carpeta configurable con `LOG_DIR`) recibe:

- `django.security.*`: host no permitido, operaciones sospechosas, rechazos CSRF;
- `seguridad.csp`: infracciones de la CSP;
- `seguridad`: fallos del health check.

Cuando llegue la autenticación se añadirán aquí los inicios de sesión fallidos y los bloqueos.

`/seguridad/health/` responde `ok` si el proceso funciona y llega a la base de datos (lo usa el proxy).

---

## 4. Servidor WSGI, proxy y HTTPS

`runserver` es solo para desarrollo: un único proceso, sin HTTPS, y todo (contraseñas, nóminas,
facturas) viaja en claro por la red. En producción:

| Pieza | Windows (PC de la oficina) | Debian (servidor, Docker) |
|---|---|---|
| Servidor WSGI | Waitress: `python manage.py servir` | Gunicorn: `deploy/gunicorn.conf.py` |
| Escucha en | `127.0.0.1:8001` | red interna de Docker (`web:8001`) |
| Proxy HTTPS | Caddy: `deploy/windows/caddy.ps1` | servicio `caddy` de `deploy/docker/compose.yaml` |
| Estáticos | WhiteNoise (desde Django) | WhiteNoise |
| Ficheros subidos | `/media/` a través de Django (`seguridad.views.media`) | igual |

Configuración común del proxy: `deploy/caddy/Caddyfile` (certificado, redirección HTTP→HTTPS,
límite de 100 MB por petición, health check y log de accesos en `logs/caddy-access.log`).

### Certificado: autoridad interna de Caddy

Con `tls internal`, Caddy crea su propia autoridad de certificación (CA) la primera vez que
arranca y emite y renueva solo el certificado del servidor. Para que los navegadores lo acepten,
se instala **una vez** el certificado raíz de esa CA en cada equipo:

- Dónde está:
  - Windows: `%AppData%\Caddy\pki\authorities\local\root.crt`
  - Docker: `docker compose -f deploy/docker/compose.yaml cp caddy:/data/caddy/pki/authorities/local/root.crt .`
- Instalar en cada PC con Windows (como administrador):
  ```powershell
  certutil -addstore -f ROOT root.crt
  ```
- Android / iOS (futura app de housekeeping y bar): copiar `root.crt` al móvil e instalarlo como
  certificado de CA desde Ajustes → Seguridad.
- Firefox usa su propio almacén: Ajustes → Privacidad → Certificados → Importar.

La clave privada de la CA no sale del servidor; nunca se copia a los PCs.

### Nombre en la red

Se entra por un nombre fijo, p. ej. `https://pms.hotel.lan`, que tiene que resolver a la IP del
servidor: entrada DNS en el router o, si no se puede, una línea en
`C:\Windows\System32\drivers\etc\hosts` de cada PC (`192.168.1.20  pms.hotel.lan`). La IP también
puede ir en `CUENTAS_HOSTS` para entrar por `https://192.168.1.20`.

`.env` del servidor:

```
DJANGO_ENV=prod
SECRET_KEY=<64 caracteres aleatorios>
ALLOWED_HOSTS=pms.hotel.lan,192.168.1.20,localhost
CUENTAS_HOSTS=pms.hotel.lan, 192.168.1.20
```

### Puesta en marcha en Windows

1. `pip install -r requirements.txt` (instala Waitress y WhiteNoise).
2. Descargar `caddy.exe` de <https://caddyserver.com/download> y dejarlo en el PATH o en
   `deploy\windows\`.
3. Cortafuegos (PowerShell como administrador): `deploy\windows\cortafuegos.ps1`. Abre 80/443
   solo a la red local y bloquea desde la red 8000, 8001 y 5432.
4. Arrancar las dos piezas (dos ventanas de PowerShell o dos tareas programadas):
   ```powershell
   powershell -ExecutionPolicy Bypass -File deploy\windows\servir.ps1
   powershell -ExecutionPolicy Bypass -File deploy\windows\caddy.ps1
   ```
5. Instalar `root.crt` en cada PC y entrar por `https://pms.hotel.lan`.
6. Arranque automático: Programador de tareas → Crear tarea → «Al iniciar el sistema»,
   «Ejecutar tanto si el usuario inició sesión como si no», acción
   `powershell.exe -ExecutionPolicy Bypass -File C:\Users\Recepcion\cuentas_app\deploy\windows\servir.ps1`
   (y otra igual para `caddy.ps1`).

`servir` se niega a arrancar con `DEBUG=True`, si no pasa `check --deploy` o si se le pide escuchar
en una IP de la red (`--host 0.0.0.0`): sin el proxy no habría HTTPS.

### Puesta en marcha en Debian (Docker)

```bash
mkdir -p /srv/cuentas/{media,private_media,logs} && chown -R 10001:10001 /srv/cuentas/{media,private_media,logs}
docker compose -f deploy/docker/compose.yaml --env-file .env up -d --build
docker compose -f deploy/docker/compose.yaml exec web python manage.py migrate
```

- Solo Caddy publica puertos (80/443, en `CUENTAS_BIND_IP`). PostgreSQL y Gunicorn están en redes
  internas de Docker sin salida a Internet; Django sale a Internet solo por la red `salida` (modelos
  de IA).
- La app corre como usuario sin privilegios (uid 10001), con el sistema de ficheros de solo lectura
  salvo `media/`, `private_media/` y `logs/`, y sin capacidades de Linux.
- El worker de Facturas con IA (`invoice_agent_worker`) es un servicio aparte con la misma imagen.

### IP real del cliente

Detrás del proxy, `REMOTE_ADDR` sería siempre la IP de Caddy. La IP real llega en
`X-Forwarded-For`, pero esa cabecera la puede escribir cualquiera, así que:

- Waitress solo la acepta de `--proxy` (127.0.0.1) y con ella rellena `REMOTE_ADDR`.
- En Docker, `seguridad.middleware.ProxyClientIPMiddleware` hace lo mismo con `TRUSTED_PROXIES` (las
  IPs fijas de Caddy en `compose.yaml`).
- Cualquier `X-Forwarded-*` que no venga del proxy se descarta, incluido `X-Forwarded-Proto`, del
  que depende que Django sepa que la petición es HTTPS.

Así la auditoría de Facturas con IA y el actor del copilot registran la IP del PC que hizo la
petición y no se pueden falsificar.

### Ficheros subidos (`/media/`)

Ya no los sirve `static()` (solo funcionaba con `DEBUG`) sino `seguridad.views.media`, también en
producción:

- No se puede salir de `MEDIA_ROOT` (`../` → 400/404).
- PDF e imágenes se ven en el navegador; cualquier otro tipo (`.html`, `.svg`…) se descarga como
  adjunto, así nunca se ejecuta dentro de la app.
- `Cache-Control: private`: no se guardan en cachés compartidas.
- Es el punto donde se comprobarán los permisos cuando llegue la autorización.

Los originales de Facturas con IA siguen en `private_media/` con URLs firmadas de corta duración.

### Comprobado

Con la pila de Docker y con Waitress + Caddy: HTTP→HTTPS (308), HSTS y CSP presentes, certificado
rechazado sin la CA instalada, CSRF aceptado con token y rechazado sin él o desde otro origen,
`X-Forwarded-For` falsificado ignorado, `.html` subido servido como descarga, `../` rechazado y
Gunicorn/PostgreSQL inaccesibles desde fuera.

---

## PMS Copilot

Cada componente nuevo se revisa por si encaja como herramienta del copilot (function calling):

| Componente | ¿Herramienta? | Motivo |
|---|---|---|
| Ajustes por entorno | No | Configuración interna, sin datos que consultar |
| CSP e informes | No | Por ahora. Cuando haya roles, un `eventos_seguridad` de solo lectura para administración podría resumir el log; con el login desactivado expondría IPs a cualquiera de la LAN |
| Health check | No | Lo consulta el proxy, no una persona |
| Waitress / Caddy / Docker / IP real | No | Infraestructura. La IP real del cliente sí mejora lo que ya registra el copilot (`actor` en `LlamadaHerramienta`) |
| Servir `/media/` | No como herramienta nueva | `documento_factura` ya da enlaces firmados a los originales; cuando haya permisos, la misma comprobación protegerá `/media/` |
