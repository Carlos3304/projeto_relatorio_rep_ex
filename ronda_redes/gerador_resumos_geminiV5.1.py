import os
import uuid
import tempfile
import re
import glob
import html
import asyncio
import requests
import shutil
import yt_dlp

try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

# Oculta o "FutureWarning" chato que a biblioteca do google começou a exibir
import warnings
warnings.filterwarnings("ignore", category=FutureWarning, module="google.generativeai")

import google.generativeai as genai
from dotenv import load_dotenv

# Garante que o .env do próprio diretório será carregado
diretorio_script = os.path.dirname(os.path.abspath(__file__))
caminho_env = os.path.join(diretorio_script, ".env")
load_dotenv(dotenv_path=caminho_env, override=True)

# Perfil Nativo do Firefox (Linux ou Windows)
def obter_perfil_firefox():
    caminho_custom = os.getenv("FIREFOX_PROFILE_PATH")
    if caminho_custom and os.path.exists(caminho_custom):
        return caminho_custom
    if os.name == "nt":
        base_win = os.path.join(os.environ.get("APPDATA", r"C:\AppData"), r"Mozilla\Firefox\Profiles")
        if os.path.exists(base_win):
            for item in sorted(os.listdir(base_win)):
                if "default" in item.lower():
                    return os.path.join(base_win, item)
        return os.path.join(base_win, "perfil_exemplo")
    candidatos_bases = [
        os.path.expanduser("~/.mozilla/firefox"),
        os.path.expanduser("~/snap/firefox/common/.mozilla/firefox"),
    ]
    for base in candidatos_bases:
        if os.path.exists(base):
            for item in sorted(os.listdir(base)):
                caminho_completo = os.path.join(base, item)
                if os.path.isdir(caminho_completo) and ("default" in item.lower()):
                    return caminho_completo
    return os.path.expanduser("~/.mozilla/firefox/perfil_exemplo.default")

PERFIL_CAMINHO = obter_perfil_firefox()

# Bloco de temas compartilhado pelos prompts de resumo único e combinado (Exemplo Corporativo / Institucional)
BLOCO_TEMAS = """3. Assuntos e Temas Monitorados:
O resumo DEVE citar nominalmente e detalhar o(s) assunto(s) monitorado(s) que estiver(em) PRESENTE(S) no conteúdo (texto, imagem, áudio ou vídeo).

• INSTITUCIONAL:
  - Diretoria Executiva e Liderança: citação ao CEO / Diretor-Presidente Roberto Santos ou porta-vozes institucionais da Organização Monitorada.
  - Empresa Exemplo S.A. e suas marcas controladas.
  - Projetos Estratégicos: iniciativas de inovação, expansão e responsabilidade social da companhia.

• TEMAS:
  - Sustentabilidade e Transição Energética: metas de descarbonização, créditos de carbono e energia limpa.
  - Inovação, IA e Tecnologia: transformação digital, inteligência artificial e modernização operacional.
  - Logística Reversa e Resíduos: iniciativas de reciclagem e economia circular.
  - Relações Trabalhistas e Modelo Híbrido: acordos sindicais, modelos de jornada e bem-estar no trabalho.
  - Governança Corporativa e Compliance: auditorias, comitês de integridade e boas práticas de mercado.

REGRAS DE USO DOS TEMAS:
- Basta UM tema presente. Foque no que a liderança ou a marca anunciou, declarou ou no que foi noticiado sobre ela.
- NUNCA invente, deduza ou force um tema que não aparece no conteúdo. A lista orienta o FOCO do resumo.
- Se vários temas aparecerem, priorize o tema com maior impacto reputacional e financeiro."""

PROMPT_SISTEMA = """Agir como um redator de notícias concisas, mantendo um tom jornalístico, direto e neutro.

1. Estrutura Padrão do Texto:
Você deve retornar APENAS um parágrafo com a seguinte estrutura exata:
*<Emoji> <Nome do Autor/Veículo>: <Identificação do autor> <ação no passado> que <resumo substantivo e detalhado citando o assunto monitorado>*

Exemplo exato do padrão esperado:
*📲 Valor Econômico: O jornal noticiou que o diretor-presidente da Empresa Exemplo, Roberto Santos, apresentou o plano de investimentos em energia renovável. O executivo detalhou metas de neutralidade de carbono até 2030 e novas parcerias com fornecedores do setor elétrico.*

Outro exemplo:
*📲 Folha de S.Paulo: O portal publicou que a Empresa Exemplo firmou acordo para expansão de centros logísticos no interior do estado. A matéria destacou a previsão de geração de novos postos de trabalho e investimentos em tecnologia de automação.*

2. Regras Rigorosas de Redação e Formatação:
- O texto inteiro DEVE OBRIGATORIAMENTE estar encapsulado por UM único par de asteriscos (*), do início ao fim (marcação markdown para negrito).
- O início deve ter SEMPRE O EMOJI 📲, o nome do autor/veículo, dois pontos (:), e o texto do resumo. Nunca use outro emoji.
- O resumo DEVE SEMPRE começar imediatamente após os dois pontos identificando o tipo/categoria do publicador seguido da ação no passado (ex: "O jornal noticiou que...", "O portal publicou que...", "O analista destacou que..."). NUNCA pule esta identificação.
- Menção a figuras públicas ou executivos: na 1ª citação mencione cargo e nome completo; depois use apenas o sobrenome.
- Resumo fluido e direto (aproximadamente 280 a 420 caracteres), sempre no tempo verbal passado.
- Sem hashtags, sem links e sem quebras de linha adicionais.

""" + BLOCO_TEMAS

