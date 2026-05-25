# Guia Da Demo EIF Energy

Este guia prepara a demo ao vivo do trabalho de reporting energético no EIF.

O objetivo da demo é mostrar que o EIF consegue expor um fluxo de Energy Event Exposure com formato 3GPP, recebendo o valor de energia a partir de uma entidade externa de laboratório chamada Energy Collector.

## Ideia Principal

O EIF recebe uma subscrição `UE_ENERGY`, consulta periodicamente o Energy Collector para obter `energyInfo.energy`, e envia uma notificação HTTP/2 compatível com o formato 3GPP para o `notifUri` da subscrição.

## Arquitetura Para Explicar

```text
Tráfego da UE UERANSIM
  -> Open5GS UPF
  -> scripts/upf_traffic_estimator.py
  -> Energy Collector /samples/traffic
  -> EIF GET /energy/v1/report
  -> EnergyEeNotif por HTTP/2 h2c para o notifUri
```

A separação importante é:

- EIF: comportamento da Network Function exposta no lado 3GPP.
- Energy Collector: entidade externa/laboratorial que fornece estimativas de energia.
- UPF estimator: fonte laboratorial de medição de tráfego.
- Notify server: servidor de callback usado para provar que a notificação chega corretamente.

## Fluxo EIF -> Collector -> Notificação

Fluxo funcional da demo:

```text
1. AF/cliente cria subscrição no EIF
   POST /neif-ee/v1/subscriptions
   event = UE_ENERGY
   supi = imsi-001011234567895
   notifUri = http://172.22.0.45:9998/notify

2. EIF guarda a subscrição
   subId
   notifUri
   event
   supi
   repPeriod

3. UPF estimator mede tráfego da UE
   UE IP = 192.168.100.2
   tx_bytes
   rx_bytes
   timestamp

4. UPF estimator envia sample para o Energy Collector
   POST /samples/traffic

5. No período de reporting, o EIF consulta o Energy Collector
   GET /energy/v1/report?supi=...&event=UE_ENERGY&start=...&end=...

6. Energy Collector calcula/obtém energia
   E = P_idle * delta_t + alpha_tx * tx_bytes + alpha_rx * rx_bytes

7. Energy Collector responde ao EIF
   energyInfo.energy

8. EIF constrói o EnergyEeReport
   event
   subscSetId
   timeStamp
   energyInfo.energy

9. EIF envia EnergyEeNotif por HTTP/2 h2c
   POST notifUri

10. Notify server recebe a notificação
    Body JSON com reports[].energyInfo.energy
```

Diagrama curto para o relatório:

```text
AF/cliente
  | POST /neif-ee/v1/subscriptions
  v
EIF
  | guarda subId/notifUri/event/supi
  |
  | GET /energy/v1/report?supi=...&event=UE_ENERGY
  v
Energy Collector
  ^                         |
  | POST /samples/traffic   | energyInfo.energy
  |                         v
UPF estimator  --------->  EIF
  ^                         |
  | mede tx/rx bytes        | POST notifUri
  |                         v
UPF / UE traffic        Notify server
```

Versão ainda mais curta:

```text
UE traffic -> UPF estimator -> Energy Collector -> EIF -> notifUri callback
```

Ponto essencial:

```text
O EIF não precisa de saber como a energia foi estimada. Só consome
energyInfo.energy e produz uma notificação 3GPP.
```

## Valores Fixos Do Laboratório

```text
EIF:              http://172.22.0.43:7777
Energy Collector: http://172.22.0.44:8088
UPF metrics:      http://172.22.0.8:9091/metrics
Notify URI:       http://172.22.0.45:9998/notify
SUPI:             imsi-001011234567895
UE IP:            192.168.100.2
Túnel da UE:      uesimtun0
Interface UPF:    ogstun
```

## O Que Mostrar Ao Vivo

1. A stack está a correr.
2. A UE tem uma PDU session.
3. A UE consegue gerar tráfego através do UPF.
4. O UPF estimator envia uma amostra de tráfego para o Collector.
5. É criada uma subscrição `UE_ENERGY` no EIF.
6. O EIF consulta o Collector.
7. O notify server recebe o callback HTTP/2.
8. O JSON do callback contém:

```json
{
  "energyInfo": {
    "energy": 0.251008
  }
}
```

O número pode ser diferente. O ponto importante é o fluxo e o nome do campo JSON: `energyInfo.energy`.

## Organização Dos Terminais

Usa quatro terminais:

- Terminal 1: stack e checks da UE.
- Terminal 2: notify server.
- Terminal 3: estimator e subscrição.
- Terminal 4: logs.

