"""
Script: relatorio_clipping.py

Objetivo:
  Analista sênior de dados e inteligência de mercado.
  Recebe bases de dados de clipping (planilhas .xlsx/.csv ou texto colado),
  filtra publicações com base em critérios temáticos estruturados,
  calcula o engajamento e métricas matemáticas via pandas, e estrutura o relatório executivo oficial em Markdown.

Uso:
  python relatorio_clipping.py [caminho_arquivo] [--top N] [--lote N]
  
  Exemplos:
    python relatorio_clipping.py dados_clipping.xlsx
    python relatorio_clipping.py dados.csv --top 5
    python relatorio_clipping.py --colar
"""

import os
import sys
import re
import io
import json
import time
import unicodedata
import argparse
from datetime import datetime
from typing import Literal, Optional, List, Dict, Any

from dotenv import load_dotenv
import pandas as pd
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

# Garante saída UTF-8 no terminal Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ============================================================
# 1. CARREGAMENTO DE AMBIENTE E CONFIGURAÇÃO
# ============================================================
PASTA_ATUAL = os.path.dirname(os.path.abspath(__file__))
PASTA_RAIZ = os.path.dirname(PASTA_ATUAL)

# Tenta carregar .env da pasta atual ou da raiz
env_rep = os.path.join(PASTA_ATUAL, ".env")
env_raiz = os.path.join(PASTA_RAIZ, ".env")

if os.path.exists(env_rep):
    load_dotenv(env_rep, override=True)
elif os.path.exists(env_raiz):
    load_dotenv(env_raiz, override=True)
else:
    load_dotenv()

api_key = os.getenv("GEMINI_API_KEY", "XXXXX_GEMINI_API_KEY_XXXXX")

MODELO_PADRAO = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
client = genai.Client(api_key=api_key)

# ============================================================
# 2. DEFINIÇÕES TEMÁTICAS E REGRAS DE TRIAGEM (EXEMPLO)
# ============================================================
TEMAS_VALIDOS = [
    "Regulamentação e Mobilidade Urbana",
    "Sustentabilidade e Logística Reversa",
    "Pequenos Negócios e Inovação",
    "Governança Corporativa e ESG",
    "Segurança da Informação e Privacidade",
    "Relações Trabalhistas e Jornada",
    "Infraestrutura e Logística",
    "Nenhum"
]

MANUAL_TRIAGEM = """
Você é um analista sênior de inteligência de mercado e dados.
Sua missão é realizar a TRIAGEM RIGOROSA de publicações de clipping setorial e jornalístico.

REGRAS DE FILTRAGEM TEMÁTICA (TRIAGEM RIGOROSA):
Você deve ler o conteúdo de cada publicação e classificá-la APENAS se atender estritamente aos requisitos abaixo.
Ignore menções superficiais ou descontextualizadas (falsos positivos).

1. Regulamentação e Mobilidade Urbana:
   - Discussões sobre regulação de serviços por aplicativo, transportes e diretrizes de mobilidade urbana.
   - Falso positivo: matérias genéricas de trânsito ou acidentes sem menção à regulação ou política setorial.

2. Sustentabilidade e Logística Reversa:
   - Iniciativas de economia circular, reciclagem, gestão de resíduos e logística reversa corporativa.

3. Pequenos Negócios e Inovação:
   - Ações, crédito, tecnologia e capacitação voltadas a microempreendedores e negócios locais.

4. Governança Corporativa e ESG:
   - Políticas de compliance, transparência, integridade corporativa e relatórios de sustentabilidade.

5. Segurança da Informação e Privacidade:
   - Boas práticas de segurança de dados, adequação regulatória à privacidade e combate a fraudes.

6. Relações Trabalhistas e Jornada:
   - Debates sobre modelos de trabalho, regimes de escala, benefícios e bem-estar do colaborador.

7. Infraestrutura e Logística:
   - Obras, portos, rodovias, ferrovias e grandes corredores de escoamento e abastecimento.

Se a publicação NÃO se enquadrar com rigor em nenhum dos temas acima ou for um falso positivo, classifique como tema "Nenhum" e enquadra = false.
"""

# ============================================================
# 3. MODELOS PYDANTIC PARA SAÍDA ESTRUTURADA
# ============================================================
class ItemTriagem(BaseModel):
    id: int
    enquadra: bool = Field(
        description="True somente se a publicação atende estritamente a um dos 7 critérios temáticos estabelecidos. False para falsos positivos ou temas não correlatos."
    )
    tema: Literal[
        "Regulamentação e Mobilidade Urbana",
        "Sustentabilidade e Logística Reversa",
        "Pequenos Negócios e Inovação",
        "Governança Corporativa e ESG",
        "Segurança da Informação e Privacidade",
        "Relações Trabalhistas e Jornada",
        "Infraestrutura e Logística",
        "Nenhum"
    ]
    justificativa: str = Field(
        description="Breve resumo de 1 a 2 linhas explicando por que a publicação se enquadra na pauta exigida (ou a razão da exclusão se for falso positivo)."
    )