PROMPT_SISTEMA_MULTI = """Agir como um redator de notícias concisas, mantendo um tom jornalístico, direto e neutro.

1. Estrutura Padrão do Texto:
Você receberá dados de MÚLTIPLAS publicações de diferentes autores/veículos sobre o mesmo assunto.
Você deve retornar APENAS um parágrafo com a seguinte estrutura exata:
*<Emoji> <Autor1 e Autor2>: <Identificação dos autores> <ação no passado> que <resumo COMBINADO substantivo citando e detalhando os temas monitorados>*

Exemplo exato do padrão esperado:
*📲 Valor Econômico e Bloomberg: Os veículos noticiaram que a diretoria da Empresa Exemplo anunciou recorde de faturamento no trimestre. Os relatórios destacaram o crescimento das operações internacionais e a redução no custo logístico impulsionada por novos acordos de distribuição.*

2. Regras Rigorosas de Redação e Estrutura:
- O texto inteiro DEVE OBRIGATORIAMENTE estar encapsulado por UM único par de asteriscos (*), do início ao fim.
- O início deve ter SEMPRE O EMOJI 📲, os nomes de TODOS os autores separados por vírgula (e "e" antes do último), dois pontos (:).
- O resumo deve combinar as informações de todas as fontes de maneira coesa (aproximadamente 320 a 480 caracteres).
- Tempo verbal: Sempre no passado.

""" + BLOCO_TEMAS

def inicializar_gemini():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("AVISO: Variável de ambiente GEMINI_API_KEY não encontrada!")
        return False
    genai.configure(api_key=api_key)
    return True

def baixar_audio_e_metadados(url, baixar_audio=True):
    """
    Extrai metadados (autor, canal, conteúdo) e, opcionalmente, o áudio em mp3 via yt-dlp.
    Tenta anônimo e depois com os cookies do perfil do Firefox (necessário em alguns posts do Facebook).
    Chrome/Edge não são tentados: no Windows sempre falham (banco de cookies bloqueado / DPAPI).
    """
    print(f"[{url}] Buscando metadados{' e áudio' if baixar_audio else ''} via yt-dlp...")
    arquivo_base = os.path.join(tempfile.gettempdir(), f"temp_audio_{uuid.uuid4().hex[:8]}")

    ffmpeg_bin = shutil.which("ffmpeg") or os.path.join(diretorio_script, "ffmpeg" if os.name != "nt" else "ffmpeg.exe")
    ffmpeg_dir = os.path.dirname(ffmpeg_bin) if os.path.exists(ffmpeg_bin) else diretorio_script

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'noprogress': True,  # 'quiet' sozinho não esconde a barra de download
        'ffmpeg_location': ffmpeg_dir,
        'outtmpl': f'{arquivo_base}.%(ext)s',
    }
    if baixar_audio:
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '64',
        }]

    metadados = {"autor": "Desconhecido", "canal": "Rede Social", "conteudo": ""}

    for cookies in (None, ('firefox', PERFIL_CAMINHO)):
        opts = dict(ydl_opts)
        if cookies:
            opts['cookiesfrombrowser'] = cookies
        modo = "com cookies do Firefox" if cookies else "anônimo"
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                # process=False quando só queremos metadados: evita seleção de formato desnecessária
                info = ydl.extract_info(url, download=baixar_audio, process=baixar_audio)
            metadados["autor"] = _limpar_autor(info.get("uploader") or info.get("channel")) or "Desconhecido"
            metadados["canal"] = info.get("extractor_key") or "Rede Social"
            metadados["conteudo"] = info.get("description") or info.get("title") or ""
            print(f"  [yt-dlp] OK ({modo})")
            break
        except Exception as e:
            msg = str(e)
            if any(erro in msg for erro in ERROS_DEFINITIVOS_YTDLP):
                print("  [yt-dlp] Publicação sem vídeo/áudio extraível. Seguindo sem áudio.")
                break
            print(f"  [yt-dlp] Falha ({modo}): {msg.splitlines()[0][:150]}")

    # Fica só com o mp3; remove restos (.part, vídeo intermediário)
    arquivo_final = None
    for caminho in glob.glob(f"{arquivo_base}*"):
        if caminho.endswith(".mp3") and arquivo_final is None:
            arquivo_final = caminho
        else:
            try:
                os.remove(caminho)
            except OSError:
                pass

    return arquivo_final, metadados


# Erros do yt-dlp que indicam que não há mídia: tentar de novo com cookies não adianta
ERROS_DEFINITIVOS_YTDLP = (
    "There is no video in this post",
    "No video formats found",
    "No video could be found",
    "Unsupported URL",
)

# Limite da API do Gemini (modelos 2.5+) de vídeos do YouTube por requisição
MAX_VIDEOS_YOUTUBE_POR_REQUISICAO = 10


def _limpar_autor(autor):
    """O yt-dlp às vezes devolve entidades HTML e, no Facebook, o título inteiro ('52 reactions · ... | Página')."""
    if not autor:
        return autor
    autor = html.unescape(autor).strip()
    if " | " in autor and len(autor) > 60:
        autor = autor.rsplit(" | ", 1)[-1].strip()
    return autor


def eh_youtube(url):
    u = url.lower()
    return "youtube.com" in u or "youtu.be" in u


def parte_video_youtube(url):
    """Parte de conteúdo que faz o Gemini assistir ao vídeo público do YouTube (imagem + áudio) direto pela URL."""
    return genai.protos.Part(file_data=genai.protos.FileData(file_uri=url, mime_type="video/*"))


def _uri_youtube(item):
    """Retorna a URL se o item for uma parte de vídeo do YouTube; senão None."""
    if isinstance(item, genai.protos.Part) and eh_youtube(item.file_data.file_uri or ""):
        return item.file_data.file_uri
    return None


