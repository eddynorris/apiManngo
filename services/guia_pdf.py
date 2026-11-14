"""Representación impresa (PDF) de una GRE remitente aceptada por SUNAT.

Usa exactamente los datos firmados (``guia.datos["gre"]``) y el QR que SUNAT
devuelve en el CDR. Imita la estructura de la representación del portal SOL.
"""
from __future__ import annotations

import io
from decimal import Decimal

import segno
from fpdf import FPDF

from services.guia_service import ESTADO_ACEPTADA, GuiaError, numero_completo

MOTIVOS = {"01": "Venta", "04": "Traslado entre establecimientos de la misma empresa"}
TIPOS_DOCUMENTO = {
    "1": "DOCUMENTO NACIONAL DE IDENTIDAD",
    "4": "CARNET DE EXTRANJERÍA",
    "6": "REGISTRO ÚNICO DE CONTRIBUYENTES",
    "7": "PASAPORTE",
}


def _t(texto) -> str:
    """Texto seguro para las fuentes base de PDF (latin-1)."""
    return str(texto if texto is not None else "").encode("latin-1", "replace").decode("latin-1")


def _miles(valor) -> str:
    numero = Decimal(str(valor))
    if numero == numero.to_integral_value():
        return f"{int(numero):,}"
    return f"{numero:,.2f}"


def _fecha(iso: str) -> str:
    anio, mes, dia = iso.split("-")
    return f"{dia}/{mes}/{anio}"


def _qr_png(url: str) -> io.BytesIO:
    buffer = io.BytesIO()
    segno.make(url, error="m").save(buffer, kind="png", scale=4, border=1)
    buffer.seek(0)
    return buffer


def generar_pdf(guia) -> bytes:
    if guia.numero is None or guia.estado != ESTADO_ACEPTADA:
        raise GuiaError("Solo se genera el PDF de una guía aceptada por SUNAT.")
    gre = (guia.datos or {}).get("gre")
    if not gre:
        raise GuiaError("La guía no conserva los datos firmados.")

    emisor, dest, transporte = gre["emisor"], gre["destinatario"], gre["transporte"]
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=9)

    # Cabecera: QR, razón social y recuadro con RUC / tipo / número.
    if guia.qr_url:
        pdf.image(_qr_png(guia.qr_url), x=12, y=10, w=28, h=28)
    pdf.set_xy(45, 12)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(80, 7, _t(emisor["razon_social"]))
    pdf.set_xy(135, 10)
    pdf.set_font("Helvetica", "B", 10)
    pdf.multi_cell(
        63, 5.5,
        _t(f"RUC N° {emisor['ruc']}\nGUÍA DE REMISIÓN ELECTRÓNICA\nREMITENTE\nN° {numero_completo(guia)}"),
        border=1, align="C",
    )
    pdf.set_xy(45, 30)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(38, 5, _t("Fecha y hora de emisión:"))
    pdf.set_font("Helvetica", size=9)
    pdf.cell(60, 5, _t(f"{_fecha(gre['fecha_emision'])} {gre['hora_emision'][:5]}"))

    def fila(etiqueta, valor, y=None):
        if y is not None:
            pdf.set_y(y)
        pdf.set_x(12)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(56, 5.5, _t(etiqueta))
        pdf.set_font("Helvetica", size=9)
        pdf.multi_cell(130, 5.5, _t(valor), align="L")

    pdf.set_y(44)
    fila("Punto de partida:", f"{gre['partida_direccion']} (ubigeo {gre['partida_ubigeo']})")
    fila("Punto de llegada:", f"{gre['llegada_direccion']} (ubigeo {gre['llegada_ubigeo']})")
    fila("Fecha de inicio de traslado:", _fecha(gre["fecha_inicio_traslado"]))
    fila("Motivo de traslado:", MOTIVOS.get(gre["motivo_traslado"], gre["motivo_traslado"]))
    tipo_dest = TIPOS_DOCUMENTO.get(dest["tipo_documento"], f"DOCUMENTO {dest['tipo_documento']}")
    fila("Datos del destinatario:", f"{dest['razon_social']} - {tipo_dest} N° {dest['numero_documento']}")

    # Bienes por transportar.
    pdf.ln(2)
    pdf.set_x(12)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, _t("Bienes por transportar:"), new_x="LMARGIN", new_y="NEXT")
    anchos = (10, 120, 28, 28)
    pdf.set_x(12)
    pdf.set_fill_color(230, 230, 230)
    for ancho, titulo in zip(anchos, ("N°", "Descripción detallada", "Unidad de medida", "Cantidad"),
                             strict=True):
        pdf.cell(ancho, 6, _t(titulo), border=1, align="C", fill=True)
    pdf.ln()
    pdf.set_font("Helvetica", size=8)
    peso_total = Decimal("0")
    for indice, bien in enumerate(gre["bienes"], start=1):
        peso_total += Decimal(bien["peso_kg"])
        pdf.set_x(12)
        pdf.cell(anchos[0], 5.5, str(indice), border=1, align="C")
        pdf.cell(anchos[1], 5.5, _t(bien["descripcion"])[:80], border=1)
        pdf.cell(anchos[2], 5.5, "KILOGRAMO" if bien["unidad"] == "KGM" else _t(bien["unidad"]),
                 border=1, align="C")
        pdf.cell(anchos[3], 5.5, _miles(bien["cantidad"]), border=1, align="R")
        pdf.ln()

    pdf.ln(2)
    pdf.set_font("Helvetica", size=9)
    fila("Unidad de medida del peso bruto:", "KGM")
    fila("Peso bruto total de la carga:", _miles(peso_total))

    pdf.ln(1)
    pdf.set_x(12)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, _t("Datos del traslado:"), new_x="LMARGIN", new_y="NEXT")
    fila("Modalidad de traslado:", "Privado")
    fila("Traslado en vehículos M1 o L:", "NO")

    pdf.ln(1)
    pdf.set_x(12)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, _t("Datos de los vehículos:"), new_x="LMARGIN", new_y="NEXT")
    fila("Principal - Número de placa:", transporte["placa"])
    pdf.set_x(12)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, _t("Datos de los conductores:"), new_x="LMARGIN", new_y="NEXT")
    tipo_cond = TIPOS_DOCUMENTO.get(transporte["conductor_tipo_documento"], "DOCUMENTO")
    nombre_cond = f"{transporte.get('conductor_apellidos') or ''} {transporte['conductor_nombre']}".strip()
    fila("Principal:", f"{nombre_cond} - {tipo_cond} N° {transporte['conductor_numero_documento']}")
    fila("Licencia de conducir:", transporte["conductor_licencia"])

    pdf.set_auto_page_break(auto=False)  # el pie va dentro del margen inferior
    pdf.set_y(-20)
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.multi_cell(
        0, 4,
        _t("Representación impresa de la Guía de Remisión Electrónica Remitente emitida desde el "
           "sistema del contribuyente y aceptada por SUNAT. Verifíquela escaneando el código QR."),
        align="C",
    )
    return bytes(pdf.output())