## Checklist Antes Da Demo

Na raiz do repositório:

```bash
cd ~/docker_open5gs_EIF
git status --short
```

Podem aparecer alterações de documentação/código ainda não commitadas. O importante é não teres artefactos grandes misturados no repo.

Pastas runtime com dono `root` podem existir:

```text
log/
energy-collector/__pycache__/
```

Essas pastas não fazem parte da lógica da demo.

Confirma os containers existentes:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

## Subir A Stack Da Demo

Terminal 1:

```bash
docker compose -f sa-deploy.yaml up -d mongo nrf scp amf smf upf eif energy-collector
docker compose -f nr-gnb.yaml up -d
docker compose -f nr-ue.yaml up -d
```

Espera alguns segundos e confirma:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E 'mongo|nrf|scp|amf|smf|upf|eif|energy-collector|nr_gnb|nr_ue'
```

## Subir O Notify Server HTTP/2

Terminal 2:

```bash
./scripts/notify_h2_server.sh
```

Esperado:

```text
HTTP/2 h2c notify server listening on 0.0.0.0:9998
```

Deixa este terminal aberto. É aqui que aparece a notificação final.

## Confirmar A Sessão Da UE

Terminal 1:

```bash
docker exec nr_ue ip addr show uesimtun0
docker exec nr_ue ping -c 3 -I uesimtun0 8.8.8.8
```

Esperado:

- `uesimtun0` existe.
- O IP da UE é `192.168.100.2`.
- O ping funciona.

Confirma também a métrica de sessão no UPF:

```bash
docker exec eif sh -c 'curl -s http://172.22.0.8:9091/metrics | grep fivegs_upffunction_upf_sessionnbr'
```

Esperado enquanto a UE está ativa:

```text
fivegs_upffunction_upf_sessionnbr 1
```

## Alimentar O Energy Collector

Caminho recomendado para a demo ao vivo: contadores temporários por UE via `iptables`.

Terminal 3:

```bash
docker exec nr_ue sh -lc 'ping -c 30 -I uesimtun0 8.8.8.8 >/tmp/nr_ue_ping.log 2>&1 &' \
  && python3 scripts/upf_traffic_estimator.py \
    --source ue-iptables \
    --register-mapping \
    --post
```

Output esperado, com valores possivelmente diferentes:

```json
{
  "supi": "imsi-001011234567895",
  "ue_ip": "192.168.100.2",
  "tx_bytes": 840,
  "rx_bytes": 840,
  "source": "upf"
}
```

O que interessa ver:

- `Mapping response: HTTP 201`
- `Collector response: HTTP 200`
- `tx_bytes` e/ou `rx_bytes` presentes.

Se o modo `ue-iptables` falhar, usa o fallback por interface:

```bash
docker exec nr_ue sh -lc 'ping -c 30 -I uesimtun0 8.8.8.8 >/tmp/nr_ue_ping.log 2>&1 &' \
  && python3 scripts/upf_traffic_estimator.py \
    --source interface \
    --register-mapping \
    --post
```

## Consultar O Collector Diretamente

Para a demo, usa uma janela temporal larga:

```bash
START=$(date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%SZ)
END=$(date -u -d '10 minutes' +%Y-%m-%dT%H:%M:%SZ)

curl -sS "http://172.22.0.44:8088/energy/v1/report?supi=imsi-001011234567895&event=UE_ENERGY&start=${START}&end=${END}" | jq .
```

Formato esperado:

```json
{
  "source": "traffic-estimator",
  "txBytes": 840,
  "rxBytes": 840,
  "energyInfo": {
    "energy": 0.251008
  }
}
```

Isto prova que o Collector consegue fornecer o campo exato que o EIF consome:

```json
{
  "energyInfo": {
    "energy": 0.251008
  }
}
```

## Criar A Subscrição No EIF

Terminal 3:

```bash
curl --http2-prior-knowledge -i \
  http://172.22.0.43:7777/neif-ee/v1/subscriptions \
  -H "Content-Type: application/json" \
  -d '{
    "notifUri": "http://172.22.0.45:9998/notify",
    "eventsSubscSets": {
      "demo1": {
        "subscSetId": "demo1",
        "event": "UE_ENERGY",
        "supi": "imsi-001011234567895",
        "repPeriod": 5
      }
    }
  }'
