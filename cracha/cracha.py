"""ExpoCamp 26 - Gerador de crachá de visitante (Streamlit)."""
import html
import io
import json
import re
import threading
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import requests
import streamlit as st
from PIL import Image, ImageDraw, ImageFont

# ---------- CONFIGURAÇÕES DO EVENTO (edite aqui) ----------
EVENTO = "ExpoCamp 26"
TEMA = "ODS 3 - 2A e 2B"
LOCAL = "COLÉGIO CAMP"
DATA = "23/10/2026"
COR_PRINCIPAL = "#1565C0"
# -----------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
ARQUIVO_LOCAL = BASE_DIR / "concorrentes_premio.txt"

st.set_page_config(page_title=f"Crachá {EVENTO}", page_icon="🎫", layout="centered")
st.write("Faça um crachá e concorra a prêmios. Basta se registrar")

# ---------------------------------------------------------------
# FONTES
# ---------------------------------------------------------------
FONTES_NEGRITO = [
    "DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "arialbd.ttf",
    "Arial Bold.ttf",
]
FONTES_NORMAL = [
    "DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "arial.ttf",
    "Arial.ttf",
]


@lru_cache(maxsize=None)
def carregar_fonte(tamanho, negrito=False):
    """Tenta fontes TrueType conhecidas; no fim usa a fonte embutida do Pillow."""
    for caminho in FONTES_NEGRITO if negrito else FONTES_NORMAL:
        try:
            return ImageFont.truetype(caminho, tamanho)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=tamanho)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def ajustar_fonte(draw, texto, largura_max, tam_max, tam_min=18, negrito=True):
    """Maior fonte (tam_min..tam_max) em que o texto cabe na largura dada."""
    for tam in range(tam_max, tam_min - 1, -2):
        fonte = carregar_fonte(tam, negrito)
        if draw.textlength(texto, font=fonte) <= largura_max:
            return fonte
    return carregar_fonte(tam_min, negrito)


def centralizar(draw, texto, y, fonte, cor, largura):
    x = (largura - draw.textlength(texto, font=fonte)) / 2
    draw.text((x, y), texto, font=fonte, fill=cor)


# ---------------------------------------------------------------
# CRACHÁ EM PDF
# ---------------------------------------------------------------
def gerar_cracha_pdf(nome):
    w, h = 600, 900
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, w, 220], fill=COR_PRINCIPAL)
    centralizar(d, EVENTO, 70, ajustar_fonte(d, EVENTO, w - 60, 60), "white", w)
    centralizar(d, "VISITANTE", 150, carregar_fonte(34), "white", w)

    centralizar(d, "Foi um prazer te ter conosco hoje!", 300,
                ajustar_fonte(d, "Foi um prazer te ter conosco hoje!", w - 60, 30, negrito=False),
                "#555555", w)
    centralizar(d, nome, 380, ajustar_fonte(d, nome, w - 80, 80), "black", w)

    d.line([60, 560, w - 60, 560], fill=COR_PRINCIPAL, width=4)
    for texto, y in ((TEMA, 600), (LOCAL, 680), (DATA, 730)):
        centralizar(d, texto, y, ajustar_fonte(d, texto, w - 80, 26, negrito=False), "#333333", w)

    d.rectangle([0, h - 40, w, h], fill=COR_PRINCIPAL)

    buf = io.BytesIO()
    img.save(buf, format="PDF", resolution=150.0)
    return buf.getvalue()


# ---------------------------------------------------------------
# VALIDAÇÃO DO TELEFONE (padrão brasileiro)
# ---------------------------------------------------------------
DDDS_VALIDOS = {
    "11", "12", "13", "14", "15", "16", "17", "18", "19",
    "21", "22", "24", "27", "28",
    "31", "32", "33", "34", "35", "37", "38",
    "41", "42", "43", "44", "45", "46", "47", "48", "49",
    "51", "53", "54", "55",
    "61", "62", "63", "64", "65", "66", "67", "68", "69",
    "71", "73", "74", "75", "77", "79",
    "81", "82", "83", "84", "85", "86", "87", "88", "89",
    "91", "92", "93", "94", "95", "96", "97", "98", "99",
}

MSG_TELEFONE = "Telefone inválido. Use DDD + número, por exemplo (11) 91234-5678."


def validar_telefone(texto):
    """Devolve (telefone_formatado, None) se válido, ou (None, mensagem_de_erro)."""
    digitos = re.sub(r"\D", "", texto)
    if digitos.startswith("55") and len(digitos) in (12, 13):  # aceita +55
        digitos = digitos[2:]
    if len(digitos) not in (10, 11):
        return None, MSG_TELEFONE
    ddd, numero = digitos[:2], digitos[2:]
    if ddd not in DDDS_VALIDOS:
        return None, f"DDD {ddd} não existe. {MSG_TELEFONE}"
    if len(numero) == 9 and numero[0] != "9":
        return None, "Celular com 9 dígitos deve começar com 9 depois do DDD."
    if len(numero) == 8 and numero[0] not in "2345":
        return None, "Telefone fixo deve começar com 2, 3, 4 ou 5 depois do DDD."
    if len(set(numero)) == 1:
        return None, MSG_TELEFONE
    return f"({ddd}) {numero[:-4]}-{numero[-4:]}", None


# ---------------------------------------------------------------
# REGISTRO DOS PARTICIPANTES
#   1) arquivo local (reserva; some se o app reiniciar)
#   2) Google Planilhas via webhook (persistente) - ver SHEETS_WEBHOOK
# ---------------------------------------------------------------
@st.cache_resource
def _trava():
    return threading.Lock()


