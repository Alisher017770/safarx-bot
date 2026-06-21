# SafarX Telegram Bot

`@Safarx_bot` uchun Toshkent va Vodiy yo'nalishlarida yo'lovchi, haydovchi va admin ishlaydigan MVP bot.

## Ishga tushirish

### GitHub Codespaces yoki Linux terminal

1. Python 3.12 yoki 3.13 bilan repository ichida shu komandalarni yozing:

```bash
python -m pip install -r requirements.txt
cp .env.example .env
nano .env
```

2. `.env` ichini to'ldiring:

```text
BOT_TOKEN=BotFather_tokeningiz
ADMIN_IDS=7074563321
DATABASE_URL=sqlite+aiosqlite:///taxi_bot.db
BOT_NAME=SafarX
BOT_USERNAME=Safarx_bot
CHANNEL_ID=@SafarX_0
```

3. Botni ishga tushiring:

```bash
python main.py
```

Yoki bitta komanda bilan:

```bash
bash start.sh
```

### Windows terminal

```powershell
py -3 -m pip install -r requirements.txt
copy .env.example .env
notepad .env
py -3 main.py
```

## GitHubga yuklash

Tokenni GitHubga yuklamang. `.env` fayli `.gitignore` ichida turibdi, shuning uchun u commit bo'lmasligi kerak.

```bash
git init
git add .
git commit -m "Add SafarX Telegram bot"
git branch -M main
git remote add origin https://github.com/USERNAME/REPO.git
git push -u origin main
```

## Railwayga chiqarish

Railway project oching, GitHub repositoryni ulang va PostgreSQL service qo'shing.

Variables bo'limiga shularni kiriting:

```text
BOT_TOKEN=BotFather_tokeningiz
ADMIN_IDS=7074563321,8654583177
DATABASE_URL=Railway_PostgreSQL_DATABASE_URL
BOT_NAME=SafarX
BOT_USERNAME=Safarx_bot
CHANNEL_ID=@SafarX_0
```

`Procfile` ichida worker start command bor:

```text
web: python main.py
```

## SafarX Mini App

Bot ichida endi "📱 SafarX App" tugmasi bor — bu Telegram Mini App orqali barcha haydovchi e'lonlarini chiroyli karta ko'rinishida ko'rsatadi (filtrlar bilan). Bu bitta jarayonda (`python main.py`) botning o'ziga qo'shilgan kichik veb-server (`webapp_server.py` + `webapp/` papka) orqali ishlaydi — alohida xizmat kerak emas.

Ishga tushirish uchun:

1. Railway'da shu loyihani **web** turidagi xizmat sifatida deploy qiling (Procfile shunday sozlangan).
2. Railway loyihangiz sozlamalarida **Networking → Generate Domain** tugmasini bosing — Railway sizga `https://....up.railway.app` ko'rinishidagi ommaviy manzil beradi.
3. Shu manzilni `MINI_APP_URL` o'zgaruvchisiga qo'ying (Railway Variables bo'limida) va botni qayta deploy qiling.
4. Botda `/start` bosing — endi pastda "📱 SafarX App" tugmasi chiqadi.

`MINI_APP_URL` bo'sh bo'lsa, bu tugma shunchaki ko'rinmaydi — botning qolgan qismi odatdagidek ishlayveradi.

Hozircha Mini App orqali faqat **e'lonlarni ko'rish va filtrlash** mumkin. "Joy band qilish" tugmasi bosilganda Mini App yopilib, bot suhbatiga qaytadi — joy band qilishning qolgan qismi (telefon, lokatsiya va h.k.) odatdagidek botning o'zida davom etadi.

## Nimalar bor

- Yo'lovchi buyurtma beradi.
- Haydovchi ro'yxatdan o'tadi.
- Admin haydovchini tasdiqlaydi.
- Haydovchi yo'nalish qo'shadi.
- Mos haydovchilarga yangi buyurtma yuboriladi.
- Haydovchi buyurtmani qabul qiladi.
- Yo'lovchi va haydovchi bir-birining kontaktini oladi.

## Admin ID ni topish

Telegramda `@userinfobot` ga kiring va o'z `id` raqamingizni oling. Shu raqamni `.env` faylidagi `ADMIN_IDS` ga yozing.