```

Esperado:

```text
HTTP/2 201
```

ou:

```text
201 Created
```

Depois olha para o Terminal 2. Callback esperado:

```json
{
  "subId": "1",
  "reports": [
    {
      "event": "UE_ENERGY",
      "subscSetId": "demo1",
      "timeStamp": "...",
      "energyInfo": {
        "energy": 0.251008,
        "energyReportTimeStamp": "..."
      }
    }
  ]
}
```

## Logs Úteis Durante A Demo

Terminal 4:

```bash
docker logs -f energy-collector
```

Noutro split:

```bash
docker logs eif 2>&1 | grep -E "Energy Collector|EIF notify|energyInfo|Notification failed"
```

O que deves salientar:

- O Collector recebe `/samples/traffic`.
- O EIF chama `/energy/v1/report`.
- O EIF envia o callback diretamente para o `notifUri`.
- O corpo do callback usa `energyInfo.energy`.

## Check De Conformidade

Corre:

```bash
python3 scripts/check_eif_3gpp_json.py
```

Esperado:

```text
OK
```

Frase importante:

```text
EnergyInfo serializa como "energy", não como "energyConsumption".
```

## Plano B Da Demo

Se a parte live da UE/UPF falhar, usa uma sample sintética no Collector. Isto continua a demonstrar o caminho EIF -> Collector -> notificação.

```bash
curl -sS -X POST http://172.22.0.44:8088/samples/traffic \
  -H "Content-Type: application/json" \
  -d '{
    "supi": "imsi-001011234567895",
    "ue_ip": "192.168.100.2",
    "tx_bytes": 1000000,
    "rx_bytes": 5000000,
    "source": "manual"
  }' | jq .
```

Depois cria a subscrição no EIF como acima.

Diz explicitamente:

```text
Este fallback ignora a medição live no UPF e valida apenas o caminho EIF/Collector/notificação.
```

## O Que Dizer Durante A Demo

Versão curta:

```text
Implementei um caminho de Energy Event Exposure no EIF. O EIF recebe uma
subscrição UE_ENERGY com formato TS 29.566, guarda o notifUri e o conjunto de
eventos, e no período de reporting consulta uma entidade externa, o Energy
Collector, para obter o valor de energia.

O Collector está fora da API 3GPP do EIF. No laboratório recebe samples de
tráfego do UPF estimator e devolve energyInfo.energy.

Depois o EIF constrói uma EnergyEeNotif e envia a notificação por HTTP/2 h2c
diretamente para o notifUri. O callback final segue os nomes 3GPP:
energyInfo.energy, não energyConsumption.
```

Explicação UPF/energia:

```text
O valor de energia atual é uma estimativa. No caminho live uso contadores de
tráfego observados no UPF, associados ao UE IP e ao SUPI. O Collector aplica um
modelo transparente:

E = P_idle * delta_t + alpha_tx * tx_bytes + alpha_rx * rx_bytes

