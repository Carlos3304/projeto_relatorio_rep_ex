"""
🔍 DOM Inspector — ferramenta auxiliar para desenvolver robôs Playwright com IA.

Abre o Chrome (perfil persistente), injeta um listener JS em todas as páginas/iframes
e captura elementos via  Ctrl+Clique  (sem disparar a ação do site).
Cada captura vira uma linha em `capturas_dom.jsonl`, que a IA lê sob demanda.

Uso:
    venv\\Scripts\\python.exe projeto_relatorio_rep\\dom_inspector.py [URL] [OPÇÕES]

Opções:
    --profile PATH     Perfil do Chrome (padrão: perfil_monitoramento_exemplo — mesmo do classificador)
    --output FILE      Arquivo de saída (padrão: capturas_dom.jsonl ao lado deste script)
    --ancestors N      Níveis de ancestrais a capturar (padrão: 5)
    --no-siblings      Não incluir info sobre elementos irmãos
    --clear            Limpar arquivo de capturas anterior
    --hotkey KEY       Tecla modificadora: ctrl | alt | shift | meta (padrão: ctrl)
    --max-html N       Limite de caracteres do outerHTML do elemento (padrão: 2000)

Dicas:
    - Ctrl+Clique         → captura o elemento sem acionar o site.
    - Ctrl+Shift+Clique   → idem, registrado como "hover_mode" (útil p/ menus que só existem no hover).
    - Estados: capture o mesmo elemento ANTES e DEPOIS de um clique normal.
    - Feche o navegador ou aperte Ctrl+C no terminal para encerrar.
"""

import os
import sys
import json
import argparse
from datetime import datetime

from playwright.sync_api import sync_playwright, Error as PlaywrightError

# Garante emojis/acentos no console do Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PASTA_SCRIPT = os.path.dirname(os.path.abspath(__file__))
URL_PADRAO = os.getenv("URL_PLATAFORMA", "https://app.social-monitor.exemplo.com/#/")
PERFIL_PADRAO = (os.path.join(os.environ.get("LOCALAPPDATA", r"C:\AppData"), "PerfilMonitoramentoExemplo")
                 if sys.platform.startswith("win") else os.path.expanduser("~/.config/perfil_monitoramento_exemplo"))
SAIDA_PADRAO = os.path.join(PASTA_SCRIPT, "capturas_dom.jsonl")
BINDING_NAME = "__domInspectorCapture"

