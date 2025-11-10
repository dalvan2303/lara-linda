import os
import re
import time
import asyncio
import subprocess
import base64
import random
import httpx
import shutil
import yt_dlp
from neonize.aioze.client import NewAClient
from neonize.aioze.events import MessageEv, ConnectedEv
from neonize.utils import build_jid
from openai import OpenAI

# --- Configurações ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
HG_WEATHER_KEY = os.getenv("HG_WEATHER_KEY")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")

if not OPENAI_API_KEY:
    raise RuntimeError("❌ OPENAI_API_KEY não definido. Configure-o nas variáveis de ambiente.")
if not NEWS_API_KEY:
    print("⚠️ AVISO: NEWS_API_KEY não definido. O comando /noticia não funcionará.")
if not HG_WEATHER_KEY:
    print("⚠️ AVISO: HG_WEATHER_KEY não definido. O comando /clima não funcionará.")

client_openai = OpenAI(api_key=OPENAI_API_KEY)

TEMP_DIR = "./temp"
DOWNLOADS_DIR = os.path.join(TEMP_DIR, "downloads")
IMAGES_DIR = os.path.join(TEMP_DIR, "images")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(IMAGES_DIR, exist_ok=True)

# --- IA / Utilitários ---
def _sync_responder_como_membro(texto_mensagem: str):
    prompt_sistema = (
        "Você é Lara, uma IA engraçada e simpática que responde de forma curta, leve e divertida em português."
    )
    try:
        response = client_openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": texto_mensagem},
            ],
            max_tokens=150,
            temperature=0.8,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"⚠️ Erro na API OpenAI: {e}")
        return "Ih, buguei aqui! 😅 Tenta de novo daqui a pouco."

def _sync_tts_gerar_audio(texto: str, chat_id: str):
    audio_path = os.path.join(TEMP_DIR, f"tts_{chat_id}_{int(time.time())}.mp3")
    try:
        with client_openai.audio.speech.with_streaming_response.create(
            model="gpt-4o-mini-tts",
            voice="alloy",
            input=texto,
        ) as response:
            response.stream_to_file(audio_path)
        return audio_path
    except Exception as e:
        print(f"⚠️ Erro no TTS: {e}")
        return None

def _sync_gerar_imagem(prompt: str) -> str:
    try:
        image_path = os.path.join(IMAGES_DIR, f"img_{int(time.time())}.png")
        result = client_openai.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            size="1024x1024",
        )
        image_base64 = result.data[0].b64_json
        with open(image_path, "wb") as f:
            f.write(base64.b64decode(image_base64))
        return image_path
    except Exception as e:
        print(f"⚠️ Erro ao gerar imagem: {e}")
        return None

# --- Funções de API ---
async def obter_clima(cidade: str):
    api_key = HG_WEATHER_KEY
    if not api_key:
        return "⚠️ A chave HG_WEATHER_KEY não está configurada."
    url = f"https://api.hgbrasil.com/weather?key={api_key}&city_name={cidade}"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10.0)
            data = resp.json()
            if data.get("results"):
                info = data["results"]
                return (
                    f"🌦️ *Clima em {info.get('city_name', cidade.title())}:*\n"
                    f"🌡️ Temperatura: {info.get('temp', 'N/A')}°C\n"
                    f"☁️ Condição: {info.get('description', 'N/A')}\n"
                    f"💧 Umidade: {info.get('humidity', 'N/A')}%\n"
                    f"💨 Vento: {info.get('wind_speedy', 'N/A')}"
                )
            return f"❌ Não encontrei dados para *{cidade}*."
    except Exception as e:
        return f"❌ Erro ao buscar clima: {e}"

async def obter_horoscopo(signo: str) -> str:
    url = f"https://horoscope-app-api.vercel.app/api/v1/get-horoscope/daily?sign={signo}&day=today"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10.0)
            data = resp.json()
            texto = data.get("data", {}).get("horoscope_data") or "❌ Horóscopo não disponível."
            return f"🔮 *Horóscopo de {signo.capitalize()}*:\n{texto}"
    except Exception as e:
        return f"❌ Erro ao buscar horóscopo: {e}"

async def obter_noticias(topico: str):
    if not NEWS_API_KEY:
        return "⚠️ NEWS_API_KEY não configurada."
    url = f"https://newsapi.org/v2/top-headlines?q={topico}&language=pt&apiKey={NEWS_API_KEY}"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10.0)
            data = resp.json()
            artigos = data.get("articles", [])[:3]
            if not artigos:
                return f"❌ Nenhuma notícia encontrada sobre *{topico}*."
            resposta = f"🗞️ *Notícias sobre {topico}:*\n\n"
            for art in artigos:
                titulo = art.get("title")
                fonte = art.get("source", {}).get("name", "")
                link = art.get("url", "")
                resposta += f"• *{titulo}* — {fonte}\n🔗 {link}\n\n"
            return resposta.strip()
    except Exception as e:
        return f"❌ Erro ao buscar notícias: {e}"

# --- Jogos e diversão ---
QUIZ_PERGUNTAS = [
    {"p": "Qual é o maior planeta do sistema solar?", "r": ["Terra", "Júpiter", "Marte", "Saturno"], "c": 1},
    {"p": "Quem pintou a Mona Lisa?", "r": ["Van Gogh", "Da Vinci", "Picasso", "Michelangelo"], "c": 1},
    {"p": "Quantos ossos tem o corpo humano adulto?", "r": ["206", "201", "210", "199"], "c": 0},
]
QUIZ_ATIVOS = {}

