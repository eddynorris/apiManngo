# Módulo de Producción y Reporte de Entradas (Frontend Angular - appManngoWeb)

Documentación técnica y arquitectura de integración para el frontend **Angular (`appManngoWeb`)** para consumir y presentar el **Reporte de Producción, Lotes y Traslados entre Almacenes** del backend **apiFlaskManngo**.

---

## 1. Especificación del Endpoint en Backend

- **URL:** `GET /reportes/produccion-entradas` *(alias: `GET /reportes/movimientos-entrada`)*
- **Headers:** `Authorization: Bearer <token>`
- **Filtros por Query Params:**
  - `fecha_inicio` *(YYYY-MM-DD)*: Fecha inicio de filtro.
  - `fecha_fin` *(YYYY-MM-DD)*: Fecha fin de filtro.
  - `almacen_id` *(opcional, number)*: Filtrar por almacén o planta.
  - `lote_id` *(opcional, number)*: Filtrar por lote específico.
  - `producto_id` *(opcional, number)*: Filtrar por producto base.
  - `presentacion_id` *(opcional, number)*: Filtrar por presentación.
  - `tipo_operacion` *(opcional)*: `'todos'` | `'produccion'` | `'transferencia'`.

---

## 2. Modelos TypeScript (`src/app/core/models/reporte-produccion.model.ts`)

```typescript
export interface IPeriodo {
  fecha_inicio: string;
  fecha_fin: string;
}

export interface IFiltrosReporteEntradas {
  fecha_inicio?: string;
  fecha_fin?: string;
  almacen_id?: number | null;
  lote_id?: number | null;
  presentacion_id?: number | null;
  producto_id?: number | null;
  tipo_operacion?: 'todos' | 'produccion' | 'transferencia';
}

export interface IResumenSubtotal {
  total_unidades: number;
  total_kg: number;
  total_lotes_utilizados?: number;
  total_operaciones: number;
}

export interface IResumenGeneral {
  total_unidades_ingresadas: number;
  total_kg_ingresados: number;
  produccion: IResumenSubtotal;
  traslados: IResumenSubtotal;
}

export interface IPresentacionLoteItem {
  presentacion_id: number | null;
  presentacion_nombre: string;
  capacidad_kg: number | null;
  unidades: number;
  kg: number;
}

export interface IProduccionPorLote {
  lote_id: number | null;
  codigo_lote: string;
  descripcion_lote: string | null;
  producto_id: number | null;
  producto_nombre: string;
  unidades_producidas: number;
  kg_producidos: number;
  operaciones_count: number;
  presentaciones: IPresentacionLoteItem[];
}

export interface IProduccionPorPresentacion {
  presentacion_id: number | null;
  presentacion_nombre: string;
  producto_nombre: string;
  tipo_presentacion: string | null;
  capacidad_kg: number | null;
  unidades_producidas: number;
  kg_producidos: number;
  lotes_involucrados: string[];
}

export interface ITrasladoEntreAlmacenes {
  movimiento_id: number;
  operacion_id: string | null;
  fecha: string | null;
  presentacion_id: number | null;
  presentacion_nombre: string;
  producto_nombre: string;
  lote_id: number | null;
  codigo_lote: string;
  cantidad_unidades: number;
  capacidad_kg: number | null;
  total_kg: number;
  almacen_origen: string;
  almacen_destino: string;
  motivo: string | null;
  usuario_nombre: string | null;
}

export interface IResumenTemporalItem {
  fecha: string;
  unidades_produccion: number;
  kg_produccion: number;
  unidades_traslado: number;
  kg_traslado: number;
  total_unidades_dia: number;
  total_kg_dia: number;
}

export interface IMovimientoDetalle {
  id: number;
  fecha: string | null;
  tipo_operacion: string;
  presentacion_id: number | null;
  presentacion_nombre: string;
  producto_id: number | null;
  producto_nombre: string;
  lote_id: number | null;
  codigo_lote: string;
  cantidad_unidades: number;
  capacidad_kg: number | null;
  total_kg: number;
  motivo: string | null;
  usuario_id: number | null;
  usuario_nombre: string | null;
  almacen_nombre: string;
}

export interface IReporteProduccionEntradasResponse {
  periodo: IPeriodo;
  filtros_aplicados: IFiltrosReporteEntradas;
  resumen_general: IResumenGeneral;
  produccion_por_lote: IProduccionPorLote[];
  produccion_por_presentacion: IProduccionPorPresentacion[];
  traslados_entre_almacenes: ITrasladoEntreAlmacenes[];
  resumen_temporal: IResumenTemporalItem[];
  movimientos_detalle: IMovimientoDetalle[];
}
```

