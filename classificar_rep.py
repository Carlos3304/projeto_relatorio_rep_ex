"""
Classificador de Menções e Sentimento — classifica publicações em 4 categorias com IA (Gemini).

Fluxo:
  1. Abre o Chromium do Playwright com perfil dedicado.
  2. Usuário faz login / navega / aplica filtros e aperta ENTER.
  3. Para cada página: expande "Ver conteúdo", extrai autor + texto, envia à IA em lote
     e escolhe a categoria no dropdown (▼) de cada card. Depois avança a página.

Uso de exemplo:
    python classificar_rep.py [--paginas N] [--max N] [--dry-run]
"""

import os
import sys
import time
import json
import logging
import argparse
from datetime import datetime
from typing import Literal

from dotenv import load_dotenv
import instructor
from google import genai as google_genai
from pydantic import BaseModel, Field
from playwright.sync_api import sync_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PASTA_SCRIPT = os.path.dirname(os.path.abspath(__file__))

# ============================================================
# 1. LOG
# ============================================================
logging.basicConfig(
    filename=os.path.join(PASTA_SCRIPT, "execucao.log"),
    filemode="w",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8",
)

# ============================================================
# 2. AMBIENTE
# ============================================================
load_dotenv(os.path.join(PASTA_SCRIPT, ".env"))
api_key_segreta = os.getenv("GEMINI_API_KEY", "XXXXX_GEMINI_API_KEY_XXXXX")

# ============================================================
# 3. CONFIGURAÇÃO (PARAMETRIZADA)
# ============================================================
URL_SITE = os.getenv("URL_PLATAFORMA", "https://app.social-monitor.exemplo.com/#/")

if sys.platform.startswith("win"):
    PERFIL_CAMINHO = os.path.join(os.environ.get("LOCALAPPDATA", r"C:\AppData"), "PerfilMonitoramentoExemplo")
else:
    PERFIL_CAMINHO = os.path.expanduser("~/.config/perfil_monitoramento_exemplo")

MODELO_IA = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
MAX_TENTATIVAS_IA = 3
DELAY_ENTRE_CLIQUES = 0.4      # pausa entre um card e outro
ESPERA_EXPANDIR = 1.5          # após clicar em todos os "Ver conteúdo"
ESPERA_MAX_PAGINA = 60         # segundos máx. aguardando a próxima página trocar
ESPERA_RETRY_CARDS = 10        # segundos entre tentativas de achar cards

ARQ_BACKUP = os.path.join(PASTA_SCRIPT, "backup_ultima_pagina.json")

# --- Seletores (baseados em estrutura pois as classes hash mudam) ---
SEL_GATILHO = "svg[viewBox='0 0 25 24']"                # Ícone de polaridade (clicável, abre o dropdown)
SEL_PROXIMA = "button:has(span.i-icon-right)"

ROTULO_SITE = {
    "positivo": "POSITIVO",
    "favoravel": "FAVORAVEL",
    "desfavoravel": "DESFAVORAVEL",
    "negativo": "NEGATIVO",
}

client = instructor.from_genai(
    google_genai.Client(api_key=api_key_segreta),
    mode=instructor.Mode.GENAI_STRUCTURED_OUTPUTS,
    model=MODELO_IA,
)

# ============================================================
# 4. MODELOS PYDANTIC
# ============================================================
class AnalisePost(BaseModel):
    id: int
    sentimento: Literal["positivo", "favoravel", "desfavoravel", "negativo"]
    logica: str = Field(description="Qual regra do manual de reputação foi aplicada e por que")


class AnaliseEmLote(BaseModel):
    resultados: list[AnalisePost]

# ============================================================
# 5. DIRETRIZES DE EXEMPLO (MANUAL DE REPUTAÇÃO E SENTIMENTO)
# ============================================================
DIRETRIZES = """
REGRA GERAL: Classifique a repercussão da publicação para a Organização / Marca Monitorada
(Empresa Exemplo S.A. e sua Diretoria Executiva).
Não confunda com viés do veículo — a publicação pode ter repercussão favorável/desfavorável
mesmo trazendo informações de maneira técnica ou descritiva.
Identifique para que lado a narrativa pende em relação à marca institucional.

CATEGORIAS:
- POSITIVO: favorece clara e explicitamente (elogio, premiação, resultados recordes, apoio de mercado).
- FAVORAVEL: pende favoravelmente (menção institucional neutra/informativa sem depreciação,
  visibilidade de produtos, contexto de expansão de setor).
- DESFAVORAVEL: pende levemente contra (reclamação pontual de serviço, especulação de mercado,
  associação indireta a temas polêmicos sem ataque institucional direto).
- NEGATIVO: crítica severa, denúncia, falha grave de produto/compliance ou ataque direto à diretoria.

MANUAL DE ANÁLISE DE REPERCUSSÃO (por ordem de prioridade):
1. Comunicados oficiais da própria Organização ou de seus porta-vozes = FAVORAVEL.
2. Matérias sobre inovações, novos lançamentos e premiações da empresa = POSITIVO.
3. Menções a concorrentes diretos sem citação da Organização = FAVORAVEL (informativo de setor).
4. Críticas diretas à qualidade, atendimento ou liderança da Organização = NEGATIVO.
5. Menções informativas sobre o segmento de mercado sem destaque negativo à marca = FAVORAVEL.
6. Equilíbrio entre pontos fortes e fracos = decida pela nuance predominante no texto: FAVORAVEL ou DESFAVORAVEL.
"""

