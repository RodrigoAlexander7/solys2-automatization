# Prueba 01 — 2026-09-28 18:20, COM10

## 1. Datos registrados en la nota original

Comando: `python -m smp22x log -d 30m --label prueba01`. Se detuvo con Ctrl+C
a los 113 ciclos, aproximadamente 2 min. Carpeta indicada:
`data/2026-09-28_182028_prueba01/`.

Esta revisión ordena los resultados previamente anotados. La carpeta de datos
original no está disponible en el árbol actual; no se han recalculado aquí los
estadísticos ni recuperado variables que no estuvieran en la nota.

| Dirección | Instrumento identificado en el montaje | Serie | Código de tipo | Modelo de datos | Software / hardware |
|---|---|---|---|---|---|
| 2 | SGR4, pirgeómetro | 240466 | 611 | 102 | 300 / 5 |
| 3 | SMP22x, piranómetro | 240349 | 607 | 100 | 201 / 5 |
| 4 | SMP22x, piranómetro | 240350 | 607 | 100 | 201 / 5 |

Los códigos 607 y 611 fueron observados; los PDF locales no los asignan
explícitamente a esos modelos. El manual SMP V20 §15.6 p. 47 documenta 635 y
636 y exige comprobar diferencias con modelos de datos anteriores a 108.
El tipo físico del sensor 2 fue aclarado por el usuario después de esta prueba.

## 2. Comunicación observada

| Sensor | Respuestas | Filas marcadas válidas por el software entonces | RTT medio |
|---|---|---|---|
| #2 | 113/113 | 113 | 28 ms |
| #3 | 113/113 | 113 | 32 ms |
| #4 | 113/113 | 113 | 34 ms |

Ciclo medio 93 ms; p95 109 ms; ocupación estimada del ciclo ~9 % con intervalo
1 s; ciclos perdidos anotados: 0. Son tiempos del programa, no de respuesta
física de los detectores. La prueba usaba el bloque 2–9 para todos; todavía no
incluía los registros 10–11 del SGR4.

`valid` refleja las reglas del software, no una validación independiente del
mapa o de la calibración. Ver [guía de registros](lectura-registros.md).

## 3. Mediciones y lectura actual de los resultados

| Sensor | Rango anotado del registro 5 convertido | Interpretación |
|---|---|---|
| #2 | −160 a −163 W/m² | Compatible con radiación neta de un pirgeómetro; no comparar con onda corta |
| #3 | 950–958 W/m² | Irradiancia de onda corta |
| #4 | 940–947 W/m² | Irradiancia de onda corta; diferencia aproximada de 10 W/m² respecto a #3 |

La interpretación original llamaba «anómalo» al valor de #2 y sugería un fallo.
Esa conclusión se basaba en tratarlo como piranómetro y queda retirada. SMP
§15.2 p. 43 identifica el registro 5 como radiación neta para SGR; SGR §7.1.5
p. 17 explica por qué puede ser negativa.

Ejemplo ilustrativo: con temperatura del cuerpo 25 °C y neta −160 W/m²,
`Ld↓ = neta + σT⁴` da aproximadamente 288 W/m². La temperatura de ese ejemplo
no es una medición recuperada de esta prueba. No permite afirmar cielo despejado
ni reconstruir Ld↓ real, porque tampoco se leyeron 10–11.

La hora 18:20 sin ubicación, orientación, fuente de radiación y condiciones
anotadas no basta para explicar las irradiancias. Que los valores publicados
sean enteros tampoco prueba por sí solo factor 0: puede haber redondeo del
sensor o de la presentación. El factor debe verificarse en una lectura real.

Humedad y presión no deben interpretarse con estos firmwares sin confirmar
16–17: en mapas anteriores pueden tener otro significado ([D12](decisiones-tecnicas.md#d12-compatibilidad-con-el-firmware-detectado-en-la-prueba-01)).

## 4. Comprobaciones siguientes

1. Ejecutar `python -m smp22x info` con hardware y registrar tipo, mapa, factor,
   modo y flags. Comparar Ld↓ con neta + σT⁴; el umbral ±5 W/m² es del proyecto.
   Una coincidencia es evidencia de consistencia, no prueba completa del mapa.
2. Confirmar con el fabricante códigos 607/611 y el mapa para data model 102,
   especialmente 10–11 y 16–17, aportando las series anteriores.
3. Repetir una prueba más larga con fecha/zona, ubicación, orientación, fuente,
   sombreado, limpieza, tiempo desde encendido y cambios de condiciones anotados.
4. Conservar la lectura original antes de aplicar nuevas conversiones y comparar
   solo magnitudes equivalentes. Las lecturas de un ciclo son secuenciales.

Referencias locales: [manual SMP V20](../references/pyranometer-SMP22x/Operational_Manual_SMP10x_SMP22x_Smart_Pyranometers_V20.pdf#page=43)
y [manual SGR V10](../references/pyrgeometer-SGR4/Operational_Manual_CGR4_SGR4_V10.pdf#page=17).
