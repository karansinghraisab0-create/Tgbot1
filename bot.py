import os
import sqlite3
import asyncio
import logging
from urllib.parse import quote
from telegram import (Update, ReplyKeyboardMarkup,
                      InlineKeyboardButton, InlineKeyboardMarkup)
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          CallbackQueryHandler, ContextTypes, filters)

logging.basicConfig(level=logging.WARNING)

TOKEN = os.environ["BOT_TOKEN"]
CHANNELS = [c.strip() for c in os.environ["CHANNEL"].split(",") if c.strip()]
ADMIN_ID = int(os.environ["ADMIN_ID"])
REWARD = 50     # paise (50 = Rs 0.50)
MIN_WD = 2000   # paise (2000 = Rs 20)

con = sqlite3.connect(os.environ.get("DB_PATH", "bot.db"), check_same_thread=False)
con.executescript("""
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, referrer INTEGER, balance INTEGER DEFAULT 0,
  credited INTEGER DEFAULT 0, name TEXT);
CREATE TABLE IF NOT EXISTS withdrawals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount INTEGER,
  upi TEXT, status TEXT DEFAULT 'pending');
""")


def db(sql, args=(), one=False, commit=False):
    cur = con.execute(sql, args)
    if commit:
        con.commit()
    return cur.fetchone() if one else cur.fetchall()


def rs(p):
    return f"₹{p / 100:.2f}"


B_WALLET, B_REFER, B_WD = "💰 Wallet", "🔗 Refer & Earn", "🏧 Withdraw"
B_STATS, B_TOP, B_HIST, B_HELP = "📊 My Stats", "🏆 Leaderboard", "📜 History", "ℹ️ Help"
BUTTONS = [B_WALLET, B_REFER, B_WD, B_STATS, B_TOP, B_HIST, B_HELP]
MENU = ReplyKeyboardMarkup(
    [[B_WALLET, B_REFER], [B_WD, B_STATS], [B_TOP, B_HIST], [B_HELP]],
    resize_keyboard=True)


def ensure_user(update, args=None):
    u = update.effective_user
    if db("SELECT 1 FROM users WHERE id=?", (u.id,), one=True):
        return
    ref = None
    if args and args[0].isdigit():
        r = int(args[0])
        if r != u.id and db("SELECT 1 FROM users WHERE id=?", (r,), one=True):
            ref = r
    db("INSERT INTO users(id, referrer, name) VALUES(?,?,?)",
       (u.id, ref, u.first_name or "User"), commit=True)


async def is_member(context, uid):
    for ch in CHANNELS:
        try:
            m = await context.bot.get_chat_member(ch, uid)
            if m.status not in ("member", "administrator", "creator"):
                return False
        except Exception:
            return False
    return True


async def verify_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.effective_message
    if not await is_member(context, uid):
        rows = [[InlineKeyboardButton(f"📢 Join {c}", url=f"https://t.me/{c.lstrip('@')}")]
                for c in CHANNELS]
        rows.append([InlineKeyboardButton("✅ Join Kar Liya", callback_data="recheck")])
        await msg.reply_text("Pehle neeche ke sabhi channels join karo, phir ✅ dabao.",
                             reply_markup=InlineKeyboardMarkup(rows))
        return False
    ref, credited = db("SELECT referrer, credited FROM users WHERE id=?", (uid,), one=True)
    if ref and not credited:
        db("UPDATE users SET credited=1 WHERE id=?", (uid,), commit=True)
        db("UPDATE users SET balance=balance+? WHERE id=?", (REWARD, ref), commit=True)
        try:
            name = update.effective_user.first_name or "Koi"
            await context.bot.send_message(ref, f"🎉 Naya referral! {name} ne join kiya.\n{rs(REWARD)} wallet mein add hue.")
        except Exception:
            pass
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update, context.args)
    if await verify_flow(update, context):
        await update.message.reply_text(
            f"Welcome! 🎉\nHar referral pe {rs(REWARD)} kamao. Menu se option chuno 👇",
            reply_markup=MENU)


async def recheck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    ensure_user(update)
    if not await is_member(context, update.effective_user.id):
        await q.answer("Abhi sabhi channels join nahi kiye ❌", show_alert=True)
        return
    await q.answer()
    if await verify_flow(update, context):
        await q.message.reply_text("✅ Verified! Menu se option chuno 👇", reply_markup=MENU)