# ============================================================
# 6. JAVASCRIPT DE EXTRAÇÃO
# ============================================================
JS_EXTRAIR = r"""
async (sel) => {
  const nodes = Array.from(document.querySelectorAll(sel));
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  // Card = ancestral do gatilho cujo pai já contém outro gatilho
  const cardOf = t => {
    let c = t;
    while (c.parentElement) {
      const p = c.parentElement;
      let k = 0;
      for (const s of nodes) { if (p.contains(s) && ++k > 1) return c; }
      c = p;
    }
    return c;
  };
  const maiorP = card => {
    let t = '';
    card.querySelectorAll('p').forEach(p => { const x = clean(p.textContent); if (x.length > t.length) t = x; });
    return t;
  };

  // Lê um card por vez: o site só mantém um "Ver conteúdo" aberto de cada vez
  const out = [];
  for (let idx = 0; idx < nodes.length; idx++) {
    const sp = nodes[idx];
    const card = cardOf(sp);
    const h4 = card.querySelector('h4');
    const autor = h4 ? clean(h4.textContent) : '';
    const curto = h4 && h4.nextElementSibling ? clean(h4.nextElementSibling.textContent) : '';

    let texto = '';
    const ver = Array.from(card.querySelectorAll('h5')).find(h => h.textContent.trim() === 'Ver conteúdo');
    if (ver) {
      ver.click();
      for (let w = 0; w < 30 && !texto; w++) {   // até ~3s (resolve em ~50-100ms se renderizar rápido)
        await sleep(50);
        texto = maiorP(card);
      }
    } else {
      texto = maiorP(card);                      // já estava aberto
    }
    const expandido = texto.length > 0;
    if (texto.length < curto.length) texto = curto;

    // Ícone "?" (pré-classificação falhou) = svg aninhado com viewBox 0 0 48 48
    const gatilho = sp.parentElement;
    const semClass = !!gatilho.querySelector('svg svg[viewBox="0 0 48 48"]');
    out.push({ idx, autor: autor || 'Desconhecido', texto: texto || 'Sem texto', sem_classificacao: semClass, expandido });
  }
  return out;
}
"""

JS_ASSINATURA = r"""
(sel) => {
  const nodes = document.querySelectorAll(sel);
  const parts = [];
  for (let i = 0; i < Math.min(nodes.length, 3); i++) {
    let c = nodes[i];
    for (let k = 0; k < 6 && c.parentElement; k++) c = c.parentElement;
    parts.push((c.textContent || '').slice(0, 200));
  }
  return nodes.length + '|' + parts.join('||');
}
"""

# ============================================================
# 7. FUNÇÕES AUXILIARES
# ============================================================
def aguardar_com_contagem(segundos: int, mensagem: str) -> None:
    for restante in range(segundos, 0, -1):
        print(f"\r  ⏳ {mensagem} ({restante}s)...  ", end="", flush=True)
        time.sleep(1)
    print()


def contar_cards(page) -> int:
    try:
        return page.locator(SEL_GATILHO).count()
    except Exception:
        return 0


def aguardar_cards(page) -> int:
    """Espera até haver cards na tela. Se não achar após 3 tentativas, pede ENTER."""
    tentativa = 1
    while True:
        n = contar_cards(page)
        if n:
            return n
        if tentativa >= 3:
            input("\n  ⚠️  Nenhum card encontrado. Ajuste a página e aperte ENTER...")
            tentativa = 0
        else:
            print(f"  ⏳ Cards não encontrados (tentativa {tentativa}). Nova tentativa em {ESPERA_RETRY_CARDS}s...")
            time.sleep(ESPERA_RETRY_CARDS)
        tentativa += 1


def assinatura_pagina(page) -> str:
    try:
        return page.evaluate(JS_ASSINATURA, SEL_GATILHO)
    except Exception:
        return ""