def _upload(caminho, arquivos_gemini):
    arquivo = genai.upload_file(path=caminho)
    arquivos_gemini.append(arquivo)
    return arquivo


def _limpar_arquivos(arquivos_gemini, arquivos_temp):
    for arquivo in arquivos_gemini:
        try:
            genai.delete_file(arquivo.name)
        except Exception:
            pass
    for caminho in arquivos_temp:
        try:
            if caminho and os.path.exists(caminho):
                os.remove(caminho)
        except OSError:
            pass


def _gerar_texto(model, conteudos, arquivos_gemini, arquivos_temp):
    """
    Chama o Gemini. Se a chamada falhar com vídeo do YouTube (não listado, privado, cota diária...),
    troca cada vídeo pelo áudio baixado via yt-dlp e tenta mais uma vez.
    """
    try:
        return model.generate_content(conteudos).text
    except Exception as e:
        if not any(_uri_youtube(item) for item in conteudos):
            raise
        print(f"  [YouTube] Gemini não conseguiu assistir ao vídeo ({str(e)[:150]}). Usando áudio via yt-dlp...")

    novos_conteudos = []
    for item in conteudos:
        uri = _uri_youtube(item)
        if not uri:
            novos_conteudos.append(item)
            continue
        arquivo_audio, _ = baixar_audio_e_metadados(uri)
        if arquivo_audio:
            arquivos_temp.append(arquivo_audio)
            try:
                novos_conteudos.append(_upload(arquivo_audio, arquivos_gemini))
            except Exception as e:
                print(f"Erro ao enviar áudio para o Gemini: {e}")
    return model.generate_content(novos_conteudos).text

def baixar_imagem(url_imagem):
    """Baixa uma imagem de uma URL e retorna o caminho do arquivo temporário."""
    if not url_imagem:
        return None
    try:
        print(f"Baixando imagem da publicação...")
        response = requests.get(url_imagem, timeout=30)
        response.raise_for_status()

        # Detecta extensão pelo content-type
        content_type = response.headers.get("Content-Type", "image/jpeg")
        ext = ".jpg"
        if "png" in content_type:
            ext = ".png"
        elif "webp" in content_type:
            ext = ".webp"

        arquivo_temp = tempfile.NamedTemporaryFile(
            delete=False, suffix=ext, prefix="temp_img_"
        )
        arquivo_temp.write(response.content)
        arquivo_temp.close()
        print(f"Imagem baixada: {arquivo_temp.name}")
        return arquivo_temp.name
    except Exception as e:
        print(f"Aviso: Não foi possível baixar a imagem: {e}")
        return None


def formatar_lista_autores(lista_autores):
    """Formata lista de autores para 'Autor1, Autor2 e Autor3'."""
    autores_limpos = []
    for a in lista_autores:
        if a and isinstance(a, str):
            nome = a.strip()
            if nome and nome.lower() != "desconhecido" and nome not in autores_limpos:
                autores_limpos.append(nome)
    if not autores_limpos:
        return "Veículo"
    if len(autores_limpos) == 1:
        return autores_limpos[0]
    if len(autores_limpos) == 2:
        return f"{autores_limpos[0]} e {autores_limpos[1]}"
    return f"{', '.join(autores_limpos[:-1])} e {autores_limpos[-1]}"


def sanitizar_resumo(texto, autores_esperados="", multi=False):
    """
    Garante rigorosamente a conformidade com a estrutura:
    *📲 <Autor(es)>: <Identificação dos autores> <ação> que <resumo>*
    """
    if not texto or not isinstance(texto, str):
        return texto

    texto = texto.strip()
    if texto.startswith("Erro na API") or texto.startswith("Erro:"):
        return texto

    # Remove blocos markdown ``` ou ```markdown se houver
    texto = re.sub(r'^```(?:markdown)?\s*', '', texto, flags=re.IGNORECASE)
    texto = re.sub(r'\s*```$', '', texto)
    texto = " ".join(texto.split()).strip('\'"')

    # Isola o miolo removendo asteriscos externos
    miolo = texto.strip('*').strip()

    # Remove emoji 📲 inicial se presente no miolo para parsear com segurança
    if miolo.startswith("📲"):
        miolo = miolo[1:].strip()

    if ":" in miolo:
        partes = miolo.split(":", 1)
        autores_texto = partes[0].strip()
        corpo_texto = partes[1].strip()
    else:
        autores_texto = autores_esperados if autores_esperados else ("Os autores" if multi else "O autor")
        corpo_texto = miolo

    # Se a IA omitiu autores ou usou autor único quando eram múltiplos esperados
    if autores_esperados:
        if not autores_texto or autores_texto.lower() in ["autores", "autor", "desconhecido"]:
            autores_texto = autores_esperados
        elif multi and (" e " not in autores_texto and "," not in autores_texto and (" e " in autores_esperados or "," in autores_esperados)):
            autores_texto = autores_esperados

    # Verifica se o corpo_texto inicia com verbo ou sem identificação de quem falou
    padrao_verbo_plural = re.compile(
        r'^(publicaram|noticiaram|compartilharam|afirmaram|divulgaram|informaram|destacaram|relataram|postaram|criticaram|defenderam|comentaram)\b',
        re.IGNORECASE
    )
    padrao_verbo_singular = re.compile(
        r'^(publicou|noticiou|compartilhou|afirmou|divulgou|informou|destacou|relatou|postou|criticou|defendeu|comentou)\b',
        re.IGNORECASE
    )

    if padrao_verbo_plural.match(corpo_texto):
        primeira_letra = corpo_texto[0].lower()
        corpo_texto = f"Os autores {primeira_letra}{corpo_texto[1:]}"
    elif padrao_verbo_singular.match(corpo_texto):
        primeira_letra = corpo_texto[0].lower()
        if multi:
            corpo_texto = f"Os autores {primeira_letra}{corpo_texto[1:]}"
        else:
            corpo_texto = f"O autor {primeira_letra}{corpo_texto[1:]}"
    else:
        identificadores = [
            "os autores", "o autor", "os jornais", "o jornal", "os portais", "o portal",
            "os influenciadores", "o influenciador", "os deputados", "o deputado",
            "os senadores", "o senador", "o veículo", "os veículos", "o site", "os sites",
            "o jornalista", "os jornalistas"
        ]
        comeca_com_ident = any(corpo_texto.lower().startswith(ident) for ident in identificadores)
        if not comeca_com_ident:
            # Padrão composto: "O portal e o deputado afirmaram..."
            padrao_composto = re.compile(r'^(?:O|A|Os|As)\s+\w+\s+e\s+(?:o|a|os|as)\s+\w+', re.IGNORECASE)
            # Padrão com locução temporal inicial: "Em publicação,...", "Nesta segunda-feira, os jornais..."
            padrao_locucao = re.compile(r'^(?:(?:Em|Nesta|Neste|Segundo|De acordo com)\s+[^,]+,\s*)(os autores|o autor|os jornais|o jornal|os portais|o portal|o influenciador|o deputado)', re.IGNORECASE)

            if not padrao_composto.match(corpo_texto) and not padrao_locucao.match(corpo_texto):
                # Começou direto com o fato sem sujeito
                verbo = "publicaram" if multi else "publicou"
                sujeito = "Os autores" if multi else "O autor"
                primeira_letra = corpo_texto[0].lower() if len(corpo_texto) > 1 else corpo_texto
                resto = corpo_texto[1:] if len(corpo_texto) > 1 else ""
                corpo_texto = f"{sujeito} {verbo} que {primeira_letra}{resto}"

    # Limpa asteriscos residuais internos nos autores e corpo
    autores_texto = autores_texto.replace("*", "").strip()
    corpo_texto = corpo_texto.replace("*", "").strip()

    return f"*📲 {autores_texto}: {corpo_texto}*"


