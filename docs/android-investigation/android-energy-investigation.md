Resultado da investigação Android/UE:

Foram testadas fontes Android via ADB:
- dumpsys batteryproperties
- dumpsys battery
- dumpsys batterystats --charged
- dumpsys powerstats
- /sys/class/power_supply

No dispositivo Samsung SM-A236B com Android 14/API 34, não foi possível obter energia específica do modem/rádio.

O sistema expõe métricas globais de bateria e métricas de atividade celular, como tempo ativo móvel, tempo ativo 5G, tempo RX/TX celular e volume de dados móveis. No entanto, estas métricas não representam energia real do modem.

Os ficheiros sysfs relevantes para corrente/tensão/energia não estão acessíveis via ADB normal, e o PowerStats/BatteryStats não expõe energy consumer stats suportados neste dispositivo.

