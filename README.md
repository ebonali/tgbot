# MovieLinkBD Telegram Bot

Telegram bot যা **MovieLinkBD** ( `x33jbm.movielinkbd.li` dynamic mirror ) থেকে movie/series search করে এবং **Live Watch (ArtPlayer) এর মতোই** Quality + Audio সহ direct CDN stream/download link Telegram এ দেখায়।

Bot Token: `8844212979:AAGkeBq5dZAZ8vAU4082nlfCUufe_y5LHoo` (Admin: `8040452117`)

## Features
- 🔍 Text দিয়ে search — `Toxic`, `Resort`, etc. (English নাম)
- 📋 Result list 8 টা পর্যন্ত — poster + quality/language meta
- 🎬 Detail view — poster, title, content type, screenshots, episodes
- 🎥 Quality selector — 2160p / 1080p / 720p / 480p (যা available)
- 🎧 Audio selector — Hindi / Tamil / Dual (external_audio track সহ)
- ▶️ Watch Online — `https://cdn.dramalinkbd.tv/p/...` direct stream link (1 ট্যাপে unlock এর মতোই, কিন্তু TG তে direct)
- 📥 Download — `https://cdn.dramalinkbd.tv/d/...` direct download link
- 📺 Telegram Player — `send_video` দিয়ে TG এর ভিতরেই streaming
- 📺 Series support — Episode grid (Resort S01 Ep 1-100) + per-episode source
- 🛡️ Cloudflare bypass — `cloudscraper` + mirror fallback (MOVIELINKBD.com hub থেকে dynamic domain)
- ⚡ Cache + async executor (blocking I/O off main loop)

## File Structure
```
tg bot/
├── bot.py              # main bot handlers
├── scraper.py          # MovieLinkBD scraper (search + mlbdInlinePlayerData parser)
├── config.py           # token, base_url, mirrors
├── requirements.txt
├── .env.example
├── run.bat             # Windows quick start
└── README.md
```

## কিভাবে চালাবেন (Windows)

1. Install deps:
```bat
py -m pip install -r requirements.txt
```

2. (Optional) `.env` বানান বা `config.py` এ সরাসরি token/base_url আছে:
```env
BOT_TOKEN=8844212979:AAGkeBq5dZAZ8vAU4082nlfCUufe_y5LHoo
ADMIN_ID=8040452117
BASE_URL=https://x33jbm.movielinkbd.li
```

3. Run:
```bat
py bot.py
# or double click run.bat
```

Bot log এ `Bot starting with token 884421...` দেখালে OK।

## ব্যবহার
- `/start` → Welcome + help
- যেকোনো movie নাম লিখুন → Bot search করবে
- Result থেকে মুভি সিলেক্ট → Quality বাটন আসবে (✅ selected)
- ▶️ Stream Now / 📥 Download → Direct CDN URL + Open button
- 📺 Telegram Player → Bot video পাঠাবে (বড় ফাইল হলে direct link ব্যবহার করুন)

## কিভাবে কাজ করে (Technical)
1. `GET /search?q=toxic` → `.movie-card` parse (title, href `/movie/mKs_...`, poster, quality/language)
2. `GET /movie/mKs_...` → `<script id="mlbdInlinePlayerData" type="application/json">` থেকে JSON parse
   - `title`, `poster`, `content_type`, `screenshots`, `episodes[]`
   - প্রতিটি episode → `sources[]` → `quality`, `audio`, `audio_languages`, `url` (stream `.../p/...`), `download_url` (`.../d/...`), `external_audio[]`, `name`
3. Bot inline keyboard এ Quality/Audio দেখায়, stream/download_url থেকে direct link দেয়
4. User এর দেওয়া `art-video` example (`src="https://cdn.dramalinkbd.tv/p/Ricn5Y..."`) আসলে `sources[0].url` এর মতোই — scraper একই CDN link বের করে

## Dynamic Mirror Handling
`MovieLinkBD.com` hub থেকে active `.li` domain auto-discover করে। যদি `x33jbm.movielinkbd.li` block হয়, `MIRRORS` list থেকে fallback try করে:
```
x33jbm.movielinkbd.li → movielinkbd.one → .shop → .work → .tv
```
`.env` এ `BASE_URL` override করতে পারেন।

## Admin
- `ADMIN_ID = 8040452117` — `/stats` command only admin
- Token rotate করতে চাইলে BotFather থেকে revoke করে `.env` update করুন

## Security Note
- Token repo তে commit করবেন না — `.env` ব্যবহার করুন, `.gitignore` এ রাখুন
- CDN links expire হতে পারে (health_token) — bot always fresh fetch করে

## Troubleshooting
- `Attention Required! | Cloudflare` → cloudscraper fail → `py -m pip install --upgrade cloudscraper` করুন
- Telegram `BadRequest: Button_data_invalid` → callback_data >64 bytes → bot idx fallback ব্যবহার করে
- Video send fail (file too large) → Direct stream link ব্যবহার করুন, Telegram 2GB limit
