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
        # Fallback via public CORS proxies — for Render/Datacenter IPs blocked by CF
        # delicate-rice worker only allows mapi, so use proxy.cors.sh for MLBD
        import urllib.parse as up
        cors_proxies = [
            lambda u: "https://proxy.cors.sh/" + u,
            lambda u: "https://delicate-rice-a82c.ahsanz0987.workers.dev/?url=" + up.quote(u, safe=''),
        ]
        for url in urls:
            for mk in cors_proxies:
                try:
                    proxied = mk(url)
                    resp = self.scraper.get(proxied, timeout=timeout)
                    if resp.status_code == 200 and "Attention Required" not in resp.text[:500] and len(resp.text) > 1000 and "movie-card" in resp.text:
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
        for k, vals in self._cache.items():
            for v in vals:
                if str(v.get("id")) == str(kinopoisk_id):
                    return v
        return None


class TheMovieBoxScraper:
    BASE = "https://h5-api.aoneroom.com/wefeed-h5api-bff"
    HOSTS = ["themoviebox.xyz", "newfilm122.xyz"]
    def __init__(self):
        self.session = cloudscraper.create_scraper(browser={"browser":"chrome","platform":"windows","mobile":False})
        self._token = None
        self._token_exp = 0
        self._cache = {}
        self._cache_time = {}

    def _gen_token(self):
        import hashlib
        e = int(time.time())
        rev = str(e)[::-1]
        md5 = hashlib.md5(rev.encode()).hexdigest()
        return f"{e},{md5}"

    def _get_token(self, host="themoviebox.xyz"):
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        h = {"X-Client-Token": self._gen_token(), "X-Client-Info": json.dumps({"timezone":"Asia/Dhaka"}), "X-Request-Lang":"en"}
        r = self.session.get(f"{self.BASE}/home?host={host}", headers=h, timeout=15)
        xuser = r.headers.get("x-user") or r.headers.get("X-User")
        if not xuser:
            # try json body
            try:
                xuser = r.json().get("x-user") or r.text
            except: pass
        try:
            data = json.loads(xuser) if isinstance(xuser, str) else xuser
            self._token = data.get("token") or data
            self._token_exp = time.time() + 3600
            return self._token
        except Exception as e:
            # fallback: try to get from response json
            try:
                self._token = r.json()["data"]["token"]
                self._token_exp = time.time() + 3600
                return self._token
            except: raise e

    def search(self, query, limit=5):
        key = f"tb:{query}"
        if key in self._cache and time.time() - self._cache_time[key] < 300:
            return self._cache[key][:limit]
        try:
            token = self._get_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "X-Client-Info": json.dumps({"timezone":"Asia/Dhaka"}),
                "X-Request-Lang":"en",
                "Content-Type":"application/json",
                "Origin":"https://themoviebox.xyz",
                "Referer":"https://themoviebox.xyz/",
            }
            r = self.session.post(f"{self.BASE}/subject/search", json={"keyword":query,"page":1,"perPage":limit,"subjectType":0}, headers=headers, timeout=15)
            data = r.json()
            items = (data.get("data") or {}).get("items") or data.get("results") or []
            results = []
            for raw in items[:limit]:
                title = raw.get("title") or raw.get("name") or "Untitled"
                year = str(raw.get("releaseDate") or "")[:4] or raw.get("year") or ""
                sid = raw.get("subjectId") or raw.get("id")
                dpath = raw.get("detailPath") or ""
                # cover
                poster = ""
                if isinstance(raw.get("cover"), dict):
                    poster = raw["cover"].get("url") or ""
                elif isinstance(raw.get("poster"), str):
                    poster = raw.get("poster")
                # fallback pbcdn
                if not poster and raw.get("coverUrl"):
                    poster = raw.get("coverUrl")
                results.append({
                    "title": f"{title} ({year})" if year else title,
                    "href": f"tb:{sid}:{dpath}",
                    "id": sid,
                    "detailPath": dpath,
                    "poster": poster,
                    "year": year,
                    "type": raw.get("subjectType"),
                    "description": (raw.get("description") or "")[:400],
                    "raw": raw,
                    "quality": "HD",
                    "language": raw.get("genre") or "",
                })
            self._cache[key] = results
            self._cache_time[key] = time.time()
            return results
        except Exception as e:
            print(f"TheMovieBox search err {e}")
            return []

    def get_detail(self, subjectId, detailPath):
        try:
            token = self._get_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "X-Client-Info": json.dumps({"timezone":"Asia/Dhaka"}),
                "X-Request-Lang":"en",
                "Referer": f"https://themoviebox.xyz/detail/{detailPath}",
            }
            # try detail api
            r = self.session.get(f"{self.BASE}/detail?detailPath={detailPath}&se=0", headers=headers, timeout=15)
            j = r.json()
            data = j.get("data") or j
            subject = data.get("subject") or {}
            resource = data.get("resource") or {}
            # try play api to get streams
            streams = []
            try:
                rp = self.session.get(f"{self.BASE}/subject/play?subjectId={subjectId}&se=0&ep=1&detailPath={detailPath}&streamSignType=1", headers={**headers, "X-Source":""}, timeout=15)
                pj = rp.json()
                d = pj.get("data") or {}
                for k in ["streams","dash","hls"]:
                    for s in (d.get(k) or []):
                        streams.append({"url": s.get("url"), "quality": s.get("resolution") or s.get("quality") or "HD", "type": k})
            except: pass
            # fallback trailer
            trailer = (subject.get("trailer") or {}).get("videoAddress") or {}
            if trailer.get("url"):
                streams.append({"url": trailer["url"], "quality":"Trailer", "type":"mp4"})
            # also add resource external source if available (iframe host like ailok.pe)
            source = resource.get("source") or (resource.get("seasons") or [{}])[0].get("source") if isinstance(resource, dict) else None
            if source and isinstance(source, str) and "." in source:
                # construct embed url pattern observed
                streams.append({"url": f"https://{source}/embed/{subjectId}", "quality":"HD", "type":"iframe"})
            return {"subject": subject, "resource": resource, "streams": streams, "raw": data}
        except Exception as e:
            print(f"TB detail err {e}")
            return None
