import cloudscraper
import json
import re
import time
import urllib.parse
from bs4 import BeautifulSoup
from config import BASE_URL, MIRRORS

# lxml requires C build tools on Windows + Python 3.14 has no wheel yet.
# Fallback to html.parser if lxml not available.
try:
    import lxml  # noqa
    PARSER = "lxml"
except ImportError:
    PARSER = "html.parser"

class MovieLinkBDScraper:
    def __init__(self, base_url=None):
        self.base_url = (base_url or BASE_URL).rstrip("/")
        self.scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        self._cache = {}
        self._cache_time = {}

    def _get_with_fallback(self, path, timeout=20):
        # Try base_url first, then mirrors
        urls = [self.base_url + path] + [m.rstrip("/") + path for m in MIRRORS if m.rstrip("/") != self.base_url]
        last_exc = None
        for url in urls:
            try:
                resp = self.scraper.get(url, timeout=timeout)
                if resp.status_code == 200 and "Attention Required" not in resp.text[:500]:
                    # update base_url to working one
                    self.base_url = "/".join(url.split("/")[:3])
                    return resp
            except Exception as e:
                last_exc = e
                continue
        if last_exc:
            raise last_exc
        raise Exception("All mirrors failed for " + path)

    def discover_active_domain(self):
        """Try to discover active domain from hub"""
        try:
            resp = self.scraper.get("https://movielinkbd.com", timeout=15)
            # look for href to movielinkbd.li
            m = re.search(r'https?://[a-z0-9\-]+\.movielinkbd\.li', resp.text)
            if m:
                self.base_url = m.group(0)
                return self.base_url
        except:
            pass
        return self.base_url

    def search(self, query, limit=10):
        cache_key = f"search:{query}"
        if cache_key in self._cache and time.time() - self._cache_time[cache_key] < 300:
            return self._cache[cache_key]

        encoded = urllib.parse.quote(query)
        path = f"/search?q={encoded}"
        resp = self._get_with_fallback(path)
        soup = BeautifulSoup(resp.text, PARSER)
        cards = soup.select(".movie-card")
        results = []
        for c in cards[:limit]:
            try:
                a = c.select_one("a")
                href = a.get("href") if a else None
                if not href:
                    continue
                # normalize href
                if href.startswith("/"):
                    full_url = self.base_url + href
                else:
                    full_url = href
                title_el = c.select_one(".title")
                title = title_el.get_text(strip=True) if title_el else "Unknown"

                # poster - try multiple attributes
                img = c.select_one(".image-container img")
                poster = ""
                if img:
                    # real poster might be in data-src or src, but placeholder is svg
                    for attr in ["data-src", "data-original", "data-lazy-src", "src"]:
                        v = img.get(attr, "")
                        if v and "mlbd_load.svg" not in v and v.startswith("http"):
                            poster = v
                            break
                    if not poster:
                        # fallback to inline style or other
                        poster = img.get("src","")
                    if "mlbd_load.svg" in poster:
                        poster = ""  # will fetch from details later if needed

                # meta badges
                quality = c.select_one(".quality")
                quality_text = quality.get_text(strip=True) if quality else ""
                lang = c.select_one(".language")
                lang_text = lang.get_text(strip=True) if lang else ""
                typ = c.select_one(".type")
                type_text = typ.get_text(strip=True) if typ else ""
                # upload time / views
                views = ""
                vb = c.select_one(".views-badge-top, .quality-top")
                if vb:
                    views = vb.get_text(strip=True)

                results.append({
                    "title": title,
                    "href": href,  # e.g. /movie/mKs_...
                    "url": full_url,
                    "poster": poster,
                    "quality": quality_text,
                    "language": lang_text,
                    "type": type_text,
                    "views": views,
                })
            except Exception as e:
                continue

        # If posters missing, they will be extracted from detail page fallback but keep search fast
        # cache
        self._cache[cache_key] = results
        self._cache_time[cache_key] = time.time()
        return results

    def get_details(self, movie_path):
        """movie_path like /movie/mKs_..."""
        if not movie_path.startswith("/"):
            movie_path = "/" + movie_path
        cache_key = f"details:{movie_path}"
        if cache_key in self._cache and time.time() - self._cache_time[cache_key] < 600:
            return self._cache[cache_key]

        resp = self._get_with_fallback(movie_path)
        soup = BeautifulSoup(resp.text, PARSER)

        # Try to parse mlbdInlinePlayerData JSON
        script = soup.find("script", id="mlbdInlinePlayerData")
        data = None
        if script and script.string:
            try:
                data = json.loads(script.string.strip())
            except:
                # sometimes string is truncated due to html entities
                txt = script.string.strip()
                # try to clean
                data = json.loads(txt)

        if not data:
            raise Exception("Player data not found for " + movie_path)

        # Extract extra info from page (storyline, genres etc) if available
        title = data.get("title", "")
        poster = data.get("poster", "")
        content_type = data.get("content_type", "movie")
        screenshots = data.get("screenshots", [])
        episodes = data.get("episodes", [])

        # Also try to get storyline, genres from HTML
        storyline = ""
        story_el = soup.select_one(".story-text, .storyline-box")
        if story_el:
            storyline = story_el.get_text(strip=True)[:1000]

        # Build normalized structure
        # For each episode, sources grouped by quality
        # Keep raw for bot

        # Extract info line for display
        info = {
            "title": title,
            "poster": poster,
            "content_type": content_type,
            "screenshots": screenshots,
            "episodes": episodes,
            "storyline": storyline,
            "raw": data,
            "url": self.base_url + movie_path,
            "movie_path": movie_path,
        }

        self._cache[cache_key] = info
        self._cache_time[cache_key] = time.time()
        return info

    def get_stream_sources(self, movie_path, quality_filter=None, audio_filter=None):
        details = self.get_details(movie_path)
        sources = []
        for ep in details["episodes"]:
            for s in ep.get("sources", []):
                if quality_filter and s.get("quality") != quality_filter:
                    continue
                if audio_filter and s.get("audio") != audio_filter:
                    continue
                s_copy = dict(s)
                s_copy["episode_label"] = ep.get("label", "Movie")
                s_copy["episode_id"] = ep.get("id")
                sources.append(s_copy)
        return sources, details

    def list_qualities(self, movie_path):
        details = self.get_details(movie_path)
        quals = set()
        audios = set()
        for ep in details["episodes"]:
            for s in ep.get("sources", []):
                quals.add(s.get("quality"))
                audios.add(s.get("audio"))
                for al in s.get("audio_languages", []):
                    audios.add(al)
        # sort qualities descending
        quals = sorted([q for q in quals if q], reverse=True)
        return quals, sorted(list(audios)), details


