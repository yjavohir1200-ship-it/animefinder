# AnimeFinder — Telegram bot + web search

O‘zbekcha va ruscha anime qidiruv loyihasi. Anime ma’lumotlari AniList GraphQL API’dan olinadi; loyiha anime videolarini noqonuniy joylashtirmaydi yoki tarqatmaydi.

## Imkoniyatlar
- Telegram bot: `/start`, `/search`, `/genres`, `/premium`, `/favorites`, `/lang`
- Anime nomi va janr bo‘yicha qidiruv
- Poster, tavsif, yil, epizodlar, reyting va AniList havolasi
- SQLite ma’lumotlar bazasi: foydalanuvchilar, sevimlilar, to‘lovlar
- Web sayt va qidiruv API
- Admin panel: `/admin` (login paroli `.env` ichida)
- Telegram Stars invoice (XTR) orqali Premium
- Uzcard/Humo uchun to‘lov buyurtmasi skeleti va adminning qo‘lda tasdiqlashi

## Muhim to‘lov eslatmasi
Telegram Stars to‘lovi bot kodi orqali ishlashi uchun bot tokeni va Telegram konfiguratsiyasi to‘g‘ri bo‘lishi kerak. Uzcard/Humo uchun aniq provayder (masalan, biznes hisobingizga berilgan to‘lov gateway) bilan shartnoma va rasmiy API kerak. Ushbu loyiha provayderga bog‘liq webhookni ataylab tayyor holatda ko‘rsatmaydi: haqiqiy to‘lovni tekshirmasdan premium ochish xavfli. `POST /webhooks/payment` ichiga tanlangan provayder hujjatidagi imzo tekshiruvi va to‘lovni tekshirishni yozing. Production’da adminning qo‘lda tasdiqlashini ommaviy to‘lov uchun ishlatmang.


## Bot token xavfsizligi
- Bot tokenini hech kimga yubormang va GitHub kabi ochiq joyga joylamang.
- Token chatda yoki boshqa joyda oshkor bo‘lsa, BotFather’da `/revoke` bilan bekor qilib, yangisini oling.
- Yangi tokenni faqat serverdagi `.env` faylining `BOT_TOKEN=` qatoriga kiriting.
- `BOT_USERNAME` maydoniga `@` belgisiz username yozing, masalan `MyAnimeFinderBot`.
- `ADMIN_PASSWORD` va `ADMIN_SESSION_TOKEN` qiymatlarini uzun, tasodifiy maxfiy qiymatlarga almashtiring.

## Lokal ishga tushirish
1. Python 3.12 o‘rnating.
2. Loyihani oching va terminalda:
   ```bash
   python -m venv .venv
   # Linux/macOS:
   source .venv/bin/activate
   # Windows:
   .venv\\Scripts\\activate
   pip install -r requirements.txt
   ```
3. `.env.example` nusxasini `.env` nomi bilan saqlang va `BOT_TOKEN`, `BOT_USERNAME`, `ADMIN_IDS`, kuchli `ADMIN_PASSWORD`, `ADMIN_SESSION_TOKEN` qiymatlarini kiriting.
4. `.env` ni shell muhitiga yuklang (yoki Docker Compose ishlating). Oddiy Python ishga tushirish:
   ```bash
   # Linux/macOS, .env faylni export qilish uchun:
   set -a; source .env; set +a
   uvicorn app:app --host 0.0.0.0 --port 8000
   ```
5. Sayt: `http://localhost:8000`; admin: `http://localhost:8000/admin`; health: `/health`.
6. Telegram’da botga `/start` yuboring.

## 24/7 server (Docker)
1. VPS oling (Ubuntu 22.04/24.04 tavsiya).
2. Docker va Docker Compose o‘rnating.
3. `.env.example` -> `.env` qiling, maxfiy qiymatlarni to‘ldiring. `PUBLIC_BASE_URL` ni domeningizga qo‘ying.
4. `docker compose up -d --build`
5. `docker compose logs -f` bilan loglarni ko‘ring.
6. Domen va HTTPS uchun Nginx/Caddy reverse proxy sozlang. 8000 portni internetga ochiq qoldirish o‘rniga reverse proxy va firewall ishlating.

## Admin
- `/admin` sahifasida `.env` dagi `ADMIN_PASSWORD` bilan kiring.
- Botda `ADMIN_IDS` ichidagi Telegram foydalanuvchisi `/approve PAYMENT_ID` bilan Uzcard/Humo buyurtmasini qo‘lda tasdiqlay oladi. Bu faqat test/qo‘lda tekshiriladigan holat uchun.
- Admin parolini va session tokenini production’da kuchli, tasodifiy qiymatga almashtiring.

## Premium funksiyalari
Hozirgi boshlang‘ich loyihada premium obuna muddati va to‘lov yozuvlari bor. Qidiruvning asosiy funksiyasi ochiq. Premiumga xos cheklovlar (masalan, kunlik qidiruv limiti yoki kengaytirilgan tavsiyalar) biznes qoidalariga qarab keyingi bosqichda ulanishi kerak.

## Ishlab chiqarishga chiqarishdan oldin
- HTTPS, reverse proxy, backup va log rotation.
- Admin login uchun rate limiting, CSRF himoyasi va kuchli sessiya boshqaruvi.
- Uzcard/Humo provayderining rasmiy webhook imzo tekshiruvi.
- Telegram Stars refund/support siyosati va `/paysupport` yo‘riqnomasi.
- Ma’lumotlar maxfiyligi siyosati va foydalanuvchi ma’lumotlarini o‘chirish yo‘li.
- AniList API foydalanish shartlarini tekshiring.
