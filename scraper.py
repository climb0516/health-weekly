# Created by 青云 on 2026/10/21.
"""流感 & 呼吸道传染病周报 数据抓取（中国疾控中心官网）。只用标准库。

- 全国急性呼吸道传染病哨点监测（每周）：流感样病例百分比、11 种病原体阳性率及周变化、南北方排名、官方分析与健康提示
- 中国流感监测周报（每周）：流行情况概要
- 全国法定传染病疫情概况（每月）：各病种发病数、死亡数
历史期次按 URL 缓存在 data.json 里，已解析过的不再请求。
"""
import html, json, re, time, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
DATA_FILE = ROOT / "data.json"
CST = timezone(timedelta(hours=8))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
CDC = "https://www.chinacdc.cn/jksj"
ARI, FLU, MONTHLY = "jksj04_14275", "jksj04_14249", "jksj01"
PATHOGENS = ["新型冠状病毒", "流感病毒", "呼吸道合胞病毒", "腺病毒", "人偏肺病毒", "副流感病毒",
             "普通冠状病毒", "博卡病毒", "鼻病毒", "肠道病毒", "肺炎支原体"]
MAX_WEEKS, MAX_MONTHS = 60, 36


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def text_of(h):
    h = re.sub(r"<script.*?</script>|<style.*?</style>", " ", h, flags=re.S)
    t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h)))
    return re.sub(r"\s*([（）%])\s*", r"\1", t)          # 老报告「（ 10.5% ）」去掉多余空格


def list_page(section, pages):
    items = []
    for i in range(pages):
        try:
            h = fetch(f"{CDC}/{section}/" + ("" if i == 0 else f"index_{i}.html"))
        except Exception:
            break
        items += [(f"{CDC}/{section}/{p}", re.sub(r"\s+", " ", title).strip())
                  for p, title in re.findall(r'href="\./(\d{6}/t\d+_\d+\.html)"[^>]*>\s*([^<]{4,80})<', h)]
    return items


def num(s):
    return float(s.replace("+", "")) if s not in ("", None) else None


