# Fase 3B — Documentação técnica de estado e continuação

## Contexto

Este documento descreve o estado técnico atual da fase 3B do projeto EIF (Energy Information Function) sobre Open5GS, após a validação da fase 3A Android.

A fase 3A já validou com sucesso a cadeia:

```text
Android estimator -> Energy Collector -> EIF -> notifUri
```

A fase 3B introduz uma **fonte laboratorial baseada em rede/UPF**, destinada a complementar ou substituir a origem Android em cenários de teste controlado.

---

## Objetivo da fase 3B

A fase 3B tem como objetivo usar atividade observável no UPF para alimentar o `Energy Collector`, mantendo o EIF como consumidor abstrato da energia através de:

```text
GET /energy/v1/report
```

Arquitetura alvo:

```text
UPF metrics / traffic activity -> Energy Collector -> EIF -> notifUri
```

Objetivos técnicos principais:

- recolher métricas do UPF;
- associar atividade de rede a um UE/SUPI;
- converter atividade observada em samples energéticos no `Energy Collector`;
- expor essa energia ao EIF sem alterar o papel do EIF na arquitetura;
- validar a cadeia completa:

```text
UPF -> Energy Collector -> EIF -> notifUri
```

---

## Estado atual da fase 3B

Neste momento, a infraestrutura base da fase 3B está operacional.

### Componentes funcionais

Estão funcionais:

- `Energy Collector`
- `EIF`
- `UPF`
- `SMF`
- associação PFCP entre `SMF` e `UPF`
- endpoint Prometheus/metrics do UPF
- mecanismo de `ue-mappings` no `Energy Collector`

### Mapping UE/SUPI já registado

Foi registado no `Energy Collector` o seguinte mapping:

- `supi = imsi-001011234567895`
- `ue_ip = 192.168.100.2`
- `source = upf`

Isto fornece a base para correlacionar atividade de rede com o UE alvo.

---

## Arquitetura técnica atual

Arquitetura funcional da fase 3B neste ponto:

```text
AF / test logic
    -> EIF
        -> Energy Collector
            -> source = UPF-based estimator
                -> UPF metrics / traffic counters
```

Na continuação da fase 3B, a fonte prevista no collector será um estimador do tipo:

```text
source = upf / traffic-estimator / upf-traffic-estimator
```

O EIF mantém o comportamento já validado:
- recebe subscrições `UE_ENERGY`;
- consulta o collector;
- constrói `EnergyEeReport`;
- envia notificação para `notifUri`.

---

## Estado atual do UPF

O UPF encontra-se a arrancar corretamente e a expor as interfaces esperadas:

- PFCP em `172.22.0.8:8805`
- GTP-U em `172.22.0.8:2152`
- métricas em `172.22.0.8:9091`

O endpoint de métricas do UPF já foi validado com sucesso.

Comando de consulta utilizado:

```bash
docker exec -it eif sh -c 'curl -s http://172.22.0.8:9091/metrics | head -100'
```

### Métricas observadas

Foram observadas, entre outras, as seguintes métricas:

```text
fivegs_ep_n3_gtp_indatapktn3upf
fivegs_ep_n3_gtp_outdatapktn3upf
fivegs_upffunction_sm_n4sessionestabreq
fivegs_upffunction_sm_n4sessionreport
fivegs_upffunction_sm_n4sessionreportsucc
fivegs_upffunction_upf_sessionnbr
pfcp_peers_active
```

### Estado atual das métricas

No estado atual:

- `pfcp_peers_active = 1`
- ainda não existem sessões UE ativas no UPF
- ainda não existe tráfego N3/GTP-U associado a UE
- os contadores de tráfego e sessão observados continuam a zero

Isto significa que a infraestrutura UPF/SMF está pronta, faltando apenas ativar UE + sessão + tráfego para começar a explorar a fonte 3B.

---

## Estado atual do SMF

O SMF encontra-se operacional e já estabeleceu associação PFCP com o UPF.

Isto valida a conectividade funcional entre:

```text
SMF <-> UPF
```

e confirma que o plano de controlo necessário para a fase 3B está pronto para suportar sessões de dados reais.

---

## Validação PFCP

A associação PFCP entre o SMF e o UPF foi validada no UPF com indicação explícita de associação ativa.

A consequência prática dessa validação é:

- o UPF já conhece o peer PFCP;
- o core está pronto para criar sessões de utilizador;
- o próximo incremento útil passa a ser a ativação do gNB e UE para gerar tráfego observável.

---

## Integração com o Energy Collector

O `Energy Collector` já dispõe dos elementos base necessários para a fase 3B:

### 1. suporte a `ue-mappings`
Permite mapear:

- `supi`
- `ue_ip`
- `source`

### 2. suporte ao modelo de consulta do EIF
O EIF continua a consultar:

```text
/energy/v1/report
```

Logo, a fase 3B pode evoluir inteiramente no collector, mantendo o EIF desacoplado da origem concreta da energia.

