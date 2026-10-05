"""Modelos de cuentas anuales del PGC 2007 (tercera parte), con el «Nº de cuentas» de cada partida.

Cada partida suma los saldos de las cuentas indicadas. Las cuentas entre paréntesis son
correctoras o restan (amortizaciones, deterioros, capital no exigido...); el asterisco
indica que el saldo puede ser de cualquier signo. Como los importes se calculan con el
saldo real de cada cuenta, los paréntesis y asteriscos solo se usan para mostrar la
columna «Nº cuentas» igual que en el PGC: el signo sale solo.
"""
from dataclasses import dataclass, field


@dataclass
class Partida:
    ref: str                        # identificador único dentro del estado
    texto: str
    cuentas_texto: str = ""         # tal como figura en el PGC: "201, (2801), (2901)"
    hijos: list = field(default_factory=list)
    formula: tuple = ()             # refs de otras partidas que se suman (resultados de la PyG)
    siempre: bool = False           # se muestra aunque esté a cero (totales)
    resultado_ejercicio: bool = False  # en el balance, suma el resultado de los grupos 6 y 7

    @property
    def cuentas(self):
        return tuple(c.strip().strip("()*").strip() for c in self.cuentas_texto.split(",") if c.strip())


def P(ref, texto, cuentas="", *hijos, **kw):
    return Partida(ref, texto, cuentas, list(hijos), **kw)


@dataclass
class Seccion:
    titulo: str
    signo: str                      # "deudor" (activo): Debe - Haber; "acreedor": Haber - Debe
    partidas: list
    total: str = ""                 # texto de la fila de total (balance)


@dataclass
class Estado:
    clave: str
    titulo: str
    secciones: list
    grupos: tuple                   # grupos del PGC que se presentan en este estado


# ===================================================================== BALANCE NORMAL

