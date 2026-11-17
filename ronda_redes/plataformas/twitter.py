"""
Plataforma X / Twitter.

Estratégia de coleta:
  1. Converte a booleana para a sintaxe de busca avançada do X,
     com janela de tempo exata via `since_time:` / `until_time:` (epoch).
  2. Abre  x.com/search?q=...&f=live  (aba "Mais recentes").
  3. PRINCIPAL → intercepta as respostas GraphQL `SearchTimeline` (JSON com
     seguidores, curtidas, respostas, reposts, views, texto completo, mídia).
  4. FALLBACK  → lê os <article data-testid="tweet"> do DOM (sem seguidores).
  5. Rola a timeline até não surgirem tweets novos (ou MAX_SCROLLS).
"""

import re
import time
import random
from datetime import datetime, timedelta
from urllib.parse import quote

from core.utils import log, wrn, er, dbg, pausa, salvar_debug, converter_valor
from core.booleana import para_x_lista, ErroBooleana
from plataformas.base import Plataforma, SessaoExpirada, novo_post


SEL_TWEET = 'article[data-testid="tweet"]'
SEL_VAZIO = '[data-testid="emptyState"]'
SEL_LOGADO = '[data-testid="SideNav_AccountSwitcher_Button"], a[data-testid="AppTabBar_Home_Link"]'
SEL_DESLOGADO = '[data-testid="loginButton"], a[href="/login"], [data-testid="login"]'
SEL_RETRY = ('button:has-text("Retry"), button:has-text("Tentar novamente"), '
             '[role="button"]:has-text("Retry"), [role="button"]:has-text("Tentar novamente")')


# ================================================================
#  JS DE FALLBACK (DOM) — adaptado do coleta_redesV2.7
# ================================================================
JS_EXTRAIR_ARTICLES = r"""
() => {
  const num = s => s ? parseInt(String(s).replace(/[.,\s]/g, ''), 10) || 0 : 0;
  const aria = (label, re) => { const m = label.match(re); return m ? num(m[1]) : 0; };
  const out = [];
  for (const art of document.querySelectorAll('article[data-testid="tweet"]')) {
    const timeEl = art.querySelector('time');
    const link = timeEl ? timeEl.closest('a[href*="/status/"]') : null;
    if (!link) continue;                       // anúncios não têm <time> com link
    const href = link.getAttribute('href');
    const m = href.match(/^\/([^/]+)\/status\/(\d+)/);
    if (!m) continue;

    let displayName = '';
    const unb = art.querySelector('[data-testid="User-Name"]');
    if (unb) {
      const a = unb.querySelector('a[href^="/"]');
      const sp = a ? a.querySelector('span span') : null;
      displayName = sp ? sp.innerText.trim() : (a ? a.innerText.trim() : '');
    }
    const textEl = art.querySelector('[data-testid="tweetText"]');
    const group = art.querySelector('[role="group"][aria-label]');
    const label = group ? group.getAttribute('aria-label') : '';

    let img = '';
    const video = art.querySelector('video');
    if (video && video.getAttribute('poster')) img = video.getAttribute('poster');
    else {
      const ph = art.querySelector('[data-testid="tweetPhoto"] img');
      if (ph && ph.src) img = ph.src;
    }
    const social = art.querySelector('[data-testid="socialContext"]');
    out.push({
      id: m[2],
      username: m[1],
      displayName,
      texto: textEl ? textEl.innerText.trim() : '',
      datetime: timeEl.getAttribute('datetime'),
      replies:  aria(label, /([\d.,]+)\s+(?:repl|respost)/i),
      reposts:  aria(label, /([\d.,]+)\s+(?:repost|retweet)/i),
      likes:    aria(label, /([\d.,]+)\s+(?:like|curtida)/i),
      views:    aria(label, /([\d.,]+)\s+(?:view|visualiza)/i),
      temVideo: !!art.querySelector('[data-testid="videoPlayer"], video'),
      imagem_url: img,
      socialContext: social ? social.innerText.trim() : '',
    });
  }
  return out;
}
"""


def _iso_para_local(dt_utc):
    """datetime aware (UTC) → datetime local naive."""
    return dt_utc.astimezone().replace(tzinfo=None)


