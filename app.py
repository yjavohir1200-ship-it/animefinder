import os, json, sqlite3, secrets, time
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, PreCheckoutQueryHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "change-this-admin-password")
DATABASE = os.getenv("DATABASE", "animebot.db")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
UZCARD_PROVIDER = os.getenv("UZCARD_PROVIDER", "manual")  # integrate a real provider before accepting live payments
UZCARD_MERCHANT_SECRET = os.getenv("UZCARD_MERCHANT_SECRET", "")
PREMIUM_DAYS = int(os.getenv("PREMIUM_DAYS", "30"))
PREMIUM_STARS = int(os.getenv("PREMIUM_STARS", "150"))

GENRES = ["Action", "Adventure", "Comedy", "Drama", "Fantasy", "Horror", "Mystery", "Romance", "Sci-Fi", "Slice of Life", "Sports", "Supernatural", "Thriller", "Isekai"]
GENRE_RU = {"Action":"Экшен","Adventure":"Приключения","Comedy":"Комедия","Drama":"Драма","Fantasy":"Фэнтези","Horror":"Ужасы","Mystery":"Детектив","Romance":"Романтика","Sci-Fi":"Фантастика","Slice of Life":"Повседневность","Sports":"Спорт","Supernatural":"Сверхъестественное","Thriller":"Триллер","Isekai":"Исекай"}
GENRE_UZ = {"Action":"Jangari","Adventure":"Sarguzasht","Comedy":"Komediya","Drama":"Drama","Fantasy":"Fantastika","Horror":"Qo‘rqinchli","Mystery":"Sirli","Romance":"Romantika","Sci-Fi":"Ilmiy-fantastika","Slice of Life":"Kundalik hayot","Sports":"Sport","Supernatural":"G‘ayritabiiy","Thriller":"Triller","Isekai":"Isekai"}

@contextmanager
def db():
    con = sqlite3.connect(DATABASE)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          telegram_id INTEGER PRIMARY KEY, language TEXT NOT NULL DEFAULT 'uz',
          is_premium INTEGER NOT NULL DEFAULT 0, premium_until TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS favorites(
          telegram_id INTEGER NOT NULL, anime_id INTEGER NOT NULL, title TEXT NOT NULL,
          PRIMARY KEY(telegram_id, anime_id)
        );
        CREATE TABLE IF NOT EXISTS payments(
          id TEXT PRIMARY KEY, telegram_id INTEGER NOT NULL, provider TEXT NOT NULL,
          amount INTEGER NOT NULL, currency TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, paid_at TEXT
        );
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('premium_days',?)", (str(PREMIUM_DAYS),))

init_db()
app = FastAPI(title="AnimeFinder")
telegram_app: Optional[Application] = None

TEXT = {
 "uz": {"hello":"🎌 AnimeFinder botiga xush kelibsiz!","search":"🔎 Anime nomini yuboring yoki janr tanlang.","genres":"🎭 Janrni tanlang:","premium":"⭐ Premium obuna","favorites":"❤️ Sevimlilar","lang":"🌐 Tilni o‘zgartirish","help":"Buyruqlar: /start /search /genres /premium /favorites /lang"},
 "ru": {"hello":"🎌 Добро пожаловать в AnimeFinder!","search":"🔎 Отправьте название аниме или выберите жанр.","genres":"🎭 Выберите жанр:","premium":"⭐ Premium-подписка","favorites":"❤️ Избранное","lang":"🌐 Сменить язык","help":"Команды: /start /search /genres /premium /favorites /lang"}
}
def user_lang(uid):
    with db() as c:
        r=c.execute("SELECT language FROM users WHERE telegram_id=?",(uid,)).fetchone()
        return r["language"] if r else "uz"
def ensure_user(uid):
    with db() as c: c.execute("INSERT OR IGNORE INTO users(telegram_id) VALUES(?)",(uid,))
def is_premium(uid):
    with db() as c:
        r=c.execute("SELECT is_premium,premium_until FROM users WHERE telegram_id=?",(uid,)).fetchone()
    if not r or not r["is_premium"]: return False
    if r["premium_until"] and r["premium_until"] < datetime.now(timezone.utc).isoformat():
        with db() as c: c.execute("UPDATE users SET is_premium=0 WHERE telegram_id=?",(uid,))
        return False
    return True

