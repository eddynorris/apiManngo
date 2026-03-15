# Documentación Completa de Anime.js (v4)

Esta referencia cubre todos los módulos y funcionalidades de **Anime.js versión 4**.

> [!NOTE]
> Anime.js v4 utiliza módulos ES modernos. Asegúrese de importar las funciones necesarias.

```javascript
import {
  animate,
  createTimeline,
  createTimer,
  createAnimatable,
  createDraggable,
  createScope,
  stagger,
  splitText,
  createMotionPath,
  utils,
  engine,
  onScroll
} from 'animejs';
```

---

## 1. Animation (`animate`)

La función principal para animar elementos.

```javascript
animate(targets, parameters);
```

### Targets (Objetivos)
Determina qué se animará. Puede ser:
- **Selector CSS:** `'.box'`, `'#id'`
- **Nodos DOM:** `document.querySelector('.el')`
- **NodeList:** `document.querySelectorAll('.el')`
- **Array:** `['.el', element, object]`
- **Objeto JS:** `{ prop: 0 }`

### Propiedades Animables
- **CSS:** `opacity`, `transform` (usando shorthands como `translateX`, `scale`), colores, variables CSS (`--var`).
- **Atributos:** `value`, `d`, `rx`, `ry`.
- **Objetos JS:** Cualquier propiedad numérica.

### Parámetros
- `duration`: (Default: `1000`) Duración en ms.
- `delay`: (Default: `1000`) Espera antes de iniciar.
- `ease`: (Default: `'out(3)'`) Función de timing.
- `loop`: Número de ciclos o `true` (infinito).
- `alternate`: (`true`/`false`) Ida y vuelta.
- `autoplay`: (`true`/`false`) Iniciar automáticamente.
- `playbackRate`: Velocidad de reproducción.

### Ejemplo Completo
```javascript
const animation = animate('.box', {
  translateX: 250,
  scale: 2,
  rotate: '1turn',
  backgroundColor: '#FFF',
  duration: 800,
  loop: true,
  alternate: true,
  ease: 'inOutExpo'
});
```

### Keyframes
Permite definir múltiples pasos para una propiedad.

```javascript
animate('.el', {
  translateX: [
    { to: 100, duration: 500 },
    { to: 0, duration: 500 }
  ],
  opacity: [0, 1] // De 0 a 1
});
```

---

## 2. Timer (`createTimer`)

Crea temporizadores sincronizados con el engine de Anime.js. Alternativa superior a `setTimeout`/`setInterval`.

```javascript
const timer = createTimer({
  duration: 1000,
  loop: true,
  onUpdate: (self) => console.log(self.progress),
  onLoop: () => console.log('Nueva vuelta')
});

// Métodos
timer.play();
timer.pause();
timer.restart();
```

---

## 3. Timeline (`createTimeline`)

Sincroniza múltiples animaciones y temporizadores en una secuencia.

```javascript
const tl = createTimeline({
  defaults: { duration: 1000, ease: 'out' }, // Heredado por los hijos
  loop: true
});

tl.add('.box1', { translateX: 100 })           // t=0
  .add('.box2', { translateY: 50 }, '-=500')   // t=500 (overlap)
  .add('.box3', { opacity: 0 }, 2000)          // t=2000 (absoluto)
  .add(timer, 0);                              // Añadir un timer existente
```

### Posicionamiento
- `Number`: Tiempo absoluto.
- `'+=100'`: 100ms después del anterior.
- `'-=100'`: 100ms antes de que termine el anterior.
- `'<<'`: Al inicio del anterior.

---

## 4. Animatable (`createAnimatable`)

Crea "setters" de alto rendimiento. Útil para interacciones en tiempo real (ej. seguir el mouse) donde `animate()` sería costoso.

```javascript
const box = createAnimatable('.box');

// Uso en loop o eventos
document.addEventListener('mousemove', (e) => {
  // .x(valor, [duración], [ease])
  box.x(e.clientX, 100, 'linear'); 
  box.y(e.clientY, 100, 'linear');
  box.rotate(e.clientX, 500, 'out(3)');
});
```

---

## 5. Draggable (`createDraggable`)

Sistema de arrastrar y soltar con física e inercia.

```javascript
createDraggable('.card', {
  // Ejes permitidos: 'x', 'y', 'both'
  axis: 'x', 
  
  // Límites (contenedor o valores min/max)
  container: '.wrapper', 
  // o bounds: { min: 0, max: 500 }
  
  // Snap (puntos de anclaje)
  snap: [0, 100, 200, 300],
  
  // Callbacks
  onDragStart: (self) => console.log('Start', self.x),
  onDrag: (self) => console.log('Drag', self.x),
  onDragEnd: (self) => console.log('End')
});
```

