# Cuentas del hotel

Aplicación web en Django + PostgreSQL para centralizar y visualizar los datos de gestión del hotel:
facturas, nóminas, contabilidad, cajas y análisis de beneficios. Funciona en local y en la red de la
oficina; no está desplegada en producción.

## Puesta en marcha (Windows)

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:8000   # accesible desde otros PCs de la red local
```

- Base de datos: PostgreSQL local (`cuentas`), configurada en `mysite/settings.py`.
- El OCR de facturas necesita **Tesseract** con el idioma español (ver `requirements.txt`).
- Los ficheros de datos (CSV, Excel, PDF, exports SQL) **no se suben a git**: están en `.gitignore`.

## Apps

El panel principal (`/`) enlaza con todas las apps.

| App | URL | Para qué sirve |
|---|---|---|
| `nominas` | `/nominas/` | Nóminas mensuales por empleado y departamento |
| `invoice_data` | `/invoices/` | Gestión de facturas: subida de PDF/imágenes y extracción con OCR |
| `cuadro_cuentas` | `/contabilidad/` | Contabilidad por partida doble según el PGC 2007 |
| `analisis` | `/analisis/` | Análisis de beneficios: ingresos frente a proveedores, acreedores y nóminas |
| `cajas` | `/cajas/` | Cajas diarias por punto de venta y otros cobros |
| `invoice_agent` | `/facturas-ia/` | Facturas con IA: extracción con un modelo, validación en Python y aprobación humana |
| `copilot_pms` | `/copilot/` | PMS Copilot: preguntas en lenguaje natural sobre saldos, facturas e informes (solo lectura) |

`albaranes` y `reservas` existen en el repositorio pero todavía no están activadas en `INSTALLED_APPS`.

### Nóminas (`nominas`)
- Importación del desglose mensual de nóminas desde CSV (web o `import_nomina_mes`). El mes y el año
  se detectan del nombre del fichero (`COSTES_MM_AAAA.csv`).
- Importación de los extras históricos desde CSV.
- Tabla por mes con devengado, salario neto, IRPF, SS trabajador, SS empresa, **coste total** y extra.
  **Total = coste total + extras**.
- Departamento y extra editables en la propia tabla; opción de agrupar por departamento con subtotales.
- Página de análisis con gráficos (Chart.js) por departamento y carga impositiva.

### Gestión de facturas (`invoice_data`)
- Subida de facturas (arrastrar y soltar) e importación por lotes desde carpetas
  `proveedores/<departamento>/` y `acreedores/` (`import_invoices`).
- Extracción con OCR (pypdfium2 + Pillow + Tesseract, con limpieza previa de la imagen en OpenCV)
  a un JSON por factura: proveedor, CIF, número, fecha, líneas y totales (`process_invoices`).
- La extracción es genérica: nada específico de un proveedor concreto en el código.
- `check_invoices` comprueba la extracción sobre una carpeta sin tocar la base de datos.
- Exportación de facturas y líneas a CSV.

### Facturas con IA (`invoice_agent`)
- Subida de PDF o imágenes; un worker (`python manage.py invoice_agent_worker`) extrae los datos con un
  modelo de IA (OpenAI o un servidor local compatible) usando salida estructurada.
- Validación en Python: sumas, impuestos (IVA, IGIC, IPSI, recargo, IRPF), NIF y duplicados.
- Revisión en pantalla con el original al lado, evidencias de cada campo, correcciones auditadas y
  aprobación humana. **No contabiliza nada.**
- Configuración en `.env` (ver `.env.example`). Detalles, API y operación en
  [`invoice_agent/README.md`](invoice_agent/README.md).
- Afinado de un modelo propio (Qwen2.5-VL-7B) con las facturas aprobadas, en una GPU alquilada:
  scripts y guía en [`training/`](training/README.md).

### PMS Copilot (`copilot_pms`)
- Preguntas en lenguaje natural («¿saldo del banco a 30 de septiembre?», «PDF de la factura de Makro de
  agosto», «PyG de 2026», «ventas del bar en julio»). El modelo (**Qwen3.5-9B** servido en local con
  vLLM; OpenAI como alternativa) elige qué consultar; **los datos solo salen de herramientas registradas** que usan
  el mismo código que las pantallas.
- Saldos con apertura, movimiento y saldo final, naturaleza deudora/acreedora, ejercicio, entidad y avisos
  (borradores, ejercicio abierto). Enlaces firmados y auditados a los PDF de facturas.
- Solo lectura. Cada consulta queda auditada. Disponibilidad de habitaciones y horarios aún no están
  conectados y el copilot lo dice.
- Configuración en `.env` (`COPILOT_*`). Detalles en [`copilot_pms/README.md`](copilot_pms/README.md).

### Contabilidad (`cuadro_cuentas`)
- Cuadro de cuentas del **PGC 2007** (`cargar_plan_cuentas`) y subcuentas de 8 dígitos
  (`crear_subcuentas_basicas`, más una herramienta para crear subcuentas a medida).
- Libro diario con asientos de partida doble (debe/haber), modelos de asiento y validación de NIF.
- Consultas: hojas de cuentas, extractos y buscador.
- Cuentas anuales: balance, pérdidas y ganancias y estado de ingresos y gastos.

### Análisis de beneficios (`analisis`)
- Importación desde ficheros (`importar_analisis`) de reservas, compras a proveedores, facturas de
  acreedores, IVA y extras de personal. Se valida todo el fichero antes de guardar nada.
- Resumen mensual y anual de ingresos, gastos, márgenes y beneficio, por departamento y categoría,
  con gráficos Chart.js. Las nóminas se toman de la app `nominas`.

### Cajas (`cajas`)
- **Resumen anual**: ventas mensuales de los tres puntos de venta (**bar, comedor y chill out**), con su
  peso sobre el total y un gráfico, y una tabla de **otros cobros** (ecotasa, menús, facturas,
  parking efectivo y parking visa) con su total.
- **Detalle de cada mes**: las mismas dos tablas día a día, con totales.
- **Meses**: se elige el año y uno de los 12 meses; aparece su tabla para completarla o corregirla
  (se aceptan importes con coma o punto decimal; los totales se recalculan al escribir). El mes se
  crea al guardar.
- **Importar Excel**: libro con una hoja por mes («Mayo 26», «abril 26_Tabla 1»…). Se importa un mes o
  todos, sin tocar los meses que ya existen salvo que se marque «Reemplazar». También por comando:

  ```powershell
  python manage.py importar_cajas ruta\cajas_26.xlsx            # todas las hojas mensuales
  python manage.py importar_cajas ruta\cajas_26.xlsx --mes 5    # solo mayo
  python manage.py importar_cajas ruta\cajas_26.xlsx --reemplazar
  ```

  Las columnas se localizan por su nombre (`dia, Bar, Comedor, chill, Ecotasa, Menús, fras, Park,
  Parkvis`); la columna «Total» solo sirve para comprobar que la suma cuadra.

## Convenciones

- Toda la interfaz está en español; los importes se muestran en formato español (1.234,56 €).
- La lógica de importación vive en funciones de servicio reutilizadas por la vista web y por un
  comando de gestión (`python manage.py <comando>`).
- Las reimportaciones se hacen en una transacción (borrar + `bulk_create`): si el fichero tiene
  errores no se guarda nada.
- Los decimales se tratan según la fuente (coma en unos ficheros, punto en otros).
- Gráficos con Chart.js, pasando los datos con `json_script`.
- Tests con datos ficticios: `python manage.py test <app>`.

## Próximos pasos

- Integrar los extractos bancarios con el mismo patrón de importación.
- Panel unificado para comparar albaranes, nóminas, bancos, facturas y cajas.
- Alimentar la contabilidad automáticamente desde facturas y albaranes; completar las cuentas anuales
  (estado de flujos de efectivo y memoria).
- Integración con el motor de reservas Neobookings (pendiente de credenciales de la API).
- Probar un LLM en local como proveedor de `invoice_agent` (misma API, cambiando `INVOICE_AGENT_BASE_URL`).
- Contabilizar en `cuadro_cuentas` las facturas aprobadas en `invoice_agent`.
- PMS Copilot: herramientas de disponibilidad de habitaciones y horarios del personal cuando existan esos datos.