# ── 呼吸道哨点监测 ──────────────────────────────────────
def parse_ari(url, title, t):
    m = re.search(r"(\d{4})年第(\d+)周", title) or re.search(r"(\d{4})年第(\d+)周", t)
    if not m:
        return None
    year, week = int(m.group(1)), int(m.group(2))
    p = re.search(r"第\d+周（(\d{4})年(\d+)月(\d+)日[—\-－至]+(?:(\d{4})年)?(\d+)月(\d+)日）", t)
    period = None
    if p:
        y1, m1, d1, y2, m2, d2 = p.groups()
        period = [f"{y1}-{int(m1):02d}-{int(d1):02d}", f"{y2 or y1}-{int(m2):02d}-{int(d2):02d}"]
    ili = re.search(r"流感样病例百分比）为([\d.]+)%", t)
    south = re.search(r"南方省份（([\d.]+)%）", t)
    north = re.search(r"北方省份（([\d.]+)%）", t)

    # 表1：病原体 门急诊% 较上周 住院% 较上周（老报告可能没有「较上周」列，或整张表都没有）
    out_p, in_p = {}, {}
    # 正文里有「见表1」，要定位到表格标题「表1 第N周」
    h1 = re.search(r"表1\s*第\d+周", t)
    seg = t[h1.start():] if h1 else ""
    seg = seg[:seg.find("注：")] if "注：" in seg else seg
    for name in PATHOGENS:
        r = re.search(re.escape(name) + r"\s+([\d.]+)\s+([+\-]?[\d.]+)\s+([\d.]+)\s+([+\-]?[\d.]+)(?!\.)", seg)
        if r:
            out_p[name] = {"rate": num(r.group(1)), "chg": num(r.group(2))}
            in_p[name] = {"rate": num(r.group(3)), "chg": num(r.group(4))}
            continue
        r = re.search(re.escape(name) + r"\s+([\d.]+)\s+([\d.]+)(?!\s*[+\-]?[\d.])", seg)
        if r:
            out_p[name] = {"rate": num(r.group(1)), "chg": None}
            in_p[name] = {"rate": num(r.group(2)), "chg": None}
    # 没有表1时，从正文「前三位病原体为X（a%）、Y（b%）」兜底
    def top3(label):
        r = re.search(label + r"[^。；]*?前三位病原体为([^。；]+)", t)
        return [(n.strip(), float(v)) for n, v in re.findall(r"([一-龥]+)（([\d.]+)%）", r.group(1))] if r else []
    top_out, top_in = top3("门急诊流感样病例"), top3("住院严重急性呼吸道感染病例")
    if not out_p:
        out_p = {n: {"rate": v, "chg": None} for n, v in top_out}
        in_p = {n: {"rate": v, "chg": None} for n, v in top_in}

    # 表2：南北方前三位
    region = {}
    h2 = re.search(r"表2\s*第\d+周", t)
    s2 = t[h2.start():] if h2 else ""
    rm = re.search(r"南方省份\s+(.+?)\s+北方省份\s+(.+?)\s+南方省份包括", s2)
    if rm:
        for key, chunk in (("south", rm.group(1)), ("north", rm.group(2))):
            parts = chunk.split()
            if len(parts) >= 6:
                region[key] = {"out": parts[:3], "in": parts[3:6]}

    # 分析与健康提示
    analysis, tips = "", []
    a = t.find("二、分析及健康提示")
    if a >= 0:
        body = t[a + len("二、分析及健康提示"):]
        body = body[:body.find("表1")] if "表1" in body else body[:1500]
        cut = body.find("为防范")
        analysis = (body[:cut] if cut > 0 else body).strip().rstrip("，")
        tips = [x.strip() for x in re.findall(r"（\d）([^（]+)", body)][:6]
    level = {}
    for key, word in (("south", "南方省份"), ("north", "北方省份")):
        lm = re.search(word + r"[^。；，]*?处于([低中高较]+流行水平)", analysis)
        if lm:
            level[key] = lm.group(1)

    return {"year": year, "week": week, "period": period, "url": url,
            "ili": num(ili.group(1)) if ili else None, "ili_south": num(south.group(1)) if south else None,
            "ili_north": num(north.group(1)) if north else None,
            "outpatient": out_p, "inpatient": in_p, "region": region, "level": level,
            "analysis": analysis, "tips": tips}


def ari(old):
    known = {w["url"]: w for w in old.get("weeks", [])}
    weeks = []
    for url, title in list_page(ARI, 5):
        if "哨点监测" not in title:
            continue
        if url in known:
            weeks.append(known[url])
            continue
        try:
            w = parse_ari(url, title, text_of(fetch(url)))
            time.sleep(0.3)                                      # 补历史时放慢一点
        except Exception:
            w = None
        if w:
            weeks.append(w)
    merged = {**known, **{w["url"]: w for w in weeks}}
    out = sorted(merged.values(), key=lambda w: (w["year"], w["week"]), reverse=True)
    # 同一周可能有更正版，按周次去重保留最新抓到的
    seen, uniq = set(), []
    for w in out:
        if (w["year"], w["week"]) not in seen:
            seen.add((w["year"], w["week"]))
            uniq.append(w)
    if not uniq:
        raise ValueError("no weeks")
    return uniq[:MAX_WEEKS]


# ── 流感监测周报概要 ─────────────────────────────────────
def flu(old):
    items = [x for x in list_page(FLU, 1) if "流感监测周报" in x[1]][:1]
    if not items:
        raise ValueError("no flu report")
    url, title = items[0]
    if old.get("flu", {}).get("url") == url:
        return old["flu"]
    t = text_of(fetch(url))
    m = re.search(r"概要（截至(\d{4})年\s*(\d+)\s*月\s*(\d+)\s*日）", t)
    body = t[t.find("概要"):]
    body = body[:body.find("文件附件")] if "文件附件" in body else body[:2000]
    bullets = [b.strip() for b in body.split("·")[1:] if b.strip()]
    sub = re.search(r"甲型流感病毒占比([\d.]+)%、乙型流感病毒占比([\d.]+)%", t)
    h3 = re.search(r"A\(H1N1\)pdm09占比([\d.]+)%、A\(H3N2\)占比([\d.]+)%", t)
    outbreak = re.search(r"共报告(\d+)起流感样病例暴发疫情", t)
    return {"title": title, "url": url, "until": f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None,
            "summary": bullets[0] if bullets else "", "a_pct": num(sub.group(1)) if sub else None,
            "b_pct": num(sub.group(2)) if sub else None, "h1n1_pct": num(h3.group(1)) if h3 else None,
            "h3n2_pct": num(h3.group(2)) if h3 else None, "outbreaks": int(outbreak.group(1)) if outbreak else None}