class LoteTriagem(BaseModel):
    itens: List[ItemTriagem]

# ============================================================
# 4. TRATAMENTO E MAPEAMENTO DE COLUNAS COM PANDAS
# ============================================================
def _normalizar_texto(texto: Any) -> str:
    """Remove acentos, pontuação e espaços em branco para comparação de chaves."""
    if not isinstance(texto, str):
        return ""
    s = unicodedata.normalize("NFKD", texto).encode("ASCII", "ignore").decode("utf-8")
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]", "", s)
    return s

def mapear_colunas(df: pd.DataFrame) -> Dict[str, Optional[str]]:
    """Identifica dinamicamente os nomes reais das colunas da planilha."""
    colunas_reais = list(df.columns)
    mapa_normalizado = {_normalizar_texto(c): c for c in colunas_reais}

    def buscar_coluna(candidatos: List[str]) -> Optional[str]:
        for cand in candidatos:
            cand_norm = _normalizar_texto(cand)
            if cand_norm in mapa_normalizado:
                return mapa_normalizado[cand_norm]
        # Busca parcial por substring
        for cand in candidatos:
            cand_norm = _normalizar_texto(cand)
            for k, real in mapa_normalizado.items():
                if cand_norm in k or k in cand_norm:
                    return real
        return None

    col_ordem = buscar_coluna(["Ordem", "Item", "ID", "Indice", "Index"])
    col_canal = buscar_coluna(["Canal", "Rede Social", "Midia", "Origem", "Veiculo", "Plataforma"])
    col_autor = buscar_coluna(["Autor Nome", "Autor", "Nome", "Perfil", "Usuario", "Author"])
    col_link = buscar_coluna(["Link", "URL", "Permalink", "Link da Publicacao", "Link da Publicação"])
    col_texto = buscar_coluna(["Conteudo", "Conteúdo", "Texto", "Descricao", "Descrição", "Mensagem", "Publicacao", "Titulo", "Título"])
    col_titulo = buscar_coluna(["Titulo", "Título", "Title", "Manchete"])
    col_seguidores = buscar_coluna(["Seguidores/Inscritos", "Seguidores / Inscritos", "Seguidores", "Inscritos", "Audiencia", "Audiência", "Followers"])
    
    col_total_reacoes = buscar_coluna(["Total de Reações", "Total de Reacoes", "Total Reações", "Total Reacoes", "Total de Reações/Gostei", "Reações", "Reacoes", "Total Reacoes"])
    col_curtidas = buscar_coluna(["Curtidas/Gostei", "Curtidas / Gostei", "Curtidas", "Gostei", "Likes", "Like"])
    col_amei = buscar_coluna(["Amei", "Love"])
    col_haha = buscar_coluna(["Haha", "Risos"])
    col_uau = buscar_coluna(["Uau", "Wow"])
    col_triste = buscar_coluna(["Triste", "Sad"])
    col_raiva = buscar_coluna(["Raiva", "Angry"])
    col_outras_reacoes = buscar_coluna(["Outras Reações", "Outras Reacoes", "Outras", "Other Reactions"])
    col_comentarios = buscar_coluna(["Comentários", "Comentarios", "Comments", "Comentario"])
    col_compartilhamentos = buscar_coluna(["Compartilhamentos", "Compartilhamento", "Shares", "Retweets", "Reposts"])

    return {
        "ordem": col_ordem,
        "canal": col_canal,
        "autor": col_autor,
        "link": col_link,
        "texto": col_texto,
        "titulo": col_titulo,
        "seguidores": col_seguidores,
        "total_reacoes": col_total_reacoes,
        "curtidas": col_curtidas,
        "amei": col_amei,
        "haha": col_haha,
        "uau": col_uau,
        "triste": col_triste,
        "raiva": col_raiva,
        "outras_reacoes": col_outras_reacoes,
        "comentarios": col_comentarios,
        "compartilhamentos": col_compartilhamentos,
    }

def _converter_numero(val: Any) -> float:
    """Converte valores diversos para float numérico seguro."""
    if val is None or pd.isna(val):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    # Se for string (ex: '1.250', '350', '2,5k')
    s = str(val).strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0

