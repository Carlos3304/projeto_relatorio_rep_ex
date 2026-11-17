# Sistema de Monitoramento, Triagem Temática e Classificação de Mídias (Exemplo / Portfólio)

> **Nota de Portfólio:** Este repositório é uma versão higienizada e demonstrativa para portfólio de **Engenharia de Inteligência Artificial (AI Engineer)**. Tokens, chaves de API, credenciais, URLs de sistemas internos e nomes de clientes/marcas foram substituídos por valores de exemplo (`XXXXX` / `Empresa Exemplo S.A.`).

Conjunto de ferramentas em Python voltadas ao monitoramento de inteligência competitiva, mídias sociais e clipping de notícias, integrando **Google Gemini (Structured Outputs e multimodalidade)**, **Playwright (automação web com anti-detecção)** e pipelines analíticos com **pandas**.

| Script | O que faz |
|---|---|
| [`classificar_rep.py`](classificar_rep.py) | Classifica o sentimento/repercussão de menções em plataformas de monitoramento: lê os cards do DOM, envia à IA em lote com validação de esquema Pydantic e interage com os controles web da interface. |
| [`relatorio_clipping.py`](relatorio_clipping.py) | Executa triagem temática rigorosa de publicações de clipping com IA, calcula métricas matemáticas de engajamento via `pandas` e gera relatórios executivos em Markdown e Excel para auditoria. |
| [`transcrever_audio.py`](transcrever_audio.py) | Pipeline de ingestão de mídia: extrai áudio de publicações online (YouTube, Instagram, etc.) via `yt-dlp` e gera transcrições de alta fidelidade com o Gemini. |
| [`dom_inspector.py`](dom_inspector.py) | Ferramenta de apoio ao desenvolvedor: captura elementos do DOM via atalho do teclado (Ctrl+Clique) e gera seletores CSS e snapshots estruturados para automações com Playwright. |
| [`ronda_redes/ronda_redes.py`](ronda_redes/ronda_redes.py) | Motor de ronda direta nas redes sociais (ex: X/Twitter): processa consultas booleanas complexas, aplica filtros de audiência/interações e gera relatórios temporais contínuos. |
| [`ronda_redes/gerador_resumos_geminiV5.1.py`](ronda_redes/gerador_resumos_geminiV5.1.py) | **Bot do Telegram**: recebe URLs de postagens, recupera metadados/mídia (banco local, scraping de fallback ou áudio/vídeo) e gera resumos jornalísticos formatados (📲) únicos ou combinados. |

---

## 1. Instalação e Requisitos

Ambiente recomendado: Python 3.10+ em ambiente virtual (`.venv`).

```bash
# Criar e ativar o ambiente virtual
python3 -m venv .venv
source .venv/bin/activate            # No Windows: .venv\Scripts\activate

# Instalar dependências principais
pip install python-dotenv google-genai instructor pydantic playwright \
            pandas openpyxl yt-dlp static-ffmpeg

# Dependências adicionais para o Bot do Telegram
pip install google-generativeai pyTelegramBotAPI requests

# Instalar os navegadores do Playwright
playwright install chromium firefox
```

Requisitos adicionais do sistema:
- **ffmpeg**: Necessário para extração e processamento de áudio (localizado no PATH ou via pacote `static-ffmpeg`).
- **Google Chrome** e **Mozilla Firefox**: Utilizados pelos fluxos de automação e scraping com perfis persistentes.

### Configuração de Ambiente (`.env`)

Crie um arquivo `.env` na raiz do projeto com as chaves necessárias:

```env
GEMINI_API_KEY=XXXXX_SUA_CHAVE_GEMINI_AQUI_XXXXX
GEMINI_MODEL=gemini-2.5-flash
TELEGRAM_BOT_TOKEN=XXXXX_SEU_TOKEN_TELEGRAM_AQUI_XXXXX
URL_PLATAFORMA=https://app.social-monitor.exemplo.com/#/
```

---

## 2. Destaques de Arquitetura e Engenharia de IA

### A. Extração Estruturada com Validação Estrita (Structured Outputs)
Em [`classificar_rep.py`](classificar_rep.py) e [`relatorio_clipping.py`](relatorio_clipping.py), o retorno do LLM é tipado e validado em tempo de execução via **Pydantic** e biblioteca **Instructor**:
- Garantia de que categorias pertençam estritamente ao conjunto de `Literal["positivo", "favoravel", "desfavoravel", "negativo"]`.
- Obtenção da justificativa lógica de cada classificação para fins de explicabilidade (XAI) e auditoria de decisões.
- Classificação em lote (*batching*) reduzindo chamadas de rede e custos de token.