# ============================================================
# JAVASCRIPT INJETADO (roda em toda página e iframe)
# ============================================================
JS_INSPECTOR = r"""
(() => {
  if (window.__domInspectorInstalled) return;
  window.__domInspectorInstalled = true;

  const CFG = __CFG__;
  const HOTKEY = CFG.hotkey + 'Key';
  const TEST_ATTRS = ['data-testid', 'data-test', 'data-test-id', 'data-cy', 'data-qa'];
  const ATTR_CANDIDATES = TEST_ATTRS.concat(['data-id', 'name', 'aria-label', 'role', 'title',
                                             'placeholder', 'formcontrolname', 'type']);

  // ---------- utilitários ----------
  const trunc = (s, n) => (typeof s === 'string' && s.length > n) ? s.slice(0, n) + '[...]' : s;
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const textOf = el => clean(el.innerText !== undefined ? el.innerText : el.textContent);
  const classesOf = el => clean(el.getAttribute && el.getAttribute('class')).split(' ').filter(Boolean);
  const getAttrs = el => {
    const o = {};
    if (el.attributes) for (const a of el.attributes) o[a.name] = trunc(a.value, 300);
    return o;
  };
  const openingTag = el => {
    const h = el.outerHTML || '';
    const i = h.indexOf('>');
    return trunc(i >= 0 ? h.slice(0, i + 1) : h, 600);
  };
  const esc = s => (window.CSS && CSS.escape) ? CSS.escape(s) : String(s).replace(/([^\w-])/g, '\\$1');
  const quote = s => '"' + String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"';

  // Classes/IDs gerados dinamicamente (Angular state, CSS-in-JS, hashes) não servem de seletor
  const unstableClass = c => !c
    || /^ng-/.test(c)
    || /^(css|sc|jsx|svelte|emotion)-/.test(c)
    || /\d{3,}/.test(c)
    || (/[A-Za-z0-9]{8,}/.test(c) && /\d/.test(c) && /[A-Z]/.test(c));
  const stableClasses = el => classesOf(el).filter(c => !unstableClass(c));
  const stableId = id => !!id && !/\d{2,}/.test(id) && !/-\d+$/.test(id);

  const rootOf = el => (el.getRootNode ? el.getRootNode() : document);
  const count = (root, sel) => { try { return root.querySelectorAll(sel).length; } catch (e) { return -1; } };
  const parentOf = el => el.parentElement || ((el.getRootNode && el.getRootNode().host) || null);

  const simpleSel = el => {
    const tag = el.tagName.toLowerCase();
    if (stableId(el.id)) return '#' + esc(el.id);
    for (const a of TEST_ATTRS) {
      const v = el.getAttribute(a);
      if (v) return `${tag}[${a}=${quote(v)}]`;
    }
    return tag + stableClasses(el).slice(0, 3).map(c => '.' + esc(c)).join('');
  };

  const cssPath = (el, maxDepth) => {
    const parts = [];
    let cur = el, depth = 0;
    while (cur && cur.nodeType === 1 && depth < maxDepth) {
      let part = simpleSel(cur);
      if (part.startsWith('#')) { parts.unshift(part); break; }
      const parent = cur.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter(c => c.tagName === cur.tagName);
        if (same.length > 1) part += `:nth-of-type(${same.indexOf(cur) + 1})`;
      }
      parts.unshift(part);
      cur = parent; depth++;
    }
    return parts.join(' > ');
  };

  // ---------- seletores sugeridos ----------
  const suggest = el => {
    const root = rootOf(el);
    const tag = el.tagName.toLowerCase();
    const out = [], seen = new Set();
    const add = (selector, engine = 'css') => {
      if (!selector || seen.has(selector)) return;
      seen.add(selector);
      out.push({ selector, engine, matches: engine === 'css' ? count(root, selector) : null });
    };

    if (stableId(el.id)) add('#' + esc(el.id));
    for (const a of ATTR_CANDIDATES) {
      const v = el.getAttribute(a);
      if (v && v.length < 120) add(`${tag}[${a}=${quote(v)}]`);
    }
    const cls = stableClasses(el);
    if (cls.length) add(tag + '.' + cls.map(esc).join('.'));

    // Ancorado no ancestral mais próximo com id/testid
    let anc = el.parentElement, hops = 0;
    while (anc && hops < 12) {
      const t = TEST_ATTRS.map(a => [a, anc.getAttribute(a)]).find(([, v]) => v);
      if (stableId(anc.id) || t) {
        const a = stableId(anc.id) ? '#' + esc(anc.id) : `[${t[0]}=${quote(t[1])}]`;
        add(a + ' ' + simpleSel(el));
        break;
      }
      anc = anc.parentElement; hops++;
    }

    add(cssPath(el, 6));

    const txt = textOf(el);
    if (txt && txt.length <= 60) {
      add(`${tag}:has-text(${quote(txt)})`, 'playwright');
      add(`text=${quote(txt)}`, 'playwright');
    }
    const implicitRole = { button: 'button', a: 'link', select: 'combobox', textarea: 'textbox' }[tag]
      || (tag === 'input' ? (el.type === 'checkbox' ? 'checkbox' : el.type === 'radio' ? 'radio' : 'textbox') : null);
    const role = el.getAttribute('role') || implicitRole;
    const name = el.getAttribute('aria-label') || (txt && txt.length <= 60 ? txt : null);
    if (role && name) add(`get_by_role(${quote(role)}, name=${quote(name)})`, 'playwright-api');
    return out;
  };

  // ---------- contexto estrutural ----------
  const ancestors = (el, n) => {
    const out = [];
    let cur = parentOf(el), lvl = 1;
    while (cur && cur.nodeType === 1 && lvl <= n) {
      const parent = cur.parentElement;
      const cls = cur.getAttribute('class');
      const similar = parent
        ? Array.from(parent.children).filter(c => c.tagName === cur.tagName && c.getAttribute('class') === cls).length
        : null;
      out.push({
        level: lvl,
        tag: cur.tagName.toLowerCase(),
        openingTag: openingTag(cur),
        id: cur.id || null,
        classes: classesOf(cur),
        attributes: getAttrs(cur),
        similar_siblings: similar,   // >1 sugere item de lista (ex.: card de publicação)
        selector: simpleSel(cur),
      });
      cur = parentOf(cur); lvl++;
    }
    return out;
  };

  // Primeiro ancestral que se repete (ex.: card de publicação) — até 15 níveis acima
  const repeatedContainer = el => {
    let cur = el, lvl = 0;
    while (cur && cur.nodeType === 1 && lvl <= 15) {
      const parent = cur.parentElement;
      if (parent) {
        const cls = cur.getAttribute('class');
        const similar = Array.from(parent.children)
          .filter(c => c.tagName === cur.tagName && c.getAttribute('class') === cls).length;
        if (similar >= 3) {
          const sel = simpleSel(cur);
          return {
            level: lvl,
            selector: sel,
            matches_in_document: count(rootOf(cur), sel),
            similar_siblings: similar,
            openingTag: openingTag(cur),
            parent_selector: simpleSel(parent),
          };
        }
      }
      cur = parent; lvl++;
    }
    return null;
  };

  const siblings = el => {
    const p = el.parentElement;
    if (!p) return null;
    const kids = Array.from(p.children);
    return {
      parent_children_count: kids.length,
      index_in_parent: kids.indexOf(el) + 1,
      parent_children_sample: kids.slice(0, CFG.siblingSample)
        .map(k => trunc(textOf(k) || `<${k.tagName.toLowerCase()}>`, 50)),
    };
  };

  // Elementos interativos/relevantes dentro do elemento clicado (útil ao clicar num card)
  const interactiveDescendants = el => {
    const sel = 'button, a[href], input, select, textarea, [role], [tabindex], ' +
                TEST_ATTRS.map(a => `[${a}]`).join(', ');
    return Array.from(el.querySelectorAll(sel)).slice(0, 25).map(d => ({
      tag: d.tagName.toLowerCase(),
      selector: simpleSel(d),
      openingTag: openingTag(d),
      text: trunc(textOf(d), 80),
    }));
  };

  const build = (el, ev) => {
    const root = rootOf(el);
    const r = el.getBoundingClientRect();
    const html = el.outerHTML || '';
    const inShadow = (typeof ShadowRoot !== 'undefined') && root instanceof ShadowRoot;
    
    // Captura o HTML da página na primeira vez
    let fullPageHtml = null;
    if (!window.__domInspectorHtmlSaved) {
        window.__domInspectorHtmlSaved = true;
        fullPageHtml = document.documentElement.outerHTML;
    }

    return {
      element: {
        tag: el.tagName.toLowerCase(),
        outerHTML: trunc(html, CFG.maxHtml),
        outerHTML_length: html.length,
        innerText: trunc(textOf(el), 500),
        attributes: getAttrs(el),
        rect: { x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height) },
      },
      ancestors: ancestors(el, CFG.ancestors),
      repeated_container: repeatedContainer(el),
      siblings: CFG.siblings ? siblings(el) : null,
      interactive_descendants: interactiveDescendants(el),
      suggested_selectors: suggest(el),
      context: {
        in_iframe: window !== window.top,
        in_shadow_dom: inShadow,
        shadow_host: inShadow ? openingTag(root.host) : null,
        hover_mode: !!ev.shiftKey && CFG.hotkey !== 'shift',
        modifiers: { ctrl: ev.ctrlKey, shift: ev.shiftKey, alt: ev.altKey, meta: ev.metaKey },
      },
      page_url: location.href,
      page_title: document.title,
      viewport: { width: window.innerWidth, height: window.innerHeight },
      full_page_html: fullPageHtml
    };
  };

  // ---------- feedback visual ----------
  const flash = (el, idx) => {
    if (!document.body) return;
    const r = el.getBoundingClientRect();
    const box = document.createElement('div');
    box.setAttribute('data-dom-inspector-overlay', '');
    Object.assign(box.style, {
      position: 'fixed', left: r.left + 'px', top: r.top + 'px',
      width: r.width + 'px', height: r.height + 'px',
      outline: '3px solid #ff2d55', background: 'rgba(255,45,85,0.12)',
      zIndex: 2147483647, pointerEvents: 'none', transition: 'opacity .4s',
    });
    const tag = document.createElement('div');
    tag.textContent = '#' + idx;
    Object.assign(tag.style, {
      position: 'absolute', top: '-22px', left: '0', background: '#ff2d55', color: '#fff',
      font: 'bold 12px sans-serif', padding: '2px 6px', borderRadius: '3px',
    });
    box.appendChild(tag);
    document.body.appendChild(box);
    setTimeout(() => { box.style.opacity = '0'; }, 900);
    setTimeout(() => box.remove(), 1400);
  };

  // ---------- listeners ----------
  const block = ev => {
    if (!ev[HOTKEY]) return false;
    ev.preventDefault();
    ev.stopPropagation();
    ev.stopImmediatePropagation();
    return true;
  };
  // Bloqueia todo o ciclo do clique para o site não reagir (menus não abrem/fecham)
  ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'dblclick', 'auxclick']
    .forEach(t => window.addEventListener(t, block, true));

  window.addEventListener('click', async ev => {
    if (!block(ev)) return;
    const path = ev.composedPath ? ev.composedPath() : [];
    const el = path.find(n => n && n.nodeType === 1) || ev.target;
    if (!el || (el.closest && el.closest('[data-dom-inspector-overlay]'))) return;
    try {
      const data = build(el, ev);
      const fn = window[CFG.binding];
      if (typeof fn !== 'function') { console.warn('[dom_inspector] binding indisponível'); return; }
      const idx = await fn(data);
      flash(el, idx);
    } catch (e) {
      console.error('[dom_inspector] erro ao capturar:', e);
    }
  }, true);

  console.log('[dom_inspector] ativo — ' + CFG.hotkey + '+Clique para capturar.');
})();
"""