LEMBRETE_TEMAS = (
    "Cite e detalhe o(s) tema(s) monitorado(s) PRESENTE(S) no conteúdo. Se tratar da Organização ou de sua Diretoria, "
    "foque nas ações e posicionamentos relatados, sem inventar temas adicionais que não constem no material."
)


def gerar_resumo(autor, canal, url, conteudo, usar_yt_dlp_para_dados=False, imagem_url=None):
    if not inicializar_gemini():
        return "Erro: API Key do Gemini não configurada."

    youtube = eh_youtube(url)
    arquivos_gemini, arquivos_temp = [], []

    # YouTube: o Gemini assiste ao vídeo pela URL; o yt-dlp só busca metadados se faltarem.
    # Demais redes: o yt-dlp baixa o áudio (e completa metadados faltantes).
    arquivo_audio = None
    if not youtube or usar_yt_dlp_para_dados or not autor or not conteudo:
        arquivo_audio, metadados = baixar_audio_e_metadados(url, baixar_audio=not youtube)
        if arquivo_audio:
            arquivos_temp.append(arquivo_audio)
        autor = autor or metadados["autor"]
        canal = canal or metadados["canal"]
        conteudo = conteudo or metadados["conteudo"]

    autor_formatado = autor.strip() if autor and autor != "Desconhecido" else "Veículo"

    model = genai.GenerativeModel(
        'gemini-3.5-flash-lite',
        system_instruction=PROMPT_SISTEMA
    )

    prompt_usuario = f"""
Por favor, gere um resumo da seguinte publicação, seguindo RIGOROSAMENTE as regras de estilo, formatação, identificação de figuras políticas e temas monitorados:

Autor: {autor_formatado}
Canal/Rede: {canal}
Link: {url}
Descrição/Conteúdo: {conteudo}

LEMBRETE OBRIGATÓRIO:
1. Inicie exatamente com a estrutura: *📲 {autor_formatado}: <O jornal/O portal/O autor> <ação no passado> que...*
2. Porta-vozes e Executivos (OBRIGATÓRIO): Na PRIMEIRA citação, use obrigatoriamente: <Cargo> <Nome Completo>, ex: "diretor-presidente Roberto Santos", "gerente de sustentabilidade Ana Souza". Em menções seguintes, use apenas o sobrenome (ex: "Santos", "Souza").
3. Ênfase e Detalhamento: Detalhe concretamente o que foi dito, pedido, proposto ou articulado sobre o assunto monitorado, evitando citações vagas como "mencionou articulações sobre o tema".
4. {LEMBRETE_TEMAS}
5. O texto inteiro DEVE estar encapsulado por asteriscos (*), formando um parágrafo único (1 a 2 frases articuladas, aprox. 280 a 420 caracteres).
"""

    conteudos_envio = [prompt_usuario]

    if youtube:
        print("Enviando o link do YouTube para o Gemini assistir ao vídeo...")
        conteudos_envio.append("Assista ao vídeo da publicação e considere o que é dito e mostrado nele:")
        conteudos_envio.append(parte_video_youtube(url))
    elif arquivo_audio:
        print("Fazendo upload do áudio para a API do Gemini...")
        try:
            arquivo_gemini = _upload(arquivo_audio, arquivos_gemini)
            conteudos_envio.append("Considere também o áudio extraído da publicação:")
            conteudos_envio.append(arquivo_gemini)
        except Exception as e:
            print(f"Erro ao enviar áudio para o Gemini: {e}")

    # Imagem da publicação (no YouTube é só a thumbnail, redundante com o vídeo)
    if not youtube:
        arquivo_imagem = baixar_imagem(imagem_url)
        if arquivo_imagem:
            arquivos_temp.append(arquivo_imagem)
            print("Fazendo upload da imagem para a API do Gemini...")
            try:
                arquivo_gemini_img = _upload(arquivo_imagem, arquivos_gemini)
                conteudos_envio.append("Considere também a imagem da publicação:")
                conteudos_envio.append(arquivo_gemini_img)
            except Exception as e:
                print(f"Erro ao enviar imagem para o Gemini: {e}")

    print("Gerando resumo com Gemini...")
    try:
        texto_resumo = _gerar_texto(model, conteudos_envio, arquivos_gemini, arquivos_temp)
        texto_resumo = sanitizar_resumo(texto_resumo, autores_esperados=autor_formatado, multi=False)
        print("Resumo gerado com sucesso!")
    except Exception as e:
        texto_resumo = f"Erro na API do Gemini ao gerar o resumo: {e}"
    finally:
        _limpar_arquivos(arquivos_gemini, arquivos_temp)

    return texto_resumo


