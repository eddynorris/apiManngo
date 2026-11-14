#!/usr/bin/env python3
"""Planificador dry-run para una carga historica de Manngo.

Modulo puro: no importa la aplicacion, ORM, servicios de negocio, dotenv,
clientes HTTP ni base de datos. Lee un JSON explicito, valida minimos y
genera un plan legible. Nunca escribe en una BD ni marca una operacion como
realizada. La repeticion es determinista y no tiene efectos secundarios.

Uso:
    python scripts/prepare_historical_load.py --input carga.json
    python scripts/prepare_historical_load.py --input carga.json --output plan.json

El archivo de salida, si se indica, es solo el plan JSON. No existe ninguna
opcion 'apply' ni conexion a base de datos.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCHEMA = "manngo.historical_load.plan.v1"
VALID_MODES = ("initial_regularization", "chronological_reconstruction")
VALID_TYPES = (
    "initial_balance",
    "purchase",
    "production",
    "distribution",
    "sale",
)
EFFECTS = {
    "initial_balance": {
        "stock": "entrada (saldo inicial documentado)",
        "gasto": "no (no es una compra del periodo)",
        "ingreso": "no",
        "neto_global": "incrementa existencias",
    },
    "purchase": {
        "stock": "entrada",
        "gasto": "si (costo_total una sola vez)",
        "ingreso": "no",
        "neto_global": "incrementa existencias y gasto",
    },
    "production": {
        "stock": "transforma (consume insumos/materia prima y da salida)",
        "gasto": "no (no duplicar el costo de la compra)",
        "ingreso": "no",
        "neto_global": "transforma, no duplica",
    },
    "distribution": {
        "stock": "mueve entre almacenes",
        "gasto": "no",
        "ingreso": "no",
        "neto_global": "neutro entre almacenes",
    },
    "sale": {
        "stock": "salida",
        "gasto": "no",
        "ingreso": "cantidad * precio_unitario",
        "neto_global": "disminuye existencias",
    },
}
PENDING_NOTE = (
    "Los datos incompletos quedan pendientes. No se crean clientes, precios, "
    "pesos, fechas ni cantidades por defecto."
)


class PlanError(Exception):
    """Error de lectura del JSON de entrada, no de validacion de negocio."""


def dec_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _is_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_date(value):
    if not _is_text(value):
        return None
    text = value.strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return None
    if parsed.isoformat() != text:
        return None
    return parsed


def _parse_decimal(value, *, allow_zero=False):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    if allow_zero:
        return number if number >= 0 else None
    return number if number > 0 else None


def _catalog_map(catalogs, key):
    raw = catalogs.get(key)
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, list):
        result = {}
        for item in raw:
            if isinstance(item, str) and item.strip():
                result[item.strip()] = {}
            elif isinstance(item, dict):
                item_id = item.get("id") or item.get("codigo")
                if _is_text(item_id):
                    result[item_id.strip()] = {
                        k: v for k, v in item.items() if k not in ("id", "codigo")
                    }
        return result
    return None


def _add_error(errors, code, message, operation_id=None, field=None):
    entry = {"codigo": code, "mensaje": message}
    if operation_id:
        entry["operation_id"] = operation_id
    if field:
        entry["campo"] = field
    errors.append(entry)


def _add_warning(warnings, code, message, operation_id=None, field=None):
    entry = {"codigo": code, "mensaje": message}
    if operation_id:
        entry["operation_id"] = operation_id
    if field:
        entry["campo"] = field
    warnings.append(entry)


def _check_almacen(value, almacenes, errors, operation_id, field="almacen"):
    if not _is_text(value):
        _add_error(errors, "ALMACEN_FALTANTE", "Falta el almacen.", operation_id, field)
        return None
    value = value.strip()
    if value not in almacenes:
        _add_error(
            errors,
            "REFERENCIA_CATALOGO_DESCONOCIDA",
            f"Almacen '{value}' no existe en los catalogos suministrados.",
            operation_id,
            field,
        )
        return None
    return value


def _check_item(raw, prefix, presentaciones, errors, operation_id, field_prefix=""):
    """Valida presentacion, unidad, cantidad y kg. Devuelve un item normalizado."""
    presentation_ref = raw.get("presentacion")
    if not _is_text(presentation_ref):
        _add_error(
            errors,
            "PRESENTACION_FALTANTE",
            "Falta la presentacion.",
            operation_id,
            f"{field_prefix}presentacion",
        )
        return None
    presentation_ref = presentation_ref.strip()
    metadata = presentaciones.get(presentation_ref)
    if metadata is None:
        _add_error(
            errors,
            "REFERENCIA_CATALOGO_DESCONOCIDA",
            f"Presentacion '{presentation_ref}' no existe en los catalogos suministrados.",
            operation_id,
            f"{field_prefix}presentacion",
        )
        return None
    if not isinstance(metadata, dict):
        metadata = {}
    catalog_unit = metadata.get("unidad")
    if not _is_text(catalog_unit):
        _add_error(
            errors,
            "PRESENTACION_SIN_UNIDAD",
            f"La presentacion '{presentation_ref}' no declara unidad en el catalogo.",
            operation_id,
            f"{field_prefix}unidad",
        )
        return None
    catalog_unit = catalog_unit.strip()

    unit = raw.get("unidad")
    if not _is_text(unit):
        _add_error(
            errors,
            "UNIDAD_FALTANTE",
            "Falta la unidad y no se deduce del catalogo sin confirmacion.",
            operation_id,
            f"{field_prefix}unidad",
        )
        return None
    unit = unit.strip()
    if unit.lower() != catalog_unit.lower():
        _add_error(
            errors,
            "UNIDAD_INCONSISTENTE",
            f"Unidad '{unit}' no coincide con '{catalog_unit}' del catalogo.",
            operation_id,
            f"{field_prefix}unidad",
        )
        return None

    quantity = _parse_decimal(raw.get("cantidad"))
    if quantity is None:
        _add_error(
            errors,
            "CANTIDAD_INVALIDA",
            "La cantidad debe ser un numero finito mayor que cero.",
            operation_id,
            f"{field_prefix}cantidad",
        )
        return None

    kg_per_unit = metadata.get("kg_por_unidad")
    computed_kg = None
    if unit.lower() == "kg":
        if kg_per_unit is not None:
            parsed_kg_per_unit = _parse_decimal(kg_per_unit)
            if parsed_kg_per_unit is None or parsed_kg_per_unit != Decimal("1"):
                _add_error(
                    errors,
                    "KG_POR_UNIDAD_INCONSISTENTE",
                    "Una presentacion en kg debe declarar kg_por_unidad = 1.",
                    operation_id,
                    f"{field_prefix}unidad",
                )
                return None
        computed_kg = quantity
    elif kg_per_unit is not None:
        parsed_kg_per_unit = _parse_decimal(kg_per_unit)
        if parsed_kg_per_unit is None:
            _add_error(
                errors,
                "KG_POR_UNIDAD_INVALIDO",
                "kg_por_unidad debe ser un numero finito mayor que cero.",
                operation_id,
                "catalogos.presentaciones",
            )
            return None
        computed_kg = quantity * parsed_kg_per_unit

    supplied_kg = raw.get("kg")
    if supplied_kg is not None:
        parsed_supplied_kg = _parse_decimal(supplied_kg)
        if parsed_supplied_kg is None:
            _add_error(
                errors,
                "KG_INVALIDO",
                "El kg explicito debe ser un numero finito mayor que cero.",
                operation_id,
                f"{field_prefix}kg",
            )
            return None
        if computed_kg is None:
            _add_error(
                errors,
                "KG_SIN_CONVERSION",
                "Se indico kg pero la presentacion no permite convertirlo; no se inventa el factor.",
                operation_id,
                f"{field_prefix}kg",
            )
            return None
        if parsed_supplied_kg != computed_kg:
            _add_error(
                errors,
                "CANTIDAD_KG_INCONSISTENTE",
                f"kg {dec_str(parsed_supplied_kg)} no coincide con cantidad*kg_por_unidad = {dec_str(computed_kg)}.",
                operation_id,
                f"{field_prefix}kg",
            )
            return None

    return {
        "presentacion": presentation_ref,
        "unidad": unit,
        "cantidad": quantity,
        "kg": computed_kg,
    }


def _error_count(errors, operation_id):
    return sum(1 for error in errors if error.get("operation_id") == operation_id)


def _normalize_operation(raw, index, context):
    errors = context["errors"]
    warnings = context["warnings"]
    pending = context["pending"]
    almacenes = context["almacenes"]
    presentaciones = context["presentaciones"]
    clientes = context["clientes"]
    proveedores = context["proveedores"]

    if not isinstance(raw, dict):
        operation_id = f"indice_{index + 1}"
        _add_error(
            errors,
            "OPERACION_INVALIDA",
            "Cada operacion debe ser un objeto JSON.",
            operation_id,
        )
        pending.append({"operation_id": operation_id, "motivo": "Operacion no es objeto JSON."})
        return None

    operation_id = raw.get("operation_id") or raw.get("id")
    if not _is_text(operation_id):
        operation_id = f"indice_{index + 1}"
        _add_error(
            errors,
            "OPERATION_ID_FALTANTE",
            "Falta operation_id estable para la operacion.",
            operation_id,
        )
    else:
        operation_id = operation_id.strip()

    before = _error_count(errors, operation_id)
    operation_type = raw.get("tipo") or raw.get("type")
    if operation_type not in VALID_TYPES:
        _add_error(
            errors,
            "TIPO_OPERACION_INVALIDO",
            f"Tipo '{operation_type}' no permitido; use uno de {', '.join(VALID_TYPES)}.",
            operation_id,
            "tipo",
        )
        pending.append(
            {"operation_id": operation_id, "motivo": "Tipo de operacion invalido o ausente."}
        )
        return None

    source_ref = raw.get("source_ref") or raw.get("referencia_fuente")
    if not _is_text(source_ref):
        _add_error(
            errors,
            "SOURCE_REF_FALTANTE",
            "Falta source_ref unico del registro fuente.",
            operation_id,
            "source_ref",
        )
        source_ref = None
    else:
        source_ref = source_ref.strip()
        if source_ref in context["seen_source_refs"]:
            _add_error(
                errors,
                "SOURCE_REF_DUPLICADA",
                f"source_ref '{source_ref}' ya fue usado por otra operacion.",
                operation_id,
                "source_ref",
            )
            pending.append(
                {
                    "operation_id": operation_id,
                    "source_ref": source_ref,
                    "motivo": "Referencia fuente duplicada; no se planifica dos veces.",
                }
            )
            return None
        context["seen_source_refs"].add(source_ref)

    operation_date = _parse_date(raw.get("fecha"))
    if operation_date is None:
        _add_error(
            errors,
            "FECHA_INVALIDA",
            "La fecha es obligatoria en formato ISO YYYY-MM-DD y no se sustituye por hoy.",
            operation_id,
            "fecha",
        )
    elif context["period_start"] and context["period_end"]:
        if not (context["period_start"] <= operation_date <= context["period_end"]):
            _add_error(
                errors,
                "FECHA_FUERA_PERIODO",
                f"Fecha {operation_date.isoformat()} fuera del periodo declarado.",
                operation_id,
                "fecha",
            )

    normalized = {
        "operation_id": operation_id,
        "tipo": operation_type,
        "source_ref": source_ref,
        "fecha": operation_date,
        "efecto": dict(EFFECTS[operation_type]),
    }

    if operation_type == "initial_balance":
        normalized["almacen"] = _check_almacen(
            raw.get("almacen"), almacenes, errors, operation_id
        )
        item = _check_item(raw, "inicial", presentaciones, errors, operation_id)
        if item:
            normalized.update(item)
    elif operation_type == "purchase":
        normalized["almacen"] = _check_almacen(
            raw.get("almacen"), almacenes, errors, operation_id
        )
        item = _check_item(raw, "compra", presentaciones, errors, operation_id)
        if item:
            normalized.update(item)
        proveedor = raw.get("proveedor")
        if not _is_text(proveedor):
            _add_error(
                errors,
                "PROVEEDOR_FALTANTE",
                "La compra debe indicar proveedor suministrado.",
                operation_id,
                "proveedor",
            )
        elif proveedor.strip() not in proveedores:
            _add_error(
                errors,
                "REFERENCIA_CATALOGO_DESCONOCIDA",
                f"Proveedor '{proveedor.strip()}' no existe en los catalogos suministrados.",
                operation_id,
                "proveedor",
            )
        else:
            normalized["proveedor"] = proveedor.strip()
        costo = _parse_decimal(raw.get("costo_total"))
        if costo is None:
            _add_error(
                errors,
                "COSTO_INVALIDO",
                "costo_total debe ser un numero finito mayor que cero.",
                operation_id,
                "costo_total",
            )
        else:
            normalized["costo_total"] = costo
    elif operation_type == "production":
        normalized["almacen"] = _check_almacen(
            raw.get("almacen"), almacenes, errors, operation_id
        )
        item = _check_item(raw, "produccion", presentaciones, errors, operation_id)
        if item:
            normalized.update(item)
        consumos_raw = raw.get("consumos")
        consumos = []
        if not isinstance(consumos_raw, list) or not consumos_raw:
            _add_error(
                errors,
                "CONSUMOS_FALTANTES",
                "La produccion debe declarar al menos un consumo real.",
                operation_id,
                "consumos",
            )
        else:
            for consumo_index, consumo_raw in enumerate(consumos_raw):
                if not isinstance(consumo_raw, dict):
                    _add_error(
                        errors,
                        "CONSUMO_INVALIDO",
                        f"Consumo #{consumo_index + 1} debe ser un objeto JSON.",
                        operation_id,
                        "consumos",
                    )
                    continue
                consumo = _check_item(
                    consumo_raw,
                    "consumo",
                    presentaciones,
                    errors,
                    operation_id,
                    field_prefix=f"consumos[{consumo_index}].",
                )
                if consumo:
                    consumos.append(consumo)
        normalized["consumos"] = consumos
    elif operation_type == "distribution":
        normalized["origen"] = _check_almacen(
            raw.get("origen"), almacenes, errors, operation_id, "origen"
        )
        normalized["destino"] = _check_almacen(
            raw.get("destino"), almacenes, errors, operation_id, "destino"
        )
        if normalized.get("origen") and normalized.get("destino"):
            if normalized["origen"] == normalized["destino"]:
                _add_error(
                    errors,
                    "TRANSFERENCIA_MISMO_ALMACEN",
                    "Origen y destino deben ser distintos.",
                    operation_id,
                )
        item = _check_item(raw, "distribucion", presentaciones, errors, operation_id)
        if item:
            normalized.update(item)
    elif operation_type == "sale":
        if raw.get("detalles") is not None or raw.get("lineas") is not None:
            _add_error(
                errors,
                "VENTA_DEBE_SER_INDIVIDUAL",
                "Cada venta historica debe ser una operacion individual con una presentacion.",
                operation_id,
            )
        normalized["almacen"] = _check_almacen(
            raw.get("almacen"), almacenes, errors, operation_id
        )
        item = _check_item(raw, "venta", presentaciones, errors, operation_id)
        if item:
            normalized.update(item)
        cliente = raw.get("cliente")
        if not _is_text(cliente):
            _add_error(
                errors,
                "SALE_SIN_CLIENTE",
                "Falta el cliente real; no se usa un cliente generico.",
                operation_id,
                "cliente",
            )
        elif cliente.strip() not in clientes:
            _add_error(
                errors,
                "REFERENCIA_CATALOGO_DESCONOCIDA",
                f"Cliente '{cliente.strip()}' no existe en los catalogos suministrados.",
                operation_id,
                "cliente",
            )
        else:
            normalized["cliente"] = cliente.strip()
        precio = _parse_decimal(raw.get("precio_unitario"))
        if precio is None:
            _add_error(
                errors,
                "SALE_SIN_PRECIO",
                "precio_unitario debe ser un numero finito mayor que cero.",
                operation_id,
                "precio_unitario",
            )
        else:
            normalized["precio_unitario"] = precio
        tipo_pago = raw.get("tipo_pago")
        if tipo_pago is not None:
            if tipo_pago not in ("contado", "credito"):
                _add_error(
                    errors,
                    "TIPO_PAGO_INVALIDO",
                    "tipo_pago debe ser 'contado' o 'credito' si se declara.",
                    operation_id,
                    "tipo_pago",
                )
            else:
                normalized["tipo_pago"] = tipo_pago
        normalized["nota_venta"] = (
            "Venta individual; el pago se registra aparte solo si realmente ocurrio."
        )

    if _error_count(errors, operation_id) > before:
        pending.append(
            {
                "operation_id": operation_id,
                "source_ref": source_ref,
                "tipo": operation_type,
                "motivo": "; ".join(
                    f"{error['codigo']}: {error['mensaje']}"
                    for error in errors
                    if error.get("operation_id") == operation_id
                ),
            }
        )
        return None

    if operation_type == "sale" and normalized.get("cantidad") and normalized.get("precio_unitario"):
        normalized["subtotal"] = normalized["cantidad"] * normalized["precio_unitario"]
    if operation_type == "sale":
        warnings.append(
            {
                "codigo": "VENTA_NO_ES_PAGO",
                "mensaje": "La venta no crea un pago automatico; registre cobros solo con evidencia real.",
                "operation_id": operation_id,
            }
        )

    return normalized


def _plan_operation(operation):
    planned = {
        "secuencia": operation["secuencia"],
        "operation_id": operation["operation_id"],
        "tipo": operation["tipo"],
        "source_ref": operation["source_ref"],
        "fecha": operation["fecha"].isoformat(),
        "efecto": operation["efecto"],
    }
    for field in (
        "almacen",
        "origen",
        "destino",
        "presentacion",
        "unidad",
        "cliente",
        "proveedor",
        "tipo_pago",
        "nota_venta",
    ):
        if field in operation:
            value = operation[field]
            planned[field] = value.strip() if isinstance(value, str) else value
    for field in ("cantidad", "kg", "precio_unitario", "subtotal", "costo_total"):
        if field in operation:
            planned[field] = dec_str(operation[field])
    if "consumos" in operation:
        planned["consumos"] = [
            {
                "presentacion": consumo["presentacion"],
                "unidad": consumo["unidad"],
                "cantidad": dec_str(consumo["cantidad"]),
                "kg": dec_str(consumo["kg"]),
            }
            for consumo in operation["consumos"]
        ]
    return planned


def _reconcile(operations, declared, errors):
    balances: dict[tuple[str, str, str], dict[str, Decimal]] = {}

    def line(almacen, presentacion, unidad):
        key = (almacen, presentacion, unidad)
        if key not in balances:
            balances[key] = {
                "saldo_inicial": Decimal("0"),
                "compras": Decimal("0"),
                "produccion": Decimal("0"),
                "transferencias_entrada": Decimal("0"),
                "transferencias_salida": Decimal("0"),
                "ventas": Decimal("0"),
                "consumos": Decimal("0"),
                "kg_neto": Decimal("0"),
            }
        return balances[key]

    for operation in operations:
        quantity = operation["cantidad"]
        kg = operation.get("kg") or Decimal("0")
        if operation["tipo"] == "initial_balance":
            entry = line(operation["almacen"], operation["presentacion"], operation["unidad"])
            entry["saldo_inicial"] += quantity
            entry["kg_neto"] += kg
        elif operation["tipo"] == "purchase":
            entry = line(operation["almacen"], operation["presentacion"], operation["unidad"])
            entry["compras"] += quantity
            entry["kg_neto"] += kg
        elif operation["tipo"] == "production":
            entry = line(operation["almacen"], operation["presentacion"], operation["unidad"])
            entry["produccion"] += quantity
            entry["kg_neto"] += kg
            for consumo in operation["consumos"]:
                consumed = line(operation["almacen"], consumo["presentacion"], consumo["unidad"])
                consumed["consumos"] += consumo["cantidad"]
                consumed["kg_neto"] -= consumo.get("kg") or Decimal("0")
        elif operation["tipo"] == "distribution":
            source = line(operation["origen"], operation["presentacion"], operation["unidad"])
            source["transferencias_salida"] += quantity
            source["kg_neto"] -= kg
            target = line(operation["destino"], operation["presentacion"], operation["unidad"])
            target["transferencias_entrada"] += quantity
            target["kg_neto"] += kg
        elif operation["tipo"] == "sale":
            entry = line(operation["almacen"], operation["presentacion"], operation["unidad"])
            entry["ventas"] += quantity
            entry["kg_neto"] -= kg

    declared_map = {}
    for entry in declared:
        key = (entry["almacen"], entry["presentacion"], entry["unidad"])
        declared_map[key] = entry["cantidad"]
        balances.setdefault(
            key,
            {
                "saldo_inicial": Decimal("0"),
                "compras": Decimal("0"),
                "produccion": Decimal("0"),
                "transferencias_entrada": Decimal("0"),
                "transferencias_salida": Decimal("0"),
                "ventas": Decimal("0"),
                "consumos": Decimal("0"),
                "kg_neto": Decimal("0"),
            },
        )

    result = []
    for key in sorted(balances):
        almacen, presentacion, unidad = key
        entry = balances[key]
        calculated = (
            entry["saldo_inicial"]
            + entry["compras"]
            + entry["produccion"]
            + entry["transferencias_entrada"]
            - entry["transferencias_salida"]
            - entry["ventas"]
            - entry["consumos"]
        )
        declared_quantity = declared_map.get(key)
        difference = None
        if declared_quantity is None:
            status = "sin_declaracion"
        else:
            difference = calculated - declared_quantity
            status = "conciliado" if difference == 0 else "diferencia"
            if difference != 0:
                _add_error(
                    errors,
                    "CONCILIACION_DIFERENCIA",
                    (
                        f"Conciliacion de {almacen}/{presentacion} ({unidad}): "
                        f"calculado {dec_str(calculated)}, declarado {dec_str(declared_quantity)}, "
                        f"diferencia {dec_str(difference)}."
                    ),
                )
        result.append(
            {
                "almacen": almacen,
                "presentacion": presentacion,
                "unidad": unidad,
                "saldo_inicial": dec_str(entry["saldo_inicial"]),
                "compras": dec_str(entry["compras"]),
                "produccion": dec_str(entry["produccion"]),
                "transferencias_entrada": dec_str(entry["transferencias_entrada"]),
                "transferencias_salida": dec_str(entry["transferencias_salida"]),
                "ventas": dec_str(entry["ventas"]),
                "consumos": dec_str(entry["consumos"]),
                "cantidad_calculada": dec_str(calculated),
                "cantidad_declarada": dec_str(declared_quantity),
                "diferencia": dec_str(difference),
                "kg_neto": dec_str(entry["kg_neto"]),
                "estado": status,
            }
        )
    return result


def _historical_incompatibilities(operations):
    supplies: dict[tuple[str, str, str], list[tuple[date, Decimal, str]]] = {}
    demands: dict[tuple[str, str, str], list[tuple[date, Decimal, str]]] = {}
    sales: list[tuple[tuple[str, str, str], date, Decimal, str, str]] = []

    def add_supply(almacen, presentacion, unidad, on_date, quantity, label):
        key = (almacen, presentacion, unidad)
        supplies.setdefault(key, []).append((on_date, quantity, label))

    def add_demand(almacen, presentacion, unidad, on_date, quantity, label):
        key = (almacen, presentacion, unidad)
        demands.setdefault(key, []).append((on_date, quantity, label))

    for operation in operations:
        on_date = operation["fecha"]
        if operation["tipo"] in ("initial_balance", "purchase"):
            add_supply(
                operation["almacen"],
                operation["presentacion"],
                operation["unidad"],
                on_date,
                operation["cantidad"],
                operation["operation_id"],
            )
        elif operation["tipo"] == "production":
            add_supply(
                operation["almacen"],
                operation["presentacion"],
                operation["unidad"],
                on_date,
                operation["cantidad"],
                operation["operation_id"],
            )
            for consumo in operation["consumos"]:
                add_demand(
                    operation["almacen"],
                    consumo["presentacion"],
                    consumo["unidad"],
                    on_date,
                    consumo["cantidad"],
                    operation["operation_id"],
                )
        elif operation["tipo"] == "distribution":
            add_demand(
                operation["origen"],
                operation["presentacion"],
                operation["unidad"],
                on_date,
                operation["cantidad"],
                operation["operation_id"],
            )
            add_supply(
                operation["destino"],
                operation["presentacion"],
                operation["unidad"],
                on_date,
                operation["cantidad"],
                operation["operation_id"],
            )
        elif operation["tipo"] == "sale":
            key = (operation["almacen"], operation["presentacion"], operation["unidad"])
            sales.append((key, on_date, operation["cantidad"], operation["operation_id"], operation["source_ref"]))

    incompatibilities = []
    for key, sale_date, sale_quantity, operation_id, source_ref in sorted(
        sales, key=lambda item: (item[1], item[3])
    ):
        almacen, presentacion, unidad = key
        supply_by_sale = sum(
            (quantity for day, quantity, _ in supplies.get(key, []) if day <= sale_date),
            Decimal("0"),
        )
        demand_by_sale = sum(
            (quantity for day, quantity, _ in demands.get(key, []) if day <= sale_date),
            Decimal("0"),
        )
        other_sales = sum(
            (
                quantity
                for other_key, day, quantity, other_id, _ in sales
                if other_key == key and day <= sale_date and other_id != operation_id
            ),
            Decimal("0"),
        )
        available = supply_by_sale - demand_by_sale - other_sales
        if available < sale_quantity:
            future_supplies = sorted(
                day for day, _, _ in supplies.get(key, []) if day > sale_date
            )
            first_future = future_supplies[0].isoformat() if future_supplies else "ninguna"
            incompatibilities.append(
                {
                    "codigo": "INCOMPATIBILIDAD_HISTORICA",
                    "operation_id": operation_id,
                    "source_ref": source_ref,
                    "almacen": almacen,
                    "presentacion": presentacion,
                    "unidad": unidad,
                    "fecha_venta": sale_date.isoformat(),
                    "cantidad_venta": dec_str(sale_quantity),
                    "disponible_calculado": dec_str(available),
                    "primera_entrada_posterior": first_future,
                    "mensaje": (
                        "La venta requiere existencias que a esa fecha no estaban disponibles. "
                        "El plan no altera FIFO ni reordena fechas en secreto: los datos son "
                        "historicamente incompatibles y deben aclararse."
                    ),
                }
            )
    return incompatibilities


def _validate_declared(raw_declared, almacenes, presentaciones, errors):
    if raw_declared is None:
        return []
    if not isinstance(raw_declared, list):
        _add_error(
            errors,
            "STOCK_DECLARADO_INVALIDO",
            "declared_final_stock debe ser una lista de objetos.",
        )
        return []
    result = []
    for index, entry in enumerate(raw_declared):
        label = f"stock_declarado_{index + 1}"
        if not isinstance(entry, dict):
            _add_error(
                errors,
                "STOCK_DECLARADO_INVALIDO",
                "Cada saldo declarado debe ser un objeto JSON.",
                label,
            )
            continue
        almacen = _check_almacen(entry.get("almacen"), almacenes, errors, label)
        presentation_ref = entry.get("presentacion")
        metadata = presentaciones.get(presentation_ref) if _is_text(presentation_ref) else None
        if metadata is None or not isinstance(metadata, dict):
            _add_error(
                errors,
                "REFERENCIA_CATALOGO_DESCONOCIDA",
                "Saldo declarado con presentacion ausente del catalogo.",
                label,
                "presentacion",
            )
            continue
        catalog_unit = metadata.get("unidad")
        unit = entry.get("unidad")
        if not _is_text(unit) or not _is_text(catalog_unit) or unit.strip().lower() != catalog_unit.lower():
            _add_error(
                errors,
                "UNIDAD_INCONSISTENTE",
                "Saldo declarado con unidad ausente o distinta del catalogo.",
                label,
                "unidad",
            )
            continue
        quantity = _parse_decimal(entry.get("cantidad"), allow_zero=True)
        if quantity is None:
            _add_error(
                errors,
                "CANTIDAD_INVALIDA",
                "El saldo declarado debe ser un numero finito mayor o igual que cero.",
                label,
                "cantidad",
            )
            continue
        if almacen:
            result.append(
                {
                    "almacen": almacen,
                    "presentacion": presentation_ref.strip(),
                    "unidad": unit.strip(),
                    "cantidad": quantity,
                }
            )
    return result


def build_plan(payload):
    """Construye el plan dry-run. No escribe archivos ni toca una BD."""
    errors: list[dict] = []
    warnings: list[dict] = []
    pending: list[dict] = []

    if not isinstance(payload, dict):
        _add_error(errors, "ENTRADA_INVALIDA", "El JSON de entrada debe ser un objeto.")
        return _empty_plan(errors)

    if payload.get("apply") is True or payload.get("aplicar") is True:
        _add_error(
            errors,
            "APPLY_NO_SOPORTADO",
            "Este planificador no aplica cambios en BD; solo genera un dry-run.",
        )

    catalogs = payload.get("catalogs")
    if not isinstance(catalogs, dict):
        catalogs = {}
        _add_error(
            errors,
            "CATALOGOS_FALTANTES",
            "Faltan catalogos explicitos (almacenes, presentaciones, clientes, proveedores).",
        )
    almacenes = _catalog_map(catalogs, "almacenes")
    presentaciones = _catalog_map(catalogs, "presentaciones")
    clientes = _catalog_map(catalogs, "clientes")
    proveedores = _catalog_map(catalogs, "proveedores")
    for name, mapping in (
        ("almacenes", almacenes),
        ("presentaciones", presentaciones),
        ("clientes", clientes),
        ("proveedores", proveedores),
    ):
        if mapping is None:
            _add_error(
                errors,
                "CATALOGO_INVALIDO",
                f"El catalogo '{name}' debe ser objeto o lista explicita.",
            )
            if name == "almacenes":
                almacenes = {}
            elif name == "presentaciones":
                presentaciones = {}
            elif name == "clientes":
                clientes = {}
            else:
                proveedores = {}

    mode = payload.get("mode") or payload.get("modo")
    if mode not in VALID_MODES:
        _add_error(
            errors,
            "MODO_INVALIDO",
            f"mode debe ser uno de {', '.join(VALID_MODES)}.",
            field="mode",
        )

    source = payload.get("source") or payload.get("fuente")
    if not _is_text(source):
        _add_error(
            errors,
            "FUENTE_FALTANTE",
            "Falta la fuente real de los datos; no se asume un origen.",
            field="source",
        )

    period = payload.get("period") or payload.get("periodo")
    period_start = period_end = None
    if not isinstance(period, dict):
        _add_error(errors, "PERIODO_FALTANTE", "Falta el periodo real del corte.", field="period")
    else:
        period_start = _parse_date(period.get("start") or period.get("inicio"))
        period_end = _parse_date(period.get("end") or period.get("fin"))
        if period_start is None:
            _add_error(
                errors,
                "PERIODO_FALTANTE",
                "Falta period.start (YYYY-MM-DD).",
                field="period.start",
            )
        if period_end is None:
            _add_error(
                errors,
                "PERIODO_FALTANTE",
                "Falta period.end (YYYY-MM-DD).",
                field="period.end",
            )
        if period_start and period_end and period_start > period_end:
            _add_error(errors, "PERIODO_INVALIDO", "period.start no puede ser posterior a period.end.")

    operations_raw = payload.get("operations")
    if not isinstance(operations_raw, list) or not operations_raw:
        _add_error(
            errors,
            "OPERACIONES_FALTANTES",
            "Debe suministrarse una lista no vacia de operations.",
            field="operations",
        )
        operations_raw = []

    context = {
        "errors": errors,
        "warnings": warnings,
        "pending": pending,
        "almacenes": almacenes or {},
        "presentaciones": presentaciones or {},
        "clientes": clientes or {},
        "proveedores": proveedores or {},
        "seen_source_refs": set(),
        "period_start": period_start,
        "period_end": period_end,
    }

    accepted = []
    for index, raw in enumerate(operations_raw):
        normalized = _normalize_operation(raw, index, context)
        if normalized is not None:
            accepted.append(normalized)

    accepted.sort(key=lambda operation: (operation["fecha"], operation["operation_id"], operation["source_ref"]))
    for sequence, operation in enumerate(accepted, start=1):
        operation["secuencia"] = sequence

    if mode == "initial_regularization":
        notes = [
            "Regularizacion inicial: los saldos iniciales son un corte documentado; "
            "no reconstruyen el kardex cronologico ni el FIFO historico.",
            "No se escribe en BD. La ejecucion requiere revision humana, copia desechable y aprobacion nueva.",
            "Ventas una por una; los huecos permanecen pendientes y no se inventan datos.",
        ]
    else:
        notes = [
            "Reconstruccion cronologica: el plan ordena por fecha real y reporta incompatibilidades; "
            "no altera FIFO ni fechas en secreto.",
            "No se escribe en BD. La ejecucion requiere revision humana, copia desechable y aprobacion nueva.",
            "Ventas una por una; los huecos permanecen pendientes y no se inventan datos.",
        ]
    notes.append(PENDING_NOTE)

    declared = _validate_declared(
        payload.get("declared_final_stock"),
        almacenes or {},
        presentaciones or {},
        errors,
    )
    reconciliation = _reconcile(accepted, declared, errors)
    incompatibilities = _historical_incompatibilities(accepted)

    by_type = {}
    for operation in accepted:
        by_type[operation["tipo"]] = by_type.get(operation["tipo"], 0) + 1

    plan = {
        "schema": SCHEMA,
        "dry_run": True,
        "database_writes": False,
        "estado": "planificado_no_ejecutado",
        "modo": mode,
        "periodo": {
            "inicio": period_start.isoformat() if period_start else None,
            "fin": period_end.isoformat() if period_end else None,
        },
        "fuente": source.strip() if _is_text(source) else None,
        "resumen": {
            "operaciones_planificadas": len(accepted),
            "por_tipo": dict(sorted(by_type.items())),
            "errores": len(errors),
            "advertencias": len(warnings),
            "pendientes": len(pending),
            "incompatibilidades_historicas": len(incompatibilities),
        },
        "operaciones": [_plan_operation(operation) for operation in accepted],
        "conciliacion": reconciliation,
        "errores": errors,
        "advertencias": warnings,
        "pendientes": pending,
        "incompatibilidades_historicas": incompatibilities,
        "notas": notes,
    }
    return plan


def _empty_plan(errors):
    return {
        "schema": SCHEMA,
        "dry_run": True,
        "database_writes": False,
        "estado": "planificado_no_ejecutado",
        "errores": errors,
        "advertencias": [],
        "pendientes": [],
        "incompatibilidades_historicas": [],
    }


def _read_payload(path):
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as error:
        raise PlanError(f"No existe el archivo de entrada: {path}") from error
    except UnicodeDecodeError as error:
        raise PlanError("El archivo de entrada no es UTF-8 valido.") from error
    except json.JSONDecodeError as error:
        raise PlanError(f"JSON invalido en {path}: {error.msg}") from error
    except OSError as error:
        raise PlanError(f"No se pudo leer {path}: {error.strerror}") from error


def _normalized_path(path_text):
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path_text))))


def _write_new_plan(path_text, text):
    """Crea un archivo nuevo de forma exclusiva; nunca sobrescribe uno existente."""
    output_path = Path(path_text)
    try:
        descriptor = os.open(
            str(output_path),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError as error:
        raise PlanError(
            "Ya existe el archivo de salida; no se sobrescribe. Use otra ruta nueva."
        ) from error
    except OSError as error:
        detail = error.strerror or "error de sistema"
        raise PlanError(f"No se pudo crear el archivo de salida: {detail}.") from error

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")
    except (OSError, UnicodeError) as error:
        try:
            output_path.unlink()
        except OSError:
            pass
        raise PlanError(
            "No se pudo escribir el plan nuevo; se descarto la salida parcial."
        ) from error


def _emit_cli_error(message):
    print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run de planificacion de carga historica. No conecta a BD, "
            "no importa la aplicacion y no aplica cambios."
        )
    )
    parser.add_argument("--input", required=True, help="JSON explicito de entrada.")
    parser.add_argument(
        "--output",
        default=None,
        help="Ruta opcional para guardar el plan JSON (nunca datos de negocio).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Indenta la salida para lectura humana.",
    )
    args = parser.parse_args(argv)

    if args.output and _normalized_path(args.input) == _normalized_path(args.output):
        _emit_cli_error(
            "--output no puede ser igual a --input; la fuente original no se sobrescribe."
        )
        return 2

    try:
        payload = _read_payload(args.input)
    except PlanError as error:
        _emit_cli_error(str(error))
        return 2

    plan = build_plan(payload)
    text = json.dumps(
        plan,
        ensure_ascii=False,
        indent=2 if args.pretty else None,
        sort_keys=True,
    )
    if args.output:
        try:
            _write_new_plan(args.output, text)
        except PlanError as error:
            _emit_cli_error(str(error))
            return 2
    else:
        print(text)

    has_blocking = bool(plan.get("errores")) or bool(plan.get("incompatibilidades_historicas"))
    return 1 if has_blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