class Twitter(Plataforma):
    nome = "twitter"
    canal = "Twitter"
    url_home = "https://x.com/home"

    def __init__(self, cfg_global, cfg):
        super().__init__(cfg_global, cfg)
        self._respostas = []      # Response objects do SearchTimeline pendentes
        self._buffer = []         # posts já parseados antes da rolagem
        self._cache_seguidores = {}  # username -> int (cache para evitar hovers repetidos)
        self._status_429 = False
        self._debug_salvo = set()

    # ────────────────────────────────────────────────────────────
    #  LISTENER GRAPHQL
    # ────────────────────────────────────────────────────────────
    def preparar(self, page):
        page.on("response", self._on_response)

    def _on_response(self, response):
        try:
            if "/SearchTimeline" not in response.url:
                return
            if response.status == 429:
                self._status_429 = True
                wrn("  [X] SearchTimeline respondeu 429 (rate limit).")
                return
            if response.status == 200:
                self._respostas.append(response)
        except Exception:
            pass

    # ────────────────────────────────────────────────────────────
    #  LOGIN
    # ────────────────────────────────────────────────────────────
    def url_login(self):
        return "https://x.com/i/flow/login"

    @staticmethod
    def _url_de_login(url):
        u = (url or "").lower().rstrip("/")
        return ("/login" in u or "/i/flow/" in u or "/logout" in u
                or u in ("https://x.com", "https://twitter.com"))

    def esta_logado(self, page) -> bool:
        try:
            page.goto(self.url_home, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            wrn(f"  [X] Falha ao abrir home: {e}")
        fim = time.time() + 20
        while time.time() < fim:
            if self._url_de_login(page.url):
                return False
            if page.query_selector(SEL_LOGADO):
                return True
            if page.query_selector(SEL_DESLOGADO):
                return False
            time.sleep(1)
        return False

    def _checar_sessao(self, page):
        if self._url_de_login(page.url) or page.query_selector(SEL_DESLOGADO):
            raise SessaoExpirada(f"X redirecionou para login ({page.url})")

    # ────────────────────────────────────────────────────────────
    #  QUERY
    # ────────────────────────────────────────────────────────────
    def montar_queries(self, busca, dt_ini, dt_fim):
        since = int(dt_ini.timestamp())
        until = int((dt_fim + timedelta(minutes=1)).timestamp())  # fim inclusivo (minuto inteiro)

        # Filtro de idioma (padrão 'pt' para português, configurável via FILTRO_IDIOMA ou 'LANG')
        idioma = self.opt("FILTRO_IDIOMA", self.opt("LANG", "pt"))
        filtro_lang = f"lang:{idioma}" if idioma else ""

        extras = " ".join(filter(None, [
            self.opt("FILTROS_EXTRAS", "").strip(),
            filtro_lang,
            "" if self.opt("INCLUIR_RESPOSTAS", True) else "-filter:replies",
            f"since_time:{since} until_time:{until}",
        ]))

        # Booleana específica para o X tem prioridade (ajuste manual em buscas.json)
        manual = busca.get("booleana_x")
        if manual:
            return [f"({manual}) {extras}"]

        return para_x_lista(
            busca["booleana"],
            modo_curinga=self.opt("MODO_CURINGA", "expandir"),
            filtros_extras=extras,
            limite=self.opt("TAMANHO_MAX_QUERY", 0),
        )

    # ────────────────────────────────────────────────────────────
    #  BUSCA
    # ────────────────────────────────────────────────────────────
    def buscar(self, page, busca, dt_ini, dt_fim):
        nome = busca["nome"]
        try:
            queries = self.montar_queries(busca, dt_ini, dt_fim)
        except ErroBooleana as e:
            er(f"  [X] Booleana inválida em '{nome}': {e}  →  adicione 'booleana_x' manual em buscas.json")
            return []

        if len(queries) > 1:
            log(f"  [X] Booleana longa → dividida em {len(queries)} sub-buscas.")

        posts = {}
        for i, q in enumerate(queries, 1):
            dbg(f"  [X] Query {i}/{len(queries)} ({len(q)} chars): {q}")
            res = self._executar_query(page, q, nome, dt_ini, dt_fim)
            if res is None:
                wrn(f"  [X] Falha na sub-busca {i}/{len(queries)} de '{nome}'. Pulando restante da booleana.")
                return None
            for p in res:
                posts.setdefault(p["id"], p)
            if i < len(queries):
                pausa(*self.opt("PAUSA_ENTRE_SUBBUSCAS_SEG", [4, 9]))
        return list(posts.values())

    def _executar_query(self, page, query, nome, dt_ini, dt_fim):
        url = f"https://x.com/search?q={quote(query)}&src=typed_query&f=live"
        tentativas_erro = self.opt("TENTATIVAS_ERRO", 3)

        for tentativa in range(1, tentativas_erro + 1):
            self._respostas.clear()
            self._buffer = []
            self._status_429 = False
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                status = self._aguardar_resultado(page)
            except SessaoExpirada:
                raise
            except Exception as e:
                dbg(f"  [X] Exceção ao abrir busca: {e}")
                status = "ERRO"

            dbg(f"  [X] Status da busca: {status}")
            if status == "VAZIO":
                log(f"  [X] Nenhum resultado para '{nome}'.")
                return []
            if status == "OK":
                try:
                    return self._rolar_e_coletar(page, nome, dt_ini)
                except SessaoExpirada:
                    raise
                except Exception as e:
                    er(f"  [X] Erro ao rolar e coletar: {e}")
                    status = "ERRO"

            # ERRO / TIMEOUT / 429 → aguarda tempo reduzido (15s padrão)
            espera = self.opt("PAUSA_RATE_LIMIT_SEG", 15)
            wrn(f"  [X] Busca falhou ({status}{', 429' if self._status_429 else ''}) "
                f"— tentativa {tentativa}/{tentativas_erro}. Aguardando {espera}s...")
            if self.opt("MODO_DEBUG", False):
                try:
                    salvar_debug(f"x_falha_{datetime.now():%H%M%S}.html", page.content())
                except Exception:
                    pass
            time.sleep(espera)

        er(f"  [X] Desistindo de '{nome}' após {tentativas_erro} tentativas.")
        return None

    def _aguardar_resultado(self, page):
        fim = time.time() + self.opt("TIMEOUT_CARREGAMENTO_SEG", 25)
        while time.time() < fim:
            self._checar_sessao(page)
            if page.query_selector(SEL_TWEET):
                return "OK"
            if page.query_selector(SEL_VAZIO):
                return "VAZIO"
            if page.query_selector(SEL_RETRY):
                return "ERRO"
            page.wait_for_timeout(1000)
        # Timeline respondeu mas sem tweets (às vezes o emptyState demora)
        if self._respostas and not self._status_429:
            self._buffer = self._drenar_graphql()
            return "OK" if self._buffer else "VAZIO"
        return "TIMEOUT"

    def _rolar_e_coletar(self, page, nome, dt_ini):
        graph, dom = {}, {}
        for p in self._buffer:
            graph.setdefault(p["id"], p)
        self._buffer = []
        sem_novidade = 0
        max_scrolls = self.opt("MAX_SCROLLS", 25)
        limite_sem_novidade = self.opt("SCROLLS_SEM_NOVIDADE", 3)

        for i in range(max_scrolls + 1):
            page.wait_for_timeout(random.randint(1200, 2200))
            antes = len(graph) + len(dom)

            for p in self._drenar_graphql(nome):
                graph.setdefault(p["id"], p)
            try:
                for b in page.evaluate(JS_EXTRAIR_ARTICLES):
                    if b["id"] not in dom:
                        dom[b["id"]] = self._post_de_dom(b)
            except Exception as e:
                dbg(f"  [X] Falha na leitura do DOM: {e}")

            novos = len(graph) + len(dom) - antes
            dbg(f"  [X] Scroll {i}/{max_scrolls}: +{novos} (graphql={len(graph)} dom={len(dom)})")

            # Para se os tweets já ficaram mais antigos que o início do período
            todos = list(graph.values()) or list(dom.values())
            datas = [p["_dt"] for p in todos if p.get("_dt")]
            if datas and min(datas) < dt_ini - timedelta(minutes=5):
                dbg("  [X] Chegou em tweets anteriores ao período — parando rolagem.")
                break

            sem_novidade = sem_novidade + 1 if novos == 0 else 0
            if sem_novidade >= limite_sem_novidade:
                break
            if page.query_selector(SEL_RETRY):
                wrn("  [X] Timeline exibiu erro durante a rolagem (possível rate limit).")
                break
            if i < max_scrolls:
                self._rolar(page)

        # GraphQL tem prioridade; DOM completa o que faltou
        final = dict(dom)
        final.update(graph)
        if not graph and dom:
            wrn("  [X] GraphQL não capturado diretamente — preenchendo seguidores via HoverCard no DOM...")

        # Completa seguidores que estejam faltando (ex.: posts que vieram do DOM ou GraphQL sem user)
        self._preencher_seguidores_hover(page, final)

        log(f"  [X] '{nome}': {len(final)} tweet(s) lidos (graphql={len(graph)}, dom={len(dom)}).")
        return [self._finalizar(p) for p in final.values()
                if not (p["is_repost"] and self.opt("EXCLUIR_REPOSTS", True))]

    def _preencher_seguidores_hover(self, page, posts_dict):
        """
        Para posts que ainda estejam com seguidores == 0, dá hover no link do autor
        para acionar o [data-testid="HoverCard"] e extrair os seguidores reais.
        """
        sem_seg = [p for p in posts_dict.values() if not p.get("seguidores") and p.get("autor") != "N/D"]
        if not sem_seg:
            return

        dbg(f"  [X] Buscando seguidores via HoverCard para {len(sem_seg)} post(s)...")
        for p in sem_seg:
            user = p["autor"].lstrip("@")
            if user in self._cache_seguidores:
                p["seguidores"] = self._cache_seguidores[user]
                continue

            try:
                # Localiza o link do autor no tweet ou qualquer elemento do autor na tela
                # No X: a[href="/username"][role="link"] dentro do article
                link_sel = f'article a[href="/{user}"][role="link"]'
                el = page.query_selector(link_sel)
                if not el:
                    # tenta no User-Name
                    el = page.query_selector(f'[data-testid="User-Name"] a[href="/{user}"]')

                if el:
                    el.hover()
                    # Aguarda surgir o HoverCard
                    page.wait_for_selector('[data-testid="HoverCard"]', timeout=2500)
                    texto_seguidores = page.evaluate("""
                        () => {
                            const card = document.querySelector('[data-testid="HoverCard"]');
                            if (!card) return null;
                            // O link de seguidores tem href que termina em /followers ou /verified_followers
                            const link = card.querySelector('a[href$="/followers"], a[href$="/verified_followers"]');
                            if (!link) return null;
                            // Pega o span interno com o número (ex: 234.4K)
                            const spans = Array.from(link.querySelectorAll('span'));
                            for (const s of spans) {
                                const t = s.innerText.trim();
                                if (/^[\\d.,]+[KkMmBb]?$/.test(t)) return t;
                            }
                            return link.innerText.trim();
                        }
                    """)
                    if texto_seguidores:
                        seg = converter_valor(texto_seguidores)
                        if seg > 0:
                            p["seguidores"] = seg
                            self._cache_seguidores[user] = seg
                            dbg(f"  [X] Hover @{user} -> {seg:,} seguidores ({texto_seguidores})")
                    # Tira o mouse de cima para fechar o card
                    page.mouse.move(10, 10)
                    time.sleep(0.3)
            except Exception as e:
                dbg(f"  [X] Falha no hover de @{user}: {e}")

    @staticmethod
    def _rolar(page):
        try:
            page.mouse.move(random.randint(500, 900), random.randint(300, 600))
            for _ in range(random.randint(2, 4)):
                page.mouse.wheel(0, random.randint(500, 1000))
                time.sleep(random.uniform(0.15, 0.4))
        except Exception:
            page.evaluate("window.scrollBy(0, window.innerHeight * 2)")

    def _finalizar(self, p):
        p.pop("_dt", None)
        p["interacoes"] = self.calcular_interacoes(p)
        return p

    # ────────────────────────────────────────────────────────────
    #  PARSE GRAPHQL
    # ────────────────────────────────────────────────────────────
    def _drenar_graphql(self, nome="debug"):
        posts = []
        pendentes, self._respostas = self._respostas, []
        for resp in pendentes:
            try:
                dados = resp.json()
            except Exception as e:
                dbg(f"  [X] Não foi possível ler JSON do SearchTimeline: {e}")
                continue
            if self.opt("MODO_DEBUG", False) and nome not in self._debug_salvo:
                self._debug_salvo.add(nome)
                salvar_debug(f"x_graphql_{nome}.json", dados)
            for r in self._achar_tweet_results(dados):
                p = self._post_de_graphql(r)
                if p:
                    posts.append(p)
        return posts

    @classmethod
    def _achar_tweet_results(cls, no, out=None):
        """Percorre o JSON e coleta cada `tweet_results.result` (sem descer em citações)."""
        if out is None:
            out = []
        if isinstance(no, dict):
            for k, v in no.items():
                if k == "tweet_results" and isinstance(v, dict):
                    if v.get("result"):
                        out.append(v["result"])
                else:
                    cls._achar_tweet_results(v, out)
        elif isinstance(no, list):
            for v in no:
                cls._achar_tweet_results(v, out)
        return out

    def _post_de_graphql(self, r):
        if r.get("__typename") == "TweetWithVisibilityResults":
            r = r.get("tweet") or {}
        leg = r.get("legacy")
        if not leg or not r.get("rest_id"):
            return None

        u = (r.get("core") or {}).get("user_results", {}).get("result", {}) or {}
        ucore, uleg = u.get("core") or {}, u.get("legacy") or {}
        rc = u.get("relationship_counts") or {}
        username = ucore.get("screen_name") or uleg.get("screen_name") or ""
        nome_exib = ucore.get("name") or uleg.get("name") or ""

        # O X migrou followers_count de legacy para relationship_counts.followers
        seguidores = (
            rc.get("followers")
            if rc.get("followers") is not None
            else (uleg.get("followers_count") or u.get("followers_count") or 0)
        )

        texto = leg.get("full_text", "")
        nota = ((r.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result") or {}
        if nota.get("text"):
            texto = nota["text"]

        dt_local, data_pub, data_iso = None, "N/D", None
        try:
            dt_utc = datetime.strptime(leg["created_at"], "%a %b %d %H:%M:%S %z %Y")
            dt_local = _iso_para_local(dt_utc)
            data_pub = dt_local.strftime("%d/%m/%Y %H:%M")
            data_iso = dt_utc.isoformat()
        except Exception:
            pass

        midias = (leg.get("extended_entities") or {}).get("media") or (leg.get("entities") or {}).get("media") or []
        imagem = midias[0].get("media_url_https") if midias else None
        tem_video = any(m.get("type") in ("video", "animated_gif") for m in midias)
        if not imagem:
            card = (r.get("card") or {}).get("legacy", {}).get("binding_values", [])
            for bv in card:
                img = (bv.get("value") or {}).get("image_value", {}).get("url")
                if img and "thumbnail" in bv.get("key", ""):
                    imagem = img
                    break

        return novo_post(
            plataforma="twitter", canal=self.canal, fonte="graphql",
            id=r["rest_id"],
            url=f"https://x.com/{username}/status/{r['rest_id']}",
            autor=f"@{username}" if username else "N/D",
            autor_nome=nome_exib or "N/D",
            seguidores=int(seguidores or 0),
            data_pub=data_pub, data_iso=data_iso, _dt=dt_local,
            curtidas=int(leg.get("favorite_count") or 0),
            comentarios=int(leg.get("reply_count") or 0),
            compartilhamentos=int(leg.get("retweet_count") or 0) + int(leg.get("quote_count") or 0),
            views=converter_valor((r.get("views") or {}).get("count")),
            conteudo=re.sub(r"\s+", " ", texto).strip() or "N/D",
            imagem_url=imagem, tem_video=tem_video,
            is_resposta=bool(leg.get("in_reply_to_status_id_str")),
            is_repost=bool(leg.get("retweeted_status_result")),
        )

    def _post_de_dom(self, b):
        dt_local, data_pub = None, "N/D"
        if b.get("datetime"):
            try:
                dt_local = _iso_para_local(datetime.fromisoformat(b["datetime"].replace("Z", "+00:00")))
                data_pub = dt_local.strftime("%d/%m/%Y %H:%M")
            except Exception:
                pass
        social = (b.get("socialContext") or "").lower()
        return novo_post(
            plataforma="twitter", canal=self.canal, fonte="dom",
            id=b["id"],
            url=f"https://x.com/{b['username']}/status/{b['id']}",
            autor=f"@{b['username']}", autor_nome=b.get("displayName") or "N/D",
            seguidores=0,
            data_pub=data_pub, data_iso=b.get("datetime"), _dt=dt_local,
            curtidas=b.get("likes", 0), comentarios=b.get("replies", 0),
            compartilhamentos=b.get("reposts", 0), views=b.get("views", 0),
            conteudo=re.sub(r"\s+", " ", b.get("texto") or "").strip() or "N/D",
            imagem_url=b.get("imagem_url") or None, tem_video=b.get("temVideo", False),
            is_repost=("repost" in social or "retweet" in social),
        )