async def text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    uid = update.effective_user.id
    t = update.message.text.strip()
    if not await verify_flow(update, context):
        return
    bal = db("SELECT balance FROM users WHERE id=?", (uid,), one=True)[0]

    if context.user_data.get("await_upi"):
        if t in BUTTONS:
            context.user_data["await_upi"] = False
        else:
            context.user_data["await_upi"] = False
            if t.lower() == "cancel":
                return await update.message.reply_text("Withdraw cancel ho gaya.", reply_markup=MENU)
            if "@" not in t or " " in t or len(t) > 50 or bal < MIN_WD:
                return await update.message.reply_text(
                    "Sahi UPI ID nahi mili (jaise name@upi). Withdraw dobara dabao.", reply_markup=MENU)
            db("UPDATE users SET balance=0 WHERE id=?", (uid,), commit=True)
            cur = con.execute("INSERT INTO withdrawals(user_id, amount, upi) VALUES(?,?,?)", (uid, bal, t))
            con.commit()
            wid = cur.lastrowid
            await update.message.reply_text(
                f"✅ Request #{wid} bhej di!\nAmount: {rs(bal)}\nUPI: {t}\nJald hi payment milega.",
                reply_markup=MENU)
            await context.bot.send_message(
                ADMIN_ID,
                f"🏧 Withdraw #{wid}\nUser: {uid} ({update.effective_user.first_name})\n"
                f"Amount: {rs(bal)}\nUPI: {t}\n\n/paid {wid}   |   /reject {wid}")
            return

    if t == B_WALLET:
        n = db("SELECT COUNT(*) FROM users WHERE referrer=? AND credited=1", (uid,), one=True)[0]
        await update.message.reply_text(
            f"💰 Wallet\n\nBalance: {rs(bal)}\nTotal referrals: {n}\n"
            f"Minimum withdrawal: {rs(MIN_WD)}")

    elif t == B_REFER:
        me = await context.bot.get_me()
        link = f"https://t.me/{me.username}?start={uid}"
        share = (f"https://t.me/share/url?url={quote(link)}"
                 f"&text={quote('Is bot se paise kamao! Join karo 👇')}")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📤 Dosto ko Share Karo", url=share)]])
        await update.message.reply_text(
            f"🔗 Tumhara referral link:\n{link}\n\n"
            f"Har valid referral pe {rs(REWARD)} milenge.", reply_markup=kb)

    elif t == B_WD:
        if bal < MIN_WD:
            await update.message.reply_text(
                f"Minimum withdrawal {rs(MIN_WD)} hai.\nTumhare paas {rs(bal)} hain. Aur refer karo! 💪")
        else:
            context.user_data["await_upi"] = True
            await update.message.reply_text(
                f"Balance: {rs(bal)}\nApni UPI ID bhejo (cancel karne ke liye 'cancel' likho):")

    elif t == B_STATS:
        done = db("SELECT COUNT(*) FROM users WHERE referrer=? AND credited=1", (uid,), one=True)[0]
        wait = db("SELECT COUNT(*) FROM users WHERE referrer=? AND credited=0", (uid,), one=True)[0]
        paid = db("SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE user_id=? AND status='paid'",
                  (uid,), one=True)[0]
        await update.message.reply_text(
            f"📊 Tumhare Stats\n\nValid referrals: {done}\n"
            f"Channel join baaki: {wait}\nTotal kamaya: {rs(done * REWARD)}\n"
            f"Ab tak mila: {rs(paid)}")

    elif t == B_TOP:
        rows = db("SELECT referrer, COUNT(*) c FROM users WHERE credited=1 AND referrer IS NOT NULL "
                  "GROUP BY referrer ORDER BY c DESC LIMIT 5")
        if not rows:
            return await update.message.reply_text("Abhi leaderboard khali hai. Pehle tum bano! 🏆")
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        lines = []
        for i, (rid, c) in enumerate(rows):
            nm = db("SELECT name FROM users WHERE id=?", (rid,), one=True)[0] or "User"
            lines.append(f"{medals[i]} {nm[:3]}*** — {c} referrals")
        await update.message.reply_text("🏆 Top Referrers\n\n" + "\n".join(lines))

    elif t == B_HIST:
        rows = db("SELECT id, amount, status FROM withdrawals WHERE user_id=? ORDER BY id DESC LIMIT 5", (uid,))
        if not rows:
            return await update.message.reply_text("Abhi koi withdrawal nahi hui.")
        icon = {"pending": "⏳", "paid": "✅", "rejected": "❌"}
        await update.message.reply_text(
            "📜 Last withdrawals\n\n" + "\n".join(f"#{i}  {rs(a)}  {icon.get(s, '')} {s}" for i, a, s in rows))

    elif t == B_HELP:
        await update.message.reply_text(
            f"ℹ️ Kaise kaam karta hai\n\n"
            f"1. 'Refer & Earn' se apna link lo\n"
            f"2. Dost link se bot start kare aur sabhi channels join kare\n"
            f"3. Tumhe {rs(REWARD)} milenge\n"
            f"4. {rs(MIN_WD)} hone pe UPI se withdraw karo\n\n"
            f"Fake ya self-referral karne pe account block ho sakta hai.")


