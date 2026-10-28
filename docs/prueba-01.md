# Prueba 01 — 2026-09-28 18:20, COM10

**Comando:** `python -m smp22x log -d 30m --label prueba01`. Se detuvo con Ctrl+C a los 113 ciclos (~2 min).

**Carpeta de datos:** `data/2026-09-28_182028_prueba01/`

## Comunicación: correcta

| Sensor | Respuestas | Válidas | RTT medio |
|---|---|---|---|
| #2 | 113/113 | 113 | 28 ms |
| #3 | 113/113 | 113 | 32 ms |
| #4 | 113/113 | 113 | 34 ms |

- **Ciclo completo:** 93 ms de media (p95: 109 ms).
- **Ocupación del bus:** ~9 % leyendo cada 1 s.
- **Ciclos perdidos:** 0.

Confirma el presupuesto de tiempo de [decisiones-tecnicas.md D10](decisiones-tecnicas.md#d10-presupuesto-de-tiempo-del-bus): ~21 ms en el cable + ~10 ms de latencia USB y respuesta del sensor.

## Identificación: firmware distinto al del manual V20

| Sensor | Serie | IO_DEVICE_TYPE | Data model | Software / hardware |
|---|---|---|---|---|
| #2 | 240466 | 611 | 102 | 300 / 5 |
| #3 | 240349 | 607 | 100 | 201 / 5 |
| #4 | 240350 | 607 | 100 | 201 / 5 |

- Los tres sensores son del lote 2024 (manual V20 §15.10).
- Ninguno reporta el código 636 (SMP22x) que documenta el manual V20 §15.6.
- El data model 100 corresponde al manual SMP de 2015, Apéndice A. En ese manual, el bloque de registros 2–9 es idéntico, así que la irradiancia se interpreta correctamente. Ver [D12](decisiones-tecnicas.md#d12-compatibilidad-con-el-firmware-detectado-en-la-prueba-01).
- El sensor #2 tiene un firmware distinto al de #3 y #4 (código 611, software 300). **Hay que confirmar que los tres son realmente SMP22x.**

## Mediciones: el sensor #2 da un valor anómalo

> **Actualización:** el sensor #2 es un **pirgeómetro SGR4**, no un SMP22x. El
> registro 5 que se leyó como "irradiancia" es, en el SGR4, la **radiación neta
> de onda larga** (manual V20 §15.2: "Net radiation for SGR"), que es negativa
> cuando el cielo está más frío que el sensor. Un valor de −160 W/m² es normal
> con cielo despejado: con el cuerpo a unos 25 °C (σT⁴ ≈ 448 W/m²), equivale a
> una onda larga descendente Ld↓ ≈ 290 W/m². El análisis de abajo se escribió
> antes de saberlo y se deja como registro. Ver
> [D15](decisiones-tecnicas.md#d15-sensor-2-pirgeómetro-sgr4).

| Sensor | Rango observado | Observación |
|---|---|---|
| #2 | −160 a −163 W/m² | Negativo y casi constante |
| #3 | 950–958 W/m² | Coherente con #4 |
| #4 | 940–947 W/m² | ~10 W/m² por debajo de #3 |

- **El valor de #2 no es físicamente plausible.** El rango de salida del sensor llega hasta −400 W/m² (DS, "Serial output range"; manual V20 §15.8, UNDERFLOW). Pero el offset térmico total esperado es menor de 7 W/m² (DS, "total zero offset"), así que un valor de −160 W/m² no se explica por la física del sensor.
- **Causas posibles:**
  1. Una escala o un mapa de registros distinto por su firmware (IO_DEVICE_TYPE 611, data model 102). Es la causa más probable porque es el único sensor con ese firmware.
  2. Un fallo del sensor.
- Con `info -a 2` se puede ver el factor de escala y la irradiancia bruta (`raw_irradiance_wm2` en el CSV) de #2.
- **Hora de la prueba:** a las 18:20 hora local, unos 950 W/m² en #3 y #4 solo es posible con una fuente artificial (lámpara) o con el sol todavía alto. **Anotar las condiciones de cada prueba.**
- **Resolución:** todos los valores son enteros. Esto indica que IO_SCALE_FACTOR = 0, es decir, resolución de 1 W/m² (manual V20 §15.6). Se puede comprobar en el CSV.

## Próximos pasos

Actualizados tras identificar el SGR4:

1. Ejecutar `python -m smp22x info` y comprobar en el sensor #2 que la verificación "neta + σT⁴" coincide con Ld↓ (diferencia dentro de ±5 W/m²). Confirma que el firmware 102 tiene los registros 10–11.
2. Preguntar a OTT HydroMet a qué modelo corresponden los códigos 607 y 611 (manual V20 §12.1).
3. Repetir la prueba 30 min con el sol o la lámpara, anotando las condiciones. Una lámpara también calienta y cambia la lectura del pirgeómetro, así que anotar si estaba encendida.

Pasos originales ya resueltos: revisar la escala de #2 con `info -a 2` e intercambiar físicamente #2 y #3. El valor no era un fallo, era otra magnitud.