---

## UERANSIM na fase 3B

Para a continuação da fase 3B, o plano operacional passa por ativar:

- `nr_gnb`
- `nr_ue`

Os ficheiros Compose identificados para esse arranque são:

- `nr-gnb.yaml`
- `nr-ue.yaml`

Os ficheiros dentro de `ueransim/` funcionam como configuração dos binários UERANSIM montados nos containers.

### Imagem necessária

A imagem usada pelos compose files é:

```text
docker_ueransim
```

A build local dessa imagem fica identificada como o próximo passo operacional imediato.

---

## Próxima etapa técnica

A próxima etapa da fase 3B é:

### 1. Build da imagem UERANSIM
```bash
docker build -t docker_ueransim ./ueransim
docker images | grep docker_ueransim
```

### 2. Arranque do gNB e UE
```bash
docker compose -f nr-gnb.yaml up -d
docker compose -f nr-ue.yaml up -d
```

### 3. Validação do registo e sessão
Verificar:

- ligação do gNB ao AMF;
- registo do UE no core;
- estabelecimento de PDU session;
- atribuição de IP ao UE.

Logs de apoio:

```bash
docker logs nr_gnb --tail 100
docker logs nr_ue --tail 100
docker logs amf --tail 100
```

### 4. Geração de tráfego do UE
Depois de o UE estar operacional:

```bash
ping -c 20 8.8.8.8
curl -I http://example.com
```

ou tráfego mais intenso com `iperf3`.

### 5. Observação das métricas do UPF
```bash
docker exec -it eif sh -c 'curl -s http://172.22.0.8:9091/metrics | grep -E "fivegs_ep_n3_gtp|upf_sessionnbr|pfcp_peers_active"'
```

Valores-alvo nesta fase:

- `pfcp_peers_active = 1`
- `fivegs_upffunction_upf_sessionnbr > 0`
- `fivegs_ep_n3_gtp_indatapktn3upf > 0`
- `fivegs_ep_n3_gtp_outdatapktn3upf > 0`

---

## Próxima peça de implementação

Depois de existirem métricas de sessão e tráfego com valores úteis, a próxima peça de desenvolvimento prevista é:

```text
scripts/upf_traffic_estimator.py
```

### Papel esperado do script

Este script deverá:

1. ler métricas do UPF em dois instantes;
2. calcular deltas de contadores;
3. estimar atividade UL/DL ou volume equivalente;
4. produzir um sample energético para o `Energy Collector`;
5. permitir que o collector responda ao EIF via `/energy/v1/report`;
6. suportar a validação:

```text
UPF -> Energy Collector -> EIF -> notifUri
```

---

## Comandos de referência da fase 3B

## Stack SA mínima

```bash
docker compose -f sa-deploy.yaml up -d --build mongo nrf scp amf smf upf eif energy-collector
docker compose -f sa-deploy.yaml ps
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -E 'upf|energy-collector|eif|smf|amf'
```

## Mapping UE -> SUPI

```bash
curl -X POST http://172.22.0.44:8088/ue-mappings   -H "Content-Type: application/json"   -d '{
    "supi": "imsi-001011234567895",
    "ue_ip": "192.168.100.2",
    "source": "upf"
  }'
```

## Consulta de métricas UPF

```bash
docker exec -it eif sh -c 'curl -s http://172.22.0.8:9091/metrics | head -100'
```

## Consulta filtrada das métricas relevantes

```bash
docker exec -it eif sh -c 'curl -s http://172.22.0.8:9091/metrics | grep -E "fivegs_ep_n3_gtp|upf_sessionnbr|pfcp_peers_active"'
```

## Ver IPs de referência

```bash
grep '^UPF_IP=' .env
grep '^SMF_IP=' .env
```

## Compose files do UERANSIM

```bash
sed -n '1,120p' nr-gnb.yaml
sed -n '1,120p' nr-ue.yaml
```

## Build da imagem UERANSIM

```bash
docker build -t docker_ueransim ./ueransim
```

## Arranque do gNB e UE

```bash
docker compose -f nr-gnb.yaml up -d
docker compose -f nr-ue.yaml up -d
```

---

## Estado de retoma

Se o trabalho for retomado mais tarde, o ponto de entrada recomendado é:

1. confirmar que `smf`, `upf`, `eif` e `energy-collector` continuam operacionais;
2. buildar a imagem `docker_ueransim`;
3. levantar `nr_gnb` e `nr_ue`;
4. gerar tráfego real;
5. observar contadores do UPF;
6. implementar o estimador 3B baseado em UPF.

---

## Conclusão

A fase 3B já tem a sua base técnica preparada:

- core mínimo funcional;
- `SMF` e `UPF` operacionais;
- associação PFCP validada;
- métricas do UPF acessíveis;
- integração pronta para evoluir no `Energy Collector`.

A próxima fase prática é ativar o UE/gNB, gerar tráfego e transformar atividade real observada no UPF em energia consumível pelo EIF.
