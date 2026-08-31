import os
import re
import json
import logging
import asyncio
import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from config import BOT_TOKEN, ADMIN_ID, BASE_URL
from scraper import MovieLinkBDScraper, NetMovieScraper

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def esc(text: str) -> str:
    return html.escape(str(text), quote=False)

scraper = MovieLinkBDScraper()
net_scraper = NetMovieScraper()

# In-memory store for search session: user_id -> last search results
user_sessions = {}
# details cache per user: movie_path -> last quality selection
user_quality = {}

def is_admin(user_id):
    return user_id == ADMIN_ID

def quality_label(q):
    return f"{q}p" if isinstance(q, int) else str(q)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome = (
        f"👋 হ্যালো {user.first_name}!\n\n"
        f"🎬 *MovieLinkBD Bot* এ স্বাগতম!\n\n"
        f"🔍 যেকোনো Movie বা Series এর *English নাম* লিখে পাঠান, আমি সাথে সাথে খুঁজে দেব।\n\n"
    )
    kb = [
        [InlineKeyboardButton("🔍 Search Example: Toxic", callback_data="example_toxic")],
        [InlineKeyboardButton("📢 Join Telegram Group", url="https://t.me/movielinkbd")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ]
    await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "❓ *Help - MovieLinkBD Bot*\n\n"
        "1️⃣ মুভির নাম লিখুন (English)\n"
        "2️⃣ Search result থেকে মুভি বেছে নিন\n"
        "3️⃣ Quality সিলেক্ট করুন (480p/720p/1080p/4K)\n"
        "4️⃣ ▶️ Stream লিংক (Telegram-এ দেখা যাবে) এবং 📥 Download লিংক পাবেন\n\n"
        "🎧 *Audio:* Hindi / Tamil / Dual - আলাদা Audio track সহ\n"
        "📺 *Series:* Episode লিস্ট থেকে Episode সিলেক্ট করুন\n\n"
        "⚠️ যদি কোনো মুভি না পাওয়া যায়, নামের বানান চেক করে আবার লিখুন।\n"
        "🔗 Official Site: MovieLinkBD.com\n"
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    if len(query) < 2:
        return
    if query.startswith("/"):
        return
    # ignore too long
    if len(query) > 80:
        query = query[:80]
    
    wait = await update.message.reply_text(f"🔍 *{query}* খুঁজছি...\n⏳ দয়া করে অপেক্ষা করুন...", parse_mode=ParseMode.MARKDOWN)
    
    try:
        loop = asyncio.get_event_loop()
        # search both sources in parallel — tolerate MLBD fail on Render IP
        mlbd_fut = loop.run_in_executor(None, lambda: scraper.search(query, limit=6))
        net_fut = loop.run_in_executor(None, lambda: net_scraper.search(query, limit=4))
        mlbd_results, net_results = await asyncio.gather(mlbd_fut, net_fut, return_exceptions=True)
        if isinstance(mlbd_results, Exception):
            logger.warning(f"MLBD search failed (Render IP blocked, fallback to proxy): {mlbd_results}")
            mlbd_results = []
        if isinstance(net_results, Exception):
            logger.warning(f"NetMovie search failed: {net_results}")
            net_results = []
        # tag source
        for r in mlbd_results:
            r["_src"] = "MLBD"
        for r in net_results:
            r["_src"] = "NetMovie"
        results = mlbd_results + net_results
    except Exception as e:
        logger.exception("search fail")
        await wait.edit_text(f"❌ Search error: `{e}`\n🔁 আবার চেষ্টা করুন।", parse_mode=ParseMode.MARKDOWN)
        return

    if not results:
        await wait.edit_text(f"😕 *{query}* এর জন্য কিছু পাওয়া যায়নি।\n🔤 English নামে আবার লিখুন।")
        return

    # store
    user_sessions[update.effective_user.id] = {"query": query, "results": results}

    # Build keyboard with results
    kb = []
    for idx, r in enumerate(results):
        src = r.get("_src","MLBD")
        icon = "🎬" if src=="MLBD" else "🌐"
        title_short = r['title'][:36] + ("..." if len(r['title'])>36 else "")
        cb = f"view:{r['href']}" if src=="MLBD" else f"nm:{r['id']}"
        if len(cb.encode()) > 64:
            cb = f"view_idx:{idx}"
        kb.append([InlineKeyboardButton(f"{icon} {idx+1}. {title_short}", callback_data=cb)])
    kb.append([InlineKeyboardButton("🔄 New Search", callback_data="new_search")])

    txt = f"✅ *{query}* এর জন্য {len(results)} টি রেজাল্ট পাওয়া গেছে (MLBD {len(mlbd_results)} + NetMovie {len(net_results)}):\n\n"
    for idx, r in enumerate(results):
        src = r.get("_src","MLBD")
        meta = " ".join(filter(None, [r.get('quality'), r.get('language'), r.get('type')]))
        txt += f"*{idx+1}. [{src}] {r['title']}*\n   `{meta}` — `{r['href']}`\n\n"
    txt += "👇 নিচে থেকে মুভি সিলেক্ট করুন:\n🎬=MovieLinkBD  🌐=NetMovie (pc.netmovie.site)"

    # Try to send with nice formatting, also add poster preview of first result if available
    try:
        await wait.delete()
    except: pass

    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data == "help":
        await help_cmd(update, context)
        return
    if data == "example_toxic":
        # simulate search
        update2 = update
        # fake message handling: just trigger search for toxic
        try:
            results = await asyncio.get_event_loop().run_in_executor(None, lambda: scraper.search("Toxic", limit=8))
            user_sessions[user_id] = {"query": "Toxic", "results": results}
            kb = []
            for idx, r in enumerate(results):
                title_short = r['title'][:40]
                kb.append([InlineKeyboardButton(f"{idx+1}. {title_short}", callback_data=f"view:{r['href']}")])
            txt = f"✅ Toxic এর জন্য {len(results)} টি রেজাল্ট:\n"
            for idx, r in enumerate(results):
                txt += f"{idx+1}. {r['title']}\n"
            await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:
            await query.edit_message_text(f"Error: {e}")
        return
    if data == "new_search":
        await query.edit_message_text("🔍 নতুন মুভির নাম লিখে পাঠান (English)। উদাহরণ: `Avengers`")
        return

    if data.startswith("view_idx:"):
        idx = int(data.split(":",1)[1])
        sess = user_sessions.get(user_id)
        if not sess:
            await query.edit_message_text("❌ Session expired. আবার search করুন।")
            return
        r = sess["results"][idx]
        if r.get("_src") == "NetMovie" or str(r.get("href","")).startswith("netmovie:"):
            data = f"nm:{r.get('id')}"
        else:
            href = r["href"]
            data = f"view:{href}"

    # NetMovie handler — must be before MLBD view
    if data.startswith("nm:"):
        kid = data.split(":",1)[1]
        await query.edit_message_text("⏳ NetMovie লোড হচ্ছে...\n🌐 pc.netmovie.site")
        # find in session
        sess = user_sessions.get(user_id)
        item = None
        if sess:
            for r in sess["results"]:
                if str(r.get("id")) == str(kid):
                    item = r
                    break
        if not item:
            item = net_scraper.get_players(kid)
        if not item:
            await query.edit_message_text("❌ NetMovie data not found, আবার search করুন।")
            return
        # Build caption with servers like user showed
        title = item.get("title","")
        poster = item.get("poster","")
        desc = item.get("description","")[:400]
        players = item.get("players", [])
        cap = f"🌐 <b>{esc(title)}</b>\n"
        cap += f"🎬 NetMovie — pc.netmovie.site\n"
        if item.get("year"):
            cap += f"📅 {esc(str(item.get('year')))}  ⭐ {esc(str(item.get('imdb')))}  🎧 {esc(item.get('language',''))}\n"
        if desc:
            cap += f"\n{esc(desc)}\n"
        # Determine m3u8 and iframe urls for servers
        # User example had 4 servers: Player1 HD, Player2 Hindi/Tamil/Telugu — we map players list
        kb = []
        # Add server buttons exactly like site: Player 1, Player 2 (Hindi) etc
        for p in players:
            label = p.get("translator") or p.get("quality") or p.get("source")
            # server-btn style: "Player 1 - HD" etc
            btn_text = f"▶️ {esc(label)} ({esc(p.get('quality','HD'))})"
            # For m3u8 we can send direct video, for iframe open URL
            if p.get("source") == "m3u8":
                # use netmovie watch callback to send video
                cb = f"nmplay:{kid}:{players.index(p)}"
                kb.append([InlineKeyboardButton(btn_text + " — TG Play", callback_data=cb)])
                # also add URL button for browser
                kb.append([InlineKeyboardButton(f"🌐 {label} Open in Browser", url=p.get("url"))])
            else:
                # iframe — must open in browser (like site's <iframe>)
                kb.append([InlineKeyboardButton(f"🌐 {label} — Open Player", url=p.get("url"))])
        # Also add direct m3u8 if available via pico? The site's player-servers had Player 1 HD etc — already covered
        kb.append([InlineKeyboardButton("🔙 Back to Results", callback_data="back_results")])
        try:
            await query.message.delete()
        except: pass
        if poster and poster.startswith("http"):
            try:
                await context.bot.send_photo(chat_id=query.message.chat_id, photo=poster, caption=cap, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
            except:
                await context.bot.send_message(chat_id=query.message.chat_id, text=cap, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        else:
            await context.bot.send_message(chat_id=query.message.chat_id, text=cap, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("nmplay:"):
        # NetMovie m3u8 direct TG play
        _, kid, idx = data.split(":",2)
        sess = user_sessions.get(user_id)
        item = None
        if sess:
            for r in sess["results"]:
                if str(r.get("id")) == str(kid):
                    item = r
                    break
        if not item:
            await query.answer("Not found", show_alert=True)
            return
        players = item.get("players", [])
        try:
            p = players[int(idx)]
        except:
            await query.answer("Player not found", show_alert=True)
            return
        url = p.get("url")
        # m3u8 urls like https://pikasmaind.site/getm3u8/7386Q2HX — need to resolve to actual m3u8
        await query.answer("📤 NetMovie TG Player loading...")
        try:
            # For m3u8 source, try to fetch resolved m3u8 (often returns json with url)
            # But direct url works as HLS — send to TG
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"▶️ NetMovie <b>{esc(item.get('title'))}</b> — {esc(p.get('translator'))} TG এ পাঠানো হচ্ছে...", parse_mode=ParseMode.HTML)
            # Telegram can play m3u8 via send_video if url is m3u8
            await context.bot.send_video(chat_id=query.message.chat_id, video=url, caption=f"{item.get('title')} — {p.get('translator')} ({p.get('quality')})", supports_streaming=True, read_timeout=60, write_timeout=60)
        except Exception as e:
            logger.warning(f"nmplay fail {e}")
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"⚠️ TG direct play fail, browser এ খুলুন:\n{url}\n\nTip: VLC > Open Network Stream এ paste করলে সবচেয়ে ভালো চলবে", disable_web_page_preview=False)
        return

    if data.startswith("view:"):
        movie_path = data.split(":",1)[1]
        # loading
        await query.edit_message_text("⏳ Details লোড হচ্ছে...\n🎬 ArtPlayer data fetch করছি...")
        try:
            details = await asyncio.get_event_loop().run_in_executor(None, lambda: scraper.get_details(movie_path))
        except Exception as e:
            logger.exception(e)
            await query.edit_message_text(f"❌ Details load failed: `{e}`", parse_mode=ParseMode.MARKDOWN)
            return

        # Build quality list
        try:
            quals, audios, _ = await asyncio.get_event_loop().run_in_executor(None, lambda: scraper.list_qualities(movie_path))
        except:
            quals = []
            audios = []

        # Build caption
        title = details.get("title","")
        poster = details.get("poster","")
        screenshots = details.get("screenshots", [])
        content_type = details.get("content_type","movie")
        episodes = details.get("episodes", [])

        # Choose default quality: prefer 720 or 1080
        default_q = None
        for pref in [720, 1080, 480, 2160, 1440]:
            if pref in quals:
                default_q = pref
                break
        if not default_q and quals:
            default_q = quals[0]
        user_quality[user_id] = {"movie_path": movie_path, "quality": default_q}

        # Count sources per quality - use HTML to avoid markdown _ issue
        cap = f"🎬 <b>{esc(title)}</b>\n"
        cap += f"🆔 <code>{esc(movie_path)}</code>\n"
        cap += f"📦 Type: <code>{esc(content_type)}</code>\n"
        if quals:
            cap += f"🎥 Available Qualities: {esc(', '.join([quality_label(q) for q in quals]))}\n"
        if audios:
            cap += f"🎧 Audio: {esc(', '.join(audios))}\n"
        if content_type != "movie" and episodes:
            cap += f"📺 Episodes: {len(episodes)}\n"
        cap += f"\n🔗 Original: {esc(details.get('url',''))}\n"
        cap += f"\n💡 নিচে Quality সিলেক্ট করুন, তারপর Stream/Download পাবেন।"

        # Keyboard: quality row
        kb = []
        if quals:
            row = []
            for q in quals:
                label = quality_label(q)
                # mark default
                if q == default_q:
                    label = f"✅ {label}"
                cb = f"q:{movie_path}:{q}"
                # ensure <64
                row.append(InlineKeyboardButton(label, callback_data=cb))
                if len(row)==3:
                    kb.append(row)
                    row=[]
            if row:
                kb.append(row)
        # Episode row if series
        if len(episodes) > 1:
            # show first 6 episodes
            ep_row = []
            for ep in episodes[:6]:
                lbl = ep.get("label","Ep")[:12]
                cb = f"ep:{movie_path}:{ep.get('id')}"
                ep_row.append(InlineKeyboardButton(lbl, callback_data=cb))
                if len(ep_row)==3:
                    kb.append(ep_row)
                    ep_row=[]
            if ep_row:
                kb.append(ep_row)
            if len(episodes)>6:
                kb.append([InlineKeyboardButton(f"📄 Show all {len(episodes)} episodes", callback_data=f"ep_all:{movie_path}")])

        # Action buttons for default quality
        if default_q is not None:
            kb.append([
                InlineKeyboardButton("▶️ Stream Now", callback_data=f"stream:{movie_path}:{default_q}"),
                InlineKeyboardButton("📥 Download", callback_data=f"dl:{movie_path}:{default_q}")
            ])
            # Telegram player button - sends video
            kb.append([InlineKeyboardButton("📺 Telegram Player (Send Video)", callback_data=f"tgplay:{movie_path}:{default_q}")])
        kb.append([
            InlineKeyboardButton("🔙 Back to Results", callback_data="back_results"),
            InlineKeyboardButton("🔍 New Search", callback_data="new_search")
        ])

        # Edit message, try to send poster as photo if possible
        # Since edit can't change to photo, we send new message with photo
        try:
            await query.message.delete()
        except: pass

        # If poster exists, send photo, else text - use HTML to avoid BadRequest
        if poster and poster.startswith("http"):
            try:
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=poster,
                    caption=cap,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(kb)
                )
            except Exception as e:
                logger.warning(f"photo send fail {e} -> fallback")
                try:
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=cap,
                        parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup(kb)
                    )
                except Exception as e2:
                    logger.warning(f"fallback also failed {e2}")
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=cap,
                        reply_markup=InlineKeyboardMarkup(kb)
                    )
        else:
            try:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=cap,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(kb)
                )
            except:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=cap,
                    reply_markup=InlineKeyboardMarkup(kb)
                )
        return

    if data.startswith("q:"):
        # quality select
        _, movie_path, q_str = data.split(":",2)
        q = int(q_str)
        user_quality[user_id] = {"movie_path": movie_path, "quality": q}
        # update keyboard to show selected
        try:
            quals, audios, details = await asyncio.get_event_loop().run_in_executor(None, lambda: scraper.list_qualities(movie_path))
        except Exception as e:
            await query.answer(f"Error {e}", show_alert=True)
            return

        # rebuild keyboard similar to view but with selected highlight - HTML
        title = details.get("title","")
        cap = f"🎬 <b>{esc(title)}</b>\n✅ Selected: <b>{esc(quality_label(q))}</b>\n\n"
        # show audio options for this quality
        sources, _ = await asyncio.get_event_loop().run_in_executor(None, lambda: scraper.get_stream_sources(movie_path, quality_filter=q))
        if sources:
            # group audios
            audios_q = set()
            for s in sources:
                audios_q.add(s.get("audio"))
                for al in s.get("audio_languages", []):
                    audios_q.add(al)
            cap += f"🎧 Available Audio for {esc(quality_label(q))}: {esc(', '.join(list(audios_q)))}\n"

        kb = []
        row=[]
        for qual in quals:
            label = quality_label(qual)
            if qual==q:
                label = f"✅ {label}"
            row.append(InlineKeyboardButton(label, callback_data=f"q:{movie_path}:{qual}"))
            if len(row)==3:
                kb.append(row); row=[]
        if row:
            kb.append(row)
        kb.append([
            InlineKeyboardButton("▶️ Stream Now", callback_data=f"stream:{movie_path}:{q}"),
            InlineKeyboardButton("📥 Download", callback_data=f"dl:{movie_path}:{q}")
        ])
        kb.append([InlineKeyboardButton("📺 Telegram Player", callback_data=f"tgplay:{movie_path}:{q}")])
        kb.append([InlineKeyboardButton("🔙 Back", callback_data=f"view:{movie_path}")])

        try:
            await query.edit_message_caption(caption=cap, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        except Exception:
            await query.edit_message_caption(caption=cap, reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("stream:") or data.startswith("dl:") or data.startswith("tgplay:"):
        is_dl = data.startswith("dl:")
        is_tg = data.startswith("tgplay:")
        parts = data.split(":",2)
        movie_path = parts[1]
        q = int(parts[2])
        action = "Download" if is_dl else ("Telegram Player" if is_tg else "Stream")
        await query.answer(f"🔗 {action} {quality_label(q)} লোড হচ্ছে...")
        try:
            sources, details = await asyncio.get_event_loop().run_in_executor(None, lambda: scraper.get_stream_sources(movie_path, quality_filter=q))
        except Exception as e:
            await query.message.reply_text(f"❌ Load failed: {e}")
            return
        if not sources:
            await query.message.reply_text("❌ এই Quality তে source পাওয়া যায়নি। অন্য Quality try করুন।")
            return

        # For video we only send first variant to avoid spam; for download show up to 2
        limit = 1 if not is_dl else 2
        for s in sources[:limit]:
            url = s.get("download_url" if is_dl else "url")
            audio_label = s.get("audio", "")
            name = s.get("name", "")[:60]
            quality = s.get("quality")
            # also external audio urls
            ext_audios = s.get("external_audio", [])
            txt = (
                f"🎬 <b>{esc(details.get('title'))}</b> — {esc(quality_label(quality))} - {esc(audio_label)}\n"
                f"📄 <code>{esc(name)}</code>\n"
                f"{'📥 Download' if is_dl else '▶️ Stream'} URL:\n{esc(url)}\n"
                f"\n<b>কিভাবে দেখবেন:</b>\n"
                f"1) <b>Open Stream</b> বাটনে ক্লিক করুন — browser এ ArtPlayer এর মতো direct play হবে\n"
                f"2) না চললে link copy করে <b>VLC &gt; Media &gt; Open Network Stream</b> এ paste করুন\n"
                f"3) 1.8GB mkv Telegram এ direct send হয় না (20s timeout), তাই browser/VLC ব্যবহার করুন\n"
            )
            if ext_audios:
                txt += f"\n🎧 External Audio:\n"
                for ea in ext_audios[:3]:
                    txt += f"• {esc(ea.get('label'))} — {esc(ea.get('url')[:80])}...\n"

            kb = []
            if url and url.startswith("http"):
                kb.append([InlineKeyboardButton(f"{'📥' if is_dl else '▶️'} Open {'Download' if is_dl else 'Stream'} ({audio_label})", url=url)])
            if not is_dl and s.get("download_url"):
                kb.append([InlineKeyboardButton("📥 Download Link", url=s.get("download_url"))])

            # Enable preview so Telegram shows playable thumbnail
            try:
                await query.message.reply_text(txt, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb) if kb else None, disable_web_page_preview=False)
            except:
                await query.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb) if kb else None, disable_web_page_preview=False)

            # NOTE: Direct Telegram send_video always fails for MovieLinkBD CDN (referer + 2GB mkv).
            # Instead we give a nice browser player — same as site's ArtPlayer.
            # If you host bot on a public server, you can add a /watch proxy.
            # For now we skip auto send_video to avoid spam/failed error loop.
            # To re-enable, uncomment below:
            # if not is_dl:
            #     try:
            #         await context.bot.send_video(chat_id=query.message.chat_id, video=url, caption=f"...", supports_streaming=True)
            #     except: pass

        return

    if data.startswith("ep:"):
        # episode selected - treat similar to stream but with episode filter
        _, movie_path, ep_id = data.split(":",2)
        await query.answer("Episode loading...")
        try:
            details = await asyncio.get_event_loop().run_in_executor(None, lambda: scraper.get_details(movie_path))
            ep = next((e for e in details["episodes"] if e.get("id")==ep_id), None)
            if not ep:
                await query.message.reply_text("Episode not found")
                return
            sources = ep.get("sources", [])
            txt = f"📺 Episode: *{ep.get('label')}*\nSources: {len(sources)}\n"
            kb=[]
            for s in sources[:5]:
                q = s.get("quality")
                lbl = f"{quality_label(q)} - {s.get('audio')}"
                # We'll make callback to stream specific episode+quality? simplified use first
                kb.append([InlineKeyboardButton(f"▶️ {lbl}", callback_data=f"stream:{movie_path}:{q}")])
                txt += f"• {lbl} — `{s.get('name','')[:50]}`\n"
            await query.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:
            await query.message.reply_text(f"Error {e}")
        return

    if data == "back_results":
        sess = user_sessions.get(user_id)
        if not sess:
            await query.edit_message_text("❌ No previous results. নতুন search করুন।")
            return
        # rebuild results keyboard
        results = sess["results"]
        kb = []
        for idx, r in enumerate(results):
            kb.append([InlineKeyboardButton(f"{idx+1}. {r['title'][:40]}", callback_data=f"view:{r['href']}")])
        txt = f"🔙 Back - {sess['query']} results:\n"
        for idx, r in enumerate(results):
            txt+=f"{idx+1}. {r['title']}\n"
        # if current is photo, we need to send new message
        try:
            await query.message.delete()
        except: pass
        await context.bot.send_message(chat_id=query.message.chat_id, text=txt, reply_markup=InlineKeyboardMarkup(kb))
        return


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(f"🤖 Bot Active\nBase: {scraper.base_url}\nSessions: {len(user_sessions)}")

def main():
    # Python 3.14 + Windows: no current event loop fix
    import asyncio, sys, os, threading
    try:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    # Render health server (for Web Service + UptimeRobot)
    # Render sets PORT, we must listen or deploy fails. Runs in background thread.
    try:
        port = int(os.getenv("PORT", "0"))
        if port:
            from http.server import HTTPServer, BaseHTTPRequestHandler
            class H(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.send_header("Content-type","text/plain")
                    self.end_headers()
                    self.wfile.write(b"MovieLinkBD Bot OK")
                def log_message(self, *a, **k): pass
            def _serve():
                try:
                    HTTPServer(("0.0.0.0", port), H).serve_forever()
                except Exception as e:
                    logger.warning(f"health server fail {e}")
            threading.Thread(target=_serve, daemon=True).start()
            print(f"Health server on 0.0.0.0:{port}")
    except Exception as e:
        logger.warning(f"health server not started {e}")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search))
    print(f"Bot starting with token {BOT_TOKEN[:6]}... base {BASE_URL} admin {ADMIN_ID}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
