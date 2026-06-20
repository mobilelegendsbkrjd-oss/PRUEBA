import os
import re
import base64
import random
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"

OUTPUT_FILE = os.getenv("OUTPUT_FILE", "M328x.m3u")

PROVIDERS = [
    ("TvporinternetHD", "https://www.tvporinternet2.com", True),
    ("Tv Libre Futbol", "https://www.librefutbol2.com", True),
    ("CableVisionHD", "https://www.cablevisionhd.com", True),
    ("Teveplus", "https://www.tvplusgratis2.com/", True),
    ("Telegratis", "https://www.telegratishd.com/", True),
    ("VerCableHD", "https://www.vertvcable.com/", False),
    ("SinTelevisor", "https://www.thesintelevisor.com/", False),
]

PROVIDER_PRIORITY = {
    "TvporinternetHD": 1,
    "CableVisionHD": 2,
    "Teveplus": 3,
    "Telegratis": 4,
    "VerCableHD": 5,
    "SinTelevisor": 6,
    "Tv Libre Futbol": 7,
}

MAX_WORKERS = int(os.getenv("MAX_WORKERS", "6"))
TIMEOUT = int(os.getenv("TIMEOUT", "20"))
MAX_DEPTH = 6


def get_origin(url):
    try:
        p = urlparse(url)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
    except Exception:
        pass
    return ""


def make_session():
    session = requests.Session()

    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.2,
        status_forcelist=[403, 408, 429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
        raise_on_status=False,
    )

    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=MAX_WORKERS * 2,
        pool_maxsize=MAX_WORKERS * 2,
    )

    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return session


SESSION = make_session()


def headers(referer=None):
    origin = get_origin(referer or "")

    return {
        "User-Agent": USER_AGENT,
        "Referer": referer or "",
        "Origin": origin,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-MX,es;q=0.9,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
    }


def get_html(url, referer=None):
    time.sleep(random.uniform(0.15, 0.65))

    r = SESSION.get(
        url,
        headers=headers(referer or url),
        timeout=TIMEOUT,
        allow_redirects=True
    )

    if r.status_code in [403, 429]:
        time.sleep(random.uniform(2.0, 4.0))
        r = SESSION.get(
            url,
            headers=headers(referer or url),
            timeout=TIMEOUT,
            allow_redirects=True
        )

    r.raise_for_status()
    return r.text, r.url


