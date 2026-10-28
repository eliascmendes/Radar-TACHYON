# Radar TACHYON

Ferramenta local de prospecção comercial para equipes pequenas de desenvolvimento de software. Ela busca empresas no Google Maps, verifica a presença digital de cada uma (site, HTTPS, versão mobile, agendamento online, WhatsApp), dá uma nota de 0 a 100 explicando os motivos e oferece um painel web para acompanhar os contatos até o fechamento.

Foi criada pela equipe TACHYON, de São Luís (MA), e vem configurada para essa cidade, mas a cidade, os segmentos, os pesos e os textos de abordagem podem ser trocados em arquivos JSON, sem programar.

> **Nenhuma mensagem sai sozinha.** O Radar monta a fila de envio pelo WhatsApp com mensagens prontas, mas cada conversa é aberta e enviada por uma pessoa. Leia a seção [Uso responsável](#uso-responsável) antes de usar.

## O que ele faz

- **Coleta**: busca empresas por termo (por exemplo, "clínica odontológica") numa cidade, usando o [google-maps-scraper-kit](https://github.com/Mahanaicoach/google-maps-scraper-kit) rodando em Docker. Não precisa de chave da API do Google.
- **Normalização**: padroniza telefones no formato brasileiro (+55), identifica celulares, extrai o bairro, remove duplicadas e descarta empresas fechadas ou fora da cidade.
- **Análise da presença digital**: visita o site de cada empresa e verifica se ele responde, se tem HTTPS e versão mobile, se parece desatualizado, se oferece agendamento online ou botão de WhatsApp, e procura redes sociais e e-mail.
- **Pontuação de 0 a 100**: combina porte (avaliações e nota no Maps), necessidade digital e aderência ao segmento. Cada nota vem com os motivos em texto.
- **Painel web**: lista filtrável, ficha de cada empresa com a composição da nota, status do funil comercial, próximo passo, anotações e histórico de alterações.
- **Funil**: quantidade de leads por status e taxa de resposta por segmento.
- **Mensagens de abordagem**: texto montado a partir dos problemas confirmados na verificação, escrito para fazer sentido mesmo quando a mesma campanha vai para várias empresas.
- **Envio pelo WhatsApp em lote**: filtre as empresas, revise a fila e abra uma conversa por vez com a mensagem pronta. Cada envio é registrado no histórico e o lead passa para "Contatado".
- **Lista "não contatar"**: quem pedir para não ser contatado fica bloqueado para abordagem e rascunho.
- **Exportação em CSV** da visão filtrada, na mesma ordem do painel.

## Requisitos

- [Python](https://www.python.org/) 3.10 ou superior
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) ligado (para o motor de coleta)
- [Git](https://git-scm.com/)
- Windows, para usar os scripts prontos `subir.ps1` / `subir.bat`. Em Linux e macOS, siga a [instalação manual](#instalação-manual).

## Como rodar

```powershell
git clone https://github.com/eliascmendes/Radar-TACHYON.git
cd Radar-TACHYON
.\subir.ps1
```

Se o PowerShell bloquear o script, use `.\subir.bat`.

O script cria o ambiente virtual Python, instala as dependências, baixa o google-maps-scraper-kit, sobe o motor no Docker e inicia o painel.

- Painel: http://127.0.0.1:8787
- Interface do motor de coleta: http://127.0.0.1:8080

### Instalação manual

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

git clone https://github.com/Mahanaicoach/google-maps-scraper-kit.git
cd google-maps-scraper-kit && docker compose up -d && cd ..

python -m uvicorn app.main:app --host 127.0.0.1 --port 8787
```

## Primeira campanha

No painel, abra a aba **Campanhas**, informe um termo de busca (por exemplo, `clínica odontológica`), a cidade e a profundidade (5 é um bom começo) e clique para rodar. O progresso aparece na própria tela.

Também dá para rodar pela linha de comando:

```powershell
.\.venv\Scripts\python.exe scripts\campanha_clinicas.py
```

O resultado vai para o banco local e para um CSV ordenado em `data/exports/`. O CSV bruto devolvido pelo motor fica em `data/brutos/`.

### Cobertura da cidade em grade

O Google Maps devolve no máximo cerca de 120 resultados por busca. Na aba **Campanhas** é possível dividir a cidade em 2×2 ou 3×3 células: uma busca por célula, em sequência, descartando o que cair fora da cidade. Use com moderação, porque isso significa 4 ou 9 buscas no Google.

### Reprocessar e recalcular sem nova busca

- **Reprocessar** (no histórico de campanhas) refaz a normalização, a verificação dos sites e a pontuação a partir do CSV bruto já baixado, sem consultar o Google de novo. Status, próximo passo e anotações definidos pela equipe são preservados; só leads em `novo` ou `qualificado` são reavaliados automaticamente.
- **Recalcular pontuações** relê `config/pesos.json` e recalcula todas as notas sem visitar os sites de novo.

## Envio pelo WhatsApp

Na aba **WhatsApp**, escolha quem vai receber (segmento, status, pontuação mínima, bairro, só empresas sem site) e clique em **Montar fila**. Na aba **Leads**, o botão **WhatsApp em lote** leva os filtros atuais direto para a fila.

Para cada empresa da fila, o painel mostra a mensagem pronta, que pode ser ajustada só para ela. **Abrir no WhatsApp e marcar como enviada** abre a conversa no WhatsApp (Web ou aplicativo) com o texto preenchido; você confere e aperta enviar. Empresas que você não quiser abordar podem ser desmarcadas ou puladas. Para uma empresa só, use **Enviar pelo WhatsApp** na ficha dela.

Ficam fora da fila automaticamente:

- empresas na lista "não contatar";
- quem não tem celular cadastrado (telefone fixo não tem WhatsApp);
- órgãos públicos e leads fechados, perdidos ou descartados;
- quem já foi contatado (a menos que você marque "Incluir quem já foi contatado");
- telefones repetidos na mesma fila.

Existe um limite diário de envios (30 por padrão, em `envio.limite_diario` no `config/pesos.json`). Envios em grande quantidade para números que não salvaram o seu contato são o padrão que o WhatsApp usa para identificar spam e bloquear contas.

### Como as mensagens são montadas

Cada mensagem junta partes definidas em `config/mensagens.json`:

1. saudação conforme o horário ("Bom dia", "Boa tarde", "Boa noite") e apresentação da equipe; quem estiver selecionado em "Você" assina a mensagem;
2. uma observação sobre a empresa, **só quando a verificação confirmou o problema** (por exemplo, "o site não abriu quando tentamos acessar"). Se o site bloqueou a verificação ou não há nada seguro para dizer, a mensagem usa uma abertura geral do segmento, sem afirmar nada sobre a empresa;
3. a proposta e o convite do segmento;
4. a opção de sair: responder "sair". Quem pedir isso deve ser adicionado à lista "não contatar".

O nome da empresa não entra no texto, porque o nome cadastrado no Maps costuma vir com descrições ("Clínica X: Dentista", "Clínica X | Shopping") e ficaria estranho numa mensagem automática.

## Configuração (sem código)

Tudo fica na pasta `config/`:

| Arquivo | O que define |
|---|---|
| `pesos.json` | pesos da pontuação, nota mínima para "qualificado", cidade padrão e coordenadas de reserva, profundidade padrão da coleta, limite diário de envios pelo WhatsApp |
| `segmentos.json` | segmentos de mercado, termos de busca e palavras de categoria que reforçam a aderência |
| `mensagens.json` | partes das mensagens: apresentação da equipe, aberturas, opção de sair e, por segmento, público, proposta e convite |
| `equipe.json` | nomes da equipe para o seletor "Você" (registra quem alterou cada lead e assina os rascunhos) |

O `equipe.json` não vai para o repositório, para não publicar nomes de pessoas. Crie o seu a partir do exemplo:

```powershell
copy config\equipe.exemplo.json config\equipe.json
```

Sem ele, o painel usa os nomes do arquivo de exemplo.

Depois de editar os pesos, use **Campanhas → Recalcular pontuações**. Para as outras alterações, reinicie o painel ou chame `POST /api/config/recarregar`.

### Usar em outra cidade

Troque `cidade_padrao`, `fallback_lat` e `fallback_lon` em `config/pesos.json`. A cidade é localizada pelo [Nominatim](https://nominatim.org/) (OpenStreetMap); as coordenadas de reserva só são usadas se essa consulta falhar.

## Como a pontuação funciona

A nota vai de 0 a 100 e soma três componentes (pesos padrão):

- **Porte e capacidade de pagamento (30)**: quantidade de avaliações e nota no Google Maps.
- **Necessidade digital (40)**: problemas encontrados na presença digital (sem site, site fora do ar, sem HTTPS, sem versão mobile, sem agendamento online etc.).
- **Aderência ao segmento (30)**: o quanto a categoria da empresa combina com o segmento da campanha.

Empresas que já têm um sistema próprio maduro recebem uma penalidade (35 pontos). A partir de 55 pontos o lead é marcado como **qualificado**.

### Como a presença digital é avaliada

- O **site informado no Maps** é classificado antes da visita: links de Instagram, Facebook, WhatsApp (`wa.me`), Linktree e Doctoralia **não contam como site próprio**; sites `gov.br` são marcados como órgão público e zerados.
- **Site fora do ar** só quando ele de fato não responde (erro de DNS ou conexão, erro 5xx ou página inicial inexistente). Se o Maps aponta para uma subpágina que sumiu, o sistema tenta a página inicial; em caso de demora, tenta de novo.
- **Verificação bloqueada** (403/429, Cloudflare) não soma pontos; a ficha pede conferência manual.
- **Certificado inválido** é registrado à parte: o site abre, mas o navegador mostra alerta.
- **Versão mobile** é detectada pela meta tag `viewport`; **desatualizado** quando o ano no rodapé tem 4 anos ou mais, ou há tecnologia antiga.
- Instagram, Facebook, LinkedIn e e-mail encontrados no site completam o cadastro.
- "Agendamento só por WhatsApp" só é apontado quando o telefone é celular.

## Testes

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

- `test_pontuacao.py`: casos típicos da pontuação.
- `test_normalizacao.py`: telefone, bairro, tipo de site e remoção de duplicadas.
- `test_enriquecimento.py`: respostas HTTP simuladas, sem acesso à rede.
- `test_rascunhos.py`: montagem das mensagens (saudação, nome, só problemas confirmados).
- `test_api.py`: pipeline e API de ponta a ponta num banco temporário (não usa o banco real nem o Docker).

Os testes usam apenas empresas, endereços e telefones fictícios.

## Estrutura

```
Radar-TACHYON/
  app/            API (FastAPI), coleta, normalização, análise dos sites, pontuação, rascunhos
  config/         arquivos JSON editáveis
  static/         painel web (HTML, CSS e JavaScript, sem etapa de build)
  tests/          testes automatizados (pytest)
  scripts/        campanha de exemplo pela linha de comando
  data/           banco SQLite e CSVs gerados (fora do repositório)
  subir.ps1       sobe tudo com um comando (Windows)
```

## Decisões de arquitetura

1. **Python, FastAPI e SQLite**: tudo roda localmente num único processo, sem custo de banco de dados.
2. **google-maps-scraper-kit em Docker como motor de coleta**: não exige chave da API do Google; roda uma busca por vez.
3. **Configuração em JSON**: pessoas que não programam podem ajustar termos, pesos e mensagens. JSON não exige nenhuma biblioteca extra.
4. **Painel em HTML, CSS e JavaScript puros**: em português, acompanha o tema claro/escuro do sistema e dispensa etapa de build.
5. **Verificação de sites própria** (`httpx`): uma falha num site não interrompe a campanha.
6. **Envio assistido, não automático**: o painel prepara a fila e abre cada conversa no WhatsApp com o texto pronto, mas quem envia é uma pessoa. Robôs que enviam sozinhos pelo WhatsApp violam os termos do serviço e levam ao banimento do número.

## Uso responsável

- **LGPD**: o Radar guarda a origem e a data de coleta de cada lead, permite excluir um lead a qualquer momento e mantém a lista "não contatar", que bloqueia abordagem e rascunho. Os dados coletados não devem ser vendidos nem compartilhados.
- **Termos de uso do Google**: a coleta automatizada de dados do Google Maps não é permitida pelos termos de uso do Google. Quem usar o Radar é responsável por avaliar esse risco. A ferramenta foi pensada para baixo volume: uma busca por vez, profundidade moderada e alerta quando uma busca falha ou volta vazia, o que pode indicar bloqueio.
- **Sem disparo automático**: cada mensagem pelo WhatsApp é aberta e enviada por uma pessoa, com limite diário. E-mail e Instagram são manuais.
- **Abordagem entre empresas**: os modelos de mensagem identificam quem está entrando em contato e oferecem a opção de sair da lista.

## Créditos

A coleta usa o [google-maps-scraper-kit](https://github.com/Mahanaicoach/google-maps-scraper-kit). A localização das cidades usa o [Nominatim](https://nominatim.org/), com dados do OpenStreetMap.