---

## 6. Scope (`createScope`)

Gestión de animaciones agrupadas. Ideal para componentes y diseño responsivo. Permite "revertir" o limpiar un grupo de animaciones.

```javascript
const myScope = createScope();

// Las animaciones creadas dentro del scope se asocian a él
myScope.animate('.el', { x: 100 });

// Medias Queries: Activar/desactivar según condiciones
createScope({
  mediaQueries: {
    desktop: '(min-width: 800px)',
    reducedMotion: '(prefers-reduced-motion: reduce)'
  }
});
```

---

## 7. Events (Scroll - `onScroll`)

Animaciones controladas por el scroll (Scroll-linked animations).

```javascript
import { onScroll } from 'animejs';

animate('.box', {
  rotate: 360,
  autoplay: onScroll({
    target: document.body, // Elemento scrolleable
    // threshold: ['0%', '100%']
    debug: true // Muestra marcadores visuales
  })
});
```

---

## 8. SVG Utilities

Herramientas potentes para gráficos vectoriales.

### Motion Path (`createMotionPath`)
Mueve un elemento a lo largo de un path SVG.

```javascript
const path = createMotionPath('#myPath');

animate('.follower', {
  // x e y se calculan automáticamente
  translateX: path.x, 
  translateY: path.y,
  rotate: path.angle, // Rota según la curva
  duration: 2000
});
```

### Morphing (`morphTo`)
Transición entre formas.

```javascript
// Requiere misma cantidad de puntos o configuración avanzada
animate('path', {
  d: document.querySelector('#shape2').getAttribute('d')
});
```

### Line Drawing
Animar el trazo de un SVG.

```javascript
animate('path', {
  strokeDashoffset: [setDashoffset, 0], // setDashoffset es un helper de animejs
  easing: 'easeInOutSine'
});
```

---

## 9. Text (`splitText`)

Utilidad para dividir texto en caracteres, palabras o líneas para animarlos individualmente.

```javascript
const text = splitText('.title', {
  chars: true,  // Dividir en caracteres
  words: true,  // Dividir en palabras
  lines: true   // Dividir en líneas
});

// Animar los caracteres resultantes
animate(text.chars, {
  translateY: [50, 0],
  opacity: [0, 1],
  delay: stagger(50)
});
```

---

## 10. Utilities & Stagger

### Stagger
Crea retrasos o valores incrementales. Indispensable para listas y grids.

```javascript
delay: stagger(100)           // 0, 100, 200...
delay: stagger(100, {
  start: 500,                 // Empieza en 500ms
  from: 'center',             // Desde el centro
  grid: [10, 10],             // Grid 10x10
  axis: 'x'                   // Dirección X
})
```

### Utils
Colección de funciones matemáticas y helpers.
- `utils.random(min, max)`: Número aleatorio.
- `utils.clamp(val, min, max)`: Restringe un valor.
- `utils.lerp(start, end, t)`: Interpolación lineal.
- `utils.snap(val, step)`: Ajusta al paso más cercano.
- `utils.$('.el')`: Alias de querySelector.
- `utils.$$('.el')`: Alias de querySelectorAll (retorna Array).

---

## 11. Easings

Funciones que definen la aceleración de la animación.

- **Predefinidos:** `'linear'`, `'inQuad'`, `'outCubic'`, `'inOutExpo'`, etc.
- **Potencia:** `'in(2.5)'`
- **Steps:** `'steps(5)'`
- **Bezier:** `'cubicBezier(0.25, 0.1, 0.25, 1.0)'`
- **Spring (Física):** Simula resortes reales.
  ```javascript
  ease: 'spring(masa, rigidez, amortiguación, velocidad)'
  ease: 'spring(1, 100, 10, 0)'
  ```

---

## 12. WAAPI (`waapi`)

Motor experimental basado en la **Web Animation API** nativa del navegador.
- **Ventajas:** Rendimiento extremo (corre en el hilo del compositor).
- **Desventajas:** Menos features que el motor JS principal (sin objetos JS, callbacks limitados).

```javascript
import { waapi } from 'animejs';

waapi.animate('.box', {
  translateX: 100,
  duration: 1000
});
```

---

## 13. Engine

Control global de todas las animaciones de Anime.js.

```javascript
import { engine } from 'animejs';

engine.speed = 0.5;   // Todo va a mitad de velocidad (slow motion global)
engine.suspend();     // Congela todo
engine.resume();      // Reanuda

// Configuración global
engine.globalFrameRate = 60; // Limitar FPS
engine.clock;               // Tiempo actual del engine
```

---
> Documentación generada para Anime.js v4. Referencia completa de la API.
