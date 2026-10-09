"""
monitor_pe.py - Radar Jornalístico de Pernambuco
Coleta tendências antecipadas no X (Recife), Google Trends e notícias locais.
"""
import html as H
import json
import math
import re
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PASTA_DADOS = RAIZ / "dados"
PASTA_HIST = PASTA_DADOS / "historico"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RadarPE/2.0"}

JANELA_PICO = 3
MIN_RAZAO = 3.5
MIN_FATIA = 0.015
PISO_BASE = 0.002

# Filtro estrito: tudo o que for entretenimento/esporte fica de fora
IGNORAR_RUIDO = re.compile(
    r"\b(flamengo|corinthians|vasco|palmeiras|sport|nautico|santa cruz|futebol|gol|rodada|daronco|"
    r"afazenda|fazenda|bbb|eliminacao|elimina|novela|neymar|anitta|show|reality)\b", re.I
)

PARE = set("""
a o e é de da do das dos em no na nos nas um uma uns umas para pra pro por com sem que se ao à às os as
mais menos muito muita já não nao nem sim sobre como mas ou foi vai vão ser ter tem têm são está estão
esse essa este esta isto aquilo aqui ali lá agora hoje ontem amanhã quando onde quem qual porque pq
ele ela eles elas seu sua seus suas meu minha nós voce você vocês vc até após entre depois antes tudo
todo toda todos todas também só ainda bem vez vezes fala falou diz disse dizer vamos faz fez ano anos
dia dias hora horas poder deve quer ver veja olha assim então aí né tá pois sob contra desde durante
aponta aponta-se pode podem podia poderá acesso acessos registra registrou registram nova novo novos novas
segundo contra durante sobre aponta realiza realizou anuncia anunciou mantém manteve mantiveram
participa participou encontro defende defendeu maioria minoria parte partes grande grandes
""".split())

COMUM_IMPRENSA = set("""
noticia noticias jornal portal folha diario pernambuco recife estado governo politica politico prefeitura
governadora prefeito prefeita blog veja confira saiba entenda apos diz veja leia video fotos imagens
""".split())

TERMOS_CHAVE_PE = [
    "raquel lyra", "joao campos", "lula", "bolsonaro", "flavio bolsonaro",
    "alepe", "prefeitura do recife", "governo de pernambuco"
]

def sem_acento(t):
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")