---

## 3. Servicio Angular (`src/app/core/services/reporte-produccion.service.ts`)

```typescript
import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from 'src/environments/environment';
import { 
  IReporteProduccionEntradasResponse, 
  IFiltrosReporteEntradas 
} from '../models/reporte-produccion.model';

@Injectable({
  providedIn: 'root'
})
export class ReporteProduccionService {
  private readonly http = inject(HttpClient);
  private readonly apiUrl = `${environment.apiUrl}/reportes/produccion-entradas`;

  /**
   * Obtiene el reporte integral de entradas (producción y traslados)
   */
  getReporteEntradas(filtros: IFiltrosReporteEntradas = {}): Observable<IReporteProduccionEntradasResponse> {
    let params = new HttpParams();

    if (filtros.fecha_inicio) {
      params = params.set('fecha_inicio', filtros.fecha_inicio);
    }
    if (filtros.fecha_fin) {
      params = params.set('fecha_fin', filtros.fecha_fin);
    }
    if (filtros.almacen_id) {
      params = params.set('almacen_id', filtros.almacen_id.toString());
    }
    if (filtros.lote_id) {
      params = params.set('lote_id', filtros.lote_id.toString());
    }
    if (filtros.producto_id) {
      params = params.set('producto_id', filtros.producto_id.toString());
    }
    if (filtros.presentacion_id) {
      params = params.set('presentacion_id', filtros.presentacion_id.toString());
    }
    if (filtros.tipo_operacion && filtros.tipo_operacion !== 'todos') {
      params = params.set('tipo_operacion', filtros.tipo_operacion);
    }

    return this.http.get<IReporteProduccionEntradasResponse>(this.apiUrl, { params });
  }
}
```

---

## 4. Componente de Página Angular (`src/app/features/admin/produccion/pages/reporte-produccion-page/...`)

### 4.1 Componente TypeScript (`reporte-produccion-page.component.ts`)