def calcular_metricas_pandas(df: pd.DataFrame, mapa: Dict[str, Optional[str]]) -> pd.DataFrame:
    """
    Aplica as Regras de Cálculo de Métricas com pandas:
      1. Verifica a coluna Total de Reações. Se estiver vazia, NaN ou zero,
         calcula a soma das colunas individuais de reação:
         (Curtidas/Gostei + Amei + Haha + Uau + Triste + Raiva + Outras Reações).
      2. Some o valor obtido acima com as colunas Comentários e Compartilhamentos.
      3. O resultado final será a métrica Volume de Interações.
      4. A métrica de audiência baseia-se exclusivamente na coluna Seguidores/Inscritos.
    """
    df = df.copy()

    # Extrai colunas numéricas de reação
    c_tot_reac = mapa["total_reacoes"]
    c_curtidas = mapa["curtidas"]
    c_amei = mapa["amei"]
    c_haha = mapa["haha"]
    c_uau = mapa["uau"]
    c_triste = mapa["triste"]
    c_raiva = mapa["raiva"]
    c_outras = mapa["outras_reacoes"]
    c_coment = mapa["comentarios"]
    c_comp = mapa["compartilhamentos"]
    c_seg = mapa["seguidores"]

    def extrair_serie(col_nome: Optional[str]) -> pd.Series:
        if col_nome and col_nome in df.columns:
            return df[col_nome].apply(_converter_numero)
        return pd.Series(0.0, index=df.index)

    s_tot_reac = extrair_serie(c_tot_reac)
    s_curtidas = extrair_serie(c_curtidas)
    s_amei = extrair_serie(c_amei)
    s_haha = extrair_serie(c_haha)
    s_uau = extrair_serie(c_uau)
    s_triste = extrair_serie(c_triste)
    s_raiva = extrair_serie(c_raiva)
    s_outras = extrair_serie(c_outras)
    s_coment = extrair_serie(c_coment)
    s_comp = extrair_serie(c_comp)
    s_seg = extrair_serie(c_seg)

    # Soma individual de reações caso Total de Reações seja 0 ou vazio
    soma_reacoes_individuais = (
        s_curtidas + s_amei + s_haha + s_uau + s_triste + s_raiva + s_outras
    )

    # Se Total de Reações estiver vazio/zero, usa a soma individual
    reacoes_finais = s_tot_reac.where(s_tot_reac > 0, soma_reacoes_individuais)

    # Volume de Interações = Reações Finais + Comentários + Compartilhamentos
    volume_interacoes = (reacoes_finais + s_coment + s_comp).round().astype(int)
    audiencia_seguidores = s_seg.round().astype(int)

    df["_volume_interacoes"] = volume_interacoes
    df["_audiencia_seguidores"] = audiencia_seguidores

    # Campos padronizados para o relatório
    df["_link"] = df[mapa["link"]].fillna("").astype(str).str.strip() if mapa["link"] else ""
    df["_autor"] = df[mapa["autor"]].fillna("Não informado").astype(str).str.strip() if mapa["autor"] else "Não informado"
    df["_canal"] = df[mapa["canal"]].fillna("Rede Social").astype(str).str.strip() if mapa["canal"] else "Rede Social"
    
    # Texto unificado (título + conteúdo se ambos existirem)
    if mapa["titulo"] and mapa["texto"] and mapa["titulo"] != mapa["texto"]:
        df["_descricao_completa"] = (
            df[mapa["titulo"]].fillna("").astype(str) + " — " + df[mapa["texto"]].fillna("").astype(str)
        ).str.strip(" — ")
    elif mapa["texto"]:
        df["_descricao_completa"] = df[mapa["texto"]].fillna("").astype(str).str.strip()
    elif mapa["titulo"]:
        df["_descricao_completa"] = df[mapa["titulo"]].fillna("").astype(str).str.strip()
    else:
        df["_descricao_completa"] = ""

    # Ordem original do arquivo (se houver, senão 1 a N)
    if mapa["ordem"] and mapa["ordem"] in df.columns:
        df["_ordem_orig"] = df[mapa["ordem"]].fillna("").astype(str).str.strip()
    else:
        df["_ordem_orig"] = [str(i + 1) for i in range(len(df))]

    return df

# ============================================================
# 5. CLASSIFICAÇÃO COM O MODELO GEMINI
# ============================================================
def classificar_lote_gemini(lote: List[Dict[str, Any]], max_tentativas: int = 3) -> List[ItemTriagem]:
    """Envia um lote de publicações para o Gemini realizar a triagem rigorosa com Structured Outputs."""
    itens_prompt = []
    for item in lote:
        texto_limpo = item["texto"].replace("\n", " ").strip()
        itens_prompt.append(
            f"Item ID: {item['id']}\n"
            f"Autor: {item['autor']} ({item['canal']})\n"
            f"Texto: {texto_limpo}\n"
        )
    prompt_usuario = (
        "Analise cada uma das publicações abaixo e faça a triagem rigorosa conforme o manual.\n\n"
        + "\n---\n".join(itens_prompt)
    )

    for tentativa in range(1, max_tentativas + 1):
        try:
            response = client.models.generate_content(
                model=MODELO_PADRAO,
                contents=prompt_usuario,
                config=types.GenerateContentConfig(
                    system_instruction=MANUAL_TRIAGEM,
                    response_mime_type="application/json",
                    response_schema=LoteTriagem,
                    temperature=0.1,
                ),
            )
            parsed = LoteTriagem.model_validate_json(response.text)
            return parsed.itens
        except Exception as e:
            if tentativa == max_tentativas:
                print(f"⚠️ Erro ao processar lote no Gemini após {tentativa} tentativas: {e}")
                # Fallback neutro
                return [
                    ItemTriagem(id=item["id"], enquadra=False, tema="Nenhum", justificativa="Erro ao comunicar com a IA")
                    for item in lote
                ]
            time.sleep(2 * tentativa)