def zulu(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")

def limpa_texto(texto):
    t = re.sub(r"https?://\S+|www\.\S+|\S+@\S+\.\S+|@\w[\w.]*", " ", texto)
    t = re.sub(r"#(\w+)", lambda m: " " + re.sub(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Þ])", " ", m.group(1)) + " ", t)
    return t

# Lista de verbos e palavras estruturais de manchetes que nunca devem virar termo de pico
VERBOS_MANCHETE = set("""
pode podem podia podera poderiam aponta apontam apontou acesso acessos registra registrou registram
mobilizam mobiliza mobilizou anuncia anunciou anunciam afirma afirmou diz disse disseram veja confira
entenda saiba faz fez fara tiveram teve tem segue seguiu mostra mostrou mantem manteve volta voltou
assume assumiu busca buscam alcanca alcancou supera superou lidera liderou deixa deixou vota votam
""".split())

def extrair_ngramas(texto):
    out = {}
    trechos = re.split(r"[.!?;:|\n\"“”()\[\]…,–—]+|\s-\s", limpa_texto(texto))
    for trecho in trechos:
        orig = re.findall(r"[0-9A-Za-zÀ-ÿ]+", trecho)
        norm = [sem_acento(w) for w in orig]
        for n in (1, 2, 3):
            for i in range(len(norm) - n + 1):
                ks = norm[i:i + n]
                
                # Bloqueia se começa ou termina com stopword
                if ks[0] in PARE or ks[-1] in PARE:
                    continue
                if all(k in PARE or k in COMUM_IMPRENSA for k in ks):
                    continue
                if any(k.isdigit() and len(k) != 4 for k in ks):
                    continue
                
                # Regra anti-verbos e palavras vagas
                if any(k in VERBOS_MANCHETE for k in ks):
                    continue

                # Se for palavra única (n=1), só aceita se for nome próprio/sigla (primeira letra maiúscula)
                # ou siglas institucionais reconhecidas
                if n == 1:
                    palavra_original = orig[i]
                    if ks[0] in COMUM_IMPRENSA or len(ks[0]) < 4:
                        if ks[0] not in ("alepe", "stf", "tce", "cpi", "tse", "ufpe", "tjpe", "oab", "pt", "psb", "pl"):
                            continue
                    # Descarta palavras minúsculas soltas para não pegar verbos ou substantivos comuns
                    if not palavra_original[0].isupper() and ks[0] not in ("alepe", "stf", "tce", "cpi", "tse", "ufpe", "tjpe"):
                        continue

                chave = " ".join(ks)
                if not IGNORAR_RUIDO.search(chave):
                    out.setdefault(chave, " ".join(orig[i:i + n]))
    return out

# ---------- 1. TRENDING TOPICS DO X (RECIFE) COM VARIAÇÃO ----------

def coletar_x_recife_detalhado():
    url = "https://trends24.in/brazil/recife/"
    try:
        req = urllib.request.Request(url, headers=UA)
        html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
    except Exception as e:
        print(f"Aviso X Recife: {e}")
        return [], []

    blocos = re.findall(r'<h3 class=title data-timestamp=([\d.]+)>.*?</h3><ol class=trend-card__list>(.*?)</ol>', html, re.S)
    if not blocos:
        return [], []

    # Extrai o ranking atual e a leitura anterior (1 hora atrás)
    agora_ts = float(blocos[0][0])
    termos_atuais = [re.sub(r"&amp;", "&", n).strip() for n in re.findall(r"class=trend-link>(.*?)</a>", blocos[0][1])]
    termos_1h = [re.sub(r"&amp;", "&", n).strip() for n in re.findall(r"class=trend-link>(.*?)</a>", blocos[1][1])] if len(blocos) > 1 else []

    itens_x = []
    itens_para_analise = []
    quando_atual = datetime.fromtimestamp(agora_ts, tz=timezone.utc)

    for pos, termo in enumerate(termos_atuais[:20]):
        pos_atual = pos + 1
        pos_anterior = (termos_1h.index(termo) + 1) if termo in termos_1h else None

        if pos_anterior is None:
            direcao = "novo"
            var = "NOVO"
        elif pos_anterior > pos_atual:
            direcao = "sobe"
            var = f"+{pos_anterior - pos_atual}"
        elif pos_anterior < pos_atual:
            direcao = "cai"
            var = f"-{pos_atual - pos_anterior}"
        else:
            direcao = "igual"
            var = "="

        eh_ruido = bool(IGNORAR_RUIDO.search(termo))
        itens_x.append({
            "termo": termo,
            "posicao": pos_atual,
            "variacao": var,
            "direcao": direcao,
            "url": f"https://x.com/search?q={urllib.parse.quote(termo)}",
            "ruido": eh_ruido
        })

        if not eh_ruido:
            peso = max(1, 21 - pos_atual)
            itens_para_analise.append(("X", "Recife", quando_atual, peso * 0.3, termo, termo, f"https://x.com/search?q={urllib.parse.quote(termo)}"))

    return itens_x, itens_para_analise

# ---------- 2. GOOGLE: CONSULTAS EM ASCENSÃO EM PERNAMBUCO (BR-PE) ----------

def coletar_google_trends():
    """Captura termos em ascensão e notícias publicadas estritamente nas últimas horas."""
    itens_google = []
    vistos = set()
    agora_utc = datetime.now(timezone.utc)

    # 1. Google Trends Brasil: tendências com pico nacional envolvendo PE
    try:
        url_trends = "https://trends.google.com/trending/rss?geo=BR"
        req = urllib.request.Request(url_trends, headers=UA)
        xml = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", "ignore")
        for it in re.findall(r"<item>(.*?)</item>", xml, re.S):
            tit = H.unescape(re.search(r"<title>(.*?)</title>", it, re.S).group(1))
            trafego = (re.search(r"<ht:approx_traffic>(.*?)</ht:approx_traffic>", it) or [None, ""])[1]
            noticia_tit = (re.search(r"<ht:news_item_title>(.*?)</ht:news_item_title>", it, re.S) or [None, ""])[1]
            noticia_url = (re.search(r"<ht:news_item_url>(.*?)</ht:news_item_url>", it, re.S) or [None, ""])[1]

            conteudo = (tit + " " + (noticia_tit or "") + " " + it).lower()
            if any(k in conteudo for k in ["pernambuco", "recife", "olinda", "caruaru", "petrolina", "raquel", "campos", "alepe"]):
                if not IGNORAR_RUIDO.search(conteudo) and tit.lower() not in vistos:
                    vistos.add(tit.lower())
                    itens_google.append({
                        "termo": tit,
                        "volume": f"▲ {trafego or 'Em alta'}",
                        "manchete": H.unescape(noticia_tit) if noticia_tit else "Tendência estadual no Google",
                        "url": noticia_url or f"https://trends.google.com/trends/explore?geo=BR-PE&q={urllib.parse.quote(tit)}"
                    })
    except Exception as e:
        print(f"Aviso Google Trends BR: {e}")

    # 2. Notícias geolocalizadas com checagem estrita de horário
    consultas_geo = [
        "Pernambuco",
        "Recife",
        "Região Metropolitana do Recife",
        "Alepe",
        "Governo de Pernambuco"
    ]

    for regiao in consultas_geo:
        try:
            q = urllib.parse.quote(f'"{regiao}" when:1d')
            url_noticias = f"https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
            req = urllib.request.Request(url_noticias, headers=UA)
            xml = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "ignore")

            for it in re.findall(r"<item>(.*?)</item>", xml, re.S)[:5]:
                tit = H.unescape(re.search(r"<title>(.*?)</title>", it, re.S).group(1))
                tit = re.sub(r"\s+-\s+[^-]+$", "", tit).strip()
                link = (re.search(r"<link>(.*?)</link>", it, re.S) or [None, ""])[1].strip()
                fonte = (re.search(r"<source[^>]*>(.*?)</source>", it, re.S) or [None, ""])[1]
                pub = (re.search(r"<pubDate>(.*?)</pubDate>", it) or [None, ""])[1]
                chave = sem_acento(tit)

                # Trava temporal matemática: descarta matérias requentadas
                if pub:
                    try:
                        data_pub = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
                        # Só aceita se foi publicada nas últimas 12 horas
                        if agora_utc - data_pub > timedelta(hours=12):
                            continue
                    except ValueError:
                        pass

                if not IGNORAR_RUIDO.search(tit) and chave not in vistos:
                    vistos.add(chave)
                    itens_google.append({
                        "termo": regiao,
                        "volume": "Últimas 12h",
                        "manchete": f"{fonte}: {tit}" if fonte else tit,
                        "url": link or f"https://trends.google.com/trends/explore?geo=BR-PE&q={urllib.parse.quote(regiao)}"
                    })
        except Exception:
            continue

    return itens_google