```typescript
import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ReporteProduccionService } from 'src/app/core/services/reporte-produccion.service';
import { 
  IReporteProduccionEntradasResponse, 
  IFiltrosReporteEntradas 
} from 'src/app/core/models/reporte-produccion.model';
import { DataTableComponent, ColumnConfig } from 'src/app/shared/components/data-table/data-table.component';

@Component({
  selector: 'app-reporte-produccion-page',
  standalone: true,
  imports: [CommonModule, FormsModule, DataTableComponent],
  templateUrl: './reporte-produccion-page.component.html',
  styleUrls: ['./reporte-produccion-page.component.scss']
})
export class ReporteProduccionPageComponent implements OnInit {
  private readonly reporteService = inject(ReporteProduccionService);

  // Estados reactivos con Signals
  readonly isLoading = signal<boolean>(false);
  readonly error = signal<string | null>(null);
  readonly reporte = signal<IReporteProduccionEntradasResponse | null>(null);
  readonly activeTab = signal<'lotes' | 'presentaciones' | 'traslados' | 'detalle'>('lotes');

  // Filtros
  filtros: IFiltrosReporteEntradas = {
    fecha_inicio: this.getDefaultStartDate(),
    fecha_fin: this.getTodayDate(),
    tipo_operacion: 'todos',
    almacen_id: null
  };

  // Columnas para DataTable: Lotes
  readonly columnasLotes: ColumnConfig[] = [
    { key: 'codigo_lote', label: 'Código Lote', sortable: true },
    { key: 'producto_nombre', label: 'Producto', sortable: true },
    { key: 'unidades_producidas', label: 'Unidades', sortable: true },
    { key: 'kg_producidos', label: 'Total (KG)', sortable: true },
    { key: 'operaciones_count', label: 'N° Órdenes', sortable: true }
  ];

  // Columnas para DataTable: Traslados
  readonly columnasTraslados: ColumnConfig[] = [
    { key: 'fecha', label: 'Fecha', sortable: true },
    { key: 'almacen_origen', label: 'Origen' },
    { key: 'almacen_destino', label: 'Destino' },
    { key: 'presentacion_nombre', label: 'Presentación', sortable: true },
    { key: 'codigo_lote', label: 'Lote' },
    { key: 'cantidad_unidades', label: 'Unidades', sortable: true },
    { key: 'total_kg', label: 'Total KG', sortable: true },
    { key: 'operacion_id', label: 'N° Operación' }
  ];

  // Columnas para DataTable: Detalle
  readonly columnasDetalle: ColumnConfig[] = [
    { key: 'fecha', label: 'Fecha', sortable: true },
    { key: 'tipo_operacion', label: 'Tipo' },
    { key: 'presentacion_nombre', label: 'Presentación' },
    { key: 'codigo_lote', label: 'Lote' },
    { key: 'cantidad_unidades', label: 'Cantidad' },
    { key: 'total_kg', label: 'Total KG' },
    { key: 'almacen_nombre', label: 'Almacén' },
    { key: 'usuario_nombre', label: 'Usuario' }
  ];

  ngOnInit(): void {
    this.cargarReporte();
  }

  cargarReporte(): void {
    this.isLoading.set(true);
    this.error.set(null);

    this.reporteService.getReporteEntradas(this.filtros).subscribe({
      next: (data) => {
        this.reporte.set(data);
        this.isLoading.set(false);
      },
      error: (err) => {
        console.error('Error al cargar reporte de producción:', err);
        this.error.set('No se pudo cargar el reporte de producción.');
        this.isLoading.set(false);
      }
    });
  }

  setTab(tab: 'lotes' | 'presentaciones' | 'traslados' | 'detalle'): void {
    this.activeTab.set(tab);
  }

  private getTodayDate(): string {
    return new Date().toISOString().split('T')[0];
  }

  private getDefaultStartDate(): string {
    const d = new Date();
    d.setDate(d.getDate() - 30);
    return d.toISOString().split('T')[0];
  }
}
```

---

### 4.2 Plantilla HTML (`reporte-produccion-page.component.html`)