async def anilist_search(query="", genre=None, page=1):
    gql = """query ($search:String,$genre:String,$page:Int) {
      Page(page:$page, perPage:10) {
        media(search:$search, genre:$genre, type:ANIME, sort:POPULARITY_DESC) {
          id title { romaji english native } description(asHtml:false)
          episodes seasonYear averageScore genres coverImage { large }
          siteUrl format status
        }
      }
    }"""
    variables={"search":query or None,"genre":genre,"page":page}
    async with httpx.AsyncClient(timeout=15) as client:
        resp=await client.post("https://graphql.anilist.co",json={"query":gql,"variables":variables})
        resp.raise_for_status()
        data=resp.json()
    return data.get("data",{}).get("Page",{}).get("media",[])

def clean(s, limit=450):
    import re, html
    s=html.unescape(re.sub(r"<[^>]+>","",s or ""))
    return (s[:limit]+"…") if len(s)>limit else s

def anime_caption(a, lang="uz"):
    title=a.get("title") or {}
    name=title.get("english") or title.get("romaji") or title.get("native") or "Unknown"
    genres=", ".join(a.get("genres") or [])
    desc=clean(a.get("description") or ("Tavsif mavjud emas." if lang=="uz" else "Описание отсутствует."),500)
    year=a.get("seasonYear") or "—"; episodes=a.get("episodes") or "—"; score=(a.get("averageScore") or "—")
    if lang=="ru":
        return f"🎬 <b>{name}</b>\n📅 Год: {year} | Серии: {episodes}\n⭐ Рейтинг AniList: {score}/100\n🎭 Жанры: {genres}\n\n{desc}\n\n<a href=\"{a.get('siteUrl','https://anilist.co')}\">Подробнее на AniList</a>"
    return f"🎬 <b>{name}</b>\n📅 Yil: {year} | Qismlar: {episodes}\n⭐ AniList reytingi: {score}/100\n🎭 Janrlar: {genres}\n\n{desc}\n\n<a href=\"{a.get('siteUrl','https://anilist.co')}\">Batafsil AniList’da</a>"