def gerar_resumo_multi(lista_dados, urls):
    """Gera um resumo único combinado para múltiplas publicações."""
    if not inicializar_gemini():
        return "Erro: API Key do Gemini não configurada."

    model = genai.GenerativeModel(
        'gemini-3.5-flash-lite',
        system_instruction=PROMPT_SISTEMA_MULTI
    )
    arquivos_temp = []
    arquivos_gemini = []
    conteudos_envio = []
    videos_youtube = 0

    for i, (dados, url) in enumerate(zip(lista_dados, urls), 1):
        # Vídeos do YouTube vão direto para o Gemini (até o limite por requisição); o resto via áudio do yt-dlp
        youtube = eh_youtube(url) and videos_youtube < MAX_VIDEOS_YOUTUBE_POR_REQUISICAO
        faltam_metadados = (not dados.get("autor") or dados["autor"] == "Desconhecido"
                            or not dados.get("conteudo"))

        arquivo_audio = None
        if not youtube or faltam_metadados:
            arquivo_audio, metadados_yt = baixar_audio_e_metadados(url, baixar_audio=not youtube)
            if arquivo_audio:
                arquivos_temp.append(arquivo_audio)
            if not dados.get("autor") or dados["autor"] == "Desconhecido":
                dados["autor"] = metadados_yt.get("autor", "Desconhecido")
            if not dados.get("conteudo"):
                dados["conteudo"] = metadados_yt.get("conteudo", "")

        if youtube:
            videos_youtube += 1
            conteudos_envio.append(f"Vídeo da publicação {i} ({url}):")
            conteudos_envio.append(parte_video_youtube(url))
        elif arquivo_audio:
            try:
                arq_gemini = _upload(arquivo_audio, arquivos_gemini)
                conteudos_envio.append(f"Áudio da publicação {i} ({url}):")
                conteudos_envio.append(arq_gemini)
            except Exception as e:
                print(f"Erro ao enviar áudio {i} para o Gemini: {e}")

        imagem_url = dados.get("imagem_url")
        if imagem_url and not youtube:
            arquivo_imagem = baixar_imagem(imagem_url)
            if arquivo_imagem:
                arquivos_temp.append(arquivo_imagem)
                try:
                    arq_gemini_img = _upload(arquivo_imagem, arquivos_gemini)
                    conteudos_envio.append(f"Imagem da publicação {i}:")
                    conteudos_envio.append(arq_gemini_img)
                except Exception as e:
                    print(f"Erro ao enviar imagem {i} para o Gemini: {e}")

    lista_autores = [d.get("autor") for d in lista_dados if d.get("autor")]
    autores_formatados = formatar_lista_autores(lista_autores)

    # Monta prompt combinado
    bloco = ""
    for i, (dados, url) in enumerate(zip(lista_dados, urls), 1):
        bloco += f"\n--- Publicação {i} ---\n"
        bloco += f"Autor: {dados.get('autor', 'Desconhecido')}\n"
        bloco += f"Canal/Rede: {dados.get('canal', 'Rede Social')}\n"
        bloco += f"Link: {url}\n"
        bloco += f"Descrição/Conteúdo: {dados.get('conteudo', '')}\n"

    prompt_usuario = f"""
Por favor, gere um ÚNICO resumo combinado das seguintes publicações, seguindo RIGOROSAMENTE as regras de estilo, formatação, identificação de figuras políticas e temas monitorados:
{bloco}

LEMBRETE OBRIGATÓRIO DE ESTRUTURA E TEMAS:
1. Inicie exatamente com a estrutura:
*📲 {autores_formatados}: <Os autores/Os jornais/Os portais> <ação no passado> que...*
(Atenção: A primeira frase após os dois pontos DEVE identificar os autores, ex: 'Os autores publicaram que...', 'Os jornais noticiaram que...').
2. Porta-vozes e Executivos (OBRIGATÓRIO): Na PRIMEIRA citação, use obrigatoriamente: <Cargo> <Nome Completo>, ex: "diretor-presidente Roberto Santos", "gerente de sustentabilidade Ana Souza". Em menções seguintes, use apenas o sobrenome (ex: "Santos", "Souza").
3. Ênfase e Detalhamento: Detalhe concretamente o que foi dito, pedido, proposto ou articulado sobre o assunto monitorado em todas as publicações combinadas, evitando citações vagas ou genéricas.
4. {LEMBRETE_TEMAS}
5. O texto inteiro DEVE estar encapsulado por asteriscos (*), formando um parágrafo único (1 a 2 frases articuladas, aprox. 320 a 480 caracteres), sem quebras de linha.
"""
    conteudos_envio.append(prompt_usuario)

    print("Gerando resumo combinado com Gemini...")
    try:
        texto_resumo = _gerar_texto(model, conteudos_envio, arquivos_gemini, arquivos_temp)
        texto_resumo = sanitizar_resumo(texto_resumo, autores_esperados=autores_formatados, multi=True)
        print("Resumo combinado gerado com sucesso!")
    except Exception as e:
        texto_resumo = f"Erro na API do Gemini ao gerar o resumo: {e}"
    finally:
        _limpar_arquivos(arquivos_gemini, arquivos_temp)

    return texto_resumo


