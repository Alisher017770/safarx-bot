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
# Ixtiyoriy: tijoriy Open-Meteo tarifi uchun
WEATHER_API_KEY=
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
worker: python main.py
```

## Nimalar bor

- Yo'lovchi buyurtma beradi.
- Haydovchi ro'yxatdan o'tadi.
- Admin haydovchini tasdiqlaydi.
- Haydovchi yo'nalish qo'shadi.
- Mos haydovchilarga yangi buyurtma yuboriladi.
- Haydovchi buyurtmani qabul qiladi.
- Yo'lovchi va haydovchi bir-birining kontaktini oladi.
- Foydalanuvchi lokatsiyasi bo'yicha ob-havoni yoqadi yoki o'chiradi; yangilik har 8 soatda yuboriladi.
- Bosh admin bot ichidan yangi admin qo'sha oladi. Yangi admin avval botga `/start` bosishi kerak.

## Admin ID ni topish

Telegramda `@userinfobot` ga kiring va o'z `id` raqamingizni oling. Shu raqamni `.env` faylidagi `ADMIN_IDS` ga yozing.
