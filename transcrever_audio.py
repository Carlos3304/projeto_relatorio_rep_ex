import os
import sys
import tempfile
import argparse
from dotenv import load_dotenv
from google import genai
from google.genai import types
import yt_dlp

# Garante saída UTF-8 no Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PASTA_ATUAL = os.path.dirname(os.path.abspath(__file__))
PASTA_RAIZ = os.path.dirname(PASTA_ATUAL)

# Carrega chave da API do Gemini
for env_path in [os.path.join(PASTA_ATUAL, ".env"), os.path.join(PASTA_RAIZ, ".env")]:
    if os.path.exists(env_path):
        load_dotenv(env_path, override=True)
        break

api_key = os.getenv("GEMINI_API_KEY", "XXXXX_GEMINI_API_KEY_XXXXX")

# Localiza ffmpeg se disponível na pasta ou padrão
ffmpeg_path = os.path.join(PASTA_ATUAL, "ffmpeg.exe")
if not os.path.exists(ffmpeg_path):
    ffmpeg_path = os.path.join(PASTA_RAIZ, "outros_scripts", "yt-dlp", "ffmpeg.exe")
if not os.path.exists(ffmpeg_path):
    ffmpeg_path = None


def baixar_audio(url: str, pasta_destino: str) -> str:
    """Baixa o áudio de uma URL usando yt-dlp e converte para MP3."""
    out_template = os.path.join(pasta_destino, "%(id)s.%(ext)s")
    
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "quiet": True,
        "no_warnings": True,
    }
    if ffmpeg_path:
        ydl_opts["ffmpeg_location"] = ffmpeg_path

    print(f"⬇️  Baixando áudio de: {url}...")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        video_id = info.get("id", "audio")
    
    mp3_file = os.path.join(pasta_destino, f"{video_id}.mp3")
    if not os.path.exists(mp3_file):
        # Procura qualquer mp3 gerado na pasta
        for f in os.listdir(pasta_destino):
            if f.endswith(".mp3"):
                return os.path.join(pasta_destino, f)
        raise FileNotFoundError("❌ Arquivo MP3 não encontrado após o download.")
    return mp3_file


def transcrever_audio(caminho_audio: str) -> str:
    """Envia o áudio para o Gemini e retorna a transcrição."""
    print("🤖 Enviando áudio para transcrição no Gemini...")
    client = genai.Client(api_key=api_key)
    
    # Upload do arquivo para a API do Gemini
    arquivo_gemini = client.files.upload(file=caminho_audio)
    
    prompt = (
        "Transcreva com máxima precisão e fidelidade todo o áudio fornecido em português. "
        "Não adicione comentários, introduções ou explicações. Retorne exclusivamente o texto falado."
    )
    
    modelo = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    resposta = client.models.generate_content(
        model=modelo,
        contents=[arquivo_gemini, prompt]
    )
    
    # Limpa o arquivo no Gemini após transcrição
    try:
        client.files.delete(name=arquivo_gemini.name)
    except Exception:
        pass
        
    return resposta.text.strip() if resposta.text else ""


def extrair_e_transcrever(url: str, salvar_arquivo: bool = True) -> str:
    """Função principal: extrai áudio e transcreve."""
    with tempfile.TemporaryDirectory() as temp_dir:
        caminho_mp3 = baixar_audio(url, temp_dir)
        texto = transcrever_audio(caminho_mp3)
        
    print("\n" + "=" * 50)
    print("📝 TRANSCRIÇÃO:")
    print("=" * 50)
    print(texto)
    print("=" * 50 + "\n")
    
    if salvar_arquivo:
        arquivo_saida = os.path.join(PASTA_ATUAL, "ultima_transcricao.txt")
        with open(arquivo_saida, "w", encoding="utf-8") as f:
            f.write(texto)
        print(f"💾 Transcrição salva em: {arquivo_saida}")
        
    return texto


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extrai e transcreve áudio de redes sociais com yt-dlp e Gemini.")
    parser.add_argument("url", nargs="?", help="URL da publicação (Instagram, TikTok, YouTube, etc.)")
    args = parser.parse_args()
    
    url_alvo = args.url
    if not url_alvo:
        url_alvo = input("🔗 Cole a URL da publicação: ").strip()
        
    if not url_alvo:
        print("❌ URL inválida.")
        sys.exit(1)
        
    extrair_e_transcrever(url_alvo)