async def show_results(update, context, query="", genre=None):
    uid=update.effective_user.id; lang=user_lang(uid)
    try: results=await anilist_search(query,genre)
    except Exception:
        await update.effective_message.reply_text("Qidiruv xizmati vaqtincha ishlamayapti. Keyinroq urinib ko‘ring.")
        return
    if not results:
        await update.effective_message.reply_text("Hech narsa topilmadi." if lang=="uz" else "Ничего не найдено.")
        return
    for a in results[:5]:
        title=a.get("title") or {}; name=title.get("english") or title.get("romaji") or "Anime"
        keyboard=InlineKeyboardMarkup([[InlineKeyboardButton("❤️ Saqlash" if lang=="uz" else "❤️ В избранное", callback_data=f"fav:{a['id']}:{name[:45]}")]])
        await update.effective_message.reply_photo(photo=a["coverImage"]["large"],caption=anime_caption(a,lang),parse_mode="HTML",reply_markup=keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; ensure_user(uid); lang=user_lang(uid)
    kb=[[InlineKeyboardButton("🔎 Qidirish" if lang=="uz" else "🔎 Поиск",callback_data="search")],
        [InlineKeyboardButton("🎭 Janrlar" if lang=="uz" else "🎭 Жанры",callback_data="genres")],
        [InlineKeyboardButton(TEXT[lang]["premium"],callback_data="premium"),InlineKeyboardButton(TEXT[lang]["favorites"],callback_data="favorites")],
        [InlineKeyboardButton(TEXT[lang]["lang"],callback_data="language")]]
    await update.effective_message.reply_text(TEXT[lang]["hello"]+"\n\n"+TEXT[lang]["help"],reply_markup=InlineKeyboardMarkup(kb))

async def search_cmd(update, context):
    lang=user_lang(update.effective_user.id)
    if context.args: await show_results(update,context," ".join(context.args))
    else:
        context.user_data["awaiting_search"]=True
        await update.effective_message.reply_text("Anime nomini yozing:" if lang=="uz" else "Введите название аниме:")

async def genres_cmd(update, context):
    lang=user_lang(update.effective_user.id)
    rows=[]
    for i in range(0,len(GENRES),2):
        rows.append([InlineKeyboardButton((GENRE_UZ if lang=="uz" else GENRE_RU).get(g,g),callback_data=f"genre:{g}") for g in GENRES[i:i+2]])
    await update.effective_message.reply_text(TEXT[lang]["genres"],reply_markup=InlineKeyboardMarkup(rows))

async def premium_cmd(update, context):
    uid=update.effective_user.id; lang=user_lang(uid)
    keyboard=InlineKeyboardMarkup([[InlineKeyboardButton(f"⭐ {PREMIUM_STARS} Stars / 30 kun" if lang=="uz" else f"⭐ {PREMIUM_STARS} Stars / 30 дней",callback_data="buy_stars")],
                                   [InlineKeyboardButton("💳 Uzcard/Humo" if lang=="uz" else "💳 Uzcard/Humo",callback_data="buy_card")]])
    await update.effective_message.reply_text("Premium: kengaytirilgan qidiruv, sevimlilar va yangi funksiyalar." if lang=="uz" else "Premium: расширенный поиск, избранное и новые функции.",reply_markup=keyboard)

async def favorites_cmd(update, context):
    uid=update.effective_user.id; lang=user_lang(uid)
    with db() as c: rows=c.execute("SELECT title FROM favorites WHERE telegram_id=? ORDER BY title",(uid,)).fetchall()
    await update.effective_message.reply_text(("❤️ Sevimlilar:\n" if lang=="uz" else "❤️ Избранное:\n")+("\n".join("• "+r["title"] for r in rows) if rows else ("Hali saqlangan anime yo‘q." if lang=="uz" else "Избранное пока пусто.")))

async def lang_cmd(update, context):
    await update.effective_message.reply_text("Tilni tanlang / Выберите язык:",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🇺🇿 O‘zbekcha",callback_data="lang:uz"),InlineKeyboardButton("🇷🇺 Русский",callback_data="lang:ru")]]))

async def on_text(update, context):
    if update.effective_user is None: return
    uid=update.effective_user.id
    if context.user_data.pop("awaiting_search",False):
        await show_results(update,context,update.effective_message.text)
    else:
        await show_results(update,context,update.effective_message.text)

async def callback(update, context):
    q=update.callback_query; await q.answer()
    uid=q.from_user.id; lang=user_lang(uid)
    if q.data=="search":
        context.user_data["awaiting_search"]=True
        await q.message.reply_text("Anime nomini yozing:" if lang=="uz" else "Введите название аниме:")
    elif q.data=="genres":
        rows=[]
        for i in range(0,len(GENRES),2):
            rows.append([InlineKeyboardButton((GENRE_UZ if lang=="uz" else GENRE_RU).get(g,g),callback_data=f"genre:{g}") for g in GENRES[i:i+2]])
        await q.message.reply_text(TEXT[lang]["genres"],reply_markup=InlineKeyboardMarkup(rows))
    elif q.data.startswith("genre:"):
        await show_results(update,context,genre=q.data.split(":",1)[1])
    elif q.data.startswith("fav:"):
        _,aid,title=q.data.split(":",2)
        with db() as c: c.execute("INSERT OR IGNORE INTO favorites(telegram_id,anime_id,title) VALUES(?,?,?)",(uid,int(aid),title))
        await q.message.reply_text("❤️ Sevimlilarga saqlandi." if lang=="uz" else "❤️ Добавлено в избранное.")
    elif q.data=="premium":
        await premium_cmd(update,context)
    elif q.data=="favorites":
        with db() as c: rows=c.execute("SELECT title FROM favorites WHERE telegram_id=? ORDER BY title",(uid,)).fetchall()
        await q.message.reply_text("\n".join("• "+r["title"] for r in rows) or ("Hali saqlangan anime yo‘q." if lang=="uz" else "Избранное пока пусто."))
    elif q.data=="language":
        await lang_cmd(update,context)
    elif q.data.startswith("lang:"):
        new=q.data.split(":")[1]
        with db() as c: c.execute("UPDATE users SET language=? WHERE telegram_id=?",(new,uid))
        await q.message.reply_text("Til o‘zgartirildi." if new=="uz" else "Язык изменён.")
    elif q.data=="buy_stars":
        if not BOT_TOKEN:
            await q.message.reply_text("Bot token sozlanmagan.")
            return
        payload=f"premium:{uid}:{secrets.token_urlsafe(8)}"
        prices=[LabeledPrice(label="AnimeFinder Premium 30 days",amount=PREMIUM_STARS)]
        await context.bot.send_invoice(chat_id=uid,title="AnimeFinder Premium",description="30 kunlik Premium obuna",payload=payload,provider_token="",currency="XTR",prices=prices)
    elif q.data=="buy_card":
        pid=secrets.token_urlsafe(12)
        with db() as c: c.execute("INSERT INTO payments(id,telegram_id,provider,amount,currency,status) VALUES(?,?,?,?,?,'pending')",(pid,uid,"uzcard_humo",19900,"UZS"))
        await q.message.reply_text(f"💳 Uzcard/Humo orqali Premium: 19 900 so‘m / 30 kun.\nTo‘lov havolasi to‘lov provayderi ulangandan keyin shu yerda chiqadi.\nBuyurtma ID: {pid}\n\nAdmin hozircha /approve {pid} orqali qo‘lda tasdiqlashi mumkin." if lang=="uz" else f"💳 Premium через Uzcard/Humo: 19 900 сум / 30 дней.\nСсылка появится после подключения платёжного провайдера.\nID заказа: {pid}")

async def precheckout(update, context):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment(update, context):
    uid=update.effective_user.id
    payload=update.effective_message.successful_payment.invoice_payload
    if payload.startswith("premium:"):
        activate_premium(uid)
        with db() as c: c.execute("INSERT OR REPLACE INTO payments(id,telegram_id,provider,amount,currency,status,paid_at) VALUES(?,?,?,?,?,'paid',?)",(payload,uid,"telegram_stars",PREMIUM_STARS,"XTR",datetime.now(timezone.utc).isoformat()))
        await update.effective_message.reply_text("🎉 Premium 30 kunga faollashtirildi!" if user_lang(uid)=="uz" else "🎉 Premium активирован на 30 дней!")

def activate_premium(uid):
    until=(datetime.now(timezone.utc)+timedelta(days=PREMIUM_DAYS)).isoformat()
    with db() as c: c.execute("UPDATE users SET is_premium=1,premium_until=? WHERE telegram_id=?",(until,uid))

async def admin_command(update, context):
    uid=update.effective_user.id
    if uid not in ADMIN_IDS:
        await update.effective_message.reply_text("Ruxsat yo‘q.")
        return
    if len(context.args)>=2 and context.args[0]=="approve":
        pid=context.args[1]
    elif context.args:
        pid=context.args[0]
    else:
        await update.effective_message.reply_text("Ishlatish: /approve PAYMENT_ID")
        return
    with db() as c: p=c.execute("SELECT * FROM payments WHERE id=?",(pid,)).fetchone()
    if not p:
        await update.effective_message.reply_text("To‘lov topilmadi."); return
    activate_premium(p["telegram_id"])
    with db() as c: c.execute("UPDATE payments SET status='paid',paid_at=? WHERE id=?",(datetime.now(timezone.utc).isoformat(),pid))
    await update.effective_message.reply_text("Tasdiqlandi.")
    try: await context.bot.send_message(p["telegram_id"],"🎉 Premium faollashtirildi!")
    except Exception: pass

def build_telegram():
    if not BOT_TOKEN: return None
    tg=Application.builder().token(BOT_TOKEN).build()
    tg.add_handler(CommandHandler("start",start))
    tg.add_handler(CommandHandler("search",search_cmd))
    tg.add_handler(CommandHandler("genres",genres_cmd))
    tg.add_handler(CommandHandler("premium",premium_cmd))
    tg.add_handler(CommandHandler("favorites",favorites_cmd))
    tg.add_handler(CommandHandler("lang",lang_cmd))
    tg.add_handler(CommandHandler("approve",admin_command))
    tg.add_handler(CallbackQueryHandler(callback))
    tg.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT,successful_payment))
    tg.add_handler(PreCheckoutQueryHandler(precheckout))
    tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,on_text))
    return tg