# ---------- 3. RSS DE NOTÍCIAS DAS ÚLTIMAS 24H ----------

def coletar_noticias_pe():
    itens = []
    vistos = set()
    buscas = [f'"{t}"' for t in TERMOS_CHAVE_PE]
    for termo in buscas:
        q = urllib.parse.quote(f"{termo} when:1d")
        url = f"https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
        try:
            req = urllib.request.Request(url, headers=UA)
            xml = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
        except Exception:
            continue

        for it in re.findall(r"<item>(.*?)</item>", xml, re.S)[:25]:
            tit = H.unescape(re.search(r"<title>(.*?)</title>", it, re.S).group(1))
            tit = re.sub(r"\s+-\s+[^-]+$", "", tit).strip()
            link = (re.search(r"<link>(.*?)</link>", it, re.S) or [None, ""])[1].strip()
            fonte = H.unescape((re.search(r"<source[^>]*>(.*?)</source>", it, re.S) or [None, ""])[1]) or "Imprensa"
            pub = (re.search(r"<pubDate>(.*?)</pubDate>", it) or [None, ""])[1]

            chave = sem_acento(tit)
            if not link or chave in vistos or IGNORAR_RUIDO.search(tit):
                continue
            vistos.add(chave)

            try:
                quando = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
            except ValueError:
                quando = datetime.now(timezone.utc)

            itens.append(("Imprensa", fonte, quando, 1.0, tit, tit, link))
    return itens

# ---------- PROCESSAMENTO CENTRAL ----------