BALANCE_NORMAL = Estado("balance", "Balance", grupos=("1", "2", "3", "4", "5"), secciones=[
    Seccion("Activo", "deudor", total="TOTAL ACTIVO (A + B)", partidas=[
        P("A", "A) ACTIVO NO CORRIENTE", "",
          P("A.I", "I. Inmovilizado intangible.", "",
            P("A.I.1", "1. Desarrollo.", "201, (2801), (2901)"),
            P("A.I.2", "2. Concesiones.", "202, (2802), (2902)"),
            P("A.I.3", "3. Patentes, licencias, marcas y similares.", "203, (2803), (2903)"),
            P("A.I.4", "4. Fondo de comercio.", "204, (2804)"),
            P("A.I.5", "5. Aplicaciones informáticas.", "206, (2806), (2906)"),
            P("A.I.6", "6. Otro inmovilizado intangible.", "205, 209, (2805), (2905)")),
          P("A.II", "II. Inmovilizado material.", "",
            P("A.II.1", "1. Terrenos y construcciones.", "210, 211, (2811), (2910), (2911)"),
            P("A.II.2", "2. Instalaciones técnicas y otro inmovilizado material.",
              "212, 213, 214, 215, 216, 217, 218, 219, (2812), (2813), (2814), (2815), (2816), (2817), "
              "(2818), (2819), (2912), (2913), (2914), (2915), (2916), (2917), (2918), (2919)"),
            P("A.II.3", "3. Inmovilizado en curso y anticipos.", "23")),
          P("A.III", "III. Inversiones inmobiliarias.", "",
            P("A.III.1", "1. Terrenos.", "220, (2920)"),
            P("A.III.2", "2. Construcciones.", "221, (282), (2921)")),
          P("A.IV", "IV. Inversiones en empresas del grupo y asociadas a largo plazo.", "",
            P("A.IV.1", "1. Instrumentos de patrimonio.", "2403, 2404, (2493), (2494), (2933), (2934)"),
            P("A.IV.2", "2. Créditos a empresas.", "2423, 2424, (2953), (2954)"),
            P("A.IV.3", "3. Valores representativos de deuda.", "2413, 2414, (2943), (2944)"),
            P("A.IV.4", "4. Derivados."),
            P("A.IV.5", "5. Otros activos financieros.")),
          P("A.V", "V. Inversiones financieras a largo plazo.", "",
            P("A.V.1", "1. Instrumentos de patrimonio.", "2405, (2495), 250, (259), (2935), (2936)"),
            P("A.V.2", "2. Créditos a terceros.", "2425, 252, 253, 254, (2955), (298)"),
            P("A.V.3", "3. Valores representativos de deuda.", "2415, 251, (2945), (297)"),
            P("A.V.4", "4. Derivados.", "255"),
            P("A.V.5", "5. Otros activos financieros.", "258, 26")),
          P("A.VI", "VI. Activos por impuesto diferido.", "474"),
          siempre=True),
        P("B", "B) ACTIVO CORRIENTE", "",
          P("B.I", "I. Activos no corrientes mantenidos para la venta.", "580, 581, 582, 583, 584, (599)"),
          P("B.II", "II. Existencias.", "",
            P("B.II.1", "1. Comerciales.", "30, (390)"),
            P("B.II.2", "2. Materias primas y otros aprovisionamientos.", "31, 32, (391), (392)"),
            P("B.II.3", "3. Productos en curso.", "33, 34, (393), (394)"),
            P("B.II.4", "4. Productos terminados.", "35, (395)"),
            P("B.II.5", "5. Subproductos, residuos y materiales recuperados.", "36, (396)"),
            P("B.II.6", "6. Anticipos a proveedores.", "407")),
          P("B.III", "III. Deudores comerciales y otras cuentas a cobrar.", "",
            P("B.III.1", "1. Clientes por ventas y prestaciones de servicios.",
              "430, 431, 432, 435, 436, (437), (490), (4935)"),
            P("B.III.2", "2. Clientes, empresas del grupo y asociadas.", "433, 434, (4933), (4934)"),
            P("B.III.3", "3. Deudores varios.", "44"),
            P("B.III.4", "4. Personal.", "460, 544"),
            P("B.III.5", "5. Activos por impuesto corriente.", "4709"),
            P("B.III.6", "6. Otros créditos con las Administraciones Públicas.", "4700, 4708, 471, 472"),
            P("B.III.7", "7. Accionistas (socios) por desembolsos exigidos.", "5580")),
          P("B.IV", "IV. Inversiones en empresas del grupo y asociadas a corto plazo.", "",
            P("B.IV.1", "1. Instrumentos de patrimonio.", "5303, 5304, (5393), (5394), (5933), (5934)"),
            P("B.IV.2", "2. Créditos a empresas.", "5323, 5324, 5343, 5344, (5953), (5954)"),
            P("B.IV.3", "3. Valores representativos de deuda.", "5313, 5314, 5333, 5334, (5943), (5944)"),
            P("B.IV.4", "4. Derivados."),
            P("B.IV.5", "5. Otros activos financieros.", "5353, 5354, 5523, 5524")),
          P("B.V", "V. Inversiones financieras a corto plazo.", "",
            P("B.V.1", "1. Instrumentos de patrimonio.", "5305, 540, (5395), (549), (5935), (5936)"),
            P("B.V.2", "2. Créditos a empresas.", "5325, 5345, 542, 543, 547, (5955), (598)"),
            P("B.V.3", "3. Valores representativos de deuda.", "5315, 5335, 541, 546, (5945), (597)"),
            P("B.V.4", "4. Derivados.", "5590, 5593"),
            P("B.V.5", "5. Otros activos financieros.", "5355, 545, 548, 551, 5525, 565, 566")),
          P("B.VI", "VI. Periodificaciones a corto plazo.", "480, 567"),
          P("B.VII", "VII. Efectivo y otros activos líquidos equivalentes.", "",
            P("B.VII.1", "1. Tesorería.", "570, 571, 572, 573, 574, 575"),
            P("B.VII.2", "2. Otros activos líquidos equivalentes.", "576")),
          siempre=True),
    ]),
    Seccion("Patrimonio neto y pasivo", "acreedor", total="TOTAL PATRIMONIO NETO Y PASIVO (A + B + C)", partidas=[
        P("PN", "A) PATRIMONIO NETO", "",
          P("PN.1", "A-1) Fondos propios.", "",
            P("PN.1.I", "I. Capital.", "",
              P("PN.1.I.1", "1. Capital escriturado.", "100, 101, 102"),
              P("PN.1.I.2", "2. (Capital no exigido).", "(1030), (1040)")),
            P("PN.1.II", "II. Prima de emisión.", "110"),
            P("PN.1.III", "III. Reservas.", "",
              P("PN.1.III.1", "1. Legal y estatutarias.", "112, 1141"),
              P("PN.1.III.2", "2. Otras reservas.", "113, 1140, 1142, 1143, 1144, 115, 119")),
            P("PN.1.IV", "IV. (Acciones y participaciones en patrimonio propias).", "(108), (109)"),
            P("PN.1.V", "V. Resultados de ejercicios anteriores.", "",
              P("PN.1.V.1", "1. Remanente.", "120"),
              P("PN.1.V.2", "2. (Resultados negativos de ejercicios anteriores).", "(121)")),
            P("PN.1.VI", "VI. Otras aportaciones de socios.", "118"),
            P("PN.1.VII", "VII. Resultado del ejercicio.", "129", resultado_ejercicio=True),
            P("PN.1.VIII", "VIII. (Dividendo a cuenta).", "(557)"),
            P("PN.1.IX", "IX. Otros instrumentos de patrimonio neto.", "111")),
          P("PN.2", "A-2) Ajustes por cambios de valor.", "",
            P("PN.2.I", "I. Activos financieros a valor razonable con cambios en el patrimonio neto.", "133"),
            P("PN.2.II", "II. Operaciones de cobertura.", "1340"),
            P("PN.2.III", "III. Otros.", "137")),
          P("PN.3", "A-3) Subvenciones, donaciones y legados recibidos.", "130, 131, 132"),
          siempre=True),
        P("PNC", "B) PASIVO NO CORRIENTE", "",
          P("PNC.I", "I. Provisiones a largo plazo.", "",
            P("PNC.I.1", "1. Obligaciones por prestaciones a largo plazo al personal.", "140"),
            P("PNC.I.2", "2. Actuaciones medioambientales.", "145"),
            P("PNC.I.3", "3. Provisiones por reestructuración.", "146"),
            P("PNC.I.4", "4. Otras provisiones.", "141, 142, 143, 147")),
          P("PNC.II", "II. Deudas a largo plazo.", "",
            P("PNC.II.1", "1. Obligaciones y otros valores negociables.", "177, 178, 179"),
            P("PNC.II.2", "2. Deudas con entidades de crédito.", "1605, 170"),
            P("PNC.II.3", "3. Acreedores por arrendamiento financiero.", "1625, 174"),
            P("PNC.II.4", "4. Derivados.", "176"),
            P("PNC.II.5", "5. Otros pasivos financieros.", "1615, 1635, 171, 172, 173, 175, 180, 185, 189")),
          P("PNC.III", "III. Deudas con empresas del grupo y asociadas a largo plazo.",
            "1603, 1604, 1613, 1614, 1623, 1624, 1633, 1634"),
          P("PNC.IV", "IV. Pasivos por impuesto diferido.", "479"),
          P("PNC.V", "V. Periodificaciones a largo plazo.", "181"),
          siempre=True),
        P("PC", "C) PASIVO CORRIENTE", "",
          P("PC.I", "I. Pasivos vinculados con activos no corrientes mantenidos para la venta.",
            "585, 586, 587, 588, 589"),
          P("PC.II", "II. Provisiones a corto plazo.", "499, 529"),
          P("PC.III", "III. Deudas a corto plazo.", "",
            P("PC.III.1", "1. Obligaciones y otros valores negociables.", "500, 501, 505, 506"),
            P("PC.III.2", "2. Deudas con entidades de crédito.", "5105, 520, 527"),
            P("PC.III.3", "3. Acreedores por arrendamiento financiero.", "5125, 524"),
            P("PC.III.4", "4. Derivados.", "5595, 5598"),
            P("PC.III.5", "5. Otros pasivos financieros.",
              "(1034), (1044), (190), (192), 194, 509, 5115, 5135, 5145, 521, 522, 523, 525, 526, 528, "
              "551, 5525, 555, 5565, 5566, 560, 561, 569")),
          P("PC.IV", "IV. Deudas con empresas del grupo y asociadas a corto plazo.",
            "5103, 5104, 5113, 5114, 5123, 5124, 5133, 5134, 5143, 5144, 5523, 5524, 5563, 5564"),
          P("PC.V", "V. Acreedores comerciales y otras cuentas a pagar.", "",
            P("PC.V.1", "1. Proveedores.", "400, 401, 405, (406)"),
            P("PC.V.2", "2. Proveedores, empresas del grupo y asociadas.", "403, 404"),
            P("PC.V.3", "3. Acreedores varios.", "41"),
            P("PC.V.4", "4. Personal (remuneraciones pendientes de pago).", "465, 466"),
            P("PC.V.5", "5. Pasivos por impuesto corriente.", "4752"),
            P("PC.V.6", "6. Otras deudas con las Administraciones Públicas.", "4750, 4751, 4758, 476, 477"),
            P("PC.V.7", "7. Anticipos de clientes.", "438")),
          P("PC.VI", "VI. Periodificaciones a corto plazo.", "485, 568"),
          siempre=True),
    ]),
])

