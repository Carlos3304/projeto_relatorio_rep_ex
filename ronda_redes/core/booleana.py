"""
Parser de consultas booleanas (AND / OR / NOT, "frases", curinga*, parênteses)
e conversores para a sintaxe de busca de cada rede social.

AST:
    ("TERM", "texto")        termo simples (pode terminar em *)
    ("PHRASE", "texto")      frase entre aspas
    ("AND", [nós])
    ("OR",  [nós])
    ("NOT", nó)
"""

import re

_TOKEN_RE = re.compile(r'"[^"]*"|\(|\)|[^\s()"]+')
OPERADORES = {"AND", "OR", "NOT"}


class ErroBooleana(ValueError):
    pass


# ================================================================
#  PARSER
# ================================================================
def tokenizar(texto):
    return _TOKEN_RE.findall(texto.replace("“", '"').replace("”", '"'))


class _Parser:
    def __init__(self, tokens):
        self.t = tokens
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def parse(self):
        if not self.t:
            raise ErroBooleana("booleana vazia")
        no = self.or_expr()
        if self.peek() is not None:
            raise ErroBooleana(f"token inesperado: {self.peek()!r}")
        return no

    def or_expr(self):
        filhos = [self.and_expr()]
        while self.peek() == "OR":
            self.take()
            filhos.append(self.and_expr())
        return filhos[0] if len(filhos) == 1 else ("OR", filhos)

    def and_expr(self):
        filhos = [self.unary()]
        while True:
            tok = self.peek()
            if tok == "AND":
                self.take()
                filhos.append(self.unary())
            elif tok is not None and tok not in ("OR", ")"):
                filhos.append(self.unary())  # AND implícito (inclui "A NOT B")
            else:
                break
        return filhos[0] if len(filhos) == 1 else ("AND", filhos)

    def unary(self):
        if self.peek() == "NOT":
            self.take()
            return ("NOT", self.unary())
        return self.primary()

    def primary(self):
        tok = self.take()
        if tok is None:
            raise ErroBooleana("fim inesperado da booleana")
        if tok == "(":
            no = self.or_expr()
            if self.take() != ")":
                raise ErroBooleana("parêntese não fechado")
            return no
        if tok == ")":
            raise ErroBooleana("parêntese ')' sem abertura")
        if tok.startswith('"'):
            return ("PHRASE", tok.strip('"'))
        return ("TERM", tok)


def parse(texto):
    return _Parser(tokenizar(texto)).parse()


# ================================================================
#  CURINGAS (X não suporta "*")
# ================================================================
def expandir_curinga(base):
    """
    Gera variações comuns em português para 'base*'
    (ex.: empresa* → empresa, empresas; produto* → produto, produtos, produtora, produtoras).
    """
    b = base.lower()
    var = [base]
    if b.endswith("ão"):
        var += [base[:-2] + "ões", base[:-2] + "ães", base + "s"]
    elif b.endswith(("r", "z", "s")):
        var += [base + "es", base + "a", base + "as"]
    elif b.endswith("o"):
        var += [base + "s", base[:-1] + "a", base[:-1] + "as"]
    elif b.endswith("l"):
        var += [base[:-1] + "is"]
    elif b.endswith("m"):
        var += [base[:-1] + "ns"]
    else:
        var += [base + "s"]
    return list(dict.fromkeys(var))


# ================================================================
#  RENDER → X / TWITTER
# ================================================================
def _x_termo_variantes(no, modo_curinga):
    tipo, val = no
    if tipo == "PHRASE":
        return [f'"{val}"']
    if val.endswith("*") and len(val) > 1:
        base = val.rstrip("*")
        if modo_curinga == "expandir":
            return expandir_curinga(base)
        return [base]
    return [val]


