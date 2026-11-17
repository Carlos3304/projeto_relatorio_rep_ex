"""
================================================================
 RONDA REDES  v1.0  —  monitoramento direto nas redes sociais
================================================================
 Monitoramento direto nas redes sociais via Playwright com perfil dedicado.
 Plataformas suportadas: X/Twitter  (arquitetura extensível para Instagram, Facebook...)

 Arquivos (na pasta do script):
   config.json   → configurações gerais + parâmetros por plataforma
   buscas.json   → consultas booleanas de busca
                   (opcional por busca: "booleana_x" e "plataformas": ["twitter"])

 Gerados automaticamente:
   output/ronda_redes_YYYYMMDD_HHMM.txt → relatório consolidado por ciclo
   historico_links.txt                  → links já coletados (deduplicação)
   cards_coletados.json                 → dados estruturados dos posts
   ronda_redes.log                      → log de execução
   debug/                               → diagnósticos em modo debug

 Execução de exemplo:
   python ronda_redes.py
================================================================
"""

import os
import sys
import json
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright

from core.utils import (
    caminho, carregar_json, configurar_log, log, wrn, er, dbg, fmt, pausa,
    aguardar_horario_alvo, aguardar_internet, is_erro_de_rede,
    Historico, salvar_cards_json,
)
from plataformas import REGISTRO, SessaoExpirada

configurar_log("ronda_redes.log")

CONFIG = carregar_json("config.json", "configurações (config.json)")
_BUSCAS_RAW = carregar_json(CONFIG.get("ARQUIVO_BUSCAS", "buscas.json"), "buscas booleanas")
BUSCAS = ([{"nome": k, "booleana": v} for k, v in _BUSCAS_RAW.items()]
          if isinstance(_BUSCAS_RAW, dict) else _BUSCAS_RAW)
GRUPOS = list(dict.fromkeys(b["nome"] for b in BUSCAS))


# ================================================================
#  NAVEGADOR
# ================================================================
def resolver_perfil():
    perfil = caminho(CONFIG.get("PERFIL_CAMINHO", "perfil_ronda_redes"))
    if not os.path.exists(perfil):
        os.makedirs(perfil)
        log(f"Perfil criado (primeiro uso): {perfil}")
    else:
        log(f"Perfil carregado de: {perfil}")
    return perfil


def abrir_contexto(p):
    perfil = resolver_perfil()
    comum = dict(headless=CONFIG.get("HEADLESS", False), no_viewport=True,
                 locale="pt-BR", timezone_id="America/Sao_Paulo")
    if CONFIG.get("NAVEGADOR", "chrome").lower() == "firefox":
        return p.firefox.launch_persistent_context(perfil, **comum)
    return p.chromium.launch_persistent_context(
        perfil, channel="chrome", **comum,
        args=["--start-maximized", "--disable-blink-features=AutomationControlled",
              "--no-proxy-server", "--disable-background-networking"],
    )


# ================================================================
#  PLATAFORMAS
# ================================================================
def carregar_plataformas():
    ativas = []
    for nome, cfg in CONFIG.get("PLATAFORMAS", {}).items():
        if not cfg.get("ATIVO", True):
            continue
        cls = REGISTRO.get(nome)
        if not cls:
            wrn(f"Plataforma '{nome}' configurada mas ainda não implementada — ignorada.")
            continue
        ativas.append(cls(CONFIG, cfg))
    if not ativas:
        raise SystemExit("Nenhuma plataforma ativa em config.json → PLATAFORMAS.")
    return ativas


def busca_vale_para(busca, plataforma):
    alvo = busca.get("plataformas")
    return not alvo or plataforma.nome in alvo


# ================================================================
#  SELEÇÃO DOS POSTS
# ================================================================
def filtrar_posts(plataforma, posts, busca_nome, historico):
    novos, duplicados, ignorados = [], [], 0
    for i, p in enumerate(posts, 1):
        ok, criterio = plataforma.selecionado(p)
        dbg(
            f"  [{busca_nome}] {plataforma.canal} {i:02d}: {p['autor']} | {p['data_pub']} | "
            f"seg={p['seguidores']:,} | curt={p['curtidas']} | coment={p['comentarios']} | "
            f"comp={p['compartilhamentos']} | views={p['views']} | inter={p['interacoes']} "
            f"→ {'✔ SELECIONADO' if ok else '✘ ignorado'} ({criterio})"
        )
        dbg(f"  [{busca_nome}] {i:02d} conteúdo: \"{p['conteudo'][:300]}\"")
        if not ok:
            ignorados += 1
            continue
        if p["url"] in historico:
            duplicados.append(p["url"])
        else:
            p["busca"] = busca_nome
            novos.append(p)
    log(f"  [{busca_nome}] {plataforma.canal}: {len(posts)} posts | {len(novos)} novos | "
        f"{len(duplicados)} já conhecidos | {ignorados} abaixo do critério")
    return novos, duplicados