@app.on_event("startup")
async def startup():
    global telegram_app
    telegram_app=build_telegram()
    if telegram_app:
        await telegram_app.initialize()
        await telegram_app.start()
        await telegram_app.updater.start_polling(drop_pending_updates=False)

@app.on_event("shutdown")
async def shutdown():
    if telegram_app:
        await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()

@app.get("/",response_class=HTMLResponse)
async def home():
    return HTMLResponse(WEB_HTML)

@app.get("/api/search")
async def api_search(q: str="", genre: str=""):
    try:
        results=await anilist_search(q,genre or None)
        out=[]
        for a in results:
            t=a.get("title") or {}
            out.append({"id":a["id"],"title":t.get("english") or t.get("romaji") or t.get("native"),"native":t.get("native"),"year":a.get("seasonYear"),"episodes":a.get("episodes"),"score":a.get("averageScore"),"genres":a.get("genres"),"cover":(a.get("coverImage") or {}).get("large"),"url":a.get("siteUrl"),"description":clean(a.get("description") or "",500)})
        return {"results":out}
    except Exception as e:
        raise HTTPException(502,"Anime ma’lumotlar xizmati vaqtincha ishlamayapti")

@app.get("/health")
async def health(): return {"ok":True}

@app.get("/admin",response_class=HTMLResponse)
async def admin(request: Request):
    token=request.cookies.get("admin_session")
    if token != os.getenv("ADMIN_SESSION_TOKEN","replace-me"):
        return HTMLResponse(ADMIN_LOGIN, status_code=200)
    with db() as c:
        users=c.execute("SELECT telegram_id,language,is_premium,premium_until,created_at FROM users ORDER BY created_at DESC LIMIT 200").fetchall()
        payments=c.execute("SELECT * FROM payments ORDER BY created_at DESC LIMIT 100").fetchall()
    rows="".join(f"<tr><td>{u['telegram_id']}</td><td>{u['language']}</td><td>{'Ha' if u['is_premium'] else 'Yo‘q'}</td><td>{u['premium_until'] or ''}</td></tr>" for u in users)
    pays="".join(f"<tr><td>{p['id']}</td><td>{p['telegram_id']}</td><td>{p['provider']}</td><td>{p['amount']} {p['currency']}</td><td>{p['status']}</td></tr>" for p in payments)
    return HTMLResponse(f"""<!doctype html><html><meta charset="utf-8"><title>AnimeFinder Admin</title><style>body{{font:16px Arial;margin:30px;background:#10111b;color:#eee}}table{{border-collapse:collapse;width:100%;margin-bottom:30px}}td,th{{border:1px solid #444;padding:8px}}a{{color:#bba4ff}}</style><h1>AnimeFinder Admin</h1><p>Foydalanuvchilar: {len(users)} | To‘lovlar: {len(payments)}</p><h2>Foydalanuvchilar</h2><table><tr><th>Telegram ID</th><th>Til</th><th>Premium</th><th>Premium tugashi</th></tr>{rows}</table><h2>To‘lovlar</h2><table><tr><th>ID</th><th>User</th><th>Provayder</th><th>Miqdor</th><th>Status</th></tr>{pays}</table></html>""")

