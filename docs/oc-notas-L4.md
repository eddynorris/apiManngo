# Notas L4 — Identidad, revocación y campos sensibles

## Estado

- L4 (A05/A20/A21) implementado: identidad JWT resuelta contra BD, revocación por `auth_version`, `UserSchema` sin campos sensibles y vinculación de Telegram por ID estricto.
- Verificación previa del padre en PostgreSQL real: **22 passed, 1 failed** en tests L4 + sondas propias.
- Único fallo pendiente: `test_parent_auth_missing_password_is_client_error`. `POST /auth` sin `password` devolvía 500 porque el `except Exception` amplio de `resources/auth_resource.py` interceptaba el `HTTPException 400` de `RequestParser`.

## Corrección aplicada en esta corrida

- `resources/auth_resource.py`: se captura y re-lanza `werkzeug.exceptions.HTTPException` antes del `except Exception` genérico. Los errores de cliente de `RequestParser` (falta de campos, JSON malformado, contenido no soportado) conservan su 400/415; un fallo inesperado sigue devolviendo el 500 genérico `{'message': 'Error en el servidor'}` sin traza ni excepción cruda en la respuesta.
- No se modificaron `app.py`, `common.py`, `models.py`, `schemas.py`, `user_resource.py`, Telegram ni archivos de stock/pagos/RLS. La propiedad de esos archivos queda en L8/L7.

## Tests propios añadidos

- `tests/test_http_auth_contract.py`: falta de `password` → 400; JSON malformado → 400; cuerpo no JSON → 400/415 coherente; credenciales inválidas → 401 con mensaje estable y sin excepción cruda.
- **No ejecutados por este agente** (restricción de shell/Docker). El padre los verifica en Docker/PostgreSQL real.

## Migración y compatibilidad

- Migración nueva: `migrations/versions/b7c1d2e3f4a5_add_user_auth_version.py`, `down_revision = 'a1b2c3d4e5f6'`; agrega `auth_version` con `server_default='0'` y es reversible. No ejecutada por este agente.
- Tokens legacy sin claim `auth_version` se aceptan solo mientras `Users.auth_version == 0`; tras el primer cambio sensible quedan revocados (401).
- Tokens con `sub` no numérico se rechazan de forma segura. Si el token trae `username`, `rol` o `almacen_id`, se comparan contra la BD para revocar claims obsoletos y evitar confusión con usernames numéricos.
- La migración debe aplicarse antes de desplegar el código nuevo en una BD existente.

## Archivos de esta corrida

- `resources/auth_resource.py` (corrección del contrato HTTP de login).
- `tests/test_http_auth_contract.py` (nuevo).
- `docs/oc-notas-L4.md` (este documento).

## Límites

- Resultados de tests propios: **no ejecutados**. No se declaran passed.
- La corrección queda pendiente de verificación independiente del padre.
