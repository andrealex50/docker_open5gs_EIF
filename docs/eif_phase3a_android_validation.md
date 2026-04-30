# Fase 3A Android — Validação end-to-end EIF / Open5GS

## Contexto

Este documento resume a validação da fase 3A Android do projeto EIF (Energy Information Function) sobre Open5GS, incluindo os resultados observados e os comandos utilizados durante os testes.

## Objetivo

Validar o fluxo completo:

`Android estimator -> Energy Collector -> EIF -> notifUri`

A fase 3A Android usa um **estimador Android**, não uma medição real de rails/consumidores do modem. O objetivo é fornecer uma estimativa defensável de energia rádio celular para `UE_ENERGY`.

## Modelo Android usado

Fonte:

- `source = android-radio-profile-estimator`

Método:

- `BatteryStats + power_profile radio.active`

Fórmula:

```text
E_radio[J] = radio.active[mA] × voltage[mV] × mobile_active_time[s] / 1_000_000
```

Limitações:

- não é uma medição direta dos rails do modem;
- não é energia isolada de 5G;
- `modem.controller.*` não forneceu valores úteis no dispositivo;
- os TX power bins são guardados como metadados/contexto, não entram diretamente no cálculo nesta versão.

## Resultado funcional final

A validação end-to-end ficou concluída com sucesso.

Foi observada uma notificação real no `notifUri` com:

- `event = UE_ENERGY`
- `subscSetId = set2`
- `subId = 2`
- `energyInfo.energy = 18.515095`

Payload recebido no `notifUri`:

```json
{
  "subId": "2",
  "reports": [
    {
      "event": "UE_ENERGY",
      "subscSetId": "set2",
      "timeStamp": "2026-04-30T20:37:41.898216Z",
      "energyInfo": {
        "energy": 18.515095,
        "energyReportTimeStamp": "2026-04-30T20:37:41.898216Z"
      }
    }
  ]
}
```

Isto valida a cadeia:

```text
Android estimator -> Energy Collector -> EIF -> notifUri
```

## Observação importante sobre os valores 0.25 J

Durante o teste, apareceram várias notificações com:

```text
energyInfo.energy = 0.25
```

Isto aconteceu porque o EIF notifica periodicamente (`repPeriod = 5`) e o Energy Collector só devolve a energia Android quando a janela pedida apanha a timestamp do sample Android.

Quando a janela não apanha esse sample, o collector cai no fallback `traffic-estimator`, que neste cenário devolveu aproximadamente:

```text
0.05 W × 5 s = 0.25 J
```

Portanto:

- `18.515095 J` corresponde ao sample Android real;
- `0.25 J` corresponde ao fallback fora da janela do sample Android.

## Evidência principal do teste final

### Sample Android publicado

```json
{
  "supi": "imsi-001011234567895",
  "ue_ip": "192.168.100.2",
  "source": "android-radio-profile-estimator",
  "method": "BatteryStats + power_profile radio.active",
  "start": "2026-04-30T20:36:54.184103Z",
  "end": "2026-04-30T20:37:39.184286Z",
  "radioActiveMa": 103.0,
  "voltageMv": 3930.0,
  "mobileActiveTimeSec": 45.74,
  "mobileActive5gTimeSec": 43.551,
  "cellularRxTimeSec": 26.493,
  "cellularTxPowerBinsSec": {
    "lt0dBm": 0.031,
    "dBm0To8": 1.584,
    "dBm8To15": 0.412,
    "dBm15To20": 0.023,
    "gt20dBm": 3.31
  },
  "cellularRxBytes": 14450000,
  "cellularTxBytes": 1090000,
  "telephony": {
    "network": "LTE",
    "overrideNetwork": "NR_NSA",
    "lteRsrp": -104,
    "lteRsrq": -9,
    "lteRssnr": 15,
    "nrSsRsrp": -107,
    "nrSsRsrq": -11,
    "nrSsSinr": 14,
    "primaryServing": "LTE",
    "secondaryServing": "NR"
  },
  "energyInfo": {
    "energy": 18.515095
  }
}
```

### Resposta do collector ao guardar o sample

```json
{
  "status": "stored",
  "source": "android",
  "sample": {
    "supi": "imsi-001011234567895",
    "ue_ip": "192.168.100.2",
    "timestamp": "2026-04-30T20:37:39.184286Z",
    "energy_joules": 18.515095,
    "current_now_ua": null,
    "voltage_now_uv": null,
    "source": "android"
  },
  "total_samples": 3
}
```

### Notificação final recebida no notify server

```json
{
  "subId": "2",
  "reports": [
    {
      "event": "UE_ENERGY",
      "subscSetId": "set2",
      "timeStamp": "2026-04-30T20:37:41.898216Z",
      "energyInfo": {
        "energy": 18.515095,
        "energyReportTimeStamp": "2026-04-30T20:37:41.898216Z"
      }
    }
  ]
}
```

## Comandos utilizados

## 1. Energy Collector local (primeiros testes)

Criação de virtualenv e arranque local:

```bash
cd energy-collector
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install fastapi "uvicorn[standard]" pydantic
python -m uvicorn app:app --host 0.0.0.0 --port 8088
```

Teste de health:

```bash
curl http://127.0.0.1:8088/health
```

## 2. Primeira recolha Android por janela

