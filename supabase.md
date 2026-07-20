Warnings
[
  {
    "name": "function_search_path_mutable",
    "title": "Function Search Path Mutable",
    "level": "WARN",
    "facing": "EXTERNAL",
    "categories": [
      "SECURITY"
    ],
    "description": "Detects functions where the search_path parameter is not set.",
    "detail": "Function \\`public.update_updated_at_column\\` has a role mutable search_path",
    "remediation": "https://supabase.com/docs/guides/database/database-linter?lint=0011_function_search_path_mutable",
    "metadata": {
      "name": "update_updated_at_column",
      "type": "function",
      "schema": "public"
    },
    "cache_key": "function_search_path_mutable_public_update_updated_at_column_873d38e2d5763140db06f687c34684b1"
  },
  {
    "name": "function_search_path_mutable",
    "title": "Function Search Path Mutable",
    "level": "WARN",
    "facing": "EXTERNAL",
    "categories": [
      "SECURITY"
    ],
    "description": "Detects functions where the search_path parameter is not set.",
    "detail": "Function \\`public.match_documents\\` has a role mutable search_path",
    "remediation": "https://supabase.com/docs/guides/database/database-linter?lint=0011_function_search_path_mutable",
    "metadata": {
      "name": "match_documents",
      "type": "function",
      "schema": "public"
    },
    "cache_key": "function_search_path_mutable_public_match_documents_427f08262c3894c23a9b91a833a141fb"
  },
  {
    "name": "function_search_path_mutable",
    "title": "Function Search Path Mutable",
    "level": "WARN",
    "facing": "EXTERNAL",
    "categories": [
      "SECURITY"
    ],
    "description": "Detects functions where the search_path parameter is not set.",
    "detail": "Function \\`public.calcular_frecuencia_compra\\` has a role mutable search_path",
    "remediation": "https://supabase.com/docs/guides/database/database-linter?lint=0011_function_search_path_mutable",
    "metadata": {
      "name": "calcular_frecuencia_compra",
      "type": "function",
      "schema": "public"
    },
    "cache_key": "function_search_path_mutable_public_calcular_frecuencia_compra_33fbd26ec46d6e29241d02130d8a201f"
  },
  {
    "name": "function_search_path_mutable",
    "title": "Function Search Path Mutable",
    "level": "WARN",
    "facing": "EXTERNAL",
    "categories": [
      "SECURITY"
    ],
    "description": "Detects functions where the search_path parameter is not set.",
    "detail": "Function \\`public.actualizar_datos_compra_cliente\\` has a role mutable search_path",
    "remediation": "https://supabase.com/docs/guides/database/database-linter?lint=0011_function_search_path_mutable",
    "metadata": {
      "name": "actualizar_datos_compra_cliente",
      "type": "function",
      "schema": "public"
    },
    "cache_key": "function_search_path_mutable_public_actualizar_datos_compra_cliente_0dc7610c7bd009f9222504a1c7e47187"
  },
  {
    "name": "function_search_path_mutable",
    "title": "Function Search Path Mutable",
    "level": "WARN",
    "facing": "EXTERNAL",
    "categories": [
      "SECURITY"
    ],
    "description": "Detects functions where the search_path parameter is not set.",
    "detail": "Function \\`public.recalcular_cliente\\` has a role mutable search_path",
    "remediation": "https://supabase.com/docs/guides/database/database-linter?lint=0011_function_search_path_mutable",
    "metadata": {
      "name": "recalcular_cliente",
      "type": "function",
      "schema": "public"
    },
    "cache_key": "function_search_path_mutable_public_recalcular_cliente_774ac3eadbf030a99b41cde40f260226"
  },
  {
    "name": "function_search_path_mutable",
    "title": "Function Search Path Mutable",
    "level": "WARN",
    "facing": "EXTERNAL",
    "categories": [
      "SECURITY"
    ],
    "description": "Detects functions where the search_path parameter is not set.",
    "detail": "Function \\`public.calcular_monto_en_gerencia\\` has a role mutable search_path",
    "remediation": "https://supabase.com/docs/guides/database/database-linter?lint=0011_function_search_path_mutable",
    "metadata": {
      "name": "calcular_monto_en_gerencia",
      "type": "function",
      "schema": "public"
    },
    "cache_key": "function_search_path_mutable_public_calcular_monto_en_gerencia_1c32d984568509e81bdc546e1425275f"
  },
  {
    "name": "function_search_path_mutable",
    "title": "Function Search Path Mutable",
    "level": "WARN",
    "facing": "EXTERNAL",
    "categories": [
      "SECURITY"
    ],
    "description": "Detects functions where the search_path parameter is not set.",
    "detail": "Function \\`public.actualizar_frecuencia_cliente_robusta\\` has a role mutable search_path",
    "remediation": "https://supabase.com/docs/guides/database/database-linter?lint=0011_function_search_path_mutable",
    "metadata": {
      "name": "actualizar_frecuencia_cliente_robusta",
      "type": "function",
      "schema": "public"
    },
    "cache_key": "function_search_path_mutable_public_actualizar_frecuencia_cliente_robusta_cbb565944b19673c8324b2c69fea8fb6"
  },
  {
    "name": "extension_in_public",
    "title": "Extension in Public",
    "level": "WARN",
    "facing": "EXTERNAL",
    "categories": [
      "SECURITY"
    ],
    "description": "Detects extensions installed in the \\`public\\` schema.",
    "detail": "Extension \\`pg_trgm\\` is installed in the public schema. Move it to another schema.",
    "remediation": "https://supabase.com/docs/guides/database/database-linter?lint=0014_extension_in_public",
    "metadata": {
      "name": "pg_trgm",
      "type": "extension",
      "schema": "public"
    },
    "cache_key": "extension_in_public_pg_trgm"
  },
  {
    "name": "vulnerable_postgres_version",
    "title": "Current Postgres version has security patches available",
    "level": "WARN",
    "facing": "EXTERNAL",
    "categories": [
      "SECURITY"
    ],
    "description": "Upgrade your postgres database to apply important security patches",
    "detail": "We have detected that the current version of postgres, supabase-postgres-15.8.1.085, has outstanding security patches available. Upgrade your database to receive the latest security patches.",
    "cache_key": "vulnerable_postgres_version",
    "remediation": "https://supabase.com/docs/guides/platform/upgrading",
    "metadata": {
      "type": "compliance",
      "entity": "Config"
    }
  }
]