### B. Ingestão Multimodal Completa
No bot [`ronda_redes/gerador_resumos_geminiV5.1.py`](ronda_redes/gerador_resumos_geminiV5.1.py):
- URLs do YouTube são processadas nativamente pela API de vídeo do Gemini (analisando áudio + frames visuais diretamente da URL).
- Outras plataformas passam por pipeline de extração de MP3 e imagens antes do envio para o modelo.
- Suporte a múltiplos links simultâneos com síntese combinada coerente.

### C. Automação Web Resiliente com Playwright
- Uso de perfis persistentes para manter autenticação de sessão.
- Estratégias anti-bloqueio com simulação de comportamento humano (curvas e passos graduais de movimento do mouse, pausas aleatórias e rolagem de viewport).
- Mecanismo em cascata (*fallback strategy*): busca primeiro em banco local indexado (`cards_coletados.json`), depois scraping via navegador headless e, por fim, extração via `yt-dlp`.

---

## 3. Guia de Uso dos Módulos

### Classificador de Menções (`classificar_rep.py`)
```bash
python classificar_rep.py --paginas 3 --dry-run
```
- `--dry-run`: Extrai do DOM e classifica via IA exibindo a tabela no terminal, sem realizar cliques de alteração no site.
- `--paginas N`: Define o total de páginas a iterar automaticamente.
- Tratamento automático de retries (até 3 tentativas) com fallback configurável de modelo.

### Relatório e Triagem de Clipping (`relatorio_clipping.py`)
```bash
# Execução interativa ou indicando planilha de entrada
python relatorio_clipping.py dados_exemplo.xlsx --top 5 --lote 15

# Modo de entrada direta por texto
python relatorio_clipping.py --colar
```
- Filtra publicações segundo diretrizes temáticas parametrizadas no manual de triagem.
- Calcula volume de engajamento matemático: $\text{Interações} = \text{Reações} + \text{Comentários} + \text{Compartilhamentos}$.
- Gera saída executiva em Markdown e exportação de planilha `.xlsx` com colunas calculadas.

### Transcrição de Áudio (`transcrever_audio.py`)
```bash
python transcrever_audio.py "https://exemplo.com/video"
```
- Baixa o áudio temporário, realiza o upload para o Gemini File API, gera o texto fiel e limpa os arquivos locais e remotos automaticamente.

### Ronda em Redes Sociais (`ronda_redes/ronda_redes.py`)
```bash
cd ronda_redes
python ronda_redes.py
```
- Executa varreduras baseadas no arquivo `buscas.json`.
- Grava histórico de deduplicação em `historico_links.txt` e salva dados completos em `cards_coletados.json`.

### Bot de Resumos Jornalísticos (`ronda_redes/gerador_resumos_geminiV5.1.py`)
```bash
cd ronda_redes
python gerador_resumos_geminiV5.1.py
```
- Ao receber links no Telegram, entrega resumos em estilo jornalístico conciso encapsulados em negrito (`📲 Veículo: O veículo publicou que...`).

---

## 4. Estrutura do Projeto

```
projeto_relatorio_rep_ex/
├── classificar_rep.py                  # Classificador com Structured Outputs (Pydantic + Playwright)
├── relatorio_clipping.py               # Triagem temática rigorosa e analytics (pandas + Gemini)
├── transcrever_audio.py                # Pipeline de transcrição de mídia com Gemini File API
├── dom_inspector.py                    # Utilitário de inspeção e engenharia reversa de seletores DOM
├── ronda_redes/
│   ├── ronda_redes.py                  # Motor de ronda periódica com Playwright
│   ├── gerador_resumos_geminiV5.1.py   # Bot Telegram multimodal para síntese de publicações
│   ├── config.json                     # Parâmetros de ciclo, intervalos e limites de engajamento
│   ├── buscas.json                     # Consultas booleanas de monitoramento de exemplo
│   ├── core/                           # Módulos utilitários (log, persistência, booleanas)
│   └── plataformas/                    # Implementação modular de adaptadores por rede social
├── .env.example                        # Modelo de variáveis de ambiente
└── README.md                           # Documentação técnica do projeto
```
