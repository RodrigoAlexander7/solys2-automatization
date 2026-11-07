# Calidad de datos

Hay dos niveles distintos que no deben confundirse:

1. **Validez de la lectura** (`valid`): lo que informa el propio sensor.
2. **Pruebas de calidad** (QC): comprobaciones físicas que hace el programa.

Ninguno de los dos modifica el valor medido. Una lectura inválida o con QC
fallido se guarda igual y se marca.

## Validez de la lectura

Una muestra es válida si el sensor respondió, trae su magnitud principal, está
en modo normal y no tiene activos `VOID_DATA`, `OVERFLOW`, `UNDERFLOW`,
`ADC_ERROR`, `CALIBRATION_ERROR` ni `BODY_TEMP_ERROR`
([SMP §15.6](/manuales/pyranometer-SMP22x/Operational_Manual_SMP10x_SMP22x_Smart_Pyranometers_V20.pdf#page=48)).

- `ERROR` y `POWER_FAILED` se activan siempre al encender el sensor; por sí
  solos no invalidan el dato. El programa los borra con el coil `IO_CLEAR_ERROR`.
- `DESICCANT_WARNING` pide cambiar el desecante: queda registrado y aparece en
  la línea de tiempo, pero no invalida el dato.

## Pruebas BSRN

Fuente: C. N. Long y E. G. Dutton (2010), *BSRN Global Network recommended QC
tests, V2.0*. Se aplican a cada muestra de 1 s y a cada promedio de 1 minuto.
El [manual de operaciones BSRN V2.1](/manuales/BSRN-standars/BSRN_Operations_Manual_V21__WCRP21_TD1274_BSRN.pdf#page=84)
(§9.3) describe la verificación por límites en general; los valores numéricos
de la tabla son los del documento de Long y Dutton, que no está en `references/`.

`Sa = 1361 W/m² / R²` es la irradiancia extraterrestre con la distancia
Tierra-Sol del día y `μ0 = max(cos z, 0)`, con `z` el ángulo cenital solar.

| Prueba | Rol | Falla si |
|---|---|---|
| Físicamente posible | GHI | `GHI < −4` o `GHI > 1,5·Sa·μ0^1,2 + 100` |
| Físicamente posible | DHI | `DHI < −4` o `DHI > 0,95·Sa·μ0^1,2 + 50` |
| Físicamente posible | Ld↓ | `Ld↓ < 40` o `Ld↓ > 700` |
| Extremadamente raro | GHI | `GHI < −2` o `GHI > 1,2·Sa·μ0^1,2 + 50` |
| Extremadamente raro | DHI | `DHI < −2` o `DHI > 0,75·Sa·μ0^1,2 + 30` |
| Extremadamente raro | Ld↓ | `Ld↓ < 60` o `Ld↓ > 500` |
| Cociente difusa/global | GHI y DHI | `DHI/GHI ≥ 1,05` con z < 75°, o `≥ 1,10` con 75° ≤ z < 93°; solo si GHI > 50 W/m² |

Ejemplo: con el sol a 10° sobre el horizonte (z = 80°), una global mayor a unos
350 W/m² es físicamente imposible.

> Los valores negativos nocturnos de pocos W/m² son normales en piranómetros sin
> ventilación (offset térmico; la hoja de datos indica < ±5–7 W/m² según modelo).
> Con −2 a −4 W/m² la prueba «extremadamente raro» ya los marca como sospechosos.

## Pruebas del proyecto

No son de BSRN ni del fabricante; sus umbrales se ajustan en **Instrumentos**.

| Prueba | Aplica | Falla si | Por qué |
|---|---|---|---|
| Sombreado | SOLYS2 en uso, z < 75°, GHI > 50, kt > 0,6 | `DHI/GHI > 0,8` | Con cielo bastante despejado la difusa es una fracción menor de la global. Si se acerca a la global, la bola probablemente no tapa el sol. El cociente BSRN no lo detecta porque DHI ≈ GHI pasa esa prueba |
| Comparación entre iguales | dos instrumentos del mismo rol, ambos válidos, media > 100 W/m² | diferencia relativa > 5 % | Detecta domo sucio, desnivel o deriva de uno de los dos |

`kt = GHI / (Sa·μ0)` es el índice de claridad.

## Resultado por lectura

Cada prueba da `ok`, `fail` o `na` (no aplica) y el resumen es:

| Resumen | Significado |
|---|---|
| `bad` | Falla un límite físicamente posible |
| `suspect` | Falla alguna otra prueba |
| `ok` | Al menos una prueba aplicó y ninguna falló |
| `na` | Ninguna prueba aplicó |

Motivos frecuentes de «no aplica»: lectura inválida, sitio sin configurar (las
pruebas de onda corta necesitan la posición del sol), noche, global ≤ 50 W/m²,
SOLYS2 inactivo o falta un instrumento con el que comparar.

## Lo que el QC no puede detectar

- Una deriva lenta y común a todos los instrumentos.
- Un desnivel cuando no hay otro instrumento del mismo rol para comparar.
- Errores de Ld↓ dentro de 60–500 W/m²: sin temperatura del aire no se aplican
  las pruebas de onda larga que dependen de ella.
- La calidad de la DNI estimada: es derivada (ver [SOLYS2 y sombreado](04-solys2-y-sombreado.md)).
