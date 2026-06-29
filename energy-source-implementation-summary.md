# Energy Source Integration Summary

## Objetivo

O objetivo desta alteração foi evoluir o Energy Collector para deixar de depender apenas da estimativa por tráfego:

```text
E = P_idle * duration + alpha_rx * rx_bytes + alpha_tx * tx_bytes
```

Esse modelo continua disponível como fallback, mas agora o Collector também consegue usar uma fonte externa de energia baseada em Scaphandre/Prometheus.

A ideia final é:

```text
Scaphandre
   |
   v
Prometheus
   |
   v
Energy Collector
   |
   v
MongoDB
   |
   v
EIF /energy/v1/report
   |
   v
HTTP/2 notification
```

O EIF não foi alterado. Ele continua a consumir apenas:

```json
{
  "energyInfo": {
    "energy": 1.23
  }
}
```

## O Que Existia Antes

Antes, o Collector calculava energia com base em bytes UPF:

```text
energy = idle_power * duration + alpha_tx * tx_bytes + alpha_rx * rx_bytes
```

Isto permitia testar o EIF e gerar notificações `EnergyEeReport`, mas tinha uma limitação importante:

```text
UPF counters dizem quanto tráfego passou, mas não medem energia real.
```

Ou seja, era uma estimativa útil para laboratório, mas não uma fonte energética medida.

## O Que Foi Adicionado

Foi adicionada uma camada opcional de fonte de energia externa no Energy Collector.

Neste momento essa fonte é:

```text
Scaphandre via Prometheus
```

O Collector passa a conseguir:

1. Consultar Prometheus.
2. Pedir a energia medida numa janela temporal.
3. Normalizar o resultado para Joules.
4. Guardar essa medição no MongoDB.
5. Atribuir parte dessa energia a um UE, sessão ou flow com base nos bytes observados no UPF.
6. Continuar a devolver `energyInfo.energy` ao EIF.

## Configuração

Por defeito, nada muda:

```bash
ENERGY_SOURCE=traffic
```

Neste modo, o Collector usa o modelo antigo por tráfego.

Para ativar Prometheus/Scaphandre:

```bash
ENERGY_SOURCE=scaphandre_prometheus
PROMETHEUS_URL=http://<prometheus-host>:9090
PROMETHEUS_TIMEOUT_S=2
```

O PromQL usado por omissão é:

```promql
increase(scaph_host_energy_microjoules[{window}]) / 1000000
```

O `{window}` é substituído pelo Collector pela duração do intervalo pedido.

Exemplo:

```text
start=10:00:00
end=10:01:00
```

gera aproximadamente:

```promql
increase(scaph_host_energy_microjoules[60s]) / 1000000
```

## Scaphandre

Os dois servidores disponíveis são:

```text
server1: 10.255.35.93
server2: 10.255.35.34
```

Ambos expõem Intel RAPL em:

```text
/sys/class/powercap
```

Isto permite usar Scaphandre como fonte de energia RAPL baseada em hardware counters. A medição representa o domínio energético coberto pelo RAPL no host e não substitui um wattímetro externo.

Comando base para correr Scaphandre no servidor:

```bash
docker run -d \
  --name scaphandre \
  --privileged \
  -v /sys/class/powercap:/sys/class/powercap:ro \
  -p 8080:8080 \
  hubblo/scaphandre prometheus
```

Depois confirmar métricas:

```bash
curl -sS http://localhost:8080/metrics | grep -E 'scaph_.*energy|scaph_host_energy_microjoules' | head
```

## Prometheus

Prometheus deve fazer scrape ao Scaphandre.

Exemplo:

```yaml
scrape_configs:
  - job_name: scaphandre-server1
    static_configs:
      - targets: ["10.255.35.93:8080"]

  - job_name: scaphandre-server2
    static_configs:
      - targets: ["10.255.35.34:8080"]
```

Query para potência aproximada:

```promql
rate(scaph_host_energy_microjoules[30s]) / 1000000
```

Query para energia numa janela:

```promql
increase(scaph_host_energy_microjoules[1m]) / 1000000
```

O Collector usa a segunda abordagem, porque o EIF pede relatórios para uma janela temporal.

## Novos Endpoints Do Collector

### Estado Da Fonte De Energia

```bash
GET /energy-sources/status
```

Exemplo:

```bash
curl -sS http://172.22.0.44:8088/energy-sources/status | jq .
```

Resposta esperada em modo traffic:

```json
{
  "mode": "traffic",
  "enabled": false,
  "prometheusUrl": null,
  "storage": "mongodb"
}
```

Resposta esperada em modo Prometheus:

```json
{
  "mode": "scaphandre_prometheus",
  "enabled": true,
  "prometheusUrl": "http://<prometheus-host>:9090",
  "storage": "mongodb"
}
```

### Medição Direta De Uma Janela

```bash
GET /energy-sources/window?start=...&end=...
```

Exemplo:

```bash
START=$(date -u -d '1 minute ago' +%Y-%m-%dT%H:%M:%SZ)
END=$(date -u +%Y-%m-%dT%H:%M:%SZ)

curl -sS "http://172.22.0.44:8088/energy-sources/window?start=${START}&end=${END}" | jq .
```

Se Prometheus estiver configurado, devolve:

```json
{
  "status": "ok",
  "sample": {
    "source": "scaphandre_prometheus",
    "metric": "host_rapl_energy",
    "unit": "joules",
    "window_start": "...",
    "window_end": "...",
    "value": 12.34
  }
}
```

Se Prometheus não estiver configurado, devolve:

```json
{
  "status": "unavailable",
  "reason": "energy source disabled or no Prometheus value returned"
}
```

### Histórico De Atribuições

```bash
GET /energy-sources/attributions?limit=5
```

Exemplo:

```bash
curl -sS http://172.22.0.44:8088/energy-sources/attributions?limit=5 | jq .
```

Este endpoint mostra as últimas atribuições de energia feitas pelo Collector.

## MongoDB

Já existiam coleções para:

```text
ue_mappings
traffic_samples
android_samples
```

Agora foram adicionadas:

```text
energy_source_samples
energy_attributions
```

### energy_source_samples

Guarda medições normalizadas vindas de Prometheus/Scaphandre:

```json
{
  "source": "scaphandre_prometheus",
  "metric": "host_rapl_energy",
  "unit": "joules",
  "window_start": "...",
  "window_end": "...",
  "value": 12.34,
  "metadata": {
    "prometheus_url": "...",
    "promql": "..."
  }
}
```

### energy_attributions

Guarda como a energia medida foi atribuída:

```json
{
  "supi": "imsi-...",
  "event": "UE_ENERGY",
  "energy": 1.23,
  "txBytes": 1000000,
  "rxBytes": 5000000,
  "attribution": {
    "method": "traffic_share",
    "selectedBytes": 6000000,
    "totalTrackedBytes": 12000000,
    "ratio": 0.5,
    "measuredWindowEnergy": 2.46,
    "trafficEstimateEnergy": 0.25
  }
}
```

## Como Funciona A Atribuição

Scaphandre mede energia do host/pacote CPU, não energia direta por UE.

Então a atribuição é feita por quota de tráfego:

```text
UE_energy = measured_window_energy * (UE_bytes / total_tracked_bytes)
```

Onde:

```text
UE_bytes = tx_bytes + rx_bytes do UE filtrado
total_tracked_bytes = soma dos bytes de todas as samples na mesma janela
```

Exemplo:

```text
Energia medida por Scaphandre = 20 J
UE A = 6 MB
Total observado = 10 MB
```

Então:

```text
UE A recebe 60% da energia medida
UE_energy = 20 * 0.6 = 12 J
```

Isto é importante para explicar:

```text
Não é energia real medida diretamente no UE.
É energia medida no host e atribuída ao UE com base em tráfego observado.
```

## Resposta Do /energy/v1/report

O EIF continua interessado apenas nesta parte:

```json
{
  "energyInfo": {
    "energy": 12.0
  }
}
```

Mas para debug/laboratório, o Collector também pode devolver:

```json
{
  "source": "scaphandre_prometheus",
  "trafficEstimateEnergy": 0.25,
  "energySource": {
    "source": "scaphandre_prometheus",
    "metric": "host_rapl_energy",
    "unit": "joules",
    "value": 20.0
  },
  "attribution": {
    "method": "traffic_share",
    "selectedBytes": 6000000,
    "totalTrackedBytes": 10000000,
    "ratio": 0.6,
    "measuredWindowEnergy": 20.0,
    "trafficEstimateEnergy": 0.25
  }
}
```