# ============================================================
# PYTHON
# ============================================================
def parse_args():
    ap = argparse.ArgumentParser(description="DOM Inspector para desenvolvimento Playwright + IA")
    ap.add_argument("url", nargs="?", default=URL_PADRAO, help="URL inicial")
    ap.add_argument("--profile", default=PERFIL_PADRAO, help="Caminho do perfil do Chrome")
    ap.add_argument("--output", default=SAIDA_PADRAO, help="Arquivo JSONL de saída")
    ap.add_argument("--ancestors", type=int, default=5, help="Níveis de ancestrais (padrão: 5)")
    ap.add_argument("--no-siblings", action="store_true", help="Não incluir info de irmãos")
    ap.add_argument("--clear", action="store_true", help="Limpar capturas anteriores")
    ap.add_argument("--hotkey", default="ctrl", choices=["ctrl", "alt", "shift", "meta"],
                    help="Tecla modificadora (padrão: ctrl)")
    ap.add_argument("--max-html", type=int, default=2000, help="Limite do outerHTML (padrão: 2000)")
    return ap.parse_args()


def contar_capturas_existentes(caminho: str) -> int:
    if not os.path.exists(caminho):
        return 0
    with open(caminho, "r", encoding="utf-8") as f:
        return sum(1 for linha in f if linha.strip())