Isto não é energia real medida no rail do modem. É um estimador laboratorial
controlável, que pode ser substituído por uma fonte melhor sem alterar o
contrato do EIF.
```

Explicação Android/root:

```text
Também investiguei métricas Android com root. O telemóvel expõe contexto rádio
NR SA, BatteryStats de atividade do modem e métricas de sinal, mas não expõe
um EnergyConsumer real no PowerStats nem energia física do rail do modem.
Portanto o Android é útil como contexto/calibração, mas neste dispositivo não
é uma fonte direta de Joules do modem.
```

## Decisões De Implementação Para O Relatório

### 1. Separar o EIF da fonte de energia

Decisão:

```text
O EIF não calcula energia diretamente. O EIF consulta uma entidade externa,
o Energy Collector, através de /energy/v1/report.
```

Motivo:

- mantém o EIF focado na API/semântica 3GPP;
- permite trocar a fonte de energia sem alterar o contrato do EIF;
- facilita usar diferentes fontes no laboratório: UPF, Android, samples manuais ou futura medição real.

Trade-off:

- passa a existir uma dependência externa;
- é necessário tratar timeouts, falhas e respostas inválidas do Collector.

Como foi tratado:

```text
Se o Collector falhar ou devolver energia inválida, o EIF descarta o report.
Se não houver reports válidos, não envia notificação.
```

### 2. Usar `energyInfo.energy` como contrato final

Decisão:

```text
O JSON final usa energyInfo.energy, não energyInfo.energyConsumption.
```

Motivo:

- TS 29.566 referencia `EnergyInfo` de TS 29.122;
- em TS 29.122 o campo obrigatório é `energy`;
- o nome interno em C pode continuar a ser `energy_consumption`, mas o JSON externo tem de seguir a norma.

Impacto:

- foi corrigida a serialização/parsing do modelo `EnergyInfo`;
- foi criado um check para garantir que `energyConsumption` não aparece no JSON final.

### 3. Callback direto para `notifUri` no modo laboratório

Decisão:

```text
Na demo, o EIF envia a notificação diretamente para o notifUri guardado na subscrição.
```

Motivo:

- inicialmente a notificação estava a ser encaminhada via SCP;
- para a demo, era importante provar claramente que o callback chega ao endpoint indicado pelo consumidor;
- o notify server HTTP/2 h2c permite observar o request completo.

Trade-off:

- em produção, pode ser necessário voltar a discutir se o callback deve passar pelo SCP;
- para laboratório, o caminho direto é mais simples e verificável.

### 4. Usar HTTP/2 h2c no callback

Decisão:

```text
O notify server da demo usa HTTP/2 sem TLS, ou seja h2c.
```

Motivo:

- o EIF/Open5GS SBI usa HTTP/2;
- no laboratório é mais simples usar h2c do que configurar certificados TLS;
- `curl --http2-prior-knowledge` e o servidor Node.js permitem testar isto facilmente.

Trade-off:

- não é uma configuração segura para produção;
- é adequada para validação local/controlada.

### 5. Usar o UPF como fonte operacional principal

Decisão:

```text
Para a demo, a fonte principal é o tráfego observado no UPF.
```

Motivo:

- o UPF está no lado da rede e faz parte do ambiente Open5GS;
- não depende de acesso root na UE;
- permite obter tráfego por janela temporal;
- encaixa bem no Energy Collector, que depois transforma tráfego em energia estimada.

Limitação:

- os contadores Prometheus N3 podem não mexer no setup atual;
- por isso foi criado fallback por interface e modo `ue-iptables`.

### 6. Usar `ue-iptables` como aproximação por UE no laboratório

Decisão:

```text
O estimator pode criar regras temporárias de iptables no UPF para contar tráfego
de/para o UE IP.
```

Motivo:

- contadores globais da interface `ogstun` não são específicos de uma UE;
- o modo `ue-iptables` é mais próximo de uma medição por UE;
- as regras são temporárias e removidas após a janela de medição.

Trade-off:

- é uma solução laboratorial;
- depende do mapeamento `SUPI -> UE IP`;
- em produção deveria ser substituída por informação de sessão/PFCP ou contadores próprios.

### 7. Energia como estimativa, não medição real

Decisão:

```text
O valor de energia é tratado como estimado.
```

Modelo usado no Collector:

```text
E = P_idle * delta_t + alpha_tx * tx_bytes + alpha_rx * rx_bytes
```

Motivo:

- não existe acesso direto a Joules reais do modem no setup atual;
- o UPF fornece bytes, não consumo energético;
- a fórmula é transparente, simples de explicar e ajustável.

Limitação:

- os coeficientes ainda precisam de calibração;
- o valor não deve ser apresentado como medição física direta.

### 8. Android/root como investigação e calibração auxiliar

Decisão:

```text
O Android não é a fonte principal da demo, mas foi investigado como possível
fonte/calibração.
```

O que foi encontrado:

- UE em NR SA;
- métricas de sinal como RSRP, RSRQ e SINR;
- BatteryStats com modelo de atividade do modem;
- ausência de `PowerStats EnergyConsumers` para energia real do modem.

Conclusão:

```text
Neste dispositivo, Android/root fornece bom contexto rádio, mas não expõe energia
real do rail do modem. Por isso fica como referência auxiliar.
```

### 9. Collector com persistência MongoDB

Decisão:

```text
O Energy Collector guarda mappings e samples numa base MongoDB própria.
```

Motivo:

- mantém os dados depois de reiniciar o `energy-collector`;
- aproveita o MongoDB que já existe na stack Open5GS;
- mantém a API do Collector igual para o EIF e para os scripts.

Trade-off:

- continua a ser uma persistência laboratorial simples;
- ainda falta política de retenção/limpeza de samples antigos;
- se o MongoDB não estiver disponível, o Collector usa fallback em memória.

### 10. Implementar primeiro `UE_ENERGY`

Decisão:

```text
O primeiro objetivo foi fechar UE_ENERGY end-to-end; depois o mesmo caminho foi estendido para escopos de DNN/S-NSSAI/PDU e serviço.
```

Motivo:

- é o evento mais direto para validar o pipeline;
- exige apenas associação SUPI/UE;
- permite provar o contrato `EnergyEeSubsc -> EnergyEeNotif -> EnergyInfo`.

Próximos eventos:

- `PDU_SESSION_ENERGY`: validado end-to-end pelo EIF com `dnn` e `snssai` na subscrição;
- `UE_SNSSAI_ENERGY`: validado end-to-end pelo EIF com `dnn` e `snssai` na subscrição;
- `SERVICE_FLOW_ENERGY`: validado end-to-end pelo EIF com `dnn`, `snssai` e `appId`; a classificação real do fluxo ainda é laboratorial.

## Perguntas Prováveis Do Professor

### Isto está conforme 3GPP?

Para as mensagens expostas pelo EIF, sim em termos de formato:

- a subscrição usa `notifUri`, `eventsSubscSets`, `event`, `supi`, `repPeriod`;
- o callback usa `EnergyEeNotif` com `subId` e `reports`;
- cada report usa `event`, `subscSetId`, `timeStamp`, `energyInfo`;
- `EnergyInfo` usa o campo `energy`.

As APIs do Energy Collector são APIs laboratoriais, não são APIs 3GPP.

### O `energyInfo.energy` é energia real medida no modem?

Não. Neste momento é energia estimada.

A demo live usa contadores de tráfego do UPF e um modelo transparente. A investigação Android/root mostrou que este dispositivo não expõe Joules reais do rail do modem.

### Porque usar o UPF?

Porque o UPF está disponível no lado da rede, não exige acesso root à UE, e fornece tráfego observado por janela temporal. Para uma demo laboratorial do EIF é a fonte default mais prática.

### Porque não usar Android como fonte principal?

O Android dá contexto útil, mas energia direta do modem depende do OEM/kernel. Neste dispositivo, `PowerStats EnergyConsumers` está vazio e os energy consumer stats não são suportados.

### O que acontece se o Collector estiver offline?

O EIF não crasha. Se não conseguir obter energia válida, descarta o report. Se não houver reports válidos, não envia notificação.

### Porque callback direto em vez de SCP?

No laboratório, o callback direto para o `notifUri` é intencional para observar claramente a notificação com um servidor HTTP/2 h2c simples. Em produção, a decisão de usar SCP ou callback direto teria de ser revista.

### Quais são as limitações atuais?

- O Collector persiste dados em MongoDB quando disponível, com fallback em memória.
- A associação no UPF depende do mapeamento `ue_ip -> supi`.
- O modo `ue-iptables` é laboratorial.
- A energia é estimada, não é medição real do rail do modem.
- `UE_ENERGY` está implementado end-to-end.
- `UE_SNSSAI_ENERGY` tem suporte end-to-end quando a subscrição inclui `snssai`.
- `PDU_SESSION_ENERGY` tem suporte end-to-end quando a subscrição inclui `dnn` e/ou `snssai`, como indicado na TS 29.566.
- `pduSessionId` é apenas uma tag laboratorial aceite pelo Collector; não faz parte do `EnergyEeSubscSet` no YAML TS 29.566 usado neste projeto.
- `SERVICE_FLOW_ENERGY` tem suporte end-to-end quando a subscrição inclui `dnn` e/ou `snssai`, mais `appId` ou `flowDescs`; a classificação real de fluxo ainda não vem automaticamente do UPF/PFCP.

### O que vem a seguir?

- `PDU_SESSION_ENERGY`: trocar a associação laboratorial por informação real da sessão/PFCP.
- `UE_SNSSAI_ENERGY`: trocar a tag manual por S-NSSAI vindo da sessão.
- `SERVICE_FLOW_ENERGY`: trocar a tag manual por classificação real de flow/app.
- Substituir a atribuição laboratorial por mapeamento real vindo de PFCP/session state.
- Adicionar política de retenção e metadata de fonte/confiança no Collector.

## Ficheiros Que Vale A Pena Abrir Na Demo

```text
base/open5gs-EIF/src/eif/neif-handler.c
base/open5gs-EIF/lib/sbi/openapi/model/energy_info.c
energy-collector/app.py
scripts/upf_traffic_estimator.py
scripts/notify_h2_server.sh
docs/eif-3gpp-message-compliance.md
docs/eif-upf-technical-status.md
docs/eif-android-radio-estimator.md
```

Se só tiveres tempo para mostrar dois ficheiros:

```text
energy-collector/app.py
scripts/upf_traffic_estimator.py
```

## Ecrã Final Ideal

Deixa o Terminal 2 com o corpo da notificação visível:

```json
{
  "reports": [
    {
      "event": "UE_ENERGY",
      "energyInfo": {
        "energy": 0.251008
      }
    }
  ]
}
```

Esta é a prova visual mais forte da demo.