def avancar_pagina(page) -> bool:
    """Clica em '›' e espera o conteúdo trocar. Retorna False se não houver próxima."""
    botao = page.locator(SEL_PROXIMA).first
    try:
        if not botao.is_visible():
            logging.info("Botão '›' não visível — última página.")
            return False
    except Exception:
        return False

    antes = assinatura_pagina(page)
    botao.click()
    logging.info("Botão '›' clicado.")

    inicio = time.time()
    while time.time() - inicio < ESPERA_MAX_PAGINA:
        time.sleep(1)
        restante = int(ESPERA_MAX_PAGINA - (time.time() - inicio))
        print(f"\r  ⏳ Aguardando próxima página ({restante}s)...  ", end="", flush=True)
        agora = assinatura_pagina(page)
        if agora and agora != antes and contar_cards(page):
            print()
            time.sleep(2)  # folga para terminar de renderizar
            return True
    print()
    logging.warning("Página não trocou após clicar em '›'.")
    return False


def extrair_dados(page) -> list[dict]:
    print("  📖 Lendo publicações uma a uma (Ver conteúdo)...")
    dados = page.evaluate(JS_EXTRAIR, SEL_GATILHO)
    sem = sum(1 for d in dados if d["sem_classificacao"])
    exp = sum(1 for d in dados if d.get("expandido"))
    logging.info(f"Extraídos {len(dados)} cards ({exp} com texto completo, {sem} sem classificação prévia).")
    if exp < len(dados):
        nao = [d["idx"] for d in dados if not d.get("expandido")]
        logging.warning(f"Texto completo NÃO lido nos IDs {nao} — usando texto curto.")
        print(f"  ⚠️  {len(nao)} card(s) sem texto completo (IDs {nao}); usando texto curto.")
    return dados


def classificar_com_ia(dados: list[dict]) -> AnaliseEmLote | None:
    total = len(dados)
    posts_formatados = "\n".join(
        f"[{i}] AUTOR: {p['autor']} | TEXTO: {p['texto']}" for i, p in enumerate(dados)
    )
    for tentativa in range(1, MAX_TENTATIVAS_IA + 1):
        try:
            resultado = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": f"Você é um analista político. Siga este manual:\n{DIRETRIZES}"},
                    {"role": "user", "content": f"Analise estes {total} posts. Retorne ID, sentimento e logica para cada um:\n\n{posts_formatados}"},
                ],
                response_model=AnaliseEmLote,
            )
            logging.info(f"IA respondeu com sucesso na tentativa {tentativa}.")
            return resultado
        except Exception as e:
            logging.warning(f"Tentativa {tentativa}/{MAX_TENTATIVAS_IA} falhou: {e}")
            if tentativa < MAX_TENTATIVAS_IA:
                time.sleep(2 * tentativa)
    logging.error("IA falhou em todas as tentativas.")
    return None


def salvar_backup(pagina: int, dados: list[dict], resultados: list) -> None:
    backup = {
        "timestamp": datetime.now().isoformat(),
        "pagina": pagina,
        "posts": dados,
        "resultados": [r.model_dump() for r in resultados],
    }
    with open(ARQ_BACKUP, "w", encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False, indent=2)


def imprimir_tabela(dados: list[dict], mapa: dict) -> None:
    print(f"\n  {'ID':>3} | {'CATEGORIA':12} | {'AUTOR':20} | TEXTO")
    print("  " + "-" * 100)
    for d in dados:
        r = mapa.get(d["idx"])
        cat = ROTULO_SITE[r.sentimento] if r else "—"
        print(f"  {d['idx']:>3} | {cat:12} | {d['autor'][:20]:20} | {d['texto'][:60]}")
        if r:
            print(f"      ↳ {r.logica[:140]}")


def selecionar_categoria(page, indice: int, rotulo: str) -> bool:
    """Abre o dropdown do card `indice` e clica na opção `rotulo`."""
    gatilho = page.locator(SEL_GATILHO).nth(indice)
    opcao = page.locator(f'li:has(span:text-is("{rotulo}")):visible').first
    try:
        gatilho.evaluate("e => e.scrollIntoView({block: 'center'})")
        time.sleep(0.5)
        gatilho.click(force=True, timeout=5000)
        opcao.wait_for(state="visible", timeout=5000)
        opcao.click(force=True, timeout=5000)
        try:
            opcao.wait_for(state="hidden", timeout=3000)
        except Exception:
            page.keyboard.press("Escape")
            logging.warning(f"ID {indice}: menu não fechou após clicar em '{rotulo}'.")
            return False
        return True
    except Exception as e:
        logging.error(f"ID {indice}: erro ao selecionar '{rotulo}': {e}")
        try:
            html = gatilho.evaluate("e => (e.parentElement || e).outerHTML.slice(0, 1500)")
            logging.error(f"[DIAGNÓSTICO] HTML do gatilho ID {indice}:\n{html}")
        except Exception:
            pass
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False