import json

def buscar_dados_ronda(url):
    arquivo = "cards_coletados.json"
    if os.path.exists(arquivo):
        try:
            with open(arquivo, "r", encoding="utf-8") as f:
                dados = json.load(f)
                return dados.get(url)
        except Exception as e:
            print(f"Erro ao ler banco de cards: {e}")
    return None


# ==========================================
# SCRAPING VIA PLAYWRIGHT (FALLBACK)
# ==========================================

def detectar_plataforma(url):
    """Identifica a rede social a partir da URL."""
    u = url.lower()
    if "twitter.com" in u or "x.com" in u:
        return "twitter"
    if "instagram.com" in u:
        return "instagram"
    if "facebook.com" in u or "fb.com" in u or "fb.watch" in u:
        return "facebook"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    return "desconhecido"


# ── Comportamento humano (anti-detecção) ──────────────────────────────────────
import random

async def espera_humana(min_seg=3, max_seg=7):
    await asyncio.sleep(random.uniform(min_seg, max_seg))

async def pausa_aleatoria():
    """Pausa curta com ~25% de chance de uma pausa extra (simula leitura/distração)."""
    await asyncio.sleep(random.uniform(0.6, 2.0))
    if random.random() < 0.25:
        await asyncio.sleep(random.uniform(2.5, 5.5))

async def simular_scroll(page):
    for _ in range(random.randint(1, 3)):
        await page.mouse.wheel(0, random.randint(150, 400))
        await pausa_aleatoria()

async def simular_movimento_mouse(page):
    try:
        vp = page.viewport_size
        if vp:
            width, height = vp['width'], vp['height']
            for _ in range(random.randint(2, 4)):
                x = random.randint(60, width - 60)
                y = random.randint(60, height - 60)
                await page.mouse.move(x, y, steps=random.randint(10, 22))
                await asyncio.sleep(random.uniform(0.3, 0.9))
    except Exception:
        pass