def executar_triagem_publicacoes(df: pd.DataFrame, tamanho_lote: int = 15) -> pd.DataFrame:
    """Percorre o DataFrame em lotes e classifica as publicações com o Gemini."""
    total = len(df)
    print(f"\n🚀 Iniciando triagem rigorosa com Gemini ({MODELO_PADRAO})...")
    print(f"📊 Total de publicações a avaliar: {total}")
    
    dados_para_ia = []
    for idx, row in df.iterrows():
        dados_para_ia.append({
            "id": idx,
            "autor": row["_autor"],
            "canal": row["_canal"],
            "texto": row["_descricao_completa"][:1200]  # Limita para eficiência de tokens
        })

    resultados_dict: Dict[int, ItemTriagem] = {}
    total_lotes = (total + tamanho_lote - 1) // tamanho_lote

    for i in range(0, total, tamanho_lote):
        lote_atual = dados_para_ia[i:i + tamanho_lote]
        num_lote = (i // tamanho_lote) + 1
        print(f"   ⏳ Processando lote {num_lote}/{total_lotes} ({len(lote_atual)} itens)...", end="", flush=True)
        
        itens_classificados = classificar_lote_gemini(lote_atual)
        for res in itens_classificados:
            resultados_dict[res.id] = res
        print(" Concluído!")

    # Atribui resultados de volta ao DataFrame
    df["_enquadra"] = [resultados_dict.get(i, ItemTriagem(id=i, enquadra=False, tema="Nenhum", justificativa="")).enquadra for i in df.index]
    df["_tema"] = [resultados_dict.get(i, ItemTriagem(id=i, enquadra=False, tema="Nenhum", justificativa="")).tema for i in df.index]
    df["_justificativa"] = [resultados_dict.get(i, ItemTriagem(id=i, enquadra=False, tema="Nenhum", justificativa="")).justificativa for i in df.index]

    selecionadas = df[df["_enquadra"] == True]
    print(f"✅ Triagem concluída! {len(selecionadas)} publicações selecionadas dentro dos critérios rigorosos.\n")
    return df

# ============================================================
# 6. ESTRUTURAÇÃO DO RELATÓRIO EXECUTIVO (MARKDOWN)
# ============================================================
def gerar_relatorio_markdown(df_filtrado: pd.DataFrame, top_n: int = 5) -> str:
    """
    Estrutura a resposta final estritamente de acordo com as especificações exigidas:
      ### Links Selecionados Dentro dos Temas
      ### Top [X] por Volume de Interações
      ### Top [X] por Número de Seguidores / Inscritos
    """
    linhas: List[str] = []

    # -------------------------------------------------------------
    # SEÇÃO 1: Links Selecionados Dentro dos Temas
    # -------------------------------------------------------------
    linhas.append("### Links Selecionados Dentro dos Temas")
    
    if df_filtrado.empty:
        linhas.append("Nenhuma publicação atendeu aos critérios temáticos rigorosos nesta base de dados.")
    else:
        # Agrupar por tema na ordem dos temas oficiais
        temas_ordenados = [t for t in TEMAS_VALIDOS if t != "Nenhum"]
        
        temas_presentes = False
        for tema in temas_ordenados:
            df_tema = df_filtrado[df_filtrado["_tema"] == tema]
            if df_tema.empty:
                continue
            
            temas_presentes = True
            linhas.append(f"\n#### {tema}")
            
            for _, row in df_tema.iterrows():
                ordem_exibicao = row["_ordem_orig"]
                link_pub = row["_link"] if row["_link"] else "Link não disponível"
                justificativa = row["_justificativa"]
                
                linhas.append(f"Item {ordem_exibicao} — {link_pub}")
                linhas.append(f"Trecho/Justificativa: {justificativa}\n")

        if not temas_presentes:
            linhas.append("Não houve menções dentro dos temas estabelecidos.")

    linhas.append("")

    # -------------------------------------------------------------
    # SEÇÃO 2: Top [X] por Volume de Interações
    # -------------------------------------------------------------
    linhas.append(f"### Top {top_n} por Volume de Interações")
    if df_filtrado.empty:
        linhas.append("Não há publicações filtradas para ranqueamento.")
    else:
        df_top_interacoes = df_filtrado.sort_values(by="_volume_interacoes", ascending=False).head(top_n)
        for rank, (_, row) in enumerate(df_top_interacoes.iterrows(), 1):
            autor = row["_autor"]
            canal = row["_canal"]
            desc = row["_descricao_completa"]
            link = row["_link"] if row["_link"] else "Link não disponível"
            
            linhas.append(f"Item {rank} — {autor} ({canal})")
            linhas.append(desc if desc else "(Sem descrição)")
            linhas.append(f"Link: {link}\n")

    # -------------------------------------------------------------
    # SEÇÃO 3: Top [X] por Número de Seguidores / Inscritos
    # -------------------------------------------------------------
    linhas.append(f"### Top {top_n} por Número de Seguidores / Inscritos")
    if df_filtrado.empty:
        linhas.append("Não há publicações filtradas para ranqueamento.")
    else:
        # Eliminando autores duplicados para focar no alcance único
        df_unicos = df_filtrado.sort_values(by="_audiencia_seguidores", ascending=False).drop_duplicates(subset=["_autor"]).head(top_n)
        for rank, (_, row) in enumerate(df_unicos.iterrows(), 1):
            autor = row["_autor"]
            canal = row["_canal"]
            desc = row["_descricao_completa"]
            link = row["_link"] if row["_link"] else "Link não disponível"
            
            linhas.append(f"Item {rank} — {autor} ({canal})")
            linhas.append(desc if desc else "(Sem descrição)")
            linhas.append(f"Link: {link}\n")

    return "\n".join(linhas)

# ============================================================
# 7. PARSER DE TEXTO COLADO / CLIPBOARD
# ============================================================
def processar_texto_colado(texto: str) -> pd.DataFrame:
    """
    Recebe texto livre ou delimitado (tab/vírgula) e converte para DataFrame estruturado.
    Caso seja texto puro de notícias/posts, o próprio Gemini faz a estruturação inicial.
    """
    texto = texto.strip()
    if not texto:
        return pd.DataFrame()

    # Se parece um CSV ou TSV colado
    if "\t" in texto or ("," in texto and "\n" in texto):
        try:
            df = pd.read_csv(io.StringIO(texto), sep=None, engine="python")
            if len(df.columns) >= 2:
                return df
        except Exception:
            pass

    # Caso seja texto livre com postagens coladas, pede ao Gemini para estruturar em lista
    print("🤖 Texto não-tabular detectado. Utilizando Gemini para estruturar as postagens...")
    prompt = (
        "Você deve ler o texto de clipping colado abaixo e extrair cada publicação individual.\n"
        "Retorne em JSON uma lista de objetos contendo: 'autor', 'canal', 'link', 'texto', "
        "'curtidas' (número), 'comentarios' (número), 'compartilhamentos' (número), 'seguidores' (número).\n"
        "Se algum dado métrico não constar no texto, defina como 0.\n\n"
        f"TEXTO DO CLIPPING:\n{texto}"
    )

    class PostEstruturado(BaseModel):
        autor: str = "Não informado"
        canal: str = "Rede Social"
        link: str = ""
        texto: str
        curtidas: float = 0.0
        comentarios: float = 0.0
        compartilhamentos: float = 0.0
        seguidores: float = 0.0

    class ListaPosts(BaseModel):
        posts: List[PostEstruturado]

    try:
        response = client.models.generate_content(
            model=MODELO_PADRAO,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ListaPosts,
                temperature=0.1,
            ),
        )
        dados = ListaPosts.model_validate_json(response.text)
        linhas = [p.model_dump() for p in dados.posts]
        return pd.DataFrame(linhas)
    except Exception as e:
        print(f"❌ Falha ao converter texto estruturado com IA: {e}")
        # Cria uma linha única com o texto completo
        return pd.DataFrame([{
            "autor": "Não informado",
            "canal": "Clipping",
            "link": "",
            "texto": texto,
            "curtidas": 0.0,
            "comentarios": 0.0,
            "compartilhamentos": 0.0,
            "seguidores": 0.0
        }])

