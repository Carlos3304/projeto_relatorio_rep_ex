"""
Utilidades compartilhadas da Ronda Redes:
log, conectividade, histórico de links, persistência de cards e helpers.
(Estruturas portadas do rondaV5.8.py e coleta_redesV2.7.py)
"""

import os
import re
import sys
import json
import time
import random
import socket
import logging
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PASTA_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def caminho(rel):
    """Resolve caminho relativo à pasta do projeto (aceita absoluto)."""
    rel = os.path.expanduser(rel)
    return rel if os.path.isabs(rel) else os.path.join(PASTA_BASE, rel)


# ================================================================
#  CARREGAMENTO DE JSON
# ================================================================
def carregar_json(nome, descricao):
    arq = caminho(nome)
    if not os.path.exists(arq):
        raise FileNotFoundError(
            f"\nArquivo '{arq}' não encontrado!\n"
            f"Certifique-se de que o arquivo de {descricao} está na pasta do script.\n"
        )
    with open(arq, "r", encoding="utf-8") as f:
        return json.load(f)


# ================================================================
#  SISTEMA DE LOG
# ================================================================
logger = logging.getLogger("ronda_redes")


def configurar_log(arquivo="ronda_redes.log"):
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    fmt_ = logging.Formatter("[%(asctime)s] %(levelname)-8s %(message)s", datefmt="%H:%M:%S")
    fh = logging.FileHandler(caminho(arquivo), encoding="utf-8")
    fh.setFormatter(fmt_)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt_)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def log(msg): logger.info(msg)
def wrn(msg): logger.warning(msg)
def er(msg):  logger.error(msg)
def dbg(msg): logger.debug(msg)


# ================================================================
#  HELPERS
# ================================================================
def fmt(dt):
    return dt.strftime("%d/%m/%Y %H:%M")


def limpar_texto(texto):
    if not texto:
        return ""
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def converter_valor(s) -> int:
    """Converte contagens ('2.2M', '1,5 mil', '1.500', '3K') para inteiro."""
    if s is None:
        return 0
    if isinstance(s, (int, float)):
        return int(s)
    s = str(s).replace("\xa0", " ").strip()
    if not s or s.lower() in ("n/d", "nan"):
        return 0
    if "." in s and "," in s:
        if s.index(".") < s.index(","):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", "") if re.search(r",\d{3}(\b|$)", s) else s.replace(",", ".")
    elif "." in s and re.search(r"\.\d{3}(\b|$)", s):
        s = s.replace(".", "")
    sl = s.lower().strip()
    for suf, mult in [(" mil", 1_000), ("mil", 1_000), (" mi", 1_000_000), ("mi", 1_000_000),
                      (" bi", 1_000_000_000), ("bi", 1_000_000_000)]:
        if sl.endswith(suf):
            try:
                return int(float(sl[:-len(suf)].strip()) * mult)
            except ValueError:
                return 0
    su = s.upper().strip()
    for letra, mult in [("K", 1_000), ("M", 1_000_000), ("B", 1_000_000_000)]:
        if su.endswith(letra):
            try:
                return int(float(su[:-1]) * mult)
            except ValueError:
                return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def pausa(min_seg, max_seg):
    time.sleep(random.uniform(min_seg, max_seg))


def aguardar_horario_alvo(horario_alvo):
    if datetime.now() >= horario_alvo:
        return
    log(f"Aguardando até {horario_alvo.strftime('%d/%m %H:%M:%S')}...")
    while datetime.now() < horario_alvo:
        time.sleep(5)


# ================================================================
#  INTERNET
# ================================================================
def verificar_internet(host="8.8.8.8", porta=53, timeout=5):
    try:
        socket.create_connection((host, porta), timeout=timeout).close()
        return True
    except (socket.timeout, OSError):
        return False


def aguardar_internet(intervalo=15):
    if verificar_internet():
        return
    wrn("⚠️  Sem conexão com a internet — aguardando reconexão...")
    tentativa = 0
    while not verificar_internet():
        tentativa += 1
        if tentativa % 4 == 0:
            wrn(f"  Ainda sem internet... ({tentativa * intervalo}s decorridos)")
        time.sleep(intervalo)
    log("✅  Internet restaurada! Retomando operação.")
    time.sleep(3)


def is_erro_de_rede(exc):
    if isinstance(exc, ConnectionError):
        return True
    padroes = [
        "net::ERR_", "NS_ERROR_UNKNOWN_HOST", "Timeout", "Você está offline",
        "Target page, context or browser has been closed",
        "browser has been disconnected", "Navigation failed because page was closed",
    ]
    msg = str(exc).lower()
    return any(p.lower() in msg for p in padroes)


# ================================================================
#  HISTÓRICO DE LINKS
# ================================================================
class Historico:
    def __init__(self, arquivo):
        self.arquivo = caminho(arquivo)
        self.links = set()
        if os.path.exists(self.arquivo):
            with open(self.arquivo, "r", encoding="utf-8") as f:
                self.links = {l.strip() for l in f if l.strip() and not l.startswith("#")}
            log(f"Histórico carregado: {len(self.links)} link(s) já conhecidos.")
        else:
            log("Histórico não encontrado — iniciando do zero.")

    def __contains__(self, url):
        return url in self.links

    def registrar(self, urls):
        novos = [u for u in urls if u and u not in self.links]
        if not novos:
            return
        with open(self.arquivo, "a", encoding="utf-8") as f:
            for u in novos:
                f.write(u + "\n")
        self.links.update(novos)
        dbg(f"{len(novos)} link(s) adicionado(s) ao histórico.")


# ================================================================
#  PERSISTÊNCIA DOS CARDS (compatível com cards_coletados.json do ronda)
# ================================================================
def salvar_cards_json(posts, arquivo="cards_coletados.json"):
    if not posts:
        return
    arq = caminho(arquivo)
    dados = {}
    if os.path.exists(arq):
        try:
            with open(arq, "r", encoding="utf-8") as f:
                dados = json.load(f)
        except Exception:
            pass
    for p in posts:
        if p.get("url"):
            dados[p["url"]] = {
                # chaves usadas pelo gerador_resumos (mesmo formato do rondaV5.8)
                "autor":      p.get("autor", "N/D"),
                "canal":      p.get("canal", "N/D"),
                "conteudo":   p.get("conteudo", "N/D"),
                "imagem_url": p.get("imagem_url"),
                # extras
                "autor_nome":        p.get("autor_nome"),
                "data_pub":          p.get("data_pub"),
                "seguidores":        p.get("seguidores"),
                "curtidas":          p.get("curtidas"),
                "comentarios":       p.get("comentarios"),
                "compartilhamentos": p.get("compartilhamentos"),
                "views":             p.get("views"),
                "tem_video":         p.get("tem_video"),
                "busca":             p.get("busca"),
            }
    with open(arq, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


def salvar_debug(nome, conteudo, pasta="debug"):
    """Grava arquivo de diagnóstico (HTML/JSON) em debug/."""
    d = caminho(pasta)
    os.makedirs(d, exist_ok=True)
    slug = re.sub(r"[^a-zA-Z0-9_.-]", "_", nome)[:120]
    arq = os.path.join(d, slug)
    with open(arq, "w", encoding="utf-8") as f:
        f.write(conteudo if isinstance(conteudo, str) else json.dumps(conteudo, ensure_ascii=False, indent=2))
    return arq
