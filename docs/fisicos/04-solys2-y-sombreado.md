# SOLYS2 y sombreado

El SOLYS2 es un seguidor solar de dos ejes con GPS. Con una **bola de sombra**
tapa el sol a un piranómetro, que así mide solo la radiación del cielo (difusa,
DHI). El piranómetro sin sombra mide la global (GHI).

## Activar o desactivar el seguidor

Habrá días sin seguidor por mantenimiento. En **Instrumentos**:

1. Marque qué piranómetro tiene la bola de sombra.
2. Cambie el interruptor **Seguidor SOLYS2**.
3. Escriba su nombre y, si el cambio fue antes, la hora real de vigencia con
   una nota que explique el motivo.
4. Guarde: se crea una versión nueva y una entrada en la bitácora.

| SOLYS2 | Piranómetro con bola | Qué se muestra |
|---|---|---|
| En uso | Mide difusa (DHI) | Difusa, cociente DHI/GHI, prueba de sombreado y DNI estimada |
| Inactivo | Sombra no garantizada | Se registra, pero no como difusa; las funciones del seguidor dicen «no aplica: SOLYS2 inactivo» |

Las lecturas nunca se borran ni se reinterpretan en silencio: cada intervalo
usa la configuración que tenía.

## Pruebas que dependen del seguidor

- **Cociente difusa/global (BSRN)**: detecta una difusa mayor que la global.
- **Sombreado (proyecto)**: con sol alto y cielo bastante despejado, una difusa
  mayor al 80 % de la global indica que la bola no tapa el sol. Si el
  interruptor dice «en uso» y esta prueba falla, el monitor muestra un aviso.

Ver fórmulas en [calidad de datos](03-calidad-de-datos.md).

## DNI estimada

```
DNI estimada = (GHI − DHI) / cos z        solo con z < 80°
```

Es una cantidad **derivada**, no una medición: hereda los errores de GHI, de
DHI y del cálculo del cenit, y se vuelve inestable con el sol bajo porque se
divide por un número pequeño. Sirve para ver la forma del día y detectar
problemas, no para reemplazar a un pirheliómetro.

## Pendiente

- **Pirheliómetro**: cuando se integre habrá DNI medida y el **test de cierre**
  `GHI ≈ DNI·cos z + DHI`, la comprobación más útil de una estación solar. BSRN
  pide diferencias menores a 2 % o 15 W/m² (lo que sea menor) con el sol a más
  de 10°, y a 3,5 % o 20 W/m² con sol bajo o cielo cambiante
  ([BSRN Operations Manual V2.1 §9.3.2.1, p. 73](/manuales/BSRN-standars/BSRN_Operations_Manual_V21__WCRP21_TD1274_BSRN.pdf#page=85)).
- **Lectura del SOLYS2 por RS-485 o Ethernet**: permitirá leer su posición solar,
  su hora GPS y su estado de seguimiento. El SOLYS2 usa su propio protocolo de
  comandos, no Modbus ([manual SOLYS, anexos A–D](/manuales/solys2-sun-traker/solys_suntracker_manual.pdf)).
  Hoy el estado se indica a mano.
- **Pirgeómetro sombreado**: el manual del SGR4 recomienda tapar el sol al
  pirgeómetro para evitar que caliente la ventana
  ([§7.1.5 p. 16](/manuales/pyrgeometer-SGR4/Operational_Manual_CGR4_SGR4_V10.pdf#page=16)).
  Marcar la bola de sombra en un pirgeómetro no cambia su magnitud (sigue siendo Ld↓).

## Sitio

La posición del sol se calcula con la latitud, la longitud y la altura
configuradas (algoritmo NOAA/Meeus con refracción, validado contra el vector de
referencia de NREL SPA con diferencias menores a 0,003°). El GPS del SOLYS2
muestra estos datos en su interfaz web.
