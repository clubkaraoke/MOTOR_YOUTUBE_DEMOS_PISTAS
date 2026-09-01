# DJGABO UI SYSTEM V1

Sistema visual compartido para el panel principal y los motores de producción.

## Objetivo

Que `/`, `/cdg/`, `/p-youtube/` y `/cdg-lyrics/` se perciban como una sola aplicación, aunque cada motor conserve su lógica y flujo de trabajo.

Motor 01 (CD+G) se usa como referencia visual. Motor 02, Motor 03 y el portal adoptan la misma jerarquía, tipografía, superficies, espaciados y comportamiento responsive.

## Tipografía

Familia:

```css
Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif
```

Escala:

- 11 px: microtexto, badges, metadatos.
- 12 px: texto secundario, labels, ayudas.
- 14 px: texto normal y controles.
- 16 px: encabezado de tarjeta/sección.
- 20 px: título secundario.
- 28 px: título de pantalla/motor.
- 38–56 px: hero del selector de motores únicamente.

## Colores

- Fondo: `#101218`
- Superficie: `#1b1f27`
- Superficie elevada: `#222733`
- Borde: `#2b303b`
- Texto: `#f4f6fb`
- Texto secundario: `#9aa3b4`
- Primario: `#8b5cf6`
- Éxito: `#58c77a`
- Proceso/atención: `#e9a23b`
- Error: `#f15b64`
- Estado técnico/actividad: `#2dd4bf`

## Geometría

- Radio pequeño: 8 px
- Radio de tarjeta: 12 px
- Radio de modal: 16 px
- Controles táctiles: mínimo 36–38 px de alto
- Padding de escritorio: 20–24 px
- Padding móvil: 12–16 px
- Ancho máximo operativo: 1500 px
- Ancho máximo del portal: 1180 px

## Responsive

### Escritorio (> 1000 px)
- Navegación completa.
- Métricas y canales en una sola zona superior.
- Listados operativos en filas/tablas.
- Densidad media: más compacta que el antiguo CD+G, pero con separación visual clara.

### Tablet (761–1000 px)
- Cabeceras flexibles.
- Métricas y canales con desplazamiento horizontal cuando haga falta.
- Formularios de dos columnas se reducen progresivamente.
- Contenido mantiene tarjetas y márgenes de 16 px.

### Celular (<= 760 px)
- Header reorganizado en bloques.
- Buscador ocupa ancho completo.
- Filas de P-YouTube se transforman en tarjetas verticales.
- Acciones táctiles de al menos 34–38 px.
- Formularios pasan a una columna.
- Métricas usan dos columnas.
- Tablas técnicas complejas de Motor 03 permiten scroll horizontal controlado.

## Regla de consistencia

Los motores pueden diferenciarse por su contenido, pero no deben inventar tamaños, radios, tipografías o colores nuevos sin incorporarlos primero a este sistema.