# ============================================================
# 8. LEITURA E UNIFICAÇÃO DE MÚLTIPLAS PLANILHAS DA PASTA
# ============================================================
PASTA_TEMAS_PADRAO = os.path.join(PASTA_ATUAL, "temas_de_interesse")

def carregar_planilhas_da_pasta(pasta: str) -> pd.DataFrame:
    """
    Varre a pasta em busca de todas as planilhas (.xlsx, .xls, .csv).
    Lê cada arquivo, aplica o mapeamento e o cálculo de métricas individualmente,
    e unifica tudo em um único DataFrame com deduplicação por Link/Texto.
    """
    if not os.path.exists(pasta):
        os.makedirs(pasta, exist_ok=True)
        return pd.DataFrame()

    arquivos = [
        f for f in os.listdir(pasta)
        if f.lower().endswith((".xlsx", ".xls", ".csv"))
        and not f.startswith("~$")
        and "filtrado" not in f.lower()
    ]

    if not arquivos:
        return pd.DataFrame()

    print(f"\n📂 Encontrada(s) {len(arquivos)} planilha(s) na pasta: '{os.path.basename(pasta)}'")
    dfs_processados = []

    for arq in arquivos:
        caminho_arq = os.path.join(pasta, arq)
        print(f"   📄 Lendo '{arq}'...", end="", flush=True)
        try:
            ext = os.path.splitext(arq)[1].lower()
            if ext in [".xlsx", ".xls"]:
                df_temp = pd.read_excel(caminho_arq)
            else:
                try:
                    df_temp = pd.read_csv(caminho_arq)
                except UnicodeDecodeError:
                    df_temp = pd.read_csv(caminho_arq, encoding="latin1")

            total_linhas = len(df_temp)
            if total_linhas > 0:
                df_temp["_origem_arquivo"] = arq
                mapa = mapear_colunas(df_temp)
                df_calc = calcular_metricas_pandas(df_temp, mapa)
                dfs_processados.append(df_calc)
                print(f" {total_linhas} linhas carregadas.")
            else:
                print(" (vazia, ignorada).")
        except Exception as e:
            print(f" ❌ Erro ao ler: {e}")

    if not dfs_processados:
        return pd.DataFrame()

    df_unificado = pd.concat(dfs_processados, ignore_index=True)
    total_antes = len(df_unificado)

    # Deduplicação inteligente
    tem_link = (df_unificado["_link"] != "") & (df_unificado["_link"] != "Link não disponível")
    df_com_link = df_unificado[tem_link].sort_values(by="_volume_interacoes", ascending=False).drop_duplicates(subset=["_link"])
    df_sem_link = df_unificado[~tem_link].drop_duplicates(subset=["_autor", "_descricao_completa"])

    df_final = pd.concat([df_com_link, df_sem_link], ignore_index=True)
    total_removidos = total_antes - len(df_final)
    if total_removidos > 0:
        print(f"   🧹 Removidas {total_removidos} publicações duplicadas entre as planilhas.")

    print(f"📊 Total unificado de publicações para triagem: {len(df_final)}\n")
    return df_final