async def _extrair_dados_pagina(page, url):
    """Navega até a URL e extrai autor, conteúdo e imagem via JS simplificado."""
    plataforma = detectar_plataforma(url)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
    except Exception as e:
        print(f"    [Playwright] Aviso no carregamento ({e}). Prosseguindo com DOM atual...")
    await espera_humana(2, 4)
    await simular_movimento_mouse(page)

    if plataforma == "twitter":
        try:
            await page.wait_for_selector('article[data-testid="tweet"]', timeout=15000)
        except:
            pass
        await simular_scroll(page)
        return await page.evaluate("""() => {
            const tweet = document.querySelector('article[data-testid="tweet"]');
            if (!tweet) return null;
            let autor = '';
            const userBlock = tweet.querySelector('[data-testid="User-Name"]');
            if (userBlock) {
                const link = userBlock.querySelector('a[href^="/"]');
                if (link) {
                    const span = link.querySelector('span span');
                    autor = span ? span.innerText.trim() : '';
                }
            }
            const textEl = tweet.querySelector('[data-testid="tweetText"]');
            const conteudo = textEl ? textEl.innerText.trim() : '';
            let imagem_url = '';
            const video = tweet.querySelector('video');
            if (video && video.getAttribute('poster')) {
                imagem_url = video.getAttribute('poster');
            } else {
                const img = tweet.querySelector('[data-testid="tweetPhoto"] img');
                if (img && img.src) imagem_url = img.src;
            }
            return { autor, conteudo, imagem_url, canal: 'twitter' };
        }""")

    elif plataforma == "facebook":
        await simular_scroll(page)
        return await page.evaluate("""() => {
            const getMeta = (p) => {
                const el = document.querySelector('meta[property="' + p + '"]');
                return el ? el.getAttribute('content') : '';
            };
            let autor = '';
            const links = document.querySelectorAll('b a[href], strong a[href], h2 a[href], h3 a[href]');
            if (links.length > 0) {
                autor = links[0].innerText.trim();
            } else {
                const pn = document.querySelector('[data-ad-rendering-role="profile_name"]');
                if (pn) {
                    const btn = pn.closest('[role="button"]') || pn.parentElement;
                    if (btn) autor = btn.innerText.trim();
                }
            }
            if (!autor) {
                const reelAuthor = document.querySelector('a[role="link"][tabindex="0"][target="_blank"]');
                if (reelAuthor) autor = reelAuthor.innerText.trim();
            }
            let conteudo = '';
            const sm = document.querySelector('[data-ad-rendering-role="story_message"]');
            if (sm) {
                conteudo = sm.innerText.trim();
            } else {
                conteudo = getMeta('og:description') || '';
            }
            if (!conteudo) {
                const dirs = Array.from(document.querySelectorAll('[dir="auto"]'));
                const folhas = dirs.filter(el => el.querySelectorAll('[dir="auto"]').length === 0);
                const textos = folhas.map(el => el.innerText.trim()).filter(t => t.length > 10 && t.length < 2000);
                if (textos.length > 0) conteudo = textos[0];
            }
            let imagem_url = '';
            const vid = document.querySelector('video');
            if (vid && vid.getAttribute('poster')) {
                imagem_url = vid.getAttribute('poster');
            } else {
                const img = document.querySelector('img[data-imgperflogname="feedImage"]');
                if (img) imagem_url = img.src || '';
            }
            return { autor, conteudo, imagem_url, canal: 'facebook' };
        }""")

    elif plataforma == "youtube":
        try:
            if "shorts" in url.lower():
                await page.wait_for_selector('ytd-reel-video-renderer[is-active]', timeout=15000)
            else:
                await page.wait_for_selector('ytd-watch-metadata', timeout=15000)
                try:
                    await page.click('#description-inline-expander, ytd-text-inline-expander', timeout=2000)
                except:
                    pass
        except:
            pass
        if "shorts" not in url.lower():
            await simular_scroll(page)
        return await page.evaluate("""() => {
            const getMeta = (p) => {
                const el = document.querySelector('meta[property="' + p + '"], meta[name="' + p + '"], meta[itemprop="' + p + '"]');
                return el ? el.getAttribute('content') : '';
            };
            let autor = '';
            const ownerEl = document.querySelector('ytd-video-owner-renderer ytd-channel-name a, ytd-reel-channel-bar-view-model a');
            if (ownerEl) autor = ownerEl.innerText.trim();
            else {
                const link = document.querySelector('a[href^="/@"]');
                if (link) autor = link.innerText.trim();
            }
            if (!autor) autor = getMeta('author') || '';
            let conteudo = '';
            const descEl = document.querySelector('#description-inline-expander > #expanded, ytd-text-inline-expander > #expanded');
            if (descEl) {
                conteudo = descEl.innerText.trim().replace(/Mostrar menos$/i, '').trim();
            } else {
                const sd = document.querySelector('ytd-reel-player-overlay-renderer .ytAttributedStringHost[dir="auto"], h2.ytd-reel-player-header-renderer');
                if (sd) conteudo = sd.innerText.trim();
                else conteudo = getMeta('og:description') || getMeta('description') || '';
            }
            const imagem_url = getMeta('og:image') || '';
            return { autor, conteudo, imagem_url, canal: 'youtube' };
        }""")

    elif plataforma == "instagram":
        try:
            await page.wait_for_selector('time, article, main', timeout=8000)
        except Exception:
            pass
        await simular_scroll(page)
        return await page.evaluate(r"""() => {
            const getMeta = (p) => {
                const el = document.querySelector('meta[property="' + p + '"], meta[name="' + p + '"]');
                return el ? el.getAttribute('content') : '';
            };
            let autor = '';
            const titulo = getMeta('og:title') || '';
            const m = titulo.match(/^(.+?)\s+on Instagram/i);
            if (m) autor = m[1];
            if (!autor) {
                // Tenta extrair de headers ou do link de perfil visível
                const h = document.querySelector('header h2, header h1, h2, h1');
                if (h) autor = h.innerText.trim();
            }
            const conteudo = getMeta('og:description') || getMeta('description') || '';
            if (!autor && conteudo) {
                // Padrão do Instagram em português/inglês: "293 likes, 14 comments - roberto.psi on October..." ou "... - roberto.psi no October..."
                const mDesc = conteudo.match(/-\s*([a-zA-Z0-9._]+)\s+(?:no|on|em)\s+[A-Za-z]/i);
                if (mDesc) autor = mDesc[1].trim();
            }
            let imagem_url = getMeta('og:image') || '';
            const vid = document.querySelector('video');
            if (vid && vid.getAttribute('poster')) {
                imagem_url = vid.getAttribute('poster');
            } else if (!imagem_url) {
                const imgs = Array.from(document.querySelectorAll('img')).filter(i =>
                    i.src && i.src.includes('scontent') && !i.src.includes('/150x150/')
                );
                if (imgs.length > 0) imagem_url = imgs[0].src;
            }
            return { autor, conteudo, imagem_url, canal: 'instagram' };
        }""")

    return None


async def _scrape_urls_async(urls):
    """Abre Firefox com perfil persistente, visita cada URL e extrai dados."""
    from playwright.async_api import async_playwright
    resultados = {}
    try:
        async with async_playwright() as p:
            context = await p.firefox.launch_persistent_context(
                PERFIL_CAMINHO,
                headless=False,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
                no_viewport=True,
                args=["--allow-downgrade"],
            )
            page = await context.new_page()
            for url in urls:
                try:
                    print(f"  [Scraping] Abrindo {url}...")
                    # Timeout estrito de 35s por URL: se o Playwright travar, não bloqueia o fluxo
                    dados = await asyncio.wait_for(_extrair_dados_pagina(page, url), timeout=35.0)
                    if dados and (dados.get("autor") or dados.get("conteudo")):
                        resultados[url] = dados
                        print(f"  [Scraping] ✅ Dados extraídos: autor={dados.get('autor', '?')}")
                    else:
                        print(f"  [Scraping] ⚠️ Nenhum dado útil extraído.")
                except asyncio.TimeoutError:
                    print(f"  [Scraping] ⏱️ Timeout atingido (35s) para {url}. Passando para fallback...")
                except Exception as e:
                    print(f"  [Scraping] ❌ Erro: {e}")
            await context.close()
    except Exception as e:
        print(f"  [Scraping] ❌ Erro ao abrir navegador: {e}")
    return resultados


def scrape_publicacoes(urls):
    """Wrapper síncrono para scraping via Playwright."""
    return asyncio.run(_scrape_urls_async(urls))