```html
<div class="p-6 space-y-6">
  <!-- Encabezado de la Página -->
  <div class="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
    <div>
      <h1 class="text-2xl font-bold text-gray-900">Reporte de Producción y Entradas</h1>
      <p class="text-sm text-gray-500">Supervisión de fabricación por lotes y transferencias entre almacenes.</p>
    </div>

    <!-- Filtros de Fecha y Operación -->
    <div class="flex flex-wrap items-center gap-3 bg-white p-3 rounded-xl shadow-sm border border-gray-200">
      <div class="flex items-center gap-2">
        <label class="text-xs font-semibold text-gray-600">Desde:</label>
        <input 
          type="date" 
          [(ngModel)]="filtros.fecha_inicio" 
          (change)="cargarReporte()" 
          class="border rounded-lg px-2 py-1 text-sm text-gray-700 focus:ring-2 focus:ring-emerald-500"
        />
      </div>

      <div class="flex items-center gap-2">
        <label class="text-xs font-semibold text-gray-600">Hasta:</label>
        <input 
          type="date" 
          [(ngModel)]="filtros.fecha_fin" 
          (change)="cargarReporte()" 
          class="border rounded-lg px-2 py-1 text-sm text-gray-700 focus:ring-2 focus:ring-emerald-500"
        />
      </div>

      <div class="flex items-center gap-2">
        <label class="text-xs font-semibold text-gray-600">Tipo:</label>
        <select 
          [(ngModel)]="filtros.tipo_operacion" 
          (change)="cargarReporte()" 
          class="border rounded-lg px-2 py-1 text-sm text-gray-700 focus:ring-2 focus:ring-emerald-500"
        >
          <option value="todos">Todos</option>
          <option value="produccion">Solo Producción</option>
          <option value="transferencia">Solo Traslados</option>
        </select>
      </div>

      <button 
        (click)="cargarReporte()" 
        class="bg-emerald-600 hover:bg-emerald-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium transition"
      >
        Filtrar
      </button>
    </div>
  </div>

  <!-- Estado de Carga / Error -->
  <div *ngIf="isLoading()" class="flex justify-center p-12">
    <div class="animate-spin rounded-full h-10 w-10 border-b-2 border-emerald-600"></div>
  </div>

  <div *ngIf="error()" class="bg-red-50 text-red-700 p-4 rounded-xl border border-red-200">
    {{ error() }}
  </div>

  <!-- Contenido Principal cuando data está disponible -->
  <div *ngIf="reporte() as r" class="space-y-6">

    <!-- KPI Summary Cards -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <div class="bg-white p-5 rounded-2xl border border-gray-200 shadow-sm">
        <span class="text-xs font-semibold uppercase text-emerald-600 tracking-wider">Producción Fabricada</span>
        <div class="mt-2 flex items-baseline gap-2">
          <span class="text-3xl font-extrabold text-gray-900">{{ r.resumen_general.produccion.total_kg | number:'1.0-2' }}</span>
          <span class="text-sm font-bold text-gray-500">kg</span>
        </div>
        <p class="text-xs text-gray-500 mt-1">{{ r.resumen_general.produccion.total_unidades | number }} unidades totales</p>
      </div>

      <div class="bg-white p-5 rounded-2xl border border-gray-200 shadow-sm">
        <span class="text-xs font-semibold uppercase text-blue-600 tracking-wider">Traslados Recibidos</span>
        <div class="mt-2 flex items-baseline gap-2">
          <span class="text-3xl font-extrabold text-gray-900">{{ r.resumen_general.traslados.total_kg | number:'1.0-2' }}</span>
          <span class="text-sm font-bold text-gray-500">kg</span>
        </div>
        <p class="text-xs text-gray-500 mt-1">{{ r.resumen_general.traslados.total_unidades | number }} unidades transferidas</p>
      </div>

      <div class="bg-white p-5 rounded-2xl border border-gray-200 shadow-sm">
        <span class="text-xs font-semibold uppercase text-amber-600 tracking-wider">Lotes Involucrados</span>
        <div class="mt-2 flex items-baseline gap-2">
          <span class="text-3xl font-extrabold text-gray-900">{{ r.resumen_general.produccion.total_lotes_utilizados }}</span>
          <span class="text-sm font-bold text-gray-500">lotes</span>
        </div>
        <p class="text-xs text-gray-500 mt-1">{{ r.resumen_general.produccion.total_operaciones }} ensambles realizados</p>
      </div>

      <div class="bg-white p-5 rounded-2xl border border-gray-200 shadow-sm">
        <span class="text-xs font-semibold uppercase text-purple-600 tracking-wider">Total Entradas Global</span>
        <div class="mt-2 flex items-baseline gap-2">
          <span class="text-3xl font-extrabold text-gray-900">{{ r.resumen_general.total_kg_ingresados | number:'1.0-2' }}</span>
          <span class="text-sm font-bold text-gray-500">kg</span>
        </div>
        <p class="text-xs text-gray-500 mt-1">{{ r.resumen_general.total_unidades_ingresadas | number }} unidades ingresadas</p>
      </div>
    </div>

    <!-- Navegación de Pestañas (Tabs) -->
    <div class="border-b border-gray-200 bg-white rounded-t-2xl px-4 pt-3 flex gap-4">
      <button 
        (click)="setTab('lotes')" 
        [class.border-emerald-600]="activeTab() === 'lotes'"
        [class.text-emerald-600]="activeTab() === 'lotes'"
        class="pb-3 px-2 text-sm font-semibold border-b-2 border-transparent text-gray-500 hover:text-gray-700 transition"
      >
        Producción por Lotes ({{ r.produccion_por_lote.length }})
      </button>

      <button 
        (click)="setTab('presentaciones')" 
        [class.border-emerald-600]="activeTab() === 'presentaciones'"
        [class.text-emerald-600]="activeTab() === 'presentaciones'"
        class="pb-3 px-2 text-sm font-semibold border-b-2 border-transparent text-gray-500 hover:text-gray-700 transition"
      >
        Por Presentación ({{ r.produccion_por_presentacion.length }})
      </button>

      <button 
        (click)="setTab('traslados')" 
        [class.border-emerald-600]="activeTab() === 'traslados'"
        [class.text-emerald-600]="activeTab() === 'traslados'"
        class="pb-3 px-2 text-sm font-semibold border-b-2 border-transparent text-gray-500 hover:text-gray-700 transition"
      >
        Traslados Recibidos ({{ r.traslados_entre_almacenes.length }})
      </button>

      <button 
        (click)="setTab('detalle')" 
        [class.border-emerald-600]="activeTab() === 'detalle'"
        [class.text-emerald-600]="activeTab() === 'detalle'"
        class="pb-3 px-2 text-sm font-semibold border-b-2 border-transparent text-gray-500 hover:text-gray-700 transition"
      >
        Historial Completo ({{ r.movimientos_detalle.length }})
      </button>
    </div>

    <!-- Contenido de las Pestañas -->
    <div class="bg-white p-6 rounded-b-2xl border border-t-0 border-gray-200 shadow-sm">
      
      <!-- TAB 1: Producción por Lotes -->
      <div *ngIf="activeTab() === 'lotes'" class="space-y-4">
        <app-data-table 
          [columns]="columnasLotes" 
          [data]="r.produccion_por_lote"
        >
        </app-data-table>
      </div>

      <!-- TAB 2: Producción por Presentación -->
      <div *ngIf="activeTab() === 'presentaciones'" class="space-y-4">
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div *ngFor="let p of r.produccion_por_presentacion" class="border border-gray-200 p-4 rounded-xl hover:shadow-md transition">
            <h4 class="font-bold text-gray-900">{{ p.presentacion_nombre }}</h4>
            <p class="text-xs text-gray-500">{{ p.producto_nombre }} • {{ p.capacidad_kg }} kg/und</p>
            <div class="mt-3 flex justify-between items-center">
              <div>
                <span class="text-xs text-gray-400">Producido:</span>
                <p class="font-bold text-emerald-700 text-lg">{{ p.kg_producidos | number:'1.0-2' }} kg</p>
              </div>
              <span class="bg-emerald-50 text-emerald-700 text-xs px-2.5 py-1 rounded-full font-semibold">
                {{ p.unidades_producidas }} und
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- TAB 3: Traslados Recibidos -->
      <div *ngIf="activeTab() === 'traslados'" class="space-y-4">
        <app-data-table 
          [columns]="columnasTraslados" 
          [data]="r.traslados_entre_almacenes"
        >
        </app-data-table>
      </div>

      <!-- TAB 4: Historial Completo -->
      <div *ngIf="activeTab() === 'detalle'" class="space-y-4">
        <app-data-table 
          [columns]="columnasDetalle" 
          [data]="r.movimientos_detalle"
        >
        </app-data-table>
      </div>

    </div>
  </div>
</div>
```

---

## 5. Resumen de Flujo de Datos

```
[Usuario en appManngoWeb] 
       │
       ▼ Selecciona Rango de Fechas / Almacén / Tipo de Operación
[ReporteProduccionPageComponent]
       │
       ▼ Invoca getReporteEntradas(filtros)
[ReporteProduccionService] (HttpClient)
       │
       ▼ GET /reportes/produccion-entradas?fecha_inicio=...&fecha_fin=...
[apiFlaskManngo Backend]
       │
       ▼ Ejecuta ReporteProduccionEntradasResource (SQLAlchemy)
       ▼ Cruce Movimientos (Entrada) + Lote + Presentación + Almacenes + Usuarios
       │
       ▼ Retorna JSON Estructurado
[ReporteProduccionPageComponent]
       │
       ▼ Renderiza:
         - Cards con KPIs (KG producidos, Trasladados, Lotes)
         - Tabs con Tablas DataTable (<app-data-table>)
```
