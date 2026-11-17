"""
Interface comum das plataformas da Ronda Redes.

Para adicionar uma nova rede (Instagram, Facebook, TikTok, YouTube...):
  1. Crie plataformas/<rede>.py com uma classe herdando de `Plataforma`
  2. Implemente `esta_logado`, `url_login` e `buscar`
  3. Registre a classe em plataformas/__init__.py (REGISTRO)
  4. Adicione o bloco de configuração em config.json → "PLATAFORMAS"

Todo post retornado por `buscar` deve seguir o formato de `novo_post()`.
"""

from core.utils import log, wrn


class SessaoExpirada(Exception):
    """Levantada quando a plataforma redireciona para a tela de login."""


def novo_post(**kw):
    post = {
        "plataforma":        None,
        "canal":             "N/D",
        "url":               None,
        "id":                None,
        "autor":             "N/D",   # @username / handle
        "autor_nome":        "N/D",   # nome de exibição
        "seguidores":        0,
        "data_pub":          "N/D",   # dd/mm/aaaa HH:MM (horário local)
        "data_iso":          None,
        "curtidas":          0,
        "comentarios":       0,
        "compartilhamentos": 0,
        "views":             0,
        "interacoes":        0,
        "conteudo":          "N/D",
        "imagem_url":        None,
        "tem_video":         False,
        "is_resposta":       False,
        "is_repost":         False,
        "fonte":             None,    # "graphql" | "dom" (diagnóstico)
    }
    post.update(kw)
    return post


class Plataforma:
    nome = "base"
    canal = "N/D"
    url_home = ""

    def __init__(self, cfg_global: dict, cfg: dict):
        self.cfg_global = cfg_global
        self.cfg = cfg or {}

    # ── configuração com fallback para o config global ──
    def opt(self, chave, padrao=None):
        if chave in self.cfg:
            return self.cfg[chave]
        return self.cfg_global.get(chave, padrao)

    # ── ciclo de vida ──
    def preparar(self, page):
        """Chamado uma vez após abrir a página (registrar listeners etc.)."""

    def url_login(self) -> str:
        return self.url_home

    def esta_logado(self, page) -> bool:
        raise NotImplementedError

    def garantir_login(self, page, interativo=True) -> bool:
        if self.esta_logado(page):
            log(f"  ✅ {self.canal}: sessão válida.")
            return True
        if not interativo:
            return False
        while True:
            page.goto(self.url_login(), wait_until="domcontentloaded", timeout=60000)
            print("\n" + "=" * 60)
            print(f"  ⚠️  {self.canal}: é necessário fazer login no navegador aberto.")
            print("  → Faça o login normalmente (usuário/senha, 2FA etc.).")
            print("  → Quando terminar, pressione ENTER aqui.")
            print("=" * 60)
            input()
            if self.esta_logado(page):
                log(f"  ✅ {self.canal}: login confirmado.")
                return True
            wrn(f"  {self.canal}: login ainda não detectado. Tente novamente.")

    # ── coleta ──
    def buscar(self, page, busca: dict, dt_ini, dt_fim) -> list:
        """Executa a busca no período e devolve lista de posts (novo_post)."""
        raise NotImplementedError

    # ── critério de seleção (pode ser sobrescrito por plataforma) ──
    def calcular_interacoes(self, post):
        total = post["curtidas"] + post["comentarios"] + post["compartilhamentos"]
        if self.opt("CONTAR_VIEWS_EM_INTERACOES", False):
            total += post["views"]
        return total

    def selecionado(self, post):
        min_seg = self.opt("MIN_SEGUIDORES", 0)
        min_int = self.opt("MIN_INTERACOES", 0)
        criterios = []
        if post["seguidores"] >= min_seg:
            criterios.append(f"seg {post['seguidores']:,} >= {min_seg:,}")
        if post["interacoes"] >= min_int:
            criterios.append(f"inter {post['interacoes']} >= {min_int}")
        return bool(criterios), (" | ".join(criterios) if criterios else "abaixo do mínimo")
