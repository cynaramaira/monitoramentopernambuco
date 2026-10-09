"""
monitor_pe.py - Monitor de Pautas e Palavras da Política de Pernambuco
Gera dados/painel_pe.json para consumo do frontend estático.
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
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MonitorPE/1.0"}

JANELA_PICO = 3
MIN_RAZAO = 3.5
MIN_FATIA = 0.015
PISO_BASE = 0.002

PARE = set("""
a o e é de da do das dos em no na nos nas um uma uns umas para pra pro por com sem que se ao à às os as
mais menos muito muita já não nao nem sim sobre como mas ou foi vai vão ser ter tem têm são está estão
esse essa este esta isto aquilo aqui ali lá agora hoje ontem amanhã quando onde quem qual porque pq
ele ela eles elas seu sua seus suas meu minha nós voce você vocês vc até após entre depois antes tudo
todo toda todos todas também só ainda bem vez vezes fala falou diz disse dizer vamos faz fez ano anos
dia dias hora horas poder deve quer ver veja olha assim então aí né tá pois sob contra desde durante
""".split())

COMUM_IMPRENSA = set("""
noticia noticias jornal portal folha diario pernambuco recife estado governo politica politico prefeitura
governadora prefeito prefeita blog veja confira saiba entenda apos diz veja leia video fotos imagens
""".split())

TERMOS_BUSCA_PE = [
    '"governo de pernambuco"',
    '"raquel lyra"',
    '"joao campos"',
    '"alepe"',
    '"prefeitura do recife"',
    'eleicao pernambuco'
]

def sem_acento(t):
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")

def zulu(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")

def limpa_texto(texto):
    t = re.sub(r"https?://\S+|www\.\S+|\S+@\S+\.\S+|@\w[\w.]*", " ", texto)
    t = re.sub(r"#(\w+)", lambda m: " " + re.sub(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Þ])", " ", m.group(1)) + " ", t)
    return t

def extrair_ngramas(texto):
    out = {}
    trechos = re.split(r"[.!?;:|\n\"“”()\[\]…,–—]+|\s-\s", limpa_texto(texto))
    for trecho in trechos:
        orig = re.findall(r"[0-9A-Za-zÀ-ÿ]+", trecho)
        norm = [sem_acento(w) for w in orig]
        for n in (1, 2, 3):
            for i in range(len(norm) - n + 1):
                ks = norm[i:i + n]
                if ks[0] in PARE or ks[-1] in PARE:
                    continue
                if all(k in PARE or k in COMUM_IMPRENSA for k in ks):
                    continue
                if any(k.isdigit() and len(k) != 4 for k in ks):
                    continue
                if n == 1 and (ks[0] in COMUM_IMPRENSA or len(ks[0]) < 4):
                    if ks[0] not in ("alepe", "stf", "tce", "cpi", "tse"):
                        continue
                chave = " ".join(ks)
                out.setdefault(chave, " ".join(orig[i:i + n]))
    return out

def coletar_noticias_pe():
    itens = []
    vistos = set()
    for termo in TERMOS_BUSCA_PE:
        q = urllib.parse.quote(f"{termo} when:1d")
        url = f"https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
        try:
            req = urllib.request.Request(url, headers=UA)
            xml = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
        except Exception as e:
            print(f"Aviso: Falha ao obter RSS para {termo}: {e}")
            continue

        for it in re.findall(r"<item>(.*?)</item>", xml, re.S)[:30]:
            tit = H.unescape(re.search(r"<title>(.*?)</title>", it, re.S).group(1))
            tit = re.sub(r"\s+-\s+[^-]+$", "", tit).strip()
            link = (re.search(r"<link>(.*?)</link>", it, re.S) or [None, ""])[1].strip()
            fonte = H.unescape((re.search(r"<source[^>]*>(.*?)</source>", it, re.S) or [None, ""])[1]) or "Imprensa"
            pub = (re.search(r"<pubDate>(.*?)</pubDate>", it) or [None, ""])[1]

            chave = sem_acento(tit)
            if not link or chave in vistos:
                continue
            vistos.add(chave)

            try:
                quando = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
            except ValueError:
                quando = datetime.now(timezone.utc)

            itens.append(("Imprensa", fonte, quando, 1.0, tit, tit, link))
    return itens

def coletar_x_recife():
    itens = []
    url = "https://trends24.in/brazil/recife/"
    try:
        req = urllib.request.Request(url, headers=UA)
        html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
    except Exception as e:
        print(f"Aviso: Falha ao coletar Trends24 Recife: {e}")
        return itens

    blocos = re.findall(r'<h3 class=title data-timestamp=([\d.]+)>.*?</h3><ol class=trend-card__list>(.*?)</ol>', html, re.S)
    if not blocos:
        return itens

    for ts, ol in blocos[:6]:
        quando = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        termos = [re.sub(r"&amp;", "&", n).strip() for n in re.findall(r"class=trend-link>(.*?)</a>", ol)]
        for pos, t in enumerate(termos[:20]):
            peso = max(1, 21 - (pos + 1))
            itens.append(("X", "Recife", quando, peso * 0.2, t, t, f"https://x.com/search?q={urllib.parse.quote(t)}"))
    return itens

def main():
    PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    PASTA_HIST.mkdir(parents=True, exist_ok=True)
    agora = datetime.now(timezone.utc)
    hora0 = agora.replace(minute=0, second=0, microsecond=0)

    itens = coletar_noticias_pe() + coletar_x_recife()
    itens = [i for i in itens if agora - i[2] <= timedelta(hours=72)]

    def hora_de(dt_obj):
        return int((hora0 - dt_obj.replace(minute=0, second=0, microsecond=0)).total_seconds() // 3600)

    total_h = defaultdict(float)
    serie = defaultdict(lambda: defaultdict(dict))
    formas = defaultdict(Counter)
    ocorrencias = defaultdict(list)

    for rede, fonte, quando, peso, texto, titulo, url in itens:
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
            "chave": c["k"],
            "peso_relativo": round(c["peso"] / topo * 100, 1),
            "fatia_pct": round(c["peso"] / tot_hoje * 100, 2),
            "fontes_distintas": c["fontes"]
        }
        for c in candidatos_nuvem[:50]
    ]

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
                "chave": k,
                "razao_aumento": round(r, 1),
                "fatia_pct": round(f * 100, 2),
                "gatilho": gatilho,
                "serie_24h": [round(peso_em(k, [h]), 1) for h in range(23, -1, -1)]
            })

    picos.sort(key=lambda x: -x["razao_aumento"])

    PASTA_HIST.joinpath(f"hist_{agora.strftime('%Y%m%d_%H%M')}.json").write_text(
    json.dumps({c["k"]: round(c["peso"] / tot_hoje, 5) for c in candidatos_nuvem[:300]}),
    encoding="utf-8"
    )   
    for arq_velho in sorted(PASTA_HIST.glob("hist_*.json"))[:-48]:
        arq_velho.unlink()

    saida = {
        "atualizado_em": zulu(agora),
        "total_noticias_analisadas": len(itens),
        "nuvem_palavras": nuvem_final,
        "picos": picos[:10]
    }

    destino = PASTA_DADOS / "painel_pe.json"
    destino.write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Sucesso: {len(nuvem_final)} palavras na nuvem e {len(picos)} picos detectados.")

if __name__ == "__main__":
    main()