Errors

[
  {
    "name": "security_definer_view",
    "title": "Security Definer View",
    "level": "ERROR",
    "facing": "EXTERNAL",
    "categories": [
      "SECURITY"
    ],
    "description": "Detects views defined with the SECURITY DEFINER property. These views enforce Postgres permissions and row level security policies (RLS) of the view creator, rather than that of the querying user",
    "detail": "View \\`public.vista_resumen_depositos\\` is defined with the SECURITY DEFINER property",
    "remediation": "https://supabase.com/docs/guides/database/database-linter?lint=0010_security_definer_view",
    "metadata": {
      "name": "vista_resumen_depositos",
      "type": "view",
      "schema": "public"
    },
    "cache_key": "security_definer_view_public_vista_resumen_depositos"
  },
  {
    "name": "security_definer_view",
    "title": "Security Definer View",
    "level": "ERROR",
    "facing": "EXTERNAL",
    "categories": [
      "SECURITY"
    ],
    "description": "Detects views defined with the SECURITY DEFINER property. These views enforce Postgres permissions and row level security policies (RLS) of the view creator, rather than that of the querying user",
    "detail": "View \\`public.vista_clientes_proyeccion\\` is defined with the SECURITY DEFINER property",
    "remediation": "https://supabase.com/docs/guides/database/database-linter?lint=0010_security_definer_view",
    "metadata": {
      "name": "vista_clientes_proyeccion",
      "type": "view",
      "schema": "public"
    },
    "cache_key": "security_definer_view_public_vista_clientes_proyeccion"
  },
  {
    "name": "rls_disabled_in_public",
    "title": "RLS Disabled in Public",
    "level": "ERROR",
    "facing": "EXTERNAL",
    "categories": [
      "SECURITY"
    ],
    "description": "Detects cases where row level security (RLS) has not been enabled on tables in schemas exposed to PostgREST",
    "detail": "Table \\`public.recetas\\` is public, but RLS has not been enabled.",
    "remediation": "https://supabase.com/docs/guides/database/database-linter?lint=0013_rls_disabled_in_public",
    "metadata": {
      "name": "recetas",
      "type": "table",
      "schema": "public"
    },
    "cache_key": "rls_disabled_in_public_public_recetas"
  },
  {
    "name": "rls_disabled_in_public",
    "title": "RLS Disabled in Public",
    "level": "ERROR",
    "facing": "EXTERNAL",
    "categories": [
      "SECURITY"
    ],
    "description": "Detects cases where row level security (RLS) has not been enabled on tables in schemas exposed to PostgREST",
    "detail": "Table \\`public.componentes_receta\\` is public, but RLS has not been enabled.",
    "remediation": "https://supabase.com/docs/guides/database/database-linter?lint=0013_rls_disabled_in_public",
    "metadata": {
      "name": "componentes_receta",
      "type": "table",
      "schema": "public"
    },
    "cache_key": "rls_disabled_in_public_public_componentes_receta"
  },
  {
    "name": "rls_disabled_in_public",
    "title": "RLS Disabled in Public",
    "level": "ERROR",
    "facing": "EXTERNAL",
    "categories": [
      "SECURITY"
    ],
    "description": "Detects cases where row level security (RLS) has not been enabled on tables in schemas exposed to PostgREST",
    "detail": "Table \\`public.comandos_voz_logs\\` is public, but RLS has not been enabled.",
    "remediation": "https://supabase.com/docs/guides/database/database-linter?lint=0013_rls_disabled_in_public",
    "metadata": {
      "name": "comandos_voz_logs",
      "type": "table",
      "schema": "public"
    },
    "cache_key": "rls_disabled_in_public_public_comandos_voz_logs"
  }
]