async def limpar_temp():
    for folder in [DOWNLOADS_DIR, IMAGES_DIR]:
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(folder, exist_ok=True)
    print("🧹 Diretórios temporários limpos.")

# --- Main ---
async def main():
    client = NewAClient("lara_bot")

    @client.event
    async def on_connected(client: NewAClient, event: ConnectedEv):
        print("🎉 Lara conectada e pronta para interagir!")

    @client.event
    async def on_message(client: NewAClient, event: MessageEv):
        msg = event.message
        if not msg.conversation or msg.info.is_from_me:
            return

        texto = msg.conversation.strip()
        chat_id = msg.info.chat

        # --- Comandos Lara ---
        if texto.startswith("/ajuda"):
            ajuda = (
                "🧠 *Comandos da Lara:*\n"
                "/clima [cidade]\n"
                "/horoscopo [signo]\n"
                "/imagem [descrição]\n"
                "/piada\n"
                "/motivacao\n"
                "/noticia [tema]\n"
                "/quiz /resposta [número]\n"
                "/caraoucoroa\n"
            )
            await client.reply_message(ajuda, msg)
            return

        if texto.startswith("/clima"):
            cidade = texto.split(maxsplit=1)[1] if len(texto.split()) > 1 else None
            if not cidade:
                await client.reply_message("Use: /clima [cidade]", msg)
                return
            await client.reply_message(f"🌤️ Consultando clima em {cidade}...", msg)
            await client.send_message(chat_id, await obter_clima(cidade))
            return

        if texto.startswith("/horoscopo"):
            signo = texto.split(maxsplit=1)[1] if len(texto.split()) > 1 else None
            if not signo:
                await client.reply_message("Use: /horoscopo [signo]", msg)
                return
            await client.reply_message(f"🔮 Consultando horóscopo de {signo}...", msg)
            await client.send_message(chat_id, await obter_horoscopo(signo))
            return

        if texto.startswith("/imagem"):
            prompt = texto.split(maxsplit=1)[1] if len(texto.split()) > 1 else None
            if not prompt:
                await client.reply_message("Use: /imagem [descrição]", msg)
                return
            await client.reply_message("🎨 Gerando imagem, aguarde...", msg)
            image_path = await asyncio.to_thread(_sync_gerar_imagem, prompt)
            if image_path:
                await client.send_image(chat_id, image_path, caption=f"🖼️ *{prompt}*")
                os.remove(image_path)
            else:
                await client.reply_message("❌ Falha ao gerar imagem.", msg)
            return

        if texto.startswith("/piada"):
            resposta = await asyncio.to_thread(_sync_responder_como_membro, "Conte uma piada curta e engraçada em português.")
            await client.reply_message(resposta, msg)
            return

        if texto.startswith("/motivacao"):
            resposta = await asyncio.to_thread(_sync_responder_como_membro, "Me diga uma frase motivacional em português.")
            await client.reply_message(resposta, msg)
            return

        if texto.startswith("/caraoucoroa"):
            await client.reply_message(random.choice(["🪙 Deu *cara!*", "🪙 Deu *coroa!*"]), msg)
            return

        if texto.startswith("/quiz"):
            pergunta = random.choice(QUIZ_PERGUNTAS)
            opcoes = "\n".join(f"{i+1}. {r}" for i, r in enumerate(pergunta["r"]))
            QUIZ_ATIVOS[chat_id] = pergunta
            await client.reply_message(f"🎯 *{pergunta['p']}*\n{opcoes}\nResponda com /resposta [número]", msg)
            return

        if texto.startswith("/resposta"):
            if chat_id not in QUIZ_ATIVOS:
                await client.reply_message("❌ Nenhum quiz ativo. Use /quiz para começar.", msg)
                return
            try:
                escolha = int(texto.split(maxsplit=1)[1]) - 1
                pergunta = QUIZ_ATIVOS.pop(chat_id)
                correta = pergunta["c"]
                if escolha == correta:
                    await client.reply_message("✅ Acertou! 👏", msg)
                else:
                    certo = pergunta["r"][correta]
                    await client.reply_message(f"❌ Errou! A resposta certa era *{certo}*.", msg)
            except Exception:
                await client.reply_message("❌ Use: /resposta [número].", msg)
            return

        if texto.startswith("/noticia"):
            topico = texto.split(maxsplit=1)[1] if len(texto.split()) > 1 else None
            if not topico:
                await client.reply_message("Use: /noticia [tema]", msg)
                return
            await client.reply_message(f"📰 Buscando notícias sobre {topico}...", msg)
            await client.send_message(chat_id, await obter_noticias(topico))
            return

        # --- Chat normal ---
        if texto.lower().startswith("lara"):
            texto_ia = texto[4:].strip() or "Oi!"
            await client.send_chat_state(chat_id, "composing")
            resposta = await asyncio.to_thread(_sync_responder_como_membro, texto_ia)
            audio_path = await asyncio.to_thread(_sync_tts_gerar_audio, resposta, chat_id.user)
            if audio_path and os.path.exists(audio_path):
                await client.send_audio(chat_id, audio_path)
                os.remove(audio_path)
            await client.reply_message(resposta, msg)

    while True:
        try:
            await limpar_temp()
            print("🔌 Conectando ao WhatsApp via Neonize...")
            await client.connect()
        except Exception as e:
            print(f"❌ Erro: {e}. Retentando em 10s...")
            await asyncio.sleep(10)

if __name__ == "__main__":
    print("🚀 Iniciando Lara Bot...")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 Encerrado pelo usuário.")