def main():
    args = parse_args()

    if args.clear and os.path.exists(args.output):
        os.remove(args.output)
        print(f"  🧹 Capturas anteriores removidas: {args.output}")

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    os.makedirs(args.profile, exist_ok=True)

    estado = {"indice": contar_capturas_existentes(args.output)}

    cfg = {
        "hotkey": args.hotkey,
        "ancestors": args.ancestors,
        "siblings": not args.no_siblings,
        "siblingSample": 15,
        "maxHtml": args.max_html,
        "binding": BINDING_NAME,
    }
    js = JS_INSPECTOR.replace("__CFG__", json.dumps(cfg))

    def ao_capturar(source, data):
        estado["indice"] += 1
        idx = estado["indice"]
        frame = source.get("frame")
        
        full_page_html = data.pop("full_page_html", None)
        if full_page_html:
            html_filename = os.path.join(PASTA_SCRIPT, f"page_snapshot_{idx}.html")
            with open(html_filename, "w", encoding="utf-8") as f:
                f.write(full_page_html)
            print(f"  💾 HTML da página salvo em: {html_filename}")

        registro = {
            "capture_index": idx,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            **data,
        }
        try:
            registro["context"]["frame"] = {
                "is_main_frame": frame == frame.page.main_frame if frame else None,
                "frame_url": frame.url if frame else None,
                "frame_name": frame.name if frame else None,
            }
        except Exception:
            pass

        with open(args.output, "a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")

        el = data.get("element", {})
        abertura = el.get("outerHTML", "")
        fim_tag = abertura.find(">")
        abertura = abertura[: fim_tag + 1] if fim_tag >= 0 else abertura
        if len(abertura) > 120:
            abertura = abertura[:117] + "..."
        melhor = next((s for s in data.get("suggested_selectors", [])
                       if s.get("engine") == "css" and s.get("matches") == 1), None)
        extra = "  [hover]" if data.get("context", {}).get("hover_mode") else ""
        print(f"  ✅ Captura #{idx}: {abertura}{extra}")
        texto = (el.get("innerText") or "")[:70]
        if texto:
            print(f"     texto: {texto!r}")
        if melhor:
            print(f"     seletor único: {melhor['selector']}")
        return idx

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            args.profile,
            channel="chrome",
            headless=False,
            no_viewport=True,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context.expose_binding(BINDING_NAME, ao_capturar)
        context.add_init_script(js)  # vale p/ toda navegação, popup e iframe

        page = context.pages[0] if context.pages else context.new_page()
        page.goto(args.url, wait_until="domcontentloaded", timeout=60000)

        # Garante injeção em frames já carregados (o init script cobre os futuros)
        for fr in page.frames:
            try:
                fr.evaluate(js)
            except Exception:
                pass

        print("\n  🔍 Inspector ativo. "
              f"{args.hotkey.capitalize()}+Clique nos elementos para capturar "
              f"({args.hotkey.capitalize()}+Shift+Clique = modo hover).")
        print(f"  📁 Salvando em: {args.output}")
        print(f"  🔢 Capturas existentes: {estado['indice']}")
        print("  ⛔ Feche o navegador ou Ctrl+C aqui para encerrar.\n")

        try:
            while context.pages:
                # wait_for_timeout mantém o loop de eventos do Playwright girando (callbacks)
                context.pages[0].wait_for_timeout(300)
        except (KeyboardInterrupt, PlaywrightError):
            pass
        finally:
            try:
                context.close()
            except Exception:
                pass

    print(f"\n  👋 Inspector encerrado. Total de capturas no arquivo: {estado['indice']}")


if __name__ == "__main__":
    main()