# ── 月度法定传染病 ──────────────────────────────────────
def parse_month(url, title, t):
    m = re.search(r"(\d{4})年(\d+)月", title)
    if not m:
        return None
    month = f"{m.group(1)}-{int(m.group(2)):02d}"
    total = re.search(r"全国共报告法定传染病(\d+)例，死亡(\d+)人", t)
    seg = t[t.find("统计表"):] if "统计表" in t else ""
    diseases = {}
    # 「病名 [脚注] 发病数 死亡数」，脚注是 1~2 位数字
    for name, cases, deaths in re.findall(r"([一-龥（）()A-Za-z]{2,20})\s+(?:\d{1,2}\s+)?(\d+)\s+(\d+)(?=\s|$)", seg):
        if name in ("病名", "发病数", "死亡数") or "统计表" in name or name in diseases:
            continue
        diseases[name] = [int(cases), int(deaths)]
    return {"month": month, "url": url, "total": int(total.group(1)) if total else None,
            "deaths": int(total.group(2)) if total else None, "diseases": diseases}


def monthly(old):
    known = {x["url"]: x for x in old.get("months", [])}
    out = []
    for url, title in list_page(MONTHLY, 3):
        if "疫情概况" not in title:
            continue
        if url in known:
            out.append(known[url])
            continue
        try:
            x = parse_month(url, title, text_of(fetch(url)))
            time.sleep(0.3)
        except Exception:
            x = None
        if x and x["diseases"]:
            out.append(x)
    merged = {**known, **{x["url"]: x for x in out}}
    res = sorted(merged.values(), key=lambda x: x["month"], reverse=True)
    if not res:
        raise ValueError("no months")
    return res[:MAX_MONTHS]


def main():
    old = {}
    if DATA_FILE.exists():
        try:
            old = json.loads(DATA_FILE.read_text("utf-8"))
        except Exception:
            old = {}
    now = datetime.now(CST).isoformat(timespec="seconds")
    sources = []

    def run(name, fn):
        try:
            res = fn(old)
            sources.append({"name": name, "ok": True, "updated": now, "count": len(res) if isinstance(res, list) else 1})
            return res
        except Exception as e:
            prev = next((s for s in old.get("sources", []) if s["name"] == name), {})
            sources.append({"name": name, "ok": False, "error": str(e)[:120], "updated": prev.get("updated")})
            return None

    weeks = run("呼吸道哨点监测（周）", ari) or old.get("weeks", [])
    flu_r = run("流感监测周报", flu) or old.get("flu")
    months = run("法定传染病概况（月）", monthly) or old.get("months", [])

    data = {"updated": now, "weeks": weeks, "flu": flu_r, "months": months, "sources": sources}   # updated 每次都变 = 心跳
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), "utf-8")
    w = weeks[0] if weeks else {}
    print("weeks", len(weeks), "latest", w.get("year"), w.get("week"), "ili", w.get("ili"), w.get("ili_south"), w.get("ili_north"), w.get("level"))
    print("outpatient", {k: v["rate"] for k, v in (w.get("outpatient") or {}).items()})
    print("region", w.get("region"), "tips", len(w.get("tips", [])))
    print("with table", sum(1 for x in weeks if len(x["outpatient"]) > 3), "/", len(weeks), "oldest", weeks[-1]["year"] if weeks else None, weeks[-1]["week"] if weeks else None)
    print("flu", flu_r and (flu_r["until"], flu_r["a_pct"], flu_r["b_pct"], flu_r["outbreaks"]))
    print("months", len(months), months[0]["month"] if months else None, months and list(months[0]["diseases"].items())[:6], months and len(months[0]["diseases"]))
    print("failed", [s["name"] for s in sources if not s["ok"]])


if __name__ == "__main__":
    main()
