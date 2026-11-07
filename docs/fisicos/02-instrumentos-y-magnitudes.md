# Instrumentos y magnitudes

## Qué mide cada equipo

| Equipo | Tipo | Magnitud principal | Referencia |
|---|---|---|---|
| Kipp & Zonen SMP22x | Piranómetro, ISO 9060:2018 Clase A espectralmente plano | Irradiancia de onda corta sobre plano horizontal, W/m² | [Hoja de datos SMPx 2025](/manuales/pyranometer-SMP22x/KippZonen_Data_sheet_SMPx_2025_online.pdf) |
| Kipp & Zonen SGR4 | Pirgeómetro, 4,5–42 µm | Onda larga descendente Ld↓, W/m² | [Manual SGR4 §5.1](/manuales/pyrgeometer-SGR4/Operational_Manual_CGR4_SGR4_V10.pdf) |

Son instrumentos *smart*: calculan la irradiancia internamente, con su propia
calibración y compensación de temperatura, y la entregan por Modbus RTU.

## El rol depende de cómo está montado

El mismo modelo puede medir cosas distintas. El programa asigna a cada lectura
un **rol** según la configuración vigente en ese instante:

| Instrumento | Bola de sombra | SOLYS2 | Rol | Se presenta como |
|---|---|---|---|---|
| Pirgeómetro | cualquiera | cualquiera | `LWD` | Onda larga descendente |
| Piranómetro | no | cualquiera | `GHI` | Global horizontal |
| Piranómetro | sí | en uso | `DHI` | Difusa horizontal |
| Piranómetro | sí | inactivo | `SHADE_OFF` | Sombra no garantizada: se guarda, no se usa como difusa |

Cambiar el rol hoy no cambia cómo se interpretan los días anteriores.

## De la palabra Modbus a W/m²

Cada ciclo se lee un bloque de registros por sensor (FC04). Los enteros se
interpretan con el signo y el factor de escala del manual
([SMP §15.6, pp. 48–49](/manuales/pyranometer-SMP22x/Operational_Manual_SMP10x_SMP22x_Smart_Pyranometers_V20.pdf#page=48)):

```
valor = S16(registro) / divisor(IO_SCALE_FACTOR)     divisor: 2→100, 1→10, 0→1, −1→0,1
temperatura = S16(registro 8) / 10   °C
tensión     = S16(registro 9) / 10   V
```

**Después de convertir, la medida principal se expresa tal como la entrega el
sensor.** El programa no aplica calibraciones adicionales, compensación térmica,
filtros, normalización solar ni ajustes empíricos. Las palabras originales se
guardan junto a cada muestra para poder reprocesar.

## Pirgeómetro: Ld↓ leída y comprobación

El SGR4 entrega la radiación neta del detector (registro 5, normalmente
negativa) y Ld↓ ya calculada (registro 10). El programa usa el registro 10 como
magnitud principal y calcula aparte la comprobación del
[manual SGR4 §7.1.5 p. 17](/manuales/pyrgeometer-SGR4/Operational_Manual_CGR4_SGR4_V10.pdf#page=17):

```
Ld↓ calculada = neta + σ·(T_cuerpo + 273,15)⁴     σ = 5,67·10⁻⁸ W m⁻² K⁻⁴
```

En los gráficos aparece como línea discontinua. Si se separa de la Ld↓ leída
más de unos W/m², hay que revisar la interpretación de registros del firmware
(ver [estado de validación](08-estado-de-validacion.md)). No es una calibración
ni reemplaza nunca a la Ld↓ leída.

## Tiempos de respuesta y estabilización

| Dato | SMP22x | SGR4 |
|---|---|---|
| Respuesta 63 % / 95 % | < 0,7 s / < 2 s | < 6 s / < 18 s |
| Estabilización tras encender | hasta 3 h (§7.3.5) | — |
| Reinicio tras cambiar dirección o baudios | ~1 min (§8.1.3) | ~1 min |

El registro del SMP se actualiza una vez por segundo: leer más rápido repite
valores. Se lee cada 1 s y se guardan además promedios de 1 minuto, como
recomienda el fabricante (§9.2).

## Diagnósticos internos

- **Temperatura del cuerpo**: interna del instrumento, no la del aire.
- **Tensión de alimentación**: medida en el conector del instrumento.
- **RTT**: tiempo de la consulta Modbus; no es el tiempo de respuesta físico.
- **Humedad y presión internas**: se leen cada 60 s, pero su significado en el
  firmware del montaje no está confirmado. No usarlas como diagnóstico fiable.