def agora():
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/Sao_Paulo"))
    except Exception:
        return datetime.now()


def url_webhook():
    try:
        return str(st.secrets.get("SHEETS_WEBHOOK", "")).strip()
    except Exception:
        return ""


def registrar_local(nome, telefone, momento):
    try:
        with _trava():
            with open(ARQUIVO_LOCAL, "a", encoding="utf-8") as f:
                f.write(f"{momento:%Y-%m-%d %H:%M:%S} | {nome} | {telefone}\n")
    except OSError:
        pass


def registrar_planilha(nome, telefone, momento):
    url = url_webhook()
    if not url:
        return False
    corpo = json.dumps(
        {"nome": nome, "telefone": telefone, "data": f"{momento:%Y-%m-%d %H:%M:%S}"}
    )
    for _ in range(2):
        try:
            r = requests.post(
                url,
                data=corpo.encode("utf-8"),
                headers={"Content-Type": "text/plain; charset=utf-8"},
                timeout=10,
            )
            if r.ok and r.text.strip().lower() == "ok":
                return True
        except requests.RequestException:
            pass
    return False


def registrar(nome, telefone):
    momento = agora()
    registrar_local(nome, telefone, momento)
    registrar_planilha(nome, telefone, momento)


def normalizar_nome(texto):
    return " ".join(texto.split()).title()


# ---------------------------------------------------------------
# INTERFACE
# ---------------------------------------------------------------
st.title(f"🎫 {EVENTO}")
st.subheader("Gere o seu crachá de visitante")

with st.form("form_cracha"):
    entrada_nome = st.text_input("Digite seu nome:", max_chars=40, placeholder="Ex.: Matheus Soares")
    entrada_tel = st.text_input(
        "Digite seu telefone (com DDD):", max_chars=20, placeholder="Ex.: (11) 91234-5678"
    )
    enviado = st.form_submit_button("Gerar crachá", type="primary")

novo = False
if enviado:
    nome = normalizar_nome(entrada_nome)
    telefone, erro_tel = validar_telefone(entrada_tel)
    if not nome:
        st.session_state.pop("cracha", None)
        st.warning("Digite seu nome para gerar o crachá.")
    elif erro_tel:
        st.session_state.pop("cracha", None)
        st.warning(erro_tel)
    else:
        registrados = st.session_state.setdefault("registrados", set())
        if (nome, telefone) not in registrados:  # evita registro duplicado na mesma sessão
            registrar(nome, telefone)
            registrados.add((nome, telefone))
        st.session_state["cracha"] = {"nome": nome, "pdf": gerar_cracha_pdf(nome)}
        novo = True

cracha = st.session_state.get("cracha")
if cracha:
    nome = cracha["nome"]
    nome_html = html.escape(nome)
    st.markdown(
        f"""
        <div style="
            max-width: 360px; margin: 24px auto; border-radius: 18px;
            border: 3px solid {COR_PRINCIPAL}; overflow: hidden;
            text-align: center; font-family: sans-serif;
            box-shadow: 0 6px 18px rgba(0,0,0,0.25); background: white;">
            <div style="background:{COR_PRINCIPAL}; color:white; padding:22px 10px;">
                <div style="font-size:30px; font-weight:bold;">{html.escape(EVENTO)}</div>
                <div style="font-size:16px; letter-spacing:3px;">VISITANTE</div>
            </div>
            <div style="padding:30px 14px; color:#555;">
                <div style="font-size:16px;">Foi um prazer te ter conosco,</div>
                <div style="font-size:30px; font-weight:bold; color:black; margin-top:8px;
                            word-wrap:break-word;">
                    {nome_html}
                </div>
            </div>
            <hr style="border:1px solid {COR_PRINCIPAL}; margin:0 30px;">
            <div style="padding:16px; color:#333; font-size:14px;">
                <div>{html.escape(TEMA)}</div>
                <div style="margin-top:6px;">📍 {html.escape(LOCAL)}</div>
                <div>📅 {html.escape(DATA)}</div>
            </div>
            <div style="background:{COR_PRINCIPAL}; height:14px;"></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    arquivo_pdf = "cracha_" + "_".join(nome.split()) + ".pdf"
    st.download_button(
        "⬇️ Baixar crachá (PDF)",
        data=cracha["pdf"],
        file_name=arquivo_pdf,
        mime="application/pdf",
    )
    if novo:
        st.balloons()
else:
    st.info("Preencha seu nome e telefone e clique em “Gerar crachá”.")

# ---------------------------------------------------------------
# ÁREA DO ORGANIZADOR: abra o app com  ?admin=1  no final do link
# ---------------------------------------------------------------
if st.query_params.get("admin") == "1":
    with st.expander("Organizador - registros desta instância do app"):
        if ARQUIVO_LOCAL.exists() and ARQUIVO_LOCAL.stat().st_size > 0:
            conteudo = ARQUIVO_LOCAL.read_text(encoding="utf-8")
            st.write(f"{len(conteudo.splitlines())} registro(s) no arquivo local.")
            st.download_button(
                "Baixar concorrentes_premio.txt",
                data=conteudo.encode("utf-8"),
                file_name="concorrentes_premio.txt",
                mime="text/plain",
            )
        else:
            st.write("Nenhum registro no arquivo local.")
        st.caption(
            "URL da planilha configurada: " + ("sim" if url_webhook() else "NÃO (apenas arquivo local)")
        )