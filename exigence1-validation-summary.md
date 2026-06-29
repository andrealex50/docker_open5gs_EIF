# Validação no servidor exigence1

## Objetivo

Validar a pipeline completa do EIF com uma fonte energética do host baseada em Intel RAPL e Scaphandre/Prometheus, em vez de usar apenas a estimativa por tráfego.

O servidor usado foi:

```text
exigence1
10.255.35.93
```

## Estado inicial

O servidor tinha suporte para medições de energia via Intel RAPL:

```text
/sys/class/powercap
intel-rapl
intel-rapl:0
intel-rapl:0:0
intel-rapl:0:1
intel-rapl:0:2
```

Isto permitiu usar Scaphandre como fonte energética.

## Componentes usados

Foram colocados a correr no `exigence1`:

```text
Open5GS / EIF
UERANSIM gNB
UERANSIM UE
UPF
Energy Collector
MongoDB
Prometheus
Scaphandre
Notify server HTTP/2 h2c
```

## Pipeline validada

A pipeline validada foi:

```text
UERANSIM UE
   |
   v
UPF traffic counters
   |
   v
UPF traffic estimator
   |
   v
Energy Collector
   |
   +----> MongoDB
   |
   +----> Prometheus
             |
             v
          Scaphandre
   |
   v
EIF /energy/v1/report
   |
   v
HTTP/2 h2c notification
```

## Validação da rede 5G

O UE registou-se com sucesso na rede 5G SA:

```text
Initial Registration is successful
PDU Session establishment is successful
TUN interface[uesimtun0, 192.168.100.2] is up
```

Foi validado tráfego IP através da interface do UE:

```bash
ping -c 3 -I uesimtun0 8.8.8.8
```

Resultado:

```text
3 packets transmitted, 3 received, 0% packet loss
```

## Validação Scaphandre/Prometheus

O Scaphandre foi executado no host físico com acesso a:

```text
/sys/class/powercap
```

O servidor foi confirmado como bare metal:

```text
systemd-detect-virt: none
Hardware: HP Z240 Tower Workstation
CPU: Intel Core i7-7700K
Scaphandre: 1.0.2
```

O Prometheus foi configurado para fazer scrape do Scaphandre:

```yaml
- job_name: 'scaphandre'
  fallback_scrape_protocol: PrometheusText0.0.4
  static_configs:
    - targets: ['172.22.0.1:8080']
```

O target ficou ativo:

```json
{
  "job": "scaphandre",
  "health": "up",
  "lastError": ""
}
```

Foi validada uma query PromQL para obter energia em Joules:

```promql
increase(scaph_host_energy_microjoules[1m]) / 1000000
```

Exemplo de resultado:

```json
"146.342849"
```

Durante a revisão foi comparada a métrica agregada do host com a soma dos
subdomínios RAPL, usando exatamente o mesmo instante e uma janela de um minuto:

```text
scaph_host_energy_microjoules:        292.853809 J
sum(scaph_domain_energy_microjoules):  83.237003 J
```

A soma dos subdomínios subestimava a energia observada nesta máquina. Por isso,
o Collector passou a usar `scaph_host_energy_microjoules`, que é o agregado do
host exposto pelo Scaphandre.

## Validação do Energy Collector

O Energy Collector foi configurado com:

```text
ENERGY_SOURCE=scaphandre_prometheus
PROMETHEUS_URL=http://metrics:9090
```

O endpoint de status confirmou:

```json
{
  "mode": "scaphandre_prometheus",
  "enabled": true,
  "storage": "mongodb"
}
```

O endpoint de janela energética devolveu a energia RAPL agregada pelo Scaphandre:

```json
{
  "source": "scaphandre_prometheus",
  "metric": "host_rapl_energy",
  "unit": "joules",
  "value": 146.342849
}
```

## Validação da atribuição de energia

O Collector atribuiu a energia medida ao UE com base na quota de tráfego observada no UPF:

```text
attributed_energy = measured_window_energy * selectedBytes / totalTrackedBytes
```

Exemplo validado:

```json
{
  "source": "scaphandre_prometheus",
  "energyInfo": {
    "energy": 584.63726
  },
  "trafficEstimateEnergy": 11.6,
  "attribution": {
    "method": "traffic_share",
    "selectedBytes": 12000000,
    "totalTrackedBytes": 12000000,
    "ratio": 1.0,
    "measuredWindowEnergy": 584.63726
  }
}
```

Isto mostra a diferença entre:

```text
trafficEstimateEnergy
```

e:

```text
energyInfo.energy
```

O primeiro é a estimativa antiga por tráfego.  
O segundo é a energia medida via Scaphandre/Prometheus e atribuída ao UE.

## Persistência MongoDB

Foram validadas as coleções:

```text
energy_source_samples
energy_attributions
```

Exemplo:

```text
energy_source_samples=10
energy_attributions=6
```

Isto confirma que as medições de energia e as atribuições ficam persistidas.

## Validação EIF

Foi criada uma subscrição `UE_ENERGY` no EIF:

```json
{
  "notifUri": "http://172.22.0.45:9998/notify",
  "eventsSubscSets": {
    "validation1": {
      "subscSetId": "validation1",
      "event": "UE_ENERGY",
      "supi": "imsi-001011234567895",
      "repPeriod": 30
    }
  }
}
```

O notify server recebeu uma notificação HTTP/2 h2c:

```text
Method: POST
Path: /notify
```

Com body:

```json
{
  "subId": "1",
  "reports": [
    {
      "event": "UE_ENERGY",
      "subscSetId": "validation1",
      "energyInfo": {
        "energy": 0.25
      }
    }
  ]
}
```

Isto valida o envio da notificação do EIF para o `notifUri`.

Quando existe tráfego/sample dentro da janela de reporte, o Collector consegue devolver energia atribuída com base em Scaphandre. Quando não existe sample dentro da janela curta, o Collector cai no fallback por tráfego, por exemplo:

```text
0.05 W * 5 s = 0.25 J
```

## Resultado final

Foi validado end-to-end:

```text
Scaphandre -> Prometheus -> Energy Collector -> MongoDB
UPF estimator -> Energy Collector
Energy Collector -> EIF
EIF -> HTTP/2 h2c notification
```

## Conclusão

A integração está funcional.

O EIF mantém a interface 3GPP e envia notificações com:

```json
"energyInfo": {
  "energy": <valor>
}
```

O Energy Collector passou a suportar energia RAPL agregada do host através de Scaphandre/Prometheus, mantendo o modelo antigo por tráfego como fallback. Esta medição não equivale a um wattímetro externo nem ao consumo direto do UE.
