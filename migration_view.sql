create or replace view public.vista_clientes_proyeccion as
with
  saldos_calculados as (
    select
      v_1.cliente_id,
      sum(
        v_1.total - COALESCE(
          (
            select
              sum(p.monto) as sum
            from
              pagos p
            where
              p.venta_id = v_1.id
          ),
          0::numeric
        )
      ) as saldo_pendiente
    from
      ventas v_1
    where
      v_1.estado_pago::text = any (
        array[
          'pendiente'::character varying,
          'parcial'::character varying
        ]::text[]
      )
    group by
      v_1.cliente_id
  )
select
  c.id,
  c.nombre,
  c.telefono,
  c.ciudad,
  c.ultima_fecha_compra,
  c.frecuencia_compra_dias,
  COALESCE(s.saldo_pendiente, 0::numeric) as saldo_pendiente,
  case
    when c.proxima_compra_manual is not null then c.proxima_compra_manual::timestamp with time zone
    when c.frecuencia_compra_dias is not null
    and c.frecuencia_compra_dias > 0 then c.ultima_fecha_compra + (
      (c.frecuencia_compra_dias || ' days'::text)::interval
    )
    else null::timestamp with time zone
  end as proxima_compra_estimada,
  case
    when c.proxima_compra_manual is not null then 
        CURRENT_DATE - c.proxima_compra_manual
    when c.ultima_fecha_compra is not null then CURRENT_DATE - c.ultima_fecha_compra::date
    else null::integer
  end as dias_desde_ultima_compra, -- Nota: Si es manual, esto representa dias desde la fecha manual (negativo si es futuro)
  case
    when c.proxima_compra_manual is not null then
        CURRENT_DATE - c.proxima_compra_manual
    when c.frecuencia_compra_dias is not null
    and c.frecuencia_compra_dias > 0 then CURRENT_DATE - (
      c.ultima_fecha_compra::date + c.frecuencia_compra_dias
    )
    else null::integer
  end as dias_retraso,
  case
    when c.proxima_compra_manual is not null then
        case 
            when CURRENT_DATE > c.proxima_compra_manual then 'retrasado'::text
            when CURRENT_DATE >= (c.proxima_compra_manual - 3) then 'proximo'::text
            else 'programado'::text
        end
    when c.frecuencia_compra_dias is null
    or c.frecuencia_compra_dias = 0 then 'sin_proyeccion'::text
    when CURRENT_DATE > (
      c.ultima_fecha_compra::date + c.frecuencia_compra_dias
    ) then 'retrasado'::text
    when CURRENT_DATE >= (
      c.ultima_fecha_compra::date + c.frecuencia_compra_dias - 3
    ) then 'proximo'::text
    else 'programado'::text
  end as estado_proyeccion,
  count(v.id) as total_ventas,
  COALESCE(sum(v.total), 0::numeric) as monto_total_comprado,
  COALESCE(round(avg(v.total), 2), 0::numeric) as promedio_compra,
  c.proxima_compra_manual,
  c.ultimo_contacto
from
  clientes c
  left join ventas v on v.cliente_id = c.id
  left join saldos_calculados s on s.cliente_id = c.id
group by
  c.id,
  c.nombre,
  c.telefono,
  c.ciudad,
  c.ultima_fecha_compra,
  c.frecuencia_compra_dias,
  s.saldo_pendiente,
  c.proxima_compra_manual,
  c.ultimo_contacto;