def main():
    PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    PASTA_HIST.mkdir(parents=True, exist_ok=True)
    agora = datetime.now(timezone.utc)
    hora0 = agora.replace(minute=0, second=0, microsecond=0)

    # Coletas especializadas
    itens_x, x_analise = coletar_x_recife_detalhado()
    itens_google = coletar_google_trends()
    itens_imprensa = coletar_noticias_pe()

    todos_itens = itens_imprensa + x_analise
    todos_itens = [i for i in todos_itens if agora - i[2] <= timedelta(hours=72)]

    def hora_de(dt_obj):
        return int((hora0 - dt_obj.replace(minute=0, second=0, microsecond=0)).total_seconds() // 3600)

    total_h = defaultdict(float)
    serie = defaultdict(lambda: defaultdict(dict))
    formas = defaultdict(Counter)
    ocorrencias = defaultdict(list)

    for rede, fonte, quando, peso, texto, titulo, url in todos_itens:
        h = hora_de(quando)
        if h < 24:
            total_h[h] += peso

        for k, forma in extrair_ngramas(texto).items():
            atual = serie[k][h].get(fonte, 0)
            if peso > atual:
                serie[k][h][fonte] = peso
            formas[k][forma] += 1
            if h < 24:
                ocorrencias[k].append({
                    "rede": rede, "fonte": fonte, "hora": zulu(quando),
                    "titulo": titulo, "url": url, "peso": round(peso, 2)
                })

    def forma_de(k):
        return formas[k].most_common(1)[0][0]

    def peso_em(k, horas):
        return sum(sum(serie[k][h].values()) for h in horas if h in serie[k])

    def fontes_em(k, horas):
        return {f for h in horas for f in serie[k].get(h, {})}

    hoje = range(0, 24)
    antes = range(24, 72)
    tot_hoje = sum(total_h[h] for h in hoje) or 1
    tot_antes = sum(total_h[h] for h in antes)

    # Nuvem de termos filtrada
    candidatos_nuvem = []
    for k in serie:
        fs = fontes_em(k, hoje)
        p = peso_em(k, hoje)
        if len(fs) >= 2 or any(o["rede"] == "X" for o in ocorrencias[k]):
            candidatos_nuvem.append({"k": k, "peso": p, "fontes": len(fs)})

    candidatos_nuvem.sort(key=lambda x: -x["peso"])
    topo = candidatos_nuvem[0]["peso"] if candidatos_nuvem else 1

    nuvem_final = [
        {
            "termo": forma_de(c["k"]),
            "peso_relativo": round(c["peso"] / topo * 100, 1),
            "fatia_pct": round(c["peso"] / tot_hoje * 100, 2),
            "fontes_distintas": c["fontes"]
        }
        for c in candidatos_nuvem[:45]
    ]

    # Cálculo dos Picos
    arquivos_hist = sorted(PASTA_HIST.glob("hist_*.json"))
    base_hist = [json.loads(a.read_text(encoding="utf-8")) for a in arquivos_hist[-16:]] if arquivos_hist else []

    def obter_base(k):
        b72 = peso_em(k, antes) / tot_antes if tot_antes > 0 else None
        bh = sum(h.get(k, 0) for h in base_hist) / len(base_hist) if base_hist else None
        validos = [v for v in (b72, bh) if v is not None]
        return max(validos) if validos else None

    picos = []
    for c in candidatos_nuvem:
        k = c["k"]
        base = obter_base(k)
        if base is None:
            continue

        melhor = None
        for ini in range(0, 24 - JANELA_PICO + 1):
            hs = range(ini, ini + JANELA_PICO)
            tw = sum(total_h[h] for h in hs)
            if tw < 0.05 * tot_hoje:
                continue
            pw = peso_em(k, hs)
            fatia = pw / tw
            if fatia < MIN_FATIA or len(fontes_em(k, hs)) < 2:
                continue

            razao = fatia / max(base, PISO_BASE)
            if razao >= MIN_RAZAO and (not melhor or razao > melhor[0]):
                melhor = (razao, ini, fatia)

        if melhor:
            r, ini, f = melhor
            do_dia = sorted(ocorrencias[k], key=lambda x: x["hora"])
            gatilho = max(do_dia, key=lambda x: x["peso"]) if do_dia else None
            picos.append({
                "termo": forma_de(k),
                "razao_aumento": round(r, 1),
                "gatilho": gatilho
            })

    picos.sort(key=lambda x: -x["razao_aumento"])

    # Salva histórico
    PASTA_HIST.joinpath(f"hist_{agora.strftime('%Y%m%d_%H%M')}.json").write_text(
        json.dumps({c["k"]: round(c["peso"] / tot_hoje, 5) for c in candidatos_nuvem[:250]}),
        encoding="utf-8"
    )
    for arq_velho in sorted(PASTA_HIST.glob("hist_*.json"))[:-48]:
        arq_velho.unlink()

    # Consolidação final do painel com todas as seções
    saida = {
        "atualizado_em": zulu(agora),
        "total_analisado": len(todos_itens),
        "x_recife": itens_x,
        "google_trends": itens_google,
        "picos": picos[:8],
        "nuvem": nuvem_final
    }

    destino = PASTA_DADOS / "painel_pe.json"
    destino.write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Sucesso: {len(itens_x)} tópicos no X Recife, {len(itens_google)} buscas Google, {len(picos)} picos.")

if __name__ == "__main__":
    main()