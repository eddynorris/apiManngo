"""Contratos del planificador dry-run de carga historica (L9).

Fixture 100% sintetico y explicitamente identificado. No contiene datos reales,
no usa red, no importa la aplicacion ni toca base de datos.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare_historical_load.py"
SPEC = importlib.util.spec_from_file_location("prepare_historical_load_l9", SCRIPT)
planner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(planner)

FIXTURE_ID = "SYNTHETIC_FIXTURE_L9_EXPLICIT_TWO_LOADS"


def synthetic_fixture():
    return {
        "fixture": FIXTURE_ID,
        "fixture_kind": "synthetic_explicit_no_real_data",
        "mode": "chronological_reconstruction",
        "source": "SYNTHETIC_SOURCE_L9 (no es una fuente real del usuario)",
        "period": {"start": "2026-01-01", "end": "2026-02-28"},
        "catalogs": {
            "almacenes": ["ALM1", "PLA"],
            "presentaciones": {
                "CARBON_KG": {
                    "unidad": "kg",
                    "kg_por_unidad": "1",
                    "tipo": "bruto",
                },
                "SACO20": {
                    "unidad": "saco",
                    "kg_por_unidad": "20",
                    "tipo": "procesado",
                },
            },
            "clientes": ["CL1", "CL2"],
            "proveedores": ["P1"],
        },
        "operations": [
            {
                "operation_id": "INIT-SACO",
                "tipo": "initial_balance",
                "source_ref": "SALDO-SACO-ALM1",
                "fecha": "2026-01-01",
                "almacen": "ALM1",
                "presentacion": "SACO20",
                "unidad": "saco",
                "cantidad": "10",
            },
            {
                "operation_id": "CARGA-1",
                "tipo": "purchase",
                "source_ref": "CARGA-1",
                "fecha": "2026-01-05",
                "almacen": "ALM1",
                "presentacion": "CARBON_KG",
                "unidad": "kg",
                "cantidad": "1000",
                "costo_total": "5000",
                "proveedor": "P1",
            },
            {
                "operation_id": "PROD-1",
                "tipo": "production",
                "source_ref": "PROD-1",
                "fecha": "2026-01-10",
                "almacen": "ALM1",
                "presentacion": "SACO20",
                "unidad": "saco",
                "cantidad": "15",
                "consumos": [
                    {
                        "presentacion": "CARBON_KG",
                        "unidad": "kg",
                        "cantidad": "300",
                    }
                ],
            },
            {
                "operation_id": "CARGA-2",
                "tipo": "purchase",
                "source_ref": "CARGA-2",
                "fecha": "2026-01-20",
                "almacen": "ALM1",
                "presentacion": "CARBON_KG",
                "unidad": "kg",
                "cantidad": "500",
                "costo_total": "2600",
                "proveedor": "P1",
            },
            {
                "operation_id": "PROD-2",
                "tipo": "production",
                "source_ref": "PROD-2",
                "fecha": "2026-01-25",
                "almacen": "ALM1",
                "presentacion": "SACO20",
                "unidad": "saco",
                "cantidad": "10",
                "consumos": [
                    {
                        "presentacion": "CARBON_KG",
                        "unidad": "kg",
                        "cantidad": "200",
                    }
                ],
            },
            {
                "operation_id": "DIST-1",
                "tipo": "distribution",
                "source_ref": "DIST-1",
                "fecha": "2026-01-27",
                "origen": "ALM1",
                "destino": "PLA",
                "presentacion": "SACO20",
                "unidad": "saco",
                "cantidad": "5",
            },
            {
                "operation_id": "VENTA-1",
                "tipo": "sale",
                "source_ref": "VENTA-1",
                "fecha": "2026-01-28",
                "almacen": "ALM1",
                "presentacion": "SACO20",
                "unidad": "saco",
                "cantidad": "5",
                "cliente": "CL1",
                "precio_unitario": "50",
                "tipo_pago": "contado",
            },
            {
                "operation_id": "VENTA-2",
                "tipo": "sale",
                "source_ref": "VENTA-2",
                "fecha": "2026-01-29",
                "almacen": "PLA",
                "presentacion": "SACO20",
                "unidad": "saco",
                "cantidad": "3",
                "cliente": "CL2",
                "precio_unitario": "55",
                "tipo_pago": "credito",
            },
        ],
        "declared_final_stock": [
            {
                "almacen": "ALM1",
                "presentacion": "SACO20",
                "unidad": "saco",
                "cantidad": "25",
            },
            {
                "almacen": "PLA",
                "presentacion": "SACO20",
                "unidad": "saco",
                "cantidad": "2",
            },
            {
                "almacen": "ALM1",
                "presentacion": "CARBON_KG",
                "unidad": "kg",
                "cantidad": "1000",
            },
        ],
    }


def plan_json(plan):
    return json.dumps(plan, ensure_ascii=False, sort_keys=True)


def test_plan_deterministic_two_loads_and_reconciliation():
    payload = synthetic_fixture()
    first = planner.build_plan(copy.deepcopy(payload))
    second = planner.build_plan(copy.deepcopy(payload))

    assert plan_json(first) == plan_json(second)
    assert first["dry_run"] is True
    assert first["database_writes"] is False
    assert first["estado"] == "planificado_no_ejecutado"
    assert "realizado" not in plan_json(first)
    assert first["errores"] == []
    assert first["incompatibilidades_historicas"] == []

    references = [operation["source_ref"] for operation in first["operaciones"]]
    assert "CARGA-1" in references
    assert "CARGA-2" in references
    assert first["resumen"]["por_tipo"]["purchase"] == 2
    assert [operation["secuencia"] for operation in first["operaciones"]] == list(
        range(1, len(first["operaciones"]) + 1)
    )

    purchases = [
        operation
        for operation in first["operaciones"]
        if operation["tipo"] == "purchase"
    ]
    assert len(purchases) == 2
    for purchase in purchases:
        assert purchase["efecto"]["gasto"].startswith("si")
        assert purchase["costo_total"] in ("5000", "2600")

    initial = [
        operation
        for operation in first["operaciones"]
        if operation["tipo"] == "initial_balance"
    ]
    assert len(initial) == 1
    assert initial[0]["efecto"]["gasto"].startswith("no")

    production = [
        operation
        for operation in first["operaciones"]
        if operation["tipo"] == "production"
    ]
    assert all(item["efecto"]["gasto"].startswith("no") for item in production)
    assert all(item["consumos"] for item in production)

    reconciliation = {
        (row["almacen"], row["presentacion"]): row
        for row in first["conciliacion"]
    }
    assert reconciliation[("ALM1", "SACO20")]["estado"] == "conciliado"
    assert reconciliation[("ALM1", "SACO20")]["cantidad_calculada"] == "25"
    assert reconciliation[("PLA", "SACO20")]["estado"] == "conciliado"
    assert reconciliation[("PLA", "SACO20")]["cantidad_calculada"] == "2"
    assert reconciliation[("ALM1", "CARBON_KG")]["estado"] == "conciliado"
    assert reconciliation[("ALM1", "CARBON_KG")]["cantidad_calculada"] == "1000"
    assert reconciliation[("ALM1", "CARBON_KG")]["kg_neto"] == "1000"


def test_repetition_without_side_effects(tmp_path, capsys):
    payload = synthetic_fixture()
    input_path = tmp_path / "entrada_sintetica.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    assert planner.main(["--input", str(input_path)]) == 0
    first_output = capsys.readouterr().out
    assert planner.main(["--input", str(input_path)]) == 0
    second_output = capsys.readouterr().out

    after = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    assert before == after
    assert first_output == second_output
    assert json.loads(first_output)["database_writes"] is False
    assert not list(tmp_path.glob("*.done"))
    assert not list(tmp_path.glob("*realizado*"))


def test_cli_does_not_offer_apply(tmp_path):
    input_path = tmp_path / "entrada.json"
    input_path.write_text(json.dumps(synthetic_fixture()), encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        planner.main(["--input", str(input_path), "--apply"])
    assert excinfo.value.code == 2


def test_cli_rejects_output_equal_to_input_and_preserves_bytes(tmp_path, capsys):
    input_path = tmp_path / "entrada.json"
    input_path.write_text(json.dumps(synthetic_fixture()), encoding="utf-8")
    alias_directory = tmp_path / "sub"
    alias_directory.mkdir()
    alias_path = alias_directory / ".." / "entrada.json"
    before = input_path.read_bytes()

    assert planner.main(["--input", str(input_path), "--output", str(input_path)]) == 2
    first_error = json.loads(capsys.readouterr().err)
    assert "no puede ser igual" in first_error["error"]
    assert input_path.read_bytes() == before

    assert planner.main(["--input", str(input_path), "--output", str(alias_path)]) == 2
    second_error = json.loads(capsys.readouterr().err)
    assert "no puede ser igual" in second_error["error"]
    assert input_path.read_bytes() == before
    assert not (tmp_path / "entrada.json.tmp").exists()


def test_cli_rejects_existing_output_and_preserves_both_files(tmp_path, capsys):
    input_path = tmp_path / "entrada.json"
    input_path.write_text(json.dumps(synthetic_fixture()), encoding="utf-8")
    output_path = tmp_path / "plan_existente.json"
    output_path.write_bytes(b"CONTENIDO_PREVIO_NO_TOCAR")
    input_before = input_path.read_bytes()
    output_before = output_path.read_bytes()

    assert planner.main(["--input", str(input_path), "--output", str(output_path)]) == 2
    captured = capsys.readouterr()
    assert "Ya existe" in json.loads(captured.err)["error"]
    assert "Traceback" not in captured.err
    assert input_path.read_bytes() == input_before
    assert output_path.read_bytes() == output_before


def test_cli_rejects_symlink_output_or_existing_fallback(tmp_path, capsys):
    input_path = tmp_path / "entrada.json"
    input_path.write_text(json.dumps(synthetic_fixture()), encoding="utf-8")
    output_path = tmp_path / "salida.json"
    symlink_created = False
    try:
        output_path.symlink_to(input_path)
        symlink_created = True
    except (OSError, NotImplementedError):
        output_path.write_bytes(b"FALLBACK_SENTINEL")

    input_before = input_path.read_bytes()
    assert planner.main(["--input", str(input_path), "--output", str(output_path)]) == 2
    captured = capsys.readouterr()
    assert input_path.read_bytes() == input_before
    assert "Traceback" not in captured.err

    if symlink_created:
        assert output_path.is_symlink()
        assert output_path.resolve() == input_path.resolve()
        assert "no puede ser igual" in json.loads(captured.err)["error"]
    else:
        assert output_path.read_bytes() == b"FALLBACK_SENTINEL"
        assert "Ya existe" in json.loads(captured.err)["error"]


def test_cli_new_output_is_deterministic(tmp_path):
    input_path = tmp_path / "entrada.json"
    input_path.write_text(json.dumps(synthetic_fixture()), encoding="utf-8")
    first_output = tmp_path / "plan_1.json"
    second_output = tmp_path / "plan_2.json"

    assert planner.main(["--input", str(input_path), "--output", str(first_output)]) == 0
    assert planner.main(["--input", str(input_path), "--output", str(second_output)]) == 0
    assert first_output.read_bytes() == second_output.read_bytes()
    assert json.loads(first_output.read_text(encoding="utf-8"))["database_writes"] is False


def test_cli_invalid_utf8_input_is_safe(tmp_path, capsys):
    input_path = tmp_path / "entrada.json"
    input_path.write_bytes(b"\xff\xfe\x00not-utf8")
    before = input_path.read_bytes()

    assert planner.main(["--input", str(input_path)]) == 2
    captured = capsys.readouterr()
    assert "UTF-8" in json.loads(captured.err)["error"]
    assert "Traceback" not in captured.err
    assert input_path.read_bytes() == before


def test_insufficient_sale_stays_pending_without_invented_data():
    payload = synthetic_fixture()
    sale = next(
        operation
        for operation in payload["operations"]
        if operation["operation_id"] == "VENTA-1"
    )
    sale.pop("cliente")
    sale.pop("cantidad")
    sale.pop("precio_unitario")
    source_before = copy.deepcopy(payload)

    plan = planner.build_plan(payload)
    codes = {error["codigo"] for error in plan["errores"]}
    assert {"SALE_SIN_CLIENTE", "CANTIDAD_INVALIDA", "SALE_SIN_PRECIO"} <= codes

    assert any(item["operation_id"] == "VENTA-1" for item in plan["pendientes"])
    assert all(
        operation["operation_id"] != "VENTA-1"
        for operation in plan["operaciones"]
    )

    assert payload == source_before
    assert "cliente" not in sale
    assert "cantidad" not in sale
    assert "precio_unitario" not in sale

    accepted_sales = [
        operation
        for operation in plan["operaciones"]
        if operation["tipo"] == "sale"
    ]
    assert [operation["operation_id"] for operation in accepted_sales] == ["VENTA-2"]
    accepted_clients = {operation["cliente"] for operation in accepted_sales}
    assert accepted_clients == {"CL2"}
    assert accepted_clients <= set(payload["catalogs"]["clientes"])

    pending_sale = next(
        item for item in plan["pendientes"] if item["operation_id"] == "VENTA-1"
    )
    for injected_field in ("cliente", "cantidad", "precio_unitario"):
        assert injected_field not in pending_sale
    assert plan["estado"] == "planificado_no_ejecutado"


def test_historical_incompatibility_reported_without_secret_fifo_change():
    payload = synthetic_fixture()
    payload["operations"] = [
        operation
        for operation in payload["operations"]
        if operation["tipo"] != "initial_balance"
    ]
    sale = next(
        operation
        for operation in payload["operations"]
        if operation["operation_id"] == "VENTA-1"
    )
    sale["fecha"] = "2026-01-02"
    payload["declared_final_stock"] = []

    plan = planner.build_plan(payload)
    incompatibilities = plan["incompatibilidades_historicas"]
    assert any(
        item["operation_id"] == "VENTA-1"
        and item["codigo"] == "INCOMPATIBILIDAD_HISTORICA"
        and item["primera_entrada_posterior"] == "2026-01-10"
        for item in incompatibilities
    )
    assert all(
        operation["fecha"] != "2026-01-02"
        or operation["operation_id"] != "VENTA-1"
        or operation["tipo"] != "production"
        for operation in plan["operaciones"]
    )


def test_duplicate_source_ref_and_unknown_catalog_are_errors():
    duplicated = synthetic_fixture()
    duplicated["operations"][1]["source_ref"] = duplicated["operations"][0]["source_ref"]
    plan = planner.build_plan(duplicated)
    assert "SOURCE_REF_DUPLICADA" in {error["codigo"] for error in plan["errores"]}

    unknown = synthetic_fixture()
    unknown_sale = next(
        operation
        for operation in unknown["operations"]
        if operation["operation_id"] == "VENTA-1"
    )
    unknown_sale["presentacion"] = "PRESENTACION_INEXISTENTE"
    plan = planner.build_plan(unknown)
    assert "REFERENCIA_CATALOGO_DESCONOCIDA" in {
        error["codigo"] for error in plan["errores"]
    }
    assert any(item["operation_id"] == "VENTA-1" for item in plan["pendientes"])


def test_missing_period_source_and_apply_are_rejected():
    payload = synthetic_fixture()
    payload.pop("period")
    payload.pop("source")
    payload["apply"] = True

    plan = planner.build_plan(payload)
    codes = {error["codigo"] for error in plan["errores"]}
    assert {"PERIODO_FALTANTE", "FUENTE_FALTANTE", "APPLY_NO_SOPORTADO"} <= codes
    assert plan["dry_run"] is True
    assert plan["database_writes"] is False


def test_module_does_not_import_application_or_network_clients():
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in (
        "import dotenv",
        "from app import",
        "from models import",
        "from services",
        "import sqlalchemy",
        "import requests",
        "import httpx",
    ):
        assert forbidden not in source