def clean_name(name):
    name = name.replace("📶", "")
    name = re.sub(r"\bEN\s+VIVO\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name)
    return name.strip(" -|")


def normalize_name(name):
    n = clean_name(name).lower()
    n = re.sub(
        r"\b(en vivo|stream|server|servidor|backup|opcion|opción|hd|fhd|sd|mx|lat|latino)\b",
        "",
        n
    )
    n = re.sub(r"\b\d+\b", "", n)
    n = re.sub(r"[^a-z0-9áéíóúñ]+", "", n)
    return n.strip()


def category(name):
    n = name.lower()

    if any(x in n for x in [
        "espn", "fox sport", "tudn", "bein", "sport", "deportes",
        "directv", "tyc", "gol", "liga", "champions", "futbol",
        "fútbol", "nba", "nfl", "mlb", "ufc"
    ]):
        return "Deportes"

    if any(x in n for x in [
        "cnn", "foro", "milenio", "noticias", "news",
        "24h", "adn", "nmas", "n+", "dw"
    ]):
        return "Noticias"

    if any(x in n for x in [
        "hbo", "cinemax", "cine", "warner", "tnt", "space",
        "star", "fx", "sony", "paramount", "universal", "golden", "amc"
    ]):
        return "Cine y Series"

    if any(x in n for x in [
        "cartoon", "disney", "nick", "boomerang",
        "tooncast", "kids", "infantil", "discovery kids"
    ]):
        return "Infantiles"

    if any(x in n for x in [
        "mtv", "music", "musica", "música", "telehit", "vh1", "bandamax"
    ]):
        return "Música"

    return "Entretenimiento"


def valid_channel(link, title, base):
    if not link or not title:
        return False

    l = link.lower()
    t = title.lower()

    bad = [
        "paypal", "telegram", "whatsapp", "facebook", "instagram",
        "twitter", "linktre", "/category/", "/tag/", "mailto:",
        "javascript:"
    ]

    bad_title = [
        "telegram", "soporte", "apoya", "donar",
        "reportar", "contacto", "dmca"
    ]

    if any(x in l for x in bad):
        return False

    if any(x in t for x in bad_title):
        return False

    if link.rstrip("/") == base.rstrip("/"):
        return False

    return link.startswith(base) or not link.startswith("http")


def parse_provider(provider, base):
    print(f"🌐 Leyendo {provider}")

    html, final_url = get_html(base, base)
    soup = BeautifulSoup(html, "html.parser")
    found = []

    def add_anchor(a):
        link = a.get("href", "").strip()
        img = a.find("img")

        title = (
            a.get("title", "").strip()
            or (img.get("alt", "").strip() if img else "")
            or a.get_text(" ", strip=True)
        )

        title = clean_name(title)

        if not valid_channel(link, title, base):
            return

        raw_img = ""
        if img:
            raw_img = (
                img.get("data-src")
                or img.get("data-lazy-src")
                or img.get("src")
                or ""
            ).strip()

        found.append({
            "name": title,
            "page": urljoin(base + "/", link),
            "logo": urljoin(base + "/", raw_img) if raw_img else "",
            "provider": provider,
            "referer": base,
            "group": category(title),
        })

    for script in soup.find_all("script"):
        data = script.string or script.get_text() or ""

        if "homeChannels" in data or "const channels" in data:
            for block in re.findall(r"`([\s\S]*?)`", data):
                if len(block) > 100:
                    sub = BeautifulSoup(block, "html.parser")
                    for a in sub.select("a"):
                        add_anchor(a)

    for a in soup.select("a:has(img), a"):
        add_anchor(a)

    unique = {}
    for item in found:
        unique[item["page"]] = item

    print(f"✅ {provider}: {len(unique)} páginas")
    return list(unique.values())


def extract_video_url(text):
    clean = (
        text.replace("\\/", "/")
        .replace("\\u0026", "&")
        .replace("&amp;", "&")
    )

    patterns = [
        r'''setupPlayer\s*\(\s*["']([^"']+\.m3u8[^"']*)["']''',
        r'''["'](https?://[^"'\s<>]+?\.m3u8[^"'\s<>]*)["']''',
        r'''file\s*:\s*["']([^"']+\.m3u8[^"']*)["']''',
        r'''["']file["']\s*:\s*["']([^"']+\.m3u8[^"']*)["']''',
        r'''source\s*:\s*["']([^"']+\.m3u8[^"']*)["']''',
        r'''src\s*:\s*["']([^"']+\.m3u8[^"']*)["']''',
        r'''videoUrl\s*=\s*["']([^"']+\.m3u8[^"']*)["']''',
        r'''hls\s*=\s*["']([^"']+\.m3u8[^"']*)["']''',
        r'''(https?://[^"'\s<>]+?hoca8\.com/[^"'\s<>]+)''',
        r'''(https?://[^"'\s<>]+?footy\.php[^"'\s<>]*)''',
    ]

    for p in patterns:
        m = re.search(p, clean, re.I | re.S)
        if m:
            value = m.group(1)
            if value.startswith("http"):
                return value.strip()

    return None


def extract_base64_urls(text):
    urls = []

    for enc in re.findall(r'''atob\(["']([^"']+)["']\)''', text):
        try:
            dec = base64.b64decode(enc).decode("utf-8", errors="ignore")

            if "http" in dec:
                urls.extend(re.findall(r'''https?://[^"'\s<>]+''', dec))

        except Exception:
            pass

    return urls


def unpack_eval_like(text):
    return (
        text
        .replace("\\x3d", "=")
        .replace("\\x26", "&")
        .replace("\\/", "/")
    )


def find_iframes(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    out = []

    for iframe in soup.select("iframe"):
        src = iframe.get("src") or iframe.get("data-src") or ""
        src = src.strip()

        if src:
            out.append(urljoin(base_url, src.replace("&amp;", "&")))

    return out


def resolve_page(start_url, start_referer):
    current = start_url
    referer = start_referer
    visited = set()

    for depth in range(MAX_DEPTH):
        if current in visited:
            break

        visited.add(current)

        try:
            html, final_url = get_html(current, referer)
            current = final_url

            direct = extract_video_url(html)
            if direct:
                return {
                    "url": direct,
                    "referer": current,
                }

            unpacked = unpack_eval_like(html)

            direct = extract_video_url(unpacked)
            if direct:
                return {
                    "url": direct,
                    "referer": current,
                }

            for u in extract_base64_urls(html):
                if ".m3u8" in u or "footy.php" in u or "hoca8.com" in u:
                    return {
                        "url": u,
                        "referer": current,
                    }

            iframes = find_iframes(html, current)

            if iframes:
                referer = current
                current = iframes[0]
                continue

            break

        except Exception:
            break

    return None


def worker(channel):
    resolved = resolve_page(channel["page"], channel["referer"])

    if not resolved:
        return None

    return {
        "name": channel["name"],
        "logo": channel["logo"],
        "group": channel["group"],
        "stream": resolved["url"],
        "referer": resolved["referer"],
        "user_agent": USER_AGENT,
        "provider": channel["provider"],
        "page": channel["page"],
        "norm": normalize_name(channel["name"]),
    }


def better_item(new_item, old_item):
    if old_item is None:
        return True

    new_priority = PROVIDER_PRIORITY.get(new_item["provider"], 99)
    old_priority = PROVIDER_PRIORITY.get(old_item["provider"], 99)

    if new_priority < old_priority:
        return True

    if new_priority > old_priority:
        return False

    return len(new_item["stream"]) > len(old_item["stream"])


def build_output_items(resolved):
    general_best = {}
    alternatives = []

    for item in resolved:
        norm = item.get("norm") or normalize_name(item["name"])

        if not norm:
            norm = item["name"].lower().strip()

        if better_item(item, general_best.get(norm)):
            general_best[norm] = item

        alt = dict(item)
        alt["group"] = item["provider"]
        alt["name"] = clean_name(item["name"])
        alternatives.append(alt)

    final_items = []

    for item in general_best.values():
        main = dict(item)
        main["name"] = clean_name(item["name"])
        main["group"] = item["group"]
        final_items.append(main)

    final_items.extend(alternatives)

    return final_items, len(general_best), len(alternatives)


def escape_attr(value):
    value = value or ""
    return (
        value
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def write_m3u(items, output):
    lines = ["#EXTM3U"]
    count = 0
    seen = set()

    for item in sorted(items, key=lambda x: (x["group"], x["name"], x["provider"])):
        key = (item["group"], item["name"], item["stream"])

        if key in seen:
            continue

        seen.add(key)

        name = clean_name(item["name"])
        logo = item["logo"]
        group = item["group"]
        referer = item["referer"]
        origin = get_origin(referer)

        lines.append(
            f'#EXTINF:-1 tvg-name="{escape_attr(name)}" tvg-logo="{escape_attr(logo)}" group-title="{escape_attr(group)}",{name}'
        )

        lines.append(f'#EXTVLCOPT:http-user-agent={item["user_agent"]}')
        lines.append(f'#EXTVLCOPT:http-referrer={referer}')
        lines.append(f'#EXTHTTP:{{"User-Agent":"{item["user_agent"]}","Referer":"{referer}","Origin":"{origin}"}}')
        lines.append(item["stream"])
        lines.append("")

        count += 1

    with open(output, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return count


def main():
    output = OUTPUT_FILE
    pages = []

    for provider, base, enabled in PROVIDERS:
        if not enabled:
            print(f"⏭️ Saltando {provider}")
            continue

        try:
            pages.extend(parse_provider(provider, base))
        except Exception as e:
            print(f"❌ Error en {provider}: {e}")

    print(f"\n🔎 Resolviendo streams finales: {len(pages)} páginas\n")

    resolved = []
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(worker, ch) for ch in pages]

        for future in as_completed(futures):
            done += 1

            try:
                item = future.result()

                if item:
                    resolved.append(item)
                    print(f"✅ {done}/{len(pages)} {item['name']} [{item['provider']}]")
                else:
                    print(f"❌ {done}/{len(pages)} sin stream")

            except Exception as e:
                print(f"❌ {done}/{len(pages)} error: {e}")

    output_items, general_count, alternative_count = build_output_items(resolved)
    total = write_m3u(output_items, output)

    print("\n🔥 LISTO")
    print(f"📺 Canales únicos en categorías generales: {general_count}")
    print(f"🔁 Alternativas por sitio web: {alternative_count}")
    print(f"📦 Entradas totales guardadas: {total}")
    print(f"📁 Archivo: {output}")


if __name__ == "__main__":
    main()