```bash
python3 scripts/android_radio_estimator.py   --duration 45   --output-dir android-radio-window   --supi imsi-001011234567895   --ue-ip 192.168.100.2   --collector-url http://127.0.0.1:8088   --post
```

## 3. Validação do collector com janela exata

Exemplo de consulta válida:

```bash
curl "http://127.0.0.1:8088/energy/v1/report?supi=imsi-001011234567895&event=UE_ENERGY&start=2026-04-30T20:09:29.395857Z&end=2026-04-30T20:10:14.396102Z"
```

## 4. Levantar o EIF e dependências mínimas em Docker

```bash
docker compose -f deploy-all.yaml up -d --build mongo nrf scp eif
docker compose -f deploy-all.yaml ps
docker ps --format 'table {{.Names}}\t{{.Status}}'
grep '^EIF_IP=' .env
```

## 5. Confirmar que o EIF está a escutar

```bash
docker exec -it eif sh -c 'ss -ltnp | grep 7777 || netstat -ltnp | grep 7777'
```

## 6. Testes ao endpoint SBI do EIF

Teste HTTP/1.1 (falha/empty reply, esperado para h2c):

```bash
curl -k http://172.22.0.43:7777/neif-ee/v1/subscriptions
```

Teste correto em HTTP/2 h2c:

```bash
curl --http2-prior-knowledge -v http://172.22.0.43:7777/neif-ee/v1/subscriptions
```

## 7. Criar subscrição EIF

Subscrição `subId 1`:

```bash
curl --http2-prior-knowledge -v -X POST http://172.22.0.43:7777/neif-ee/v1/subscriptions   -H "Content-Type: application/json"   -d '{
    "notifUri": "http://172.22.0.45:9998/notify",
    "eventsSubscSets": {
      "set1": {
        "event": "UE_ENERGY",
        "subscSetId": "set1",
        "supi": "imsi-001011234567895",
        "repPeriod": 5
      }
    }
  }'
```

Subscrição `subId 2`:

```bash
curl --http2-prior-knowledge -v -X POST http://172.22.0.43:7777/neif-ee/v1/subscriptions   -H "Content-Type: application/json"   -d '{
    "notifUri": "http://172.22.0.45:9998/notify",
    "eventsSubscSets": {
      "set2": {
        "event": "UE_ENERGY",
        "subscSetId": "set2",
        "supi": "imsi-001011234567895",
        "repPeriod": 5
      }
    }
  }'
```

## 8. Apagar subscrição antiga

```bash
curl --http2-prior-knowledge -v -X DELETE http://172.22.0.43:7777/neif-ee/v1/subscriptions/1
```

## 9. Arrancar o Energy Collector na rede Docker correta

```bash
docker rm -f energy-collector 2>/dev/null || true

docker run -d   --name energy-collector   --network docker_open5gs_default   --ip 172.22.0.44   -v "$PWD/energy-collector":/app   -w /app   python:3.12-slim   sh -c "pip install fastapi 'uvicorn[standard]' pydantic && uvicorn app:app --host 0.0.0.0 --port 8088"
```

## 10. Confirmar conectividade EIF -> Collector

```bash
docker exec -it eif sh -c 'curl -v http://172.22.0.44:8088/health'
```

## 11. Inserir sample Android diretamente no collector Docker

```bash
curl -X POST http://172.22.0.44:8088/samples/android   -H "Content-Type: application/json"   -d '{
    "supi": "imsi-001011234567895",
    "ue_ip": "192.168.100.2",
    "timestamp": "2026-04-30T20:10:14.396102Z",
    "energy_joules": 18.540596,
    "source": "android"
  }'
```

## 12. Confirmar report do collector Docker

```bash
curl "http://172.22.0.44:8088/energy/v1/report?supi=imsi-001011234567895&event=UE_ENERGY&start=2026-04-30T20:09:29.395857Z&end=2026-04-30T20:10:14.396102Z"
```

## 13. Arrancar o notify server HTTP/2 h2c

Script usado:

```bash
cd scripts
./notify_h2_server.sh
```

Esse script arranca um servidor HTTP/2 h2c inline em Node na rede `docker_open5gs_default`, IP `172.22.0.45`, porta `9998`.

## 14. Confirmar conectividade EIF -> notifUri

Teste de reachability:

```bash
docker exec -it eif sh -c 'curl -v http://172.22.0.45:9998/notify'
```

## 15. Teste final Android -> Collector Docker -> EIF -> notifUri

```bash
python3 scripts/android_radio_estimator.py   --duration 45   --output-dir android-radio-window   --supi imsi-001011234567895   --ue-ip 192.168.100.2   --collector-url http://172.22.0.44:8088   --post
```

## 16. Observação contínua dos logs do EIF

```bash
docker logs -f eif
```

## Conclusão final

A fase 3A Android ficou validada com sucesso como fonte de energia para `UE_ENERGY`.

Foi demonstrado que:

1. o Android estimator produz uma estimativa coerente de energia rádio baseada em `BatteryStats + power_profile`;
2. o Energy Collector aceita e expõe a energia Android em `/energy/v1/report`;
3. o EIF consulta essa energia e constrói `EnergyEeReport`;
4. o `notifUri` recebe `EnergyEeNotif` por HTTP/2 h2c;
5. foi observada uma notificação real com `energyInfo.energy = 18.515095`.

Assim, a integração end-to-end ficou validada:

```text
Android estimator -> Energy Collector -> EIF -> notifUri
```
