"""Qué se puede dar de alta y de qué cuentas del PGC 2007 cuelga cada cosa.

La primera cuenta de "bases" es la que se propone por defecto.
nif: "obligatorio", "opcional" o "no".
"""

TIPOS_ALTA = [
    {
        "clave": "proveedor",
        "etiqueta": "Proveedor",
        "ayuda": "Te vende mercancías o materias primas: alimentos, bebidas, amenities, "
                 "productos de limpieza... Sus facturas van a compras (subgrupo 60).",
        "bases": ["400", "403", "404", "405"],
        "nif": "obligatorio",
    },
    {
        "clave": "acreedor",
        "etiqueta": "Acreedor de servicios",
        "ayuda": "Te presta un servicio: luz, agua, teléfono, mantenimiento, asesoría, "
                 "lavandería externa... Sus facturas van a servicios exteriores (subgrupo 62).",
        "bases": ["410"],
        "nif": "obligatorio",
    },
    {
        "clave": "proveedor_inmovilizado",
        "etiqueta": "Proveedor de inmovilizado",
        "ayuda": "Te vende bienes que se quedan en el hotel más de un año: mobiliario, maquinaria, "
                 "equipos informáticos, obras. Usa la 523 si lo pagas en menos de un año, la 173 si no.",
        "bases": ["523", "173"],
        "nif": "obligatorio",
    },
    {
        "clave": "cliente",
        "etiqueta": "Cliente",
        "ayuda": "Agencias, touroperadores, empresas o particulares a los que facturas.",
        "bases": ["430", "433", "434", "435"],
        "nif": "opcional",
    },
    {
        "clave": "deudor",
        "etiqueta": "Deudor",
        "ayuda": "Te debe dinero por algo que no es la actividad principal, como el alquiler de un local.",
        "bases": ["440"],
        "nif": "opcional",
    },
    {
        "clave": "banco",
        "etiqueta": "Cuenta bancaria",
        "ayuda": "Una subcuenta por cada cuenta del banco. Pon en el nombre el banco y los "
                 "últimos dígitos del IBAN.",
        "bases": ["572", "573", "574", "575"],
        "nif": "no",
    },
    {
        "clave": "caja",
        "etiqueta": "Caja",
        "ayuda": "Efectivo: caja de recepción, caja del bar...",
        "bases": ["570", "571"],
        "nif": "no",
    },
    {
        "clave": "prestamo",
        "etiqueta": "Préstamo o póliza",
        "ayuda": "La parte que vence en más de un año va en la 170 y la de los próximos doce "
                 "meses en la 520: crea las dos con el mismo nombre. Las pólizas de crédito, en la 5201.",
        "bases": ["170", "520", "5201", "171", "521", "174", "524"],
        "nif": "no",
    },
    {
        "clave": "personal",
        "etiqueta": "Personal",
        "ayuda": "Anticipos de nómina (460) y sueldos pendientes de pago (465), si los llevas por empleado.",
        "bases": ["460", "465"],
        "nif": "opcional",
    },
    {
        "clave": "administracion",
        "etiqueta": "Hacienda y Seguridad Social",
        "ayuda": "IVA soportado y repercutido, retenciones de IRPF, Seguridad Social... "
                 "Útil si llevas, por ejemplo, el IVA de cada tipo por separado.",
        "bases": ["472", "477", "4751", "476", "4750", "4700", "4709", "473", "4752", "471"],
        "nif": "no",
    },
    {
        "clave": "gasto",
        "etiqueta": "Cuenta de gasto",
        "ayuda": "Para detallar un gasto: la 628 en electricidad, agua y gas, o la 600 por departamento.",
        "bases": ["600", "601", "602", "607", "621", "622", "623", "624", "625", "626", "627",
                  "628", "629", "631", "640", "641", "642", "649", "662", "669", "678", "681"],
        "nif": "no",
    },
    {
        "clave": "ingreso",
        "etiqueta": "Cuenta de ingreso",
        "ayuda": "Para separar los ingresos por actividad: alojamiento, restaurante, bar, eventos...",
        "bases": ["705", "700", "752", "754", "759", "769", "778"],
        "nif": "no",
    },
    {
        "clave": "inmovilizado",
        "etiqueta": "Elemento de inmovilizado",
        "ayuda": "Para seguir cada bien: el edificio, un ascensor, la maquinaria de cocina... "
                 "Crea también su amortización acumulada (281x) con el mismo nombre.",
        "bases": ["211", "212", "213", "215", "216", "217", "218", "219", "206",
                  "2811", "2812", "2813", "2815", "2816", "2817", "2818", "2819"],
        "nif": "no",
    },
    {
        "clave": "otra",
        "etiqueta": "Otra cuenta del plan",
        "ayuda": "Cualquier cuenta de 3 a 5 dígitos del plan.",
        "bases": None,
        "nif": "opcional",
    },
]

TIPOS_POR_CLAVE = {t["clave"]: t for t in TIPOS_ALTA}