# ============================================================
# 9. FLUXO PRINCIPAL DE EXECUÇÃO
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="Analista Sênior de Dados e Clipping com IA (Gemini)."
    )
    parser.add_argument("arquivo", nargs="?", help="Caminho do arquivo ou pasta com planilhas")
    parser.add_argument("--pasta", type=str, default=PASTA_TEMAS_PADRAO, help="Pasta contendo as planilhas de entrada (padrão: temas_de_interesse)")
    parser.add_argument("--top", type=int, default=5, help="Quantidade de itens no Top Ranking (padrão: 5)")
    parser.add_argument("--lote", type=int, default=15, help="Tamanho do lote de envio ao Gemini (padrão: 15)")
    parser.add_argument("--colar", action="store_true", help="Ativa modo para colar texto diretamente no terminal")
    parser.add_argument("--clipboard", action="store_true", help="Lê conteúdo da Área de Transferência do Windows")
    parser.add_argument("--saida", type=str, help="Caminho personalizado para salvar o relatório Markdown")

    args = parser.parse_args()

    caminho_arquivo = args.arquivo
    df = None

    # Modo 1: Área de transferência (Clipboard)
    if args.clipboard:
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            texto_clip = root.clipboard_get()
            print("📋 Conteúdo capturado da Área de Transferência!")
            df = processar_texto_colado(texto_clip)
            mapa = mapear_colunas(df)
            df = calcular_metricas_pandas(df, mapa)
        except Exception as e:
            print(f"❌ Erro ao ler da Área de Transferência: {e}")

    # Modo 2: Colar texto interativo no terminal
    elif args.colar:
        print("✍️ Cole o texto do clipping abaixo. Quando terminar, digite 'FIM' em uma nova linha e dê Enter:")
        linhas_coladas = []
        while True:
            try:
                linha = input()
                if linha.strip() == "FIM":
                    break
                linhas_coladas.append(linha)
            except EOFError:
                break
        texto_colado = "\n".join(linhas_coladas)
        df = processar_texto_colado(texto_colado)
        mapa = mapear_colunas(df)
        df = calcular_metricas_pandas(df, mapa)

    # Modo 3: Arquivo ou pasta específica passada via argumento posicional
    elif caminho_arquivo:
        if not os.path.isabs(caminho_arquivo):
            caminho_arquivo = os.path.abspath(caminho_arquivo)

        if not os.path.exists(caminho_arquivo):
            print(f"❌ Caminho não encontrado: {caminho_arquivo}")
            return

        if os.path.isdir(caminho_arquivo):
            df = carregar_planilhas_da_pasta(caminho_arquivo)
        else:
            ext = os.path.splitext(caminho_arquivo)[1].lower()
            print(f"📂 Lendo arquivo de dados: {caminho_arquivo}")
            if ext in [".xlsx", ".xls"]:
                df_raw = pd.read_excel(caminho_arquivo)
            elif ext == ".csv":
                try:
                    df_raw = pd.read_csv(caminho_arquivo)
                except UnicodeDecodeError:
                    df_raw = pd.read_csv(caminho_arquivo, encoding="latin1")
            elif ext in [".txt", ".md"]:
                with open(caminho_arquivo, "r", encoding="utf-8") as f:
                    df_raw = processar_texto_colado(f.read())
            else:
                print(f"❌ Formato de arquivo '{ext}' não suportado. Use .xlsx, .csv ou .txt.")
                return
            mapa = mapear_colunas(df_raw)
            df = calcular_metricas_pandas(df_raw, mapa)

    # Modo 4: Sem argumentos - Checa automaticamente a pasta 'temas_de_interesse'
    else:
        # Verifica se há planilhas na pasta temas_de_interesse
        pasta_temas = args.pasta
        arquivos_na_pasta = []
        if os.path.exists(pasta_temas):
            arquivos_na_pasta = [
                f for f in os.listdir(pasta_temas)
                if f.lower().endswith((".xlsx", ".xls", ".csv"))
                and not f.startswith("~$")
                and "filtrado" not in f.lower()
            ]

        if arquivos_na_pasta:
            print("=" * 70)
            print(f"📂 PASTA DETECTADA: 'temas_de_interesse' ({len(arquivos_na_pasta)} planilha(s))")
            print("=" * 70)
            for i, f in enumerate(arquivos_na_pasta, 1):
                print(f"   [{i}] {f}")
            print("\n1. Processar todas as planilhas da pasta 'temas_de_interesse' (Recomendado)")
            print("2. Informar outro caminho de arquivo ou pasta")
            print("3. Colar texto de clipping diretamente")
            print("4. Ler da Área de Transferência (Clipboard)")
            escolha = input("\nEscolha uma opção (1 a 4) [Padrão: 1]: ").strip() or "1"

            if escolha == "1":
                df = carregar_planilhas_da_pasta(pasta_temas)
            elif escolha == "2":
                caminho = input("Digite ou arraste o arquivo/pasta aqui: ").strip().strip('"').strip("'")
                if os.path.isdir(caminho):
                    df = carregar_planilhas_da_pasta(caminho)
                elif os.path.exists(caminho):
                    ext = os.path.splitext(caminho)[1].lower()
                    if ext in [".xlsx", ".xls"]:
                        df_raw = pd.read_excel(caminho)
                    elif ext == ".csv":
                        try:
                            df_raw = pd.read_csv(caminho)
                        except UnicodeDecodeError:
                            df_raw = pd.read_csv(caminho, encoding="latin1")
                    else:
                        with open(caminho, "r", encoding="utf-8") as f:
                            df_raw = processar_texto_colado(f.read())
                    mapa = mapear_colunas(df_raw)
                    df = calcular_metricas_pandas(df_raw, mapa)
                else:
                    print(f"❌ Caminho não encontrado: {caminho}")
                    return
            elif escolha == "3":
                print("\n✍️ Cole o texto do clipping. Quando terminar, digite 'FIM' em uma nova linha:")
                linhas_coladas = []
                while True:
                    try:
                        linha = input()
                        if linha.strip() == "FIM":
                            break
                        linhas_coladas.append(linha)
                    except EOFError:
                        break
                df = processar_texto_colado("\n".join(linhas_coladas))
                mapa = mapear_colunas(df)
                df = calcular_metricas_pandas(df, mapa)
            elif escolha == "4":
                try:
                    import tkinter as tk
                    root = tk.Tk()
                    root.withdraw()
                    texto_clip = root.clipboard_get()
                    print("📋 Conteúdo capturado da Área de Transferência!")
                    df = processar_texto_colado(texto_clip)
                    mapa = mapear_colunas(df)
                    df = calcular_metricas_pandas(df, mapa)
                except Exception as e:
                    print(f"❌ Erro ao ler da Área de Transferência: {e}")
                    return
            else:
                print("Opção inválida.")
                return
        else:
            print("=" * 70)
            print("  MONITORAMENTO & ANÁLISE DE CLIPPING COM IA (GEMINI)")
            print("=" * 70)
            print("ℹ️ A pasta 'temas_de_interesse' está vazia.")
            print(f"   Coloque suas planilhas em:\n   {pasta_temas}\n")
            print("1. Informar caminho de outro arquivo (.xlsx / .csv / .txt)")
            print("2. Colar texto de clipping diretamente")
            print("3. Ler da Área de Transferência (Clipboard)")
            escolha = input("\nEscolha uma opção (1, 2 ou 3): ").strip()

            if escolha == "1":
                caminho = input("Digite ou arraste o arquivo aqui: ").strip().strip('"').strip("'")
                if os.path.exists(caminho):
                    ext = os.path.splitext(caminho)[1].lower()
                    if ext in [".xlsx", ".xls"]:
                        df_raw = pd.read_excel(caminho)
                    elif ext == ".csv":
                        try:
                            df_raw = pd.read_csv(caminho)
                        except UnicodeDecodeError:
                            df_raw = pd.read_csv(caminho, encoding="latin1")
                    else:
                        with open(caminho, "r", encoding="utf-8") as f:
                            df_raw = processar_texto_colado(f.read())
                    mapa = mapear_colunas(df_raw)
                    df = calcular_metricas_pandas(df_raw, mapa)
                else:
                    print(f"❌ Arquivo não encontrado: {caminho}")
                    return
            elif escolha == "2":
                print("\n✍️ Cole o texto do clipping. Quando terminar, digite 'FIM' em uma nova linha:")
                linhas_coladas = []
                while True:
                    try:
                        linha = input()
                        if linha.strip() == "FIM":
                            break
                        linhas_coladas.append(linha)
                    except EOFError:
                        break
                df = processar_texto_colado("\n".join(linhas_coladas))
                mapa = mapear_colunas(df)
                df = calcular_metricas_pandas(df, mapa)
            elif escolha == "3":
                try:
                    import tkinter as tk
                    root = tk.Tk()
                    root.withdraw()
                    texto_clip = root.clipboard_get()
                    print("📋 Conteúdo capturado da Área de Transferência!")
                    df = processar_texto_colado(texto_clip)
                    mapa = mapear_colunas(df)
                    df = calcular_metricas_pandas(df, mapa)
                except Exception as e:
                    print(f"❌ Erro ao ler da Área de Transferência: {e}")
                    return
            else:
                print("Opção inválida.")
                return

    if df is None or df.empty:
        print("❌ Nenhuma publicação carregada para processamento.")
        return

    # Mapeamento e cálculo das métricas matemáticas com pandas
    mapa = mapear_colunas(df)
    df_calculado = calcular_metricas_pandas(df, mapa)

    # Filtragem com IA (Triagem Temática Rigorosa)
    df_triado = executar_triagem_publicacoes(df_calculado, tamanho_lote=args.lote)
    df_final = df_triado[df_triado["_enquadra"] == True].copy()

    # Estruturação do Relatório Markdown
    relatorio_md = gerar_relatorio_markdown(df_final, top_n=args.top)

    # Impressão do Relatório no Terminal
    print("\n" + "=" * 70)
    print("RELATÓRIO EXECUTIVO GERADO")
    print("=" * 70 + "\n")
    print(relatorio_md)
    print("\n" + "=" * 70)

    # Salvamento automático do arquivo de relatório
    pasta_saida = os.path.join(PASTA_ATUAL, "relatorios_gerados")
    os.makedirs(pasta_saida, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    arq_md = args.saida or os.path.join(pasta_saida, f"relatorio_clipping_{timestamp}.md")
    
    with open(arq_md, "w", encoding="utf-8") as f:
        f.write(relatorio_md)
    print(f"📄 Relatório Markdown salvo com sucesso em:\n   {arq_md}")

    # Salva também a planilha com os dados calculados e filtrados para auditoria
    if not df_final.empty:
        arq_excel = os.path.join(pasta_saida, f"clipping_filtrado_{timestamp}.xlsx")
        colunas_export = [
            col for col in df_final.columns 
            if not col.startswith("_")
        ]
        # Adiciona colunas calculadas no início da exportação
        export_df = df_final[["_ordem_orig", "_tema", "_justificativa", "_volume_interacoes", "_audiencia_seguidores"] + colunas_export].copy()
        export_df.rename(columns={
            "_ordem_orig": "Ordem de Triagem",
            "_tema": "Tema Identificado",
            "_justificativa": "Justificativa da IA",
            "_volume_interacoes": "Volume de Interações (Calculado)",
            "_audiencia_seguidores": "Audiência / Seguidores"
        }, inplace=True)
        export_df.to_excel(arq_excel, index=False)
        print(f"📊 Planilha auditada salva em:\n   {arq_excel}")

if __name__ == "__main__":
    main()