# ================================================================
#  RELATÓRIO (mesmo formato do rondaV5.8)
# ================================================================
def escrever_relatorio(caminho_arq, ini, fim, novos_g, antigos_g, grupos_visiveis=None, finalizado=True):
    atraso = CONFIG.get("ATRASO_MINUTOS", 0)
    grupos = grupos_visiveis if grupos_visiveis is not None else GRUPOS

    linhas = []
    linhas.append(f"Período : {fmt(ini)} → {fmt(fim)}\n")
    linhas.append(f"Gerado  : {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
    linhas.append(f"Fonte   : Ronda Redes (busca direta nas redes)\n")
    if atraso:
        linhas.append(f"Atraso  : {atraso} min de sobreposição com ciclo anterior\n")
    linhas.append("=" * 60 + "\n\n")

    for g in grupos:
        linhas.append(f"*{g}*\n")
        if novos_g[g]:
            for p in novos_g[g]:
                linhas.append(f"{p['url']}\n")
        else:
            linhas.append("Sem menções relevantes nas redes\n")
        linhas.append("\n")

    if finalizado:
        if any(antigos_g[g] for g in GRUPOS):
            linhas.append("=" * 60 + "\nPUBLICAÇÕES JÁ REGISTRADAS ANTERIORMENTE\n" + "=" * 60 + "\n\n")
            for g in GRUPOS:
                if antigos_g[g]:
                    linhas.append(f"*{g}*\n" + "".join(f"{u}\n" for u in antigos_g[g]) + "\n")

        linhas.append("\n" + "=" * 60 + "\nRESUMO DO CICLO\n" + "=" * 60 + "\n")
        tn = td = 0
        for g in GRUPOS:
            n, d = len(novos_g[g]), len(antigos_g[g])
            tn, td = tn + n, td + d
            linhas.append(f"  {g:<45} {n:>3} novos | {d:>3} já conhecidos\n")
        linhas.append(f"  {'TOTAL':<45} {tn:>3} novos | {td:>3} já conhecidos\n")
    else:
        tn = sum(len(novos_g[g]) for g in grupos)
        td = sum(len(antigos_g[g]) for g in grupos)

    conteudo = "".join(linhas)
    caminho_tmp = f"{caminho_arq}.tmp"
    try:
        with open(caminho_tmp, "w", encoding="utf-8") as f:
            f.write(conteudo)
        os.replace(caminho_tmp, caminho_arq)
    except Exception:
        with open(caminho_arq, "w", encoding="utf-8") as f:
            f.write(conteudo)

    return tn, td


# ================================================================
#  INTERVALOS
# ================================================================
def calcular_proximo_intervalo(fim_atual):
    atraso = CONFIG.get("ATRASO_MINUTOS", 0)
    intervalo = CONFIG["INTERVALO_MINUTOS"]
    novo_fim = fim_atual + timedelta(minutes=intervalo)
    return novo_fim - timedelta(minutes=intervalo + atraso - 1), novo_fim


def atualizar_config_ronda(ini, fim):
    arq = caminho("config.json")
    try:
        with open(arq, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["INICIO_RONDA"], cfg["FIM_RONDA"] = fmt(ini), fmt(fim)
        with open(arq, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4, ensure_ascii=False)
    except Exception as e:
        er(f"  Erro ao atualizar config.json: {e}")


# ================================================================
#  CICLO
# ================================================================
def executar_ciclo(page, plataformas, ini, fim, historico, caminho_relatorio=None):
    novos_g = {g: [] for g in GRUPOS}
    antigos_g = {g: [] for g in GRUPOS}
    max_relogin = 2

    # Cria o arquivo de relatório em tempo real no início do ciclo
    if caminho_relatorio:
        escrever_relatorio(caminho_relatorio, ini, fim, novos_g, antigos_g, grupos_visiveis=[], finalizado=False)

    for idx, busca in enumerate(BUSCAS, 1):
        nome = busca["nome"]
        log(f"\n[{idx}/{len(BUSCAS)}] 🔎 {nome}")

        pular_booleana = False
        for plat in plataformas:
            if not busca_vale_para(busca, plat):
                continue
            posts = None
            for tentativa in range(max_relogin + 1):
                try:
                    posts = plat.buscar(page, busca, ini, fim)
                    break
                except SessaoExpirada as e:
                    wrn(f"  {plat.canal}: sessão expirada ({e}). Solicitando login...")
                    plat.garantir_login(page, interativo=True)

            if posts is None:
                wrn(f"  [{nome}] Busca falhou após tentativas. Pulando para a próxima booleana.")
                pular_booleana = True
                break

            novos, dupl = filtrar_posts(plat, posts, nome, historico)
            novos_g[nome].extend(novos)
            antigos_g[nome].extend(dupl)
            historico.registrar([p["url"] for p in novos])
            salvar_cards_json(novos, CONFIG.get("ARQUIVO_CARDS", "cards_coletados.json"))

        # Atualiza o arquivo de saída em tempo real a cada busca concluída
        if caminho_relatorio:
            grupos_visiveis = list(dict.fromkeys(b["nome"] for b in BUSCAS[:idx]))
            escrever_relatorio(caminho_relatorio, ini, fim, novos_g, antigos_g, grupos_visiveis=grupos_visiveis, finalizado=False)

        if pular_booleana:
            if idx < len(BUSCAS):
                pausa(*CONFIG.get("PAUSA_ENTRE_BUSCAS_SEG", [6, 15]))
            continue

        if idx < len(BUSCAS):
            pausa(*CONFIG.get("PAUSA_ENTRE_BUSCAS_SEG", [6, 15]))

    return novos_g, antigos_g


# ================================================================
#  MAIN
# ================================================================
def main():
    atraso = CONFIG.get("ATRASO_MINUTOS", 0)
    plataformas = carregar_plataformas()

    log("══════════════════════════════════════════════════════")
    log("  RONDA REDES  v1.0")
    log(f"  Plataformas  → {', '.join(p.canal for p in plataformas)}")
    log(f"  Critério     → seg>={CONFIG['MIN_SEGUIDORES']:,} | inter>={CONFIG['MIN_INTERACOES']} "
        f"(pode ser sobrescrito por plataforma)")
    log(f"  buscas.json  → {len(BUSCAS)} busca(s) em {len(GRUPOS)} grupo(s)")
    log(f"  Intervalo    → {CONFIG['INTERVALO_MINUTOS']} min | Atraso/sobreposição → {atraso} min")
    log("══════════════════════════════════════════════════════")

    historico = Historico(CONFIG.get("ARQUIVO_HISTORICO", "historico_links.txt"))
    ini = datetime.strptime(CONFIG["INICIO_RONDA"], "%d/%m/%Y %H:%M")
    fim = datetime.strptime(CONFIG["FIM_RONDA"], "%d/%m/%Y %H:%M")
    pasta_saida = caminho(CONFIG.get("PASTA_SAIDA", "output"))
    os.makedirs(pasta_saida, exist_ok=True)

    with sync_playwright() as p:
        context = abrir_contexto(p)
        page = context.pages[0] if context.pages else context.new_page()

        for plat in plataformas:
            plat.preparar(page)
            while True:
                try:
                    aguardar_internet()
                    plat.garantir_login(page, interativo=True)
                    break
                except Exception as e:
                    if not is_erro_de_rede(e):
                        raise
                    er(f"Erro de rede ao verificar login: {e}")

        if CONFIG.get("AGUARDAR_ENTER", True):
            input(">>> Pressione ENTER para iniciar as rondas...\n")

        ciclo = 1
        while True:
            log(f"\n{'═' * 55}\n  CICLO {ciclo} | Período: {fmt(ini)} → {fmt(fim)}")
            if atraso:
                log(f"  (sobreposição de {atraso} min com o ciclo anterior)")
            log("═" * 55)

            aguardar_horario_alvo(fim + timedelta(minutes=1))
            nome_arq = f"ronda_redes_{fim:%Y%m%d_%H%M}.txt"
            caminho_relatorio = os.path.join(pasta_saida, nome_arq)
            log(f"Iniciando coleta → {nome_arq}")

            try:
                aguardar_internet()
                novos_g, antigos_g = executar_ciclo(page, plataformas, ini, fim, historico, caminho_relatorio)
            except Exception as e:
                if is_erro_de_rede(e):
                    er(f"⚠️  Erro de rede durante o ciclo {ciclo}: {e}")
                    aguardar_internet()
                    wrn(f"Repetindo ciclo {ciclo} desde o início...")
                    continue
                raise

            tn, td = escrever_relatorio(caminho_relatorio, ini, fim, novos_g, antigos_g, finalizado=True)

            log(f"\n{'─' * 55}\nCiclo {ciclo} concluído → {nome_arq}")
            for g in GRUPOS:
                log(f"   {g:<45} {len(novos_g[g]):>3} novos | {len(antigos_g[g]):>3} já conhecidos")
            log(f"   {'TOTAL':<45} {tn:>3} novos | {td:>3} já conhecidos\n{'─' * 55}")

            if CONFIG.get("CICLO_UNICO", False):
                log("CICLO_UNICO=true → encerrando.")
                break

            ini, fim = calcular_proximo_intervalo(fim)
            atualizar_config_ronda(ini, fim)
            log(f"\nPróximo ciclo: {fmt(ini)} → {fmt(fim)}")
            ciclo += 1

        context.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Interrompido pelo usuário.")