# ===================================================================== BALANCE ABREVIADO

BALANCE_ABREVIADO = Estado("balance", "Balance abreviado", grupos=("1", "2", "3", "4", "5"), secciones=[
    Seccion("Activo", "deudor", total="TOTAL ACTIVO (A + B)", partidas=[
        P("A", "A) ACTIVO NO CORRIENTE", "",
          P("A.I", "I. Inmovilizado intangible.", "20, (280), (290)"),
          P("A.II", "II. Inmovilizado material.", "21, (281), (291), 23"),
          P("A.III", "III. Inversiones inmobiliarias.", "22, (282), (292)"),
          P("A.IV", "IV. Inversiones en empresas del grupo y asociadas a largo plazo.",
            "2403, 2404, 2413, 2414, 2423, 2424, (2493), (2494), (2933), (2934), (2943), (2944), (2953), (2954)"),
          P("A.V", "V. Inversiones financieras a largo plazo.",
            "2405, 2415, 2425, (2495), 250, 251, 252, 253, 254, 255, 257, 258, (259), 26, (2935), (2936), "
            "(2945), (2955), (297), (298)"),
          P("A.VI", "VI. Activos por impuesto diferido.", "474"),
          siempre=True),
        P("B", "B) ACTIVO CORRIENTE", "",
          P("B.I", "I. Activos no corrientes mantenidos para la venta.", "580, 581, 582, 583, 584, (599)"),
          P("B.II", "II. Existencias.", "30, 31, 32, 33, 34, 35, 36, (39), 407"),
          P("B.III", "III. Deudores comerciales y otras cuentas a cobrar.", "",
            P("B.III.1", "1. Clientes por ventas y prestaciones de servicios.",
              "430, 431, 432, 433, 434, 435, 436, (437), (490), (493)"),
            P("B.III.2", "2. Accionistas (socios) por desembolsos exigidos.", "5580"),
            P("B.III.3", "3. Otros deudores.", "44, 460, 470, 471, 472, 544")),
          P("B.IV", "IV. Inversiones en empresas del grupo y asociadas a corto plazo.",
            "5303, 5304, 5313, 5314, 5323, 5324, 5333, 5334, 5343, 5344, 5353, 5354, (5393), (5394), 5523, "
            "5524, (5933), (5934), (5943), (5944), (5953), (5954)"),
          P("B.V", "V. Inversiones financieras a corto plazo.",
            "5305, 5315, 5325, 5335, 5345, 5355, (5395), 540, 541, 542, 543, 545, 546, 547, 548, (549), 551, "
            "5525, 5590, 5593, 565, 566, (5935), (5936), (5945), (5955), (597), (598)"),
          P("B.VI", "VI. Periodificaciones a corto plazo.", "480, 567"),
          P("B.VII", "VII. Efectivo y otros activos líquidos equivalentes.", "57"),
          siempre=True),
    ]),
    Seccion("Patrimonio neto y pasivo", "acreedor", total="TOTAL PATRIMONIO NETO Y PASIVO (A + B + C)", partidas=[
        P("PN", "A) PATRIMONIO NETO", "",
          P("PN.1", "A-1) Fondos propios.", "",
            P("PN.1.I", "I. Capital.", "",
              P("PN.1.I.1", "1. Capital escriturado.", "100, 101, 102"),
              P("PN.1.I.2", "2. (Capital no exigido).", "(1030), (1040)")),
            P("PN.1.II", "II. Prima de emisión.", "110"),
            P("PN.1.III", "III. Reservas.", "112, 113, 114, 115, 119"),
            P("PN.1.IV", "IV. (Acciones y participaciones en patrimonio propias).", "(108), (109)"),
            P("PN.1.V", "V. Resultados de ejercicios anteriores.", "120, (121)"),
            P("PN.1.VI", "VI. Otras aportaciones de socios.", "118"),
            P("PN.1.VII", "VII. Resultado del ejercicio.", "129", resultado_ejercicio=True),
            P("PN.1.VIII", "VIII. (Dividendo a cuenta).", "(557)"),
            P("PN.1.IX", "IX. Otros instrumentos de patrimonio neto.", "111")),
          P("PN.2", "A-2) Ajustes por cambios de valor.", "133, 1340, 137"),
          P("PN.3", "A-3) Subvenciones, donaciones y legados recibidos.", "130, 131, 132"),
          siempre=True),
        P("PNC", "B) PASIVO NO CORRIENTE", "",
          P("PNC.I", "I. Provisiones a largo plazo.", "14"),
          P("PNC.II", "II. Deudas a largo plazo.", "",
            P("PNC.II.1", "1. Deudas con entidades de crédito.", "1605, 170"),
            P("PNC.II.2", "2. Acreedores por arrendamiento financiero.", "1625, 174"),
            P("PNC.II.3", "3. Otras deudas a largo plazo.",
              "1615, 1635, 171, 172, 173, 175, 176, 177, 178, 179, 180, 185, 189")),
          P("PNC.III", "III. Deudas con empresas del grupo y asociadas a largo plazo.",
            "1603, 1604, 1613, 1614, 1623, 1624, 1633, 1634"),
          P("PNC.IV", "IV. Pasivos por impuesto diferido.", "479"),
          P("PNC.V", "V. Periodificaciones a largo plazo.", "181"),
          siempre=True),
        P("PC", "C) PASIVO CORRIENTE", "",
          P("PC.I", "I. Pasivos vinculados con activos no corrientes mantenidos para la venta.",
            "585, 586, 587, 588, 589"),
          P("PC.II", "II. Provisiones a corto plazo.", "499, 529"),
          P("PC.III", "III. Deudas a corto plazo.", "",
            P("PC.III.1", "1. Deudas con entidades de crédito.", "5105, 520, 527"),
            P("PC.III.2", "2. Acreedores por arrendamiento financiero.", "5125, 524"),
            P("PC.III.3", "3. Otras deudas a corto plazo.",
              "(1034), (1044), (190), (192), 194, 500, 501, 505, 506, 509, 5115, 5135, 5145, 521, 522, 523, "
              "525, 526, 528, 551, 5525, 555, 5565, 5566, 5595, 5598, 560, 561, 569")),
          P("PC.IV", "IV. Deudas con empresas del grupo y asociadas a corto plazo.",
            "5103, 5104, 5113, 5114, 5123, 5124, 5133, 5134, 5143, 5144, 5523, 5524, 5563, 5564"),
          P("PC.V", "V. Acreedores comerciales y otras cuentas a pagar.", "",
            P("PC.V.1", "1. Proveedores.", "400, 401, 403, 404, 405, (406)"),
            P("PC.V.2", "2. Otros acreedores.", "41, 438, 465, 466, 475, 476, 477")),
          P("PC.VI", "VI. Periodificaciones a corto plazo.", "485, 568"),
          siempre=True),
    ]),
])