Assim consegues comparar:

```text
trafficEstimateEnergy = valor antigo estimado só por tráfego
energyInfo.energy = valor final atribuído a partir da medição Scaphandre
```

## Fallback

Se Prometheus estiver desligado, indisponível ou não devolver valor válido, o Collector não falha.

Ele volta ao modelo antigo:

```text
source = traffic-estimator
energyInfo.energy = P_idle * duration + alpha_rx * rx_bytes + alpha_tx * tx_bytes
```

Isto mantém a demo e o EIF funcionais mesmo sem Scaphandre.

## Script De Validação

Foi criado:

```bash
scripts/validate_energy_source_integration.sh
```

O script faz:

1. Verifica `/health`.
2. Verifica `/energy-sources/status`.
3. Testa `/energy-sources/window`.
4. Regista mapping SUPI -> UE IP.
5. Insere sample de tráfego.
6. Pede relatório `UE_ENERGY`.
7. Pede relatório `SERVICE_FLOW_ENERGY` filtrado por `appId` e `flowDescs`.
8. Mostra últimas atribuições.
9. Se Mongo estiver ativo, mostra contadores e documentos recentes.

Comando:

```bash
./scripts/validate_energy_source_integration.sh
```

Com subscrição EIF:

```bash
CREATE_EIF_SUBSCRIPTION=true ./scripts/validate_energy_source_integration.sh
```

## Como Validar Tudo

### 1. Subir Scaphandre

No servidor escolhido:

```bash
docker rm -f scaphandre 2>/dev/null || true

docker run -d \
  --name scaphandre \
  --privileged \
  -v /sys/class/powercap:/sys/class/powercap:ro \
  -p 8080:8080 \
  hubblo/scaphandre prometheus
```

### 2. Confirmar Métricas

```bash
curl -sS http://localhost:8080/metrics | grep -E 'scaph_.*energy|scaph_host_energy_microjoules' | head
```

### 3. Confirmar Prometheus

```bash
curl -G http://<prometheus-host>:9090/api/v1/query \
  --data-urlencode 'query=increase(scaph_host_energy_microjoules[1m]) / 1000000' | jq .
```

### 4. Arrancar Collector Com Scaphandre

```bash
ENERGY_SOURCE=scaphandre_prometheus \
PROMETHEUS_URL=http://<prometheus-host>:9090 \
docker compose -f sa-deploy.yaml up -d --build --force-recreate energy-collector
```

### 5. Validar Collector

```bash
./scripts/validate_energy_source_integration.sh
```

### 6. Validar EIF

Numa consola:

```bash
./scripts/notify_h2_server.sh
```

Noutra:

```bash
CREATE_EIF_SUBSCRIPTION=true ./scripts/validate_energy_source_integration.sh
```

Resultado esperado:

```text
notify server recebe HTTP/2 POST com energyInfo.energy
```

## Como Explicar Ao Professor

Frase curta:

```text
Antes o Energy Collector estimava energia apenas com base em tráfego UPF. Agora o Collector suporta uma fonte externa de energia via Scaphandre/Prometheus. A energia medida numa janela é normalizada em Joules, guardada em MongoDB e atribuída ao UE ou flow com base na quota de tráfego observada no UPF. O EIF não muda: continua a receber energyInfo.energy e a enviar a notificação 3GPP.
```

Frase sobre limitação:

```text
Scaphandre mede energia ao nível do host/pacote CPU, não diretamente por UE. Por isso, a energia por UE/PDU/session/service flow é uma atribuição baseada em tráfego, não uma medição direta individual.
```

Frase sobre fallback:

```text
Se a fonte Prometheus/Scaphandre não estiver disponível, o Collector mantém o modelo anterior por tráfego, garantindo que o pipeline EIF continua funcional.
```

## Estado Atual

Implementado:

- Fonte opcional Prometheus/Scaphandre.
- Normalização para Joules.
- Persistência em MongoDB de samples de energia.
- Atribuição por quota de tráfego.
- Histórico de atribuições.
- Endpoint de status.
- Endpoint de janela de energia.
- Script de validação.
- EIF inalterado.

Por validar em runtime:

- Scaphandre real nos servidores.
- Prometheus a fazer scrape.
- Collector com `ENERGY_SOURCE=scaphandre_prometheus`.
- Callback EIF completo com energia atribuída a partir de Scaphandre.