def admin_only(update):
    return update.effective_user.id == ADMIN_ID


async def stats_cmd(update, context):
    if not admin_only(update):
        return
    total = db("SELECT COUNT(*) FROM users", one=True)[0]
    joined = db("SELECT COUNT(*) FROM users WHERE credited=1", one=True)[0]
    owed = db("SELECT COALESCE(SUM(balance),0) FROM users", one=True)[0]
    pc, pa = db("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM withdrawals WHERE status='pending'", one=True)
    await update.message.reply_text(
        f"👥 Total users: {total}\n✅ Referred & joined: {joined}\n"
        f"💰 Users ka total balance: {rs(owed)}\n⏳ Pending withdrawals: {pc} ({rs(pa)})")


async def pending_cmd(update, context):
    if not admin_only(update):
        return
    rows = db("SELECT id, user_id, amount, upi FROM withdrawals WHERE status='pending'")
    if not rows:
        return await update.message.reply_text("Koi pending request nahi.")
    await update.message.reply_text("\n\n".join(
        f"#{i} | User {u}\n{rs(a)} | {upi}\n/paid {i}  /reject {i}" for i, u, a, upi in rows))


async def paid_cmd(update, context):
    if not admin_only(update) or not context.args or not context.args[0].isdigit():
        return
    wid = int(context.args[0])
    row = db("SELECT user_id, amount FROM withdrawals WHERE id=? AND status='pending'", (wid,), one=True)
    if not row:
        return await update.message.reply_text("Request nahi mili ya pehle hi process ho chuki.")
    db("UPDATE withdrawals SET status='paid' WHERE id=?", (wid,), commit=True)
    await update.message.reply_text(f"#{wid} paid ✅")
    try:
        await context.bot.send_message(row[0], f"💸 {rs(row[1])} tumhe bhej diye gaye! Withdrawal #{wid} complete.")
    except Exception:
        pass


async def reject_cmd(update, context):
    if not admin_only(update) or not context.args or not context.args[0].isdigit():
        return
    wid = int(context.args[0])
    row = db("SELECT user_id, amount FROM withdrawals WHERE id=? AND status='pending'", (wid,), one=True)
    if not row:
        return await update.message.reply_text("Request nahi mili ya pehle hi process ho chuki.")
    db("UPDATE withdrawals SET status='rejected' WHERE id=?", (wid,), commit=True)
    db("UPDATE users SET balance=balance+? WHERE id=?", (row[1], row[0]), commit=True)
    await update.message.reply_text(f"#{wid} reject, paise wapas wallet mein ❌")
    try:
        await context.bot.send_message(row[0], f"❌ Withdrawal #{wid} reject hui. {rs(row[1])} wallet mein wapas aa gaye.")
    except Exception:
        pass


async def broadcast_cmd(update, context):
    if not admin_only(update):
        return
    parts = update.message.text.split(" ", 1)
    if len(parts) < 2:
        return await update.message.reply_text("Use: /broadcast tumhara message")
    ok = 0
    for (uid,) in db("SELECT id FROM users"):
        try:
            await context.bot.send_message(uid, parts[1])
            ok += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await update.message.reply_text(f"📢 {ok} users ko bhej diya.")


def main():
    asyncio.set_event_loop(asyncio.new_event_loop())
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("pending", pending_cmd))
    app.add_handler(CommandHandler("paid", paid_cmd))
    app.add_handler(CommandHandler("reject", reject_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CallbackQueryHandler(recheck, pattern="^recheck$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))
    print("Bot started", flush=True)
    app.run_polling()


if __name__ == "__main__":
    main()