# ===================================================================== PÉRDIDAS Y GANANCIAS

# Norma 7ª.9: los ingresos y gastos excepcionales y significativos (inundaciones, incendios,
# multas...) van en una partida «Otros resultados» dentro del resultado de explotación.
OTROS_RESULTADOS = "(678), 778"

PYG_NORMAL = Estado("pyg", "Cuenta de pérdidas y ganancias", grupos=("6", "7"), secciones=[
    Seccion("", "acreedor", partidas=[
        P("1", "1. Importe neto de la cifra de negocios.", "",
          P("1a", "a) Ventas.", "700, 701, 702, 703, 704, (706), (708), (709)"),
          P("1b", "b) Prestaciones de servicios.", "705")),
        P("2", "2. Variación de existencias de productos terminados y en curso de fabricación.",
          "(6930), 71*, 7930"),
        P("3", "3. Trabajos realizados por la empresa para su activo.", "73"),
        P("4", "4. Aprovisionamientos.", "",
          P("4a", "a) Consumo de mercaderías.", "(600), 6060, 6080, 6090, 610*"),
          P("4b", "b) Consumo de materias primas y otras materias consumibles.",
            "(601), (602), 6061, 6062, 6081, 6082, 6091, 6092, 611*, 612*"),
          P("4c", "c) Trabajos realizados por otras empresas.", "(607)"),
          P("4d", "d) Deterioro de mercaderías, materias primas y otros aprovisionamientos.",
            "(6931), (6932), (6933), 7931, 7932, 7933")),
        P("5", "5. Otros ingresos de explotación.", "",
          P("5a", "a) Ingresos accesorios y otros de gestión corriente.", "75"),
          P("5b", "b) Subvenciones de explotación incorporadas al resultado del ejercicio.", "740, 747")),
        P("6", "6. Gastos de personal.", "",
          P("6a", "a) Sueldos, salarios y asimilados.", "(640), (641), (6450)"),
          P("6b", "b) Cargas sociales.", "(642), (643), (649)"),
          P("6c", "c) Provisiones.", "(644), (6457), 7950, 7957")),
        P("7", "7. Otros gastos de explotación.", "",
          P("7a", "a) Servicios exteriores.", "(62)"),
          P("7b", "b) Tributos.", "(631), (634), 636, 639"),
          P("7c", "c) Pérdidas, deterioro y variación de provisiones por operaciones comerciales.",
            "(650), (694), (695), 794, 7954"),
          P("7d", "d) Otros gastos de gestión corriente.", "(651), (659)")),
        P("8", "8. Amortización del inmovilizado.", "(68)"),
        P("9", "9. Imputación de subvenciones de inmovilizado no financiero y otras.", "746"),
        P("10", "10. Excesos de provisiones.", "7951, 7952, 7955, 7956"),
        P("11", "11. Deterioro y resultado por enajenaciones del inmovilizado.", "",
          P("11a", "a) Deterioros y pérdidas.", "(690), (691), (692), 790, 791, 792"),
          P("11b", "b) Resultados por enajenaciones y otras.", "(670), (671), (672), 770, 771, 772")),
        P("OR", "Otros resultados.", OTROS_RESULTADOS),
        P("A1", "A.1) RESULTADO DE EXPLOTACIÓN (1+2+3+4+5+6+7+8+9+10+11)",
          formula=("1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "OR"), siempre=True),
        P("12", "12. Ingresos financieros.", "",
          P("12a", "a) De participaciones en instrumentos de patrimonio.", "",
            P("12a1", "a1) En empresas del grupo y asociadas.", "7600, 7601"),
            P("12a2", "a2) En terceros.", "7602, 7603")),
          P("12b", "b) De valores negociables y otros instrumentos financieros.", "",
            P("12b1", "b1) De empresas del grupo y asociadas.", "7610, 7611, 76200, 76201, 76210, 76211"),
            P("12b2", "b2) De terceros.", "7612, 7613, 76202, 76203, 76212, 76213, 767, 769"))),
        P("13", "13. Gastos financieros.", "",
          P("13a", "a) Por deudas con empresas del grupo y asociadas.",
            "(6610), (6611), (6615), (6616), (6620), (6621), (6640), (6641), (6650), (6651), (6654), (6655)"),
          P("13b", "b) Por deudas con terceros.",
            "(6612), (6613), (6617), (6618), (6622), (6623), (6624), (6642), (6643), (6652), (6653), "
            "(6656), (6657), (669)"),
          P("13c", "c) Por actualización de provisiones.", "(660)")),
        P("14", "14. Variación de valor razonable en instrumentos financieros.", "",
          P("14a", "a) Valor razonable con cambios en pérdidas y ganancias.",
            "(6630), (6631), (6633), (6634), 7630, 7631, 7633, 7634"),
          P("14b", "b) Transferencia de ajustes de valor razonable con cambios en el patrimonio neto.",
            "(6632), 7632")),
        P("15", "15. Diferencias de cambio.", "(668), 768"),
        P("16", "16. Deterioro y resultado por enajenaciones de instrumentos financieros.", "",
          P("16a", "a) Deterioros y pérdidas.", "(696), (697), (698), (699), 796, 797, 798, 799"),
          P("16b", "b) Resultados por enajenaciones y otras.", "(666), (667), (673), (675), 766, 773, 775")),
        P("A2", "A.2) RESULTADO FINANCIERO (12+13+14+15+16)",
          formula=("12", "13", "14", "15", "16"), siempre=True),
        P("A3", "A.3) RESULTADO ANTES DE IMPUESTOS (A.1+A.2)", formula=("A1", "A2"), siempre=True),
        P("17", "17. Impuestos sobre beneficios.", "(6300)*, 6301*, (633), 638"),
        P("A4", "A.4) RESULTADO DEL EJERCICIO PROCEDENTE DE OPERACIONES CONTINUADAS (A.3+17)",
          formula=("A3", "17"), siempre=True),
        P("18", "18. Resultado del ejercicio procedente de operaciones interrumpidas neto de impuestos."),
        P("A5", "A.5) RESULTADO DEL EJERCICIO (A.4+18)", formula=("A4", "18"), siempre=True),
    ]),
])

PYG_ABREVIADA = Estado("pyg", "Cuenta de pérdidas y ganancias abreviada", grupos=("6", "7"), secciones=[
    Seccion("", "acreedor", partidas=[
        P("1", "1. Importe neto de la cifra de negocios.",
          "700, 701, 702, 703, 704, 705, (706), (708), (709)"),
        P("2", "2. Variación de existencias de productos terminados y en curso de fabricación.",
          "(6930), 71*, 7930"),
        P("3", "3. Trabajos realizados por la empresa para su activo.", "73"),
        P("4", "4. Aprovisionamientos.",
          "(600), (601), (602), 606, (607), 608, 609, 61*, (6931), (6932), (6933), 7931, 7932, 7933"),
        P("5", "5. Otros ingresos de explotación.", "740, 747, 75"),
        P("6", "6. Gastos de personal.", "(64), 7950, 7957"),
        P("7", "7. Otros gastos de explotación.",
          "(62), (631), (634), 636, 639, (65), (694), (695), 794, 7954"),
        P("8", "8. Amortización del inmovilizado.", "(68)"),
        P("9", "9. Imputación de subvenciones de inmovilizado no financiero y otras.", "746"),
        P("10", "10. Excesos de provisiones.", "7951, 7952, 7955, 7956"),
        P("11", "11. Deterioro y resultado por enajenaciones del inmovilizado.",
          "(670), (671), (672), (690), (691), (692), 770, 771, 772, 790, 791, 792"),
        P("OR", "Otros resultados.", OTROS_RESULTADOS),
        P("A", "A) RESULTADO DE EXPLOTACIÓN (1+2+3+4+5+6+7+8+9+10+11)",
          formula=("1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "OR"), siempre=True),
        P("12", "12. Ingresos financieros.", "760, 761, 762, 767, 769"),
        P("13", "13. Gastos financieros.", "(660), (661), (662), (664), (665), (669)"),
        P("14", "14. Variación de valor razonable en instrumentos financieros.", "(663), 763"),
        P("15", "15. Diferencias de cambio.", "(668), 768"),
        P("16", "16. Deterioro y resultado por enajenaciones de instrumentos financieros.",
          "(666), (667), (673), (675), (696), (697), (698), (699), 766, 773, 775, 796, 797, 798, 799"),
        P("B", "B) RESULTADO FINANCIERO (12+13+14+15+16)", formula=("12", "13", "14", "15", "16"), siempre=True),
        P("C", "C) RESULTADO ANTES DE IMPUESTOS (A+B)", formula=("A", "B"), siempre=True),
        P("17", "17. Impuestos sobre beneficios.", "(6300)*, 6301*, (633), 638"),
        P("D", "D) RESULTADO DEL EJERCICIO (C+17)", formula=("C", "17"), siempre=True),
    ]),
])

# ============================================ ESTADO DE INGRESOS Y GASTOS RECONOCIDOS (ECPN, parte A)

def _ecpn_a(normal):
    instrumentos = (
        [P("I", "I. Por valoración de instrumentos financieros.", "",
           P("I.1", "1. Activos financieros a valor razonable con cambios en el patrimonio neto.",
             "(800), (89), 900, 991, 992"),
           P("I.2", "2. Otros ingresos/gastos."))]
        if normal else
        [P("I", "I. Por valoración de instrumentos financieros.", "(800), (89), 900, 991, 992")]
    )
    transferencias = (
        [P("VI", "VI. Por valoración de instrumentos financieros.", "",
           P("VI.1", "1. Activos financieros a valor razonable con cambios en el patrimonio neto.",
             "(802), 902, 993, 994"),
           P("VI.2", "2. Otros ingresos/gastos."))]
        if normal else
        [P("VI", "VI. Por valoración de instrumentos financieros.", "(802), 902, 993, 994")]
    )
    return Estado("ecpn", "Estado de ingresos y gastos reconocidos" + ("" if normal else " (abreviado)"),
                  grupos=("8", "9"), secciones=[Seccion("", "acreedor", partidas=[
        P("RA", "A) Resultado de la cuenta de pérdidas y ganancias.", siempre=True),
        P("B", "B) Total ingresos y gastos imputados directamente en el patrimonio neto (I+II+III+IV+V)", "",
          *instrumentos,
          P("II", "II. Por coberturas de flujos de efectivo.", "(810), 910"),
          P("III", "III. Subvenciones, donaciones y legados recibidos.", "94"),
          P("IV", "IV. Por ganancias y pérdidas actuariales y otros ajustes.", "(85), 95"),
          P("V", "V. Efecto impositivo.", "(8300)*, 8301*, (833), 834, 835, 838"),
          siempre=True),
        P("C", "C) Total transferencias a la cuenta de pérdidas y ganancias (VI+VII+VIII+IX)", "",
          *transferencias,
          P("VII", "VII. Por coberturas de flujos de efectivo.", "(812), 912"),
          P("VIII", "VIII. Subvenciones, donaciones y legados recibidos.", "(84)"),
          P("IX", "IX. Efecto impositivo.", "8301*, (836), (837)"),
          siempre=True),
        P("TOTAL", "TOTAL DE INGRESOS Y GASTOS RECONOCIDOS (A + B + C)", formula=("RA", "B", "C"), siempre=True),
    ])])


ECPN_NORMAL = _ecpn_a(normal=True)
ECPN_ABREVIADO = _ecpn_a(normal=False)

MODELOS = {
    "normal": {"balance": BALANCE_NORMAL, "pyg": PYG_NORMAL, "ecpn": ECPN_NORMAL},
    "abreviado": {"balance": BALANCE_ABREVIADO, "pyg": PYG_ABREVIADA, "ecpn": ECPN_ABREVIADO},
}
# Partida de la PyG con el resultado del ejercicio y con la cifra de negocios, según el modelo
RESULTADO_PYG = {"normal": "A5", "abreviado": "D"}
CIFRA_NEGOCIOS = "1"