@app.post("/admin/login")
async def admin_login(password: str=Form(...)):
    if not secrets.compare_digest(password,ADMIN_PASSWORD):
        return HTMLResponse("Parol noto‘g‘ri. <a href='/admin'>Qayta urinish</a>",status_code=401)
    response=RedirectResponse("/admin",status_code=303)
    response.set_cookie("admin_session",os.getenv("ADMIN_SESSION_TOKEN","replace-me"),httponly=True,samesite="strict",secure=os.getenv("COOKIE_SECURE","false").lower()=="true")
    return response

@app.post("/webhooks/payment")
async def payment_webhook(request: Request):
    # IMPORTANT: Implement signature verification according to your chosen local provider.
    # Never trust a browser redirect as proof of payment.
    if not UZCARD_MERCHANT_SECRET:
        raise HTTPException(503,"Payment provider is not configured")
    body=await request.json()
    # Provider-specific signature validation MUST be added before production use.
    raise HTTPException(501,"Connect a real provider and implement its documented signature verification first")

ADMIN_LOGIN = """<!doctype html><html><meta charset='utf-8'><title>Admin login</title><style>body{font:16px Arial;background:#10111b;color:#eee;margin:50px}input,button{padding:12px;margin:5px}</style><h1>AnimeFinder Admin</h1><form method='post' action='/admin/login'><input name='password' type='password' placeholder='Admin paroli' required><button>Kirish</button></form></html>"""

