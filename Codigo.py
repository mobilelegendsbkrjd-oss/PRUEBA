import os
import re
import base64
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"

OUTPUT_FILE = os.getenv("OUTPUT_FILE", "M328x.m3u")

PROVIDERS = [
    ("TvporinternetHD", "https://www.tvporinternet2.com"),
    ("Tv Libre Futbol", "https://www.librefutbol2.com"),
    ("CableVisionHD", "https://www.cablevisionhd.com"),
    ("Teveplus", "https://www.tvplusgratis2.com/"),
    ("Telegratis", "https://www.telegratishd.com/"),
]

PROVIDER_PRIORITY = {
    "TvporinternetHD": 1,
    "CableVisionHD": 2,
    "Teveplus": 3,
    "Telegratis": 4,
    "Tv Libre Futbol": 7,
}

MAX_WORKERS = 8
TIMEOUT = 15
MAX_DEPTH = 6
STREAM_TEST_TIMEOUT = 10


def get_origin(url):
    try:
        p = urlparse(url)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
    except Exception:
        pass
    return ""


def headers(referer=None):
    return {
        "User-Agent": USER_AGENT,
        "Referer": referer or "",
        "Origin": get_origin(referer or ""),
        "Accept": "*/*",
        "Connection": "keep-alive",
    }


def get_html(url, referer=None):
    r = requests.get(
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
        "cnn", "foro", "milenio", "noticias", "news", "24h",
        "adn", "nmas", "n+", "dw"
    ]):
        return "Noticias"

    if any(x in n for x in [
        "hbo", "cinemax", "cine", "warner", "tnt", "space",
        "star", "fx", "sony", "paramount", "universal",
        "golden", "amc"
    ]):
        return "Cine y Series"

    if any(x in n for x in [
        "cartoon", "disney", "nick", "boomerang", "tooncast",
        "kids", "infantil", "discovery kids"
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
        "telegram", "soporte", "apoya", "donar", "reportar",
        "contacto", "dmca"
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
        text.replace("\\x3d", "=")
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


def stream_works(url, referer):
    try:
        r = requests.get(
            url,
            headers=headers(referer),
            timeout=STREAM_TEST_TIMEOUT,
            allow_redirects=True
        )

        if r.status_code >= 400:
            return False

        text = r.text[:8000]

        if "#EXTM3U" not in text:
            return False

        variant = None

        for line in r.text.splitlines():
            line = line.strip()

            if not line or line.startswith("#"):
                continue

            if ".m3u8" in line:
                variant = urljoin(r.url, line)
                break

        if variant:
            r2 = requests.get(
                variant,
                headers=headers(referer),
                timeout=STREAM_TEST_TIMEOUT,
                allow_redirects=True
            )

            if r2.status_code >= 400:
                return False

            text2 = r2.text[:8000]

            if "#EXTM3U" not in text2:
                return False

            if "#EXTINF" in text2 or "#EXT-X-TARGETDURATION" in text2 or "#EXT-X-MEDIA-SEQUENCE" in text2:
                return True

            return False

        if "#EXTINF" in text or "#EXT-X-TARGETDURATION" in text or "#EXT-X-MEDIA-SEQUENCE" in text:
            return True

        return False

    except Exception:
        return False


def worker(channel):
    resolved = resolve_page(channel["page"], channel["referer"])

    if not resolved:
        return None

    if not stream_works(resolved["url"], resolved["referer"]):
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

    for item in resolved:
        norm = item.get("norm") or normalize_name(item["name"])

        if not norm:
            norm = item["name"].lower().strip()

        if better_item(item, general_best.get(norm)):
            general_best[norm] = item

    final_items = []

    for item in general_best.values():
        main = dict(item)
        main["name"] = clean_name(item["name"])
        main["group"] = item["group"]
        final_items.append(main)

    return final_items, len(general_best)


def write_m3u(items, output):
    folder = os.path.dirname(output)
    if folder:
        os.makedirs(folder, exist_ok=True)

    lines = ["#EXTM3U"]
    count = 0
    seen = set()

    for item in sorted(items, key=lambda x: (x["group"], x["name"])):
        key = item["stream"]

        if key in seen:
            continue

        seen.add(key)

        name = clean_name(item["name"])
        logo = item["logo"]
        group = item["group"]

        lines.append(
            f'#EXTINF:-1 tvg-name="{name}" tvg-logo="{logo}" group-title="{group}",{name}'
        )
        lines.append(f'#EXTVLCOPT:http-user-agent={item["user_agent"]}')
        lines.append(f'#EXTVLCOPT:http-referrer={item["referer"]}')
        lines.append(item["stream"])
        lines.append("")
        count += 1

    with open(output, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return count


def main():
    print(f"📁 Archivo de salida: {OUTPUT_FILE}")

    pages = []

    for provider, base in PROVIDERS:
        try:
            pages.extend(parse_provider(provider, base))
        except Exception as e:
            print(f"❌ Error en {provider}: {e}")

    print(f"\n🔎 Resolviendo y probando streams finales: {len(pages)} páginas")
    print("Solo se guardan canales que sí respondan como HLS válido.\n")

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
                    print(f"❌ {done}/{len(pages)} sin stream válido")

            except Exception:
                print(f"❌ {done}/{len(pages)} error")

    output_items, general_count = build_output_items(resolved)
    total = write_m3u(output_items, OUTPUT_FILE)

    print("\n🔥 LISTO")
    print(f"📺 Canales únicos guardados: {general_count}")
    print(f"📦 Entradas totales guardadas: {total}")
    print("📁 Archivo:")
    print(OUTPUT_FILE)


if __name__ == "__main__":
    main()
