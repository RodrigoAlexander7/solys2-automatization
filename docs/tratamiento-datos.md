# Qué se hace con los datos de los sensores

## Medidas originales, conversiones y derivadas

No es correcto decir que todos los números se muestran tal como llegan por el
cable: llegan enteros codificados. Se interpreta signo y escala del manual para
expresarlos en unidades físicas. **Una vez convertida, la medida principal se
expresa tal como la entrega el sensor; el programa no aplica una nueva corrección
de calibración, compensación térmica, filtro, normalización solar o ajuste empírico.**

| Etapa | Qué se hace | Qué se guarda o muestra |
|---|---|---|
| Recepción | FC04 obtiene palabras U16 | `raw_registers` en SQLite, sin modificar; también en lecturas con escala desconocida |
| Decodificación | Interpretar S16 y factor; temperatura/tensión ÷10 | Valor físico, factor, estado y `conversion_version` |
| Sensor SMP | Se usa registro 5 compensado por el propio instrumento | Irradiancia de onda corta en W/m² |
| Sensor SGR | Se conserva registro 5 neto y registro 10 Ld↓ | Magnitud principal Ld↓; neta aparte. No sumar σT⁴ otra vez al registro 10 |
| Calidad | Respuesta, modo, presencia de magnitud y máscara de flags | `valid`, `ok`, `error_code` y error; no se borra la muestra inválida |
| Consistencia SGR | `neta + 5,67·10⁻⁸·(T_°C+273,15)⁴` | `lw_down_calc` y `lw_check_delta`; nunca reemplazan Ld↓ leído |
| Comparación SMP | `E3−E4`; porcentaje `100·(E3−E4)/((E3+E4)/2)` | Solo pareja válida del mismo ciclo; % ausente si media ≤100 W/m² |
| Promedio de minuto | Media, mínimo/máximo, desviación poblacional sobre válidas | Irradiancia SMP o Ld↓ SGR; números y cobertura acompañan al resultado |
| Presentación | Redondeo para tabla/CSV, gráfica y conversión de UTC a hora local | La BD conserva flotantes antes del redondeo de presentación |

La fórmula SGR procede de [manual SGR §7.1.5 p. 17](../references/pyrgeometer-SGR4/Operational_Manual_CGR4_SGR4_V10.pdf#page=17).
Las escalas proceden de [SMP §15.6 pp. 48–49](../references/pyranometer-SMP22x/Operational_Manual_SMP10x_SMP22x_Smart_Pyranometers_V20.pdf#page=48).
El porcentaje, el umbral de baja irradiancia, la máscara de validez y la
cobertura mínima de minuto son decisiones del proyecto. No son especificaciones
de calibración del fabricante.

## Significados que no deben confundirse

- `raw_registers`: palabras originales; `raw_irradiance` o `raw_irradiance_wm2`:
  canal bruto del sensor **ya convertido a W/m²**, no palabras del bus.
- `stdev` del registro 7: variabilidad del canal 1 desde su lectura anterior;
  en SGR4 corresponde a la neta, no a Ld↓. La desviación de minuto de Ld↓ se
  calcula aparte con sus muestras válidas.
- `lw_down_calc` no es una medición independiente ni un fallback automático.
  Una discrepancia exige investigar. ±5 W/m² es del proyecto y no cambia hoy
  el `valid` del sensor de manera automática.
- El tiempo lo toma el PC después de leer; los sensores de un ciclo no son
  simultáneos. RTT mide comunicación, no tiempo de respuesta física.
- Un hueco no es cero. Los fallos se guardan; no se interpolan ni se completan
  valores artificiales. `complete=1` en CSV significa ≥80 % de válidas esperadas,
  no minuto perfecto. Los minutos inicial y final pueden ser parciales.

SQLite guarda configuración e identificación del instrumento junto con cada
corrida. Si el proceso muere, solo los commits completos son recuperables; el
hardware no tiene un histórico que permita recuperar lecturas nunca adquiridas.
Los CSV son proyecciones y exportaciones de esos valores, no otro origen de verdad.
Los perfiles antiguos 100/102 y el mapa SGR 10–11 siguen pendientes de confirmación
experimental; véase la [guía de registros](lectura-registros.md).