WEB_HTML = r"""<!doctype html><html lang="uz"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AnimeFinder</title><style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;font:16px system-ui;background:radial-gradient(circle at top,#292044,#0d0f18 55%);color:#f4f1ff;min-height:100vh}.wrap{max-width:1100px;margin:auto;padding:24px}.top{display:flex;justify-content:space-between;align-items:center;gap:15px;flex-wrap:wrap}.logo{font-weight:900;font-size:25px}.hero{padding:35px 0 20px}.hero h1{font-size:clamp(32px,5vw,56px);margin:0 0 10px}.muted{color:#b7b2c9}.controls{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0}.controls input,.controls select,.controls button{padding:13px 15px;border:1px solid #51466d;border-radius:12px;background:#191727;color:#fff;font-size:16px}.controls input{flex:1;min-width:220px}.controls button{background:#8b5cf6;border:0;font-weight:700;cursor:pointer}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}.card{overflow:hidden;background:#191727;border:1px solid #322b46;border-radius:16px}.card img{width:100%;height:300px;object-fit:cover;background:#272236}.pad{padding:14px}.pad h3{margin:0 0 8px}.tag{display:inline-block;font-size:12px;padding:4px 7px;border-radius:7px;background:#302448;margin:2px}.bar{display:flex;justify-content:space-between;gap:10px;align-items:center}.lang{padding:8px;border-radius:9px;background:#201a30;color:#fff;border:1px solid #51466d}.foot{padding:35px 0;color:#aaa;font-size:13px}a{color:#c9b6ff}
</style></head><body><div class="wrap"><header class="top"><div class="logo">🎌 AnimeFinder</div><div class="bar"><select id="lang" class="lang" onchange="setLang()"><option value="uz">O‘zbekcha</option><option value="ru">Русский</option></select><a id="telegram" href="#" target="_blank" rel="noreferrer">Telegram bot ↗</a></div></header><section class="hero"><h1 id="heading">Sevimli animengni top</h1><div id="sub" class="muted">Nom yoki janr bo‘yicha qidir. Ma’lumotlar AniList orqali olinadi.</div><div class="controls"><input id="q" placeholder="Anime nomi..." onkeydown="if(event.key==='Enter')search()"><select id="genre"><option value="">Barcha janrlar</option></select><button onclick="search()" id="searchBtn">Qidirish</button></div></section><div id="status" class="muted"></div><section id="results" class="grid"></section><footer class="foot">AnimeFinder • Anime ma’lumotlari AniList API’dan olinadi. Sayt anime videolarini joylashtirmaydi.</footer></div><script>
const genres=["Action","Adventure","Comedy","Drama","Fantasy","Horror","Mystery","Romance","Sci-Fi","Slice of Life","Sports","Supernatural","Thriller","Isekai"];
const uz={"Action":"Jangari","Adventure":"Sarguzasht","Comedy":"Komediya","Drama":"Drama","Fantasy":"Fantastika","Horror":"Qo‘rqinchli","Mystery":"Sirli","Romance":"Romantika","Sci-Fi":"Ilmiy-fantastika","Slice of Life":"Kundalik hayot","Sports":"Sport","Supernatural":"G‘ayritabiiy","Thriller":"Triller","Isekai":"Isekai"};
const ru={"Action":"Экшен","Adventure":"Приключения","Comedy":"Комедия","Drama":"Драма","Fantasy":"Фэнтези","Horror":"Ужасы","Mystery":"Детектив","Romance":"Романтика","Sci-Fi":"Фантастика","Slice of Life":"Повседневность","Sports":"Спорт","Supernatural":"Сверхъестественное","Thriller":"Триллер","Isekai":"Исекай"};
function t(k){let l=document.getElementById('lang').value;return ({heading:{uz:'Sevimli animengni top',ru:'Найди любимое аниме'},sub:{uz:'Nom yoki janr bo‘yicha qidir. Ma’lumotlar AniList orqali olinadi.',ru:'Ищи по названию или жанру. Данные предоставлены AniList.'},placeholder:{uz:'Anime nomi...',ru:'Название аниме...'},search:{uz:'Qidirish',ru:'Поиск'},all:{uz:'Barcha janrlar',ru:'Все жанры'},loading:{uz:'Qidirilmoqda…',ru:'Поиск…'},empty:{uz:'Natija topilmadi.',ru:'Ничего не найдено.'},error:{uz:'Xatolik. Keyinroq urinib ko‘ring.',ru:'Ошибка. Попробуйте позже.'},year:{uz:'Yil',ru:'Год'},episodes:{uz:'Qism',ru:'Серии'},score:{uz:'Reyting',ru:'Рейтинг'},more:{uz:'Batafsil ↗',ru:'Подробнее ↗'}}[k]||{})[l]}
function setLang(){document.getElementById('heading').textContent=t('heading');document.getElementById('sub').textContent=t('sub');document.getElementById('q').placeholder=t('placeholder');document.getElementById('searchBtn').textContent=t('search');buildGenres();search()}
function buildGenres(){let s=document.getElementById('genre'),v=s.value;s.innerHTML='<option value="">'+t('all')+'</option>';genres.forEach(g=>{let o=document.createElement('option');o.value=g;o.textContent=(document.getElementById('lang').value==='ru'?ru:uz)[g]||g;s.appendChild(o)});s.value=v}
async function search(){let status=document.getElementById('status'),res=document.getElementById('results');status.textContent=t('loading');res.innerHTML='';let params=new URLSearchParams({q:document.getElementById('q').value,genre:document.getElementById('genre').value});try{let r=await fetch('/api/search?'+params);if(!r.ok)throw Error();let data=await r.json();status.textContent=data.results.length?'':' '+t('empty');data.results.forEach(a=>{let card=document.createElement('article');card.className='card';let img=document.createElement('img');img.loading='lazy';img.src=a.cover||'';img.alt=a.title||'Anime';let pad=document.createElement('div');pad.className='pad';let h=document.createElement('h3');h.textContent=a.title||'Anime';let meta=document.createElement('p');meta.className='muted';meta.textContent=`${t('year')}: ${a.year||'—'} · ${t('episodes')}: ${a.episodes||'—'} · ${t('score')}: ${a.score||'—'}/100`;let tags=document.createElement('div');(a.genres||[]).forEach(g=>{let tag=document.createElement('span');tag.className='tag';tag.textContent=(document.getElementById('lang').value==='ru'?ru:uz)[g]||g;tags.appendChild(tag)});let desc=document.createElement('p');desc.className='muted';desc.textContent=a.description||'';let link=document.createElement('a');link.href=a.url||'#';link.target='_blank';link.rel='noreferrer';link.textContent=t('more');pad.append(h,meta,tags,desc,link);card.append(img,pad);res.append(card)})}catch(e){status.textContent=t('error')}}
document.getElementById('telegram').href='https://t.me/'+('YOUR_BOT_USERNAME');buildGenres();search();
</script></body></html>"""


# Populate the Telegram link from .env configuration.
WEB_HTML = WEB_HTML.replace("YOUR_BOT_USERNAME", BOT_USERNAME.lstrip("@") if BOT_USERNAME else "YOUR_BOT_USERNAME")