class NetMovieScraper:
    """pc.netmovie.site — API mapi.elochkaigolochla.com"""
    BASE = "https://mapi.elochkaigolochla.com/api/v1"
    POSTER_BASE = "https://img.elochkaigolochla.com"

    def __init__(self):
        self.session = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        self._cache = {}
        self._cache_time = {}

    def search(self, query, limit=6):
        import requests
        key = f"nm:{query}"
        if key in self._cache and time.time() - self._cache_time[key] < 300:
            return self._cache[key][:limit]
        # direct without proxy works (tested)
        url = f"{self.BASE}/new-search/movies?title={urllib.parse.quote(query)}&page=1&limit={limit}"
        try:
            # try direct first, fallback via worker proxy
            try:
                r = requests.get(url, timeout=12, headers={"Accept": "application/json"})
                if r.status_code != 200:
                    raise Exception(f"HTTP {r.status_code}")
                data = r.json()
            except:
                # fallback via cloudflare worker
                worker = f"https://delicate-rice-a82c.ahsanz0987.workers.dev/?url={urllib.parse.quote(url, safe='')}"
                r = self.session.get(worker, timeout=15)
                data = r.json()
            results = []
            for raw in (data.get("results") or data.get("data") or [])[:limit]:
                title = raw.get("title_en") or raw.get("title_ru") or raw.get("title") or "Untitled"
                year = raw.get("year") or 0
                pid = raw.get("kinopoisk_id") or raw.get("id")
                poster = raw.get("poster") or ""
                # players like in pc.netmovie.site
                players = []
                for p in (raw.get("player") or []):
                    players.append({
                        "url": p.get("url",""),
                        "translator": p.get("translator",""),
                        "quality": p.get("quality","HD"),
                        "source": p.get("source","iframe"),
                    })
                # also try to get server buttons like user showed: Player 1, Player 2 (Hindi) etc
                # players already cover
                results.append({
                    "title": f"{title} ({year})" if year else title,
                    "href": f"netmovie:{pid}",  # for bot callback
                    "id": pid,
                    "poster": poster,
                    "year": year,
                    "type": raw.get("type",""),
                    "imdb": (raw.get("ratings") or {}).get("imdb", {}).get("rating", 0) if isinstance(raw.get("ratings"), dict) else 0,
                    "description": raw.get("description","")[:500],
                    "players": players,
                    "raw": raw,
                    "quality": "HD",
                    "language": ", ".join([l.get("name","") for l in (raw.get("languages") or [])][:2]),
                })
            self._cache[key] = results
            self._cache_time[key] = time.time()
            return results
        except Exception as e:
            # don't crash whole bot
            print(f"NetMovie search err {e}")
            return []

    def get_players(self, kinopoisk_id):
        # Already have players from search cache, but also fetch fresh
        # Search cache has raw, otherwise fetch via catalog? For now search again with exact id via search?
        # Use get by id not available, so use search result cache
        for k, vals in self._cache.items():
            for v in vals:
                if str(v.get("id")) == str(kinopoisk_id):
                    return v
        # fallback: try to search by id via catalog endpoint (not ideal) — just return empty
        return None