def _x_render(no, modo_curinga, dentro_de=None):
    tipo = no[0]
    if tipo in ("TERM", "PHRASE"):
        var = _x_termo_variantes(no, modo_curinga)
        if len(var) == 1:
            return var[0]
        s = " OR ".join(var)
        return s if dentro_de in (None, "OR") else f"({s})"

    if tipo == "OR":
        s = " OR ".join(_x_render(f, modo_curinga, "OR") for f in no[1])
        return s if dentro_de is None else f"({s})"

    if tipo == "AND":
        s = " ".join(_x_render(f, modo_curinga, "AND") for f in no[1])
        return f"({s})" if dentro_de == "OR" else s

    if tipo == "NOT":
        alvo = no[1]
        # NOT termo/frase  → -a [-a2 -a3 (variantes do curinga)]
        if alvo[0] in ("TERM", "PHRASE"):
            return " ".join("-" + v for v in _x_termo_variantes(alvo, modo_curinga))
        # NOT (a OR b OR c) → -a -b -c   (De Morgan; X não nega grupos de forma confiável)
        if alvo[0] == "OR" and all(f[0] in ("TERM", "PHRASE") for f in alvo[1]):
            return " ".join(
                "-" + v for f in alvo[1] for v in _x_termo_variantes(f, modo_curinga)
            )
        return "-(" + _x_render(alvo, modo_curinga, None) + ")"

    raise ErroBooleana(f"nó desconhecido: {tipo}")


def para_x(booleana, modo_curinga="expandir", filtros_extras=""):
    """
    Converte booleana padrão → query de busca do X.
    `filtros_extras` (ex.: '-filter:retweets lang:pt since_time:...') é anexado ao final.
    """
    return _x_query_de_ast(parse(booleana), modo_curinga, filtros_extras)


def _x_query_de_ast(ast, modo_curinga, filtros_extras=""):
    q = _x_render(ast, modo_curinga, None)
    if filtros_extras:
        if ast[0] == "OR" or (ast[0] in ("TERM", "PHRASE") and " OR " in q):
            q = f"({q})"
        q = f"{q} {filtros_extras.strip()}"
    return re.sub(r"\s+", " ", q).strip()


def _dividir_ast(ast, cabe):
    """Divide a AST em sub-buscas equivalentes (união) até cada uma caber no limite."""
    if cabe(ast):
        return [ast]
    tipo = ast[0]
    if tipo == "OR" and len(ast[1]) > 1:
        meio = len(ast[1]) // 2
        partes = [ast[1][:meio], ast[1][meio:]]
        out = []
        for p in partes:
            out += _dividir_ast(p[0] if len(p) == 1 else ("OR", p), cabe)
        return out
    if tipo == "AND":
        # (A OR B OR C) AND X  ≡  (A OR B) AND X  ∪  C AND X  → divide o maior OR
        candidatos = [(i, f) for i, f in enumerate(ast[1]) if f[0] == "OR" and len(f[1]) > 1]
        if not candidatos:
            return [ast]
        i, maior = max(candidatos, key=lambda c: len(str(c[1])))
        meio = len(maior[1]) // 2
        out = []
        for p in (maior[1][:meio], maior[1][meio:]):
            novo_filho = p[0] if len(p) == 1 else ("OR", p)
            filhos = list(ast[1])
            filhos[i] = novo_filho
            out += _dividir_ast(("AND", filhos), cabe)
        return out
    return [ast]


def para_x_lista(booleana, modo_curinga="expandir", filtros_extras="", limite=0):
    """
    Igual a `para_x`, mas se a query passar de `limite` caracteres divide em
    várias sub-queries cujos resultados somados equivalem à booleana original.
    """
    ast = parse(booleana)
    if not limite:
        return [_x_query_de_ast(ast, modo_curinga, filtros_extras)]
    cabe = lambda a: len(_x_query_de_ast(a, modo_curinga, filtros_extras)) <= limite
    return [_x_query_de_ast(a, modo_curinga, filtros_extras) for a in _dividir_ast(ast, cabe)]