# ==========================================
# INTEGRAÇÃO TELEGRAM (BOT)
# ==========================================
import telebot

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "XXXXX_SEU_TOKEN_TELEGRAM_AQUI_XXXXX")
bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "👋 Olá! Sou o Gerador de Resumos.\nEnvie o link (ou múltiplos links) de uma publicação para eu analisá-la.")

@bot.message_handler(func=lambda message: True)
def processar_link(message):
    texto = message.text.strip()
    urls = re.findall(r'https?://\S+', texto)

    if not urls:
        bot.reply_to(message, "Por favor, envie um link válido (começando com http:// ou https://).")
        return

    # Remove duplicatas mantendo a ordem
    urls = list(dict.fromkeys(urls))

    print(f"\n[Telegram] {len(urls)} link(s) recebido(s): {urls}")
    msg_status = bot.reply_to(message, "⏳ Iniciando processamento... Buscando dados da(s) publicação(ões).")

    try:
        # Coleta dados de cada URL
        lista_dados = []
        urls_sem_dados = []

        for url in urls:
            dados_salvos = buscar_dados_ronda(url)
            if dados_salvos:
                print(f"  [Banco] ✅ Dados encontrados para {url}")
                lista_dados.append({"url": url, "dados": dados_salvos, "fonte": "banco"})
            else:
                urls_sem_dados.append(url)

        # Scraping para URLs não encontradas no banco
        if urls_sem_dados:
            bot.edit_message_text(
                f"⚠️ {len(urls_sem_dados)} link(s) sem dados no banco. Abrindo navegador para scraping...",
                chat_id=message.chat.id, message_id=msg_status.message_id
            )
            dados_scraping = scrape_publicacoes(urls_sem_dados)

            for url in urls_sem_dados:
                if url in dados_scraping:
                    print(f"  [Scraping] ✅ Dados extraídos para {url}")
                    lista_dados.append({"url": url, "dados": dados_scraping[url], "fonte": "scraping"})
                else:
                    print(f"  [Fallback] yt-dlp será usado para {url}")
                    lista_dados.append({
                        "url": url,
                        "dados": {"autor": "", "canal": "", "conteudo": "", "imagem_url": None},
                        "fonte": "yt-dlp"
                    })

        # Ordena pela ordem original dos URLs
        lista_dados.sort(key=lambda x: urls.index(x["url"]))

        if len(urls) == 1:
            # ── Fluxo de link único (mantém comportamento original) ──
            item = lista_dados[0]
            url = item["url"]
            dados = item["dados"]
            fonte = item["fonte"]

            if fonte == "banco":
                bot.edit_message_text("✅ Dados encontrados no banco! Gerando resumo com a IA...",
                                      chat_id=message.chat.id, message_id=msg_status.message_id)
                resultado = gerar_resumo(
                    autor=dados["autor"],
                    canal=dados["canal"],
                    url=url,
                    conteudo=dados["conteudo"],
                    usar_yt_dlp_para_dados=False,
                    imagem_url=dados.get("imagem_url")
                )
            elif fonte == "scraping":
                bot.edit_message_text("🔍 Dados extraídos via scraping! Gerando resumo com a IA...",
                                      chat_id=message.chat.id, message_id=msg_status.message_id)
                resultado = gerar_resumo(
                    autor=dados.get("autor", ""),
                    canal=dados.get("canal", ""),
                    url=url,
                    conteudo=dados.get("conteudo", ""),
                    usar_yt_dlp_para_dados=False,
                    imagem_url=dados.get("imagem_url")
                )
            else:  # yt-dlp
                bot.edit_message_text(
                    "⚠️ Dados não encontrados.\nBaixando áudio/conteúdo (isso pode levar um minuto) e gerando resumo...",
                    chat_id=message.chat.id, message_id=msg_status.message_id
                )
                resultado = gerar_resumo(
                    autor="",
                    canal="",
                    url=url,
                    conteudo="",
                    usar_yt_dlp_para_dados=True
                )

            bot.send_message(message.chat.id, "🚀 RESUMO GERADO:")
            mensagem_final = f"{resultado.strip()}\n{url}"
            bot.send_message(message.chat.id, mensagem_final)

        else:
            # ── Fluxo de múltiplos links ──
            bot.edit_message_text(
                f"📝 Coletando dados de {len(urls)} publicações para resumo combinado...",
                chat_id=message.chat.id, message_id=msg_status.message_id
            )

            dados_para_resumo = []
            for item in lista_dados:
                dados = item["dados"]
                dados_para_resumo.append({
                    "autor": dados.get("autor", "Desconhecido"),
                    "canal": dados.get("canal", "Rede Social"),
                    "conteudo": dados.get("conteudo", ""),
                    "imagem_url": dados.get("imagem_url")
                })

            bot.edit_message_text(
                "🤖 Gerando resumo combinado com a IA...",
                chat_id=message.chat.id, message_id=msg_status.message_id
            )
            resultado = gerar_resumo_multi(dados_para_resumo, urls)

            bot.send_message(message.chat.id, "🚀 RESUMO GERADO:")
            links_texto = "\n".join(urls)
            mensagem_final = f"{resultado.strip()}\n{links_texto}"
            bot.send_message(message.chat.id, mensagem_final)

        print("[Telegram] Resumo enviado com sucesso!")

    except Exception as e:
        erro = f"❌ Erro ao processar: {str(e)}"
        print(f"[Telegram] {erro}")
        bot.send_message(message.chat.id, erro)

if __name__ == "__main__":
    print("=== Bot do Telegram Iniciado ===")
    print("Aguardando mensagens (pressione Ctrl+C para encerrar)...")
    try:
        bot.infinity_polling()
    except KeyboardInterrupt:
        print("\nEncerrando bot...")