def aplicar_classificacoes(page, dados: list[dict], mapa: dict) -> int:
    ok = 0
    total = len(dados)
    for d in dados:
        i = d["idx"]
        res = mapa.get(i)
        if not res:
            logging.warning(f"ID {i} sem resultado da IA — pulado.")
            continue
        rotulo = ROTULO_SITE[res.sentimento]
        if selecionar_categoria(page, i, rotulo):
            ok += 1
            logging.info(f"ID {i:02d}/{total} | {rotulo:12s} | Autor: {d['autor']} | Motivo: {res.logica}")
        print(f"\r  Classificando {i + 1}/{total}... ({ok} OK)", end="", flush=True)
        time.sleep(DELAY_ENTRE_CLIQUES)
    print()
    return ok

# ============================================================
# 8. LOOP PRINCIPAL
# ============================================================
def parse_args():
    ap = argparse.ArgumentParser(description="Classificador de Menções com IA")
    ap.add_argument("--paginas", type=int, help="Quantas páginas classificar")
    ap.add_argument("--max", type=int, default=None, help="Limite total de publicações")
    ap.add_argument("--dry-run", action="store_true", help="Não clica, só mostra a classificação")
    return ap.parse_args()


def perguntar_paginas() -> int:
    while True:
        valor = input("  📄 Quantas páginas classificar? ").strip()
        if valor.isdigit() and int(valor) > 0:
            return int(valor)
        print("  ⚠️  Digite um número inteiro maior que zero.")


def main():
    args = parse_args()
    os.makedirs(PERFIL_CAMINHO, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            PERFIL_CAMINHO,
            channel="chrome",
            headless=False,
            no_viewport=True,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(URL_SITE, wait_until="domcontentloaded", timeout=60000)

        print("\n  🌐 Faça login, navegue até as menções e aplique os filtros.")
        input("  ✅ Quando a primeira página estiver pronta, pressione ENTER...")
        
        total_geral = 0
        while True:
            total_paginas = args.paginas or perguntar_paginas()
            modo = "DRY-RUN (sem cliques)" if args.dry_run else "REAL"
            print(f"\n  ▶️  Classificando {total_paginas} página(s) — modo {modo}.")
            logging.info(f"Início: {total_paginas} páginas, max={args.max}, modo={modo}.")

            atingiu_limite = False
            for pagina in range(1, total_paginas + 1):
                if args.max is not None and total_geral >= args.max:
                    print(f"\n  🎯 Limite de {args.max} publicações atingido.")
                    atingiu_limite = True
                    break

                if pagina > 1:
                    print(f"\n  🔄 Avançando para a página {pagina}...")
                    if not avancar_pagina(page):
                        print("  ℹ️  Não há próxima página. Encerrando.")
                        break

                print(f"\n{'=' * 55}\n  PÁGINA {pagina}/{total_paginas}  |  Classificados: {total_geral}\n{'=' * 55}")
                aguardar_cards(page)
                dados = extrair_dados(page)

                if args.max is not None:
                    dados = dados[: max(0, args.max - total_geral)]
                if not dados:
                    print("  ✅ Nenhuma publicação nesta página.")
                    break

                sem = sum(1 for d in dados if d["sem_classificacao"])
                print(f"  📋 {len(dados)} publicações ({sem} com '?').")

                print(f"  🤖 Enviando para a IA ({MODELO_IA})...")
                analise = classificar_com_ia(dados)
                if analise is None:
                    print("  ❌ IA falhou em todas as tentativas. Verifique execucao.log.")
                    break

                mapa = {r.id: r for r in analise.resultados}
                if len(mapa) != len(dados):
                    logging.warning(f"IA retornou {len(mapa)} resultados, esperado {len(dados)}.")
                salvar_backup(pagina, dados, analise.resultados)

                if args.dry_run:
                    imprimir_tabela(dados, mapa)
                    total_geral += len(mapa)
                    continue

                ok = aplicar_classificacoes(page, dados, mapa)
                total_geral += ok
                logging.info(f"Página {pagina}: {ok}/{len(dados)} classificados. Total: {total_geral}.")
                print(f"  ✅ Página {pagina}: {ok}/{len(dados)} classificados.  📊 Total: {total_geral}")

            if atingiu_limite:
                break

            print(f"\n  📄 Log: execucao.log  |  Backup: backup_ultima_pagina.json")
            resp = input("\n  🏁 Fila concluída. Deseja classificar mais páginas? (s/n): ").strip().lower()
            if resp != 's':
                break
            # Limpa o argumento para que o script pergunte a quantidade na próxima rodada
            args.paginas = None

        print(f"\n  👋 Encerrando o navegador...")
        context.close()
        print(f"  ✅ Sessão finalizada. Total geral: {total_geral} publicação(ões).")
        logging.info(f"Sessão encerrada. Total: {total_geral}.")


if __name__ == "__main__":
    main()
