# Mantenimiento y bitácora

## Calendario del fabricante

Piranómetros SMP22x ([manual SMP §10.1, pp. 30–31](/manuales/pyranometer-SMP22x/Operational_Manual_SMP10x_SMP22x_Smart_Pyranometers_V20.pdf#page=30))
y pirgeómetro SGR4 ([manual SGR4 §9.1, p. 25](/manuales/pyrgeometer-SGR4/Operational_Manual_CGR4_SGR4_V10.pdf#page=25)):

| Frecuencia | Tarea | Categoría en la bitácora |
|---|---|---|
| Dos veces por semana | Limpiar domo o ventana con paño seco sin pelusa; agua destilada si hace falta; alcohol puro solo con suciedad severa; sin dejar marcas | Limpieza de domo |
| Mensual | Comprobar nivelación y ajustar; verificar que la pantalla solar esté firme | Nivelación |
| Anual | Revisar conectores, cables, fijaciones y soportes; limpiar la pantalla solar | Mantenimiento |
| 2 años | Recalibración (SMP: para cumplir IEC 61724-1 Clase A; SGR4: control de sensibilidad o recalibración) | Mantenimiento |
| 10 años | Cambio de desecante interno (fabricante) | Desecante |

El manual del SMP recomienda, idealmente, limpiar el domo cada mañana antes
del amanecer. Rocío, escarcha o hielo sobre el domo pueden aumentar la señal
hasta un 50 % en las primeras horas tras el amanecer.

## Qué anotar

Anote todo lo que pueda explicar un cambio en los datos:

- Limpiezas, aunque no se viera suciedad.
- Nivelaciones y cualquier movimiento de un instrumento.
- Sombras: personas, vehículos, objetos nuevos o mantenimiento cercano.
- Rocío, escarcha, nieve, polvo o aves sobre los domos.
- Reemplazo de cables, conversor RS-485 o fuente.
- Paradas y arranques del SOLYS2 (el cambio de configuración lo anota solo).
- Ajustes del reloj del PC.

Si lo registra después, ponga la **hora real del hecho**. Las notas aparecen
como líneas punteadas en los gráficos, en las figuras y en `bitacora.csv` de
cada ZIP.

## Anular una nota

Una nota no se borra. Si fue un error, use **Anular** e indique el motivo:
queda tachada, con quién la anuló y cuándo.

## Después de un mantenimiento

- Tras encender un sensor, hasta 3 h de estabilización térmica (SMP §7.3.5).
- Tras cambiar dirección o baudios con SmartExplorer, el sensor tarda ~1 min en
  reiniciarse (SMP §8.1.3). **SmartExplorer y este programa no pueden usar el
  puerto a la vez**: detenga la adquisición antes.
- Revise en la línea de tiempo que los flags de arranque (`ERROR`,
  `POWER_FAILED`) se hayan borrado.
