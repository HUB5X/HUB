#!/usr/bin/python3

import os
import time
import requests
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, CallbackContext
from telegram.error import RetryAfter, TimedOut, BadRequest, Forbidden

# --- Configuration ---
#              <<<<< သင်၏ BOT TOKEN ကို ဤနေရာတွင် ထည့်ပါ >>>>>
BOT_TOKEN = "7900910485:AAGGnEHEbjhf8oPZioLQx5D5vDQDAS1yWvE"
# ===============================================================

# <--- NEW ADMIN & USER MANAGEMENT --->
#               <<<<< သင်၏ TELEGRAM USER ID ကို ဤနေရာတွင် ထည့်ပါ >>>>>
ADMIN_IDS = [6646404639]  # ဥပမာ - [123456789, 987654321] Admin တစ်ယောက်ထက်ပိုလျှင် , ခြားပြီးထည့်ပါ
# ===============================================================
USER_IDS_FILE = "bot_user_ids.txt"
# <------------------------------------->

# --- Global variables for State & Concurrency Management ---
status_data = {}
data_lock = asyncio.Lock()

# --- NEW: User ID Persistence Functions ---
def load_user_ids():
    """File မှ user ID များကို set တစ်ခုအနေဖြင့် load လုပ်သည်။"""
    if not os.path.exists(USER_IDS_FILE):
        return set()
    with open(USER_IDS_FILE, "r") as f:
        return set(int(line.strip()) for line in f if line.strip())

def save_user_id(user_id):
    """User ID အသစ်တစ်ခုကို file ထဲသို့ ထည့်သွင်းသိမ်းဆည်းသည်။"""
    user_ids = load_user_ids()
    if user_id not in user_ids:
        user_ids.add(user_id)
        with open(USER_IDS_FILE, "w") as f:
            for uid in user_ids:
                f.write(f"{uid}\n")

# --- Reusable Keyboards ---

def get_main_menu_keyboard():
    """ပင်မစာမျက်နှာ၏ keyboard ကို ပြန်ပေးသည်။"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("♻️ Proxy အသစ်ထုတ်ပြီး စစ်ဆေးရန်", callback_data='generate_check')],
        [InlineKeyboardButton("📂 .txt ဖိုင်ဖြင့် စစ်ဆေးရန်", callback_data='check_from_file')],
        [InlineKeyboardButton("👑 Admin Plan", callback_data='admin_plan')]
    ])
    
# <--- NEW: Admin Menu Keyboard --->
def get_admin_menu_keyboard():
    """Admin များအတွက် control panel keyboard ကို ပြန်ပေးသည်။"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Broadcast ပို့ရန်", callback_data='admin_broadcast')],
        [InlineKeyboardButton("📊 အခြေအနေကြည့်ရန် (Stats)", callback_data='admin_stats')],
        [InlineKeyboardButton("« နောက်သို့ (ပင်မစာမျက်နှာ)", callback_data='back_to_main')]
    ])

def get_type_selection_keyboard():
    """Proxy အမျိုးအစား ရွေးချယ်ရန် keyboard ကို ပြန်ပေးသည်။"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("HTTP", callback_data='select_http'), InlineKeyboardButton("SOCKS4", callback_data='select_socks4')],
        [InlineKeyboardButton("SOCKS5", callback_data='select_socks5'), InlineKeyboardButton("အားလုံး", callback_data='select_all')],
        [InlineKeyboardButton("« နောက်သို့ (ပင်မစာမျက်နှာ)", callback_data='back_to_main')]
    ])
    
def get_back_to_type_selection_keyboard():
    """Proxy အမျိုးအစား ရွေးချယ်သည့်နေရာသို့ ပြန်သွားရန် keyboard ကို ပြန်ပေးသည်။"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("« နောက်သို့ (အမျိုးအစား ရွေးရန်)", callback_data='back_to_type_select')]
    ])

def get_cancel_keyboard():
    """စစ်ဆေးမှုကို ရပ်တန့်ရန် keyboard ကို ပြန်ပေးသည်။"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ ရပ်တန့်မည် (Cancel)", callback_data='cancel_check')]
    ])

# --- Proxy Logic (No Changes) ---
def get_proxies_from_api(proxy_type, limit):
    print(f"Fetching {limit} {proxy_type} proxies...")
    try:
        url = f"https://api.proxyscrape.com/v2/?request=displayproxies&protocol={proxy_type}&timeout=10000&country=all&ssl=all&anonymity=all&limit={limit}"
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        proxies = response.text.strip().splitlines()
        print(f"Successfully fetched {len(proxies)} {proxy_type} proxies.")
        return proxies
    except requests.exceptions.RequestException as e:
        print(f"Connection Error fetching {proxy_type}: {e}")
        return []

def proxy_check_worker(ip):
    protocols = ["http", "socks4", "socks5"]
    for p_type in protocols:
        proxy_dict = {"http": f"{p_type}://{ip}", "https": f"{p_type}://{ip}"}
        try:
            response = requests.get("http://ip-api.com/json/", proxies=proxy_dict, timeout=7)
            response.raise_for_status()
            print("-" * 40)
            print(f"✅ SUCCESS | Proxy: {ip} | Type: {p_type}")
            try:
                json_response = response.json()
                print(json.dumps(json_response, indent=2, ensure_ascii=False))
            except json.JSONDecodeError:
                print("Could not decode JSON, showing raw text:")
                print(response.text)
            print("-" * 40)
            return ("active", f"{p_type}://{ip}")
        except requests.exceptions.RequestException:
            continue
    return ("dead", ip)

# --- Bot's Asynchronous Functions (Proxy Checking part - unchanged) ---

async def live_update_message(context: CallbackContext, chat_id: int, message_id: int, start_time):
    while status_data.get(chat_id, {}).get('running', False):
        async with data_lock:
            s_data = status_data.get(chat_id, {}).copy()
        total = s_data.get('total', 0)
        checked = s_data.get('checked', 0)
        active = s_data.get('active', 0)
        progress = checked / total if total > 0 else 0
        bar = '█' * int(10 * progress) + '░' * (10 - int(10 * progress))
        text = (f"🔎 **Proxy စစ်ဆေးနေပါသည်**\n\n"
                f"ခေတ္တစောင့်ဆိုင်းပေးပါ...\n\n"
                f"`{bar}` {checked} / {total} ({progress:.1%})\n\n"
                f"🟢 Active: {active}\n"
                f"🔴 Dead: {checked - active}\n"
                f"⏱️ ကြာမြင့်ချိန်: {time.time() - start_time:.2f}s")
        try:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, parse_mode='Markdown', reply_markup=get_cancel_keyboard())
        except BadRequest as e:
            if 'Message is not modified' not in str(e): print(f"Update Error: {e}")
        except (RetryAfter, TimedOut) as e:
            await asyncio.sleep(getattr(e, 'retry_after', 5))
        await asyncio.sleep(2.5)

async def run_checker(context: CallbackContext, chat_id: int, proxies_to_check: list):
    message = await context.bot.send_message(chat_id, "စစ်ဆေးရန် ပြင်ဆင်နေပါသည်...")
    start_time = time.time()
    unique_proxies = list(set(proxies_to_check))
    async with data_lock:
        status_data[chat_id] = {'total': len(unique_proxies), 'checked': 0, 'active': 0, 'active_proxies': [], 'running': True, 'cancelled': False}
    update_task = asyncio.create_task(live_update_message(context, chat_id, message.message_id, start_time))
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=100) as pool:
        futures = [loop.run_in_executor(pool, proxy_check_worker, ip) for ip in unique_proxies]
        for future in asyncio.as_completed(futures):
            async with data_lock:
                if status_data.get(chat_id, {}).get('cancelled', False):
                    break
            status, result_ip = await future
            async with data_lock:
                if chat_id in status_data:
                    status_data[chat_id]['checked'] += 1
                    if status == 'active':
                        status_data[chat_id]['active'] += 1
                        status_data[chat_id]['active_proxies'].append(result_ip)
    async with data_lock:
        status_data[chat_id]['running'] = False
        final_data = status_data[chat_id].copy()
    update_task.cancel()
    if final_data.get('cancelled', False):
        result_header = "🛑 **လုပ်ငန်းစဉ်ကို ရပ်တန့်လိုက်ပါပြီ!**"
        result_body = f"စုစုပေါင်း proxy {final_data['total']} ခုထဲမှ {final_data['checked']} ခုကို စစ်ဆေးပြီးချိန်တွင် ရပ်တန့်ခဲ့ပါသည်။"
    else:
        result_header = "✅ **စစ်ဆေးမှု ပြီးဆုံးပါပြီ!**"
        result_body = f"Checked {final_data['total']} unique proxies in {time.time() - start_time:.2f}s."
    result_text = (f"{result_header}\n\n{result_body}\n\n"
                   f"🟢 **Active Proxies: {final_data['active']}**\n"
                   f"🔴 Dead Proxies: {final_data['checked'] - final_data['active']}")
    end_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 ပင်မစာမျက်နှာသို့ ပြန်သွားရန်", callback_data='back_to_main')]])
    try:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=message.message_id, text=result_text, parse_mode='Markdown', reply_markup=end_keyboard)
    except BadRequest:
        await context.bot.send_message(chat_id=chat_id, text=result_text, parse_mode='Markdown', reply_markup=end_keyboard)
    if final_data['active_proxies']:
        file_path = f"active_proxies_{chat_id}.txt"
        ip_port_list = [proxy.split("://")[1] for proxy in final_data['active_proxies']]
        with open(file_path, "w") as f: f.write("\n".join(ip_port_list))
        with open(file_path, "rb") as f:
            await context.bot.send_document(chat_id=chat_id, document=InputFile(f, filename="Hub_Active_Proxies.txt"),
                                            caption="ဤသည်မှာ Active ဖြစ်သော Proxy များ (ip:port) ဖြစ်ပါသည်။\nCredit: Hub")
        os.remove(file_path)
    if chat_id in status_data: del status_data[chat_id]

# --- Bot Handlers ---

# <--- MODIFIED (save_user_id ကိုခေါ်ရန်) --->
async def start_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    save_user_id(user.id) # User ID ကို file ထဲသိမ်းပါ
    welcome_text = (f"👋 **မင်္ဂလာပါ {user.first_name}၊**\n"
                    f"Hub Proxy Checker Bot မှ ကြိုဆိုပါတယ်။\n\n"
                    f"ကျေးဇူးပြု၍ အောက်ပါတို့မှ လုပ်ဆောင်ချက်တစ်ခုကို ရွေးချယ်ပါ။")
    if update.callback_query:
        await update.callback_query.edit_message_text(welcome_text, reply_markup=get_main_menu_keyboard(), parse_mode='Markdown')
    else:
        await update.message.reply_text(welcome_text, reply_markup=get_main_menu_keyboard(), parse_mode='Markdown')
    for key in list(context.user_data.keys()):
        del context.user_data[key]


# <--- NEW Admin Handler Functions --->
async def admin_plan_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    user_id = query.from_user.id

    if user_id in ADMIN_IDS:
        # User is an Admin
        await query.edit_message_text(
            text="🔑 **Admin Control Panel**\n\nကျေးဇူးပြု၍ လုပ်ဆောင်ချက်တစ်ခုကို ရွေးချယ်ပါ။",
            reply_markup=get_admin_menu_keyboard()
        )
    else:
        # User is not an Admin
        admin_text = ("**👑 Admin Plan Information**\n\n"
                      "Admin Plan နှင့် ပတ်သက်သော အသေးစိတ် အချက်အလက်များအတွက် "
                      "Admin ကို ဆက်သွယ်မေးမြန်းနိုင်ပါသည်။\n\n"
                      "ဆက်သွယ်ရန်: [Your Contact Link or Username]")
        await query.edit_message_text(text=admin_text, parse_mode='Markdown',
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« နောက်သို့ (ပင်မစာမျက်နှာ)", callback_data='back_to_main')]]))

async def admin_stats_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    user_ids = load_user_ids()
    stats_text = (f"📊 **Bot Statistics**\n\n"
                  f"👥 စုစုပေါင်း အသုံးပြုသူ: {len(user_ids)} ဦး")
    await query.edit_message_text(text=stats_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« နောက်သို့ (Admin Menu)", callback_data='admin_plan')]]))

async def run_broadcast(context: CallbackContext, chat_id: int, message_id: int):
    """Broadcast message ပို့ခြင်းကို နောက်ကွယ်ကနေ run ပေးသည်။"""
    user_ids = load_user_ids()
    successful_sends = 0
    failed_sends = 0
    
    await context.bot.send_message(chat_id, f"📢 Broadcast စတင်နေပါပြီ... User {len(user_ids)} ဦးထံ ပေးပို့ပါမည်။")

    for user_id in user_ids:
        try:
            await context.bot.copy_message(chat_id=user_id, from_chat_id=chat_id, message_id=message_id)
            successful_sends += 1
        except Forbidden:
            failed_sends += 1
            print(f"Failed to send to {user_id}: Bot was blocked or kicked.")
        except Exception as e:
            failed_sends += 1
            print(f"An unexpected error occurred for user {user_id}: {e}")
        await asyncio.sleep(0.1) # To avoid hitting rate limits

    result_text = (f"✅ **Broadcast ပြီးဆုံးပါပြီ**\n\n"
                   f"📤 အောင်မြင်စွာ ပေးပို့ပြီး: {successful_sends} ဦး\n"
                   f"❌ မအောင်မြင် (Bot ကို block ထား): {failed_sends} ဦး")
    await context.bot.send_message(chat_id, result_text)
# <------------------------------------->


# <--- MODIFIED (Admin features များထည့်သွင်းထား) --->
async def button_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat.id

    if data == 'cancel_check':
        if status_data.get(chat_id, {}).get('running', False):
            async with data_lock:
                status_data[chat_id]['running'] = False
                status_data[chat_id]['cancelled'] = True
            await query.edit_message_text("🛑 ရပ်တန့်နေပါသည်... ရလဒ์များကို စုစည်းနေသည်...")
        return

    if status_data.get(chat_id, {}).get('running', False):
        await context.bot.send_message(chat_id, "⏳ ကျေးဇူးပြု၍ လက်ရှိလုပ်ဆောင်နေသော လုပ်ငန်းစဉ်ပြီးဆုံးသည်အထိ (သို့မဟုတ်) ရပ်တန့်ပြီးသည်အထိ စောင့်ဆိုင်းပေးပါ။")
        return
    
    # --- Broadcast Confirmation ---
    if data == 'broadcast_confirm':
        message_id = context.user_data.get('broadcast_message_id')
        if message_id:
            await query.edit_message_text("Broadcast ကို အတည်ပြုပြီးပါပြီ။ မကြာမီ စတင်ပါမည်...")
            asyncio.create_task(run_broadcast(context, chat_id, message_id))
            del context.user_data['broadcast_message_id']
            if 'next_action' in context.user_data: del context.user_data['next_action']
        return
    elif data == 'broadcast_cancel':
        if 'broadcast_message_id' in context.user_data: del context.user_data['broadcast_message_id']
        if 'next_action' in context.user_data: del context.user_data['next_action']
        await query.edit_message_text("❌ Broadcast ကို ပယ်ဖျက်လိုက်ပါသည်။", reply_markup=get_admin_menu_keyboard())
        return

    # --- Main Menu & Admin Menu Routing ---
    if data == 'generate_check':
        context.user_data['next_action'] = 'get_proxy_count'
        await query.edit_message_text("👇 ကျေးဇူးပြု၍ Proxy အမျိုးအစားကို ရွေးချယ်ပါ:", reply_markup=get_type_selection_keyboard())
    elif data == 'check_from_file':
        context.user_data['next_action'] = 'get_proxy_file'
        await query.edit_message_text("📂 **သင်၏ proxy list ကို `.txt` ဖိုင်ဖြင့် ပေးပို့ပါ။**\n\n"
                                      "Proxy တစ်ခုစီကို လိုင်းတစ်ကြောင်းစီတွင် ထားပေးပါ (`ip:port`)။",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« နောက်သို့ (ပင်မစာမျက်နှာ)", callback_data='back_to_main')]]))
    elif data.startswith('select_'):
        proxy_type = data.split('_')[1]
        context.user_data['proxy_type_to_generate'] = proxy_type
        type_name = "All Types" if proxy_type == "all" else proxy_type.upper()
        await query.edit_message_text(f"❓ **{type_name} proxy အရေအတွက် ဘယ်လောက်လိုချင်ပါသလဲ။**\n\nကျေးဇူးပြု၍ နံပါတ်တစ်ခု ရိုက်ထည့်ပါ (ဥပမာ- 500)။",
                                      reply_markup=get_back_to_type_selection_keyboard())
    elif data == 'admin_plan':
        await admin_plan_handler(update, context)
    elif data == 'admin_stats':
        await admin_stats_handler(update, context)
    elif data == 'admin_broadcast':
        context.user_data['next_action'] = 'get_broadcast_message'
        await query.edit_message_text("📢 **Broadcast Message**\n\nပေးပို့လိုသော message ကို ဤနေရာသို့ ရိုက်ထည့်ပါ သို့မဟုတ် forward လုပ်ပါ။",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« နောက်သို့ (Admin Menu)", callback_data='admin_plan')]]))
    elif data == 'back_to_main':
        await start_command(update, context)
    elif data == 'back_to_type_select':
        context.user_data['next_action'] = 'get_proxy_count'
        await query.edit_message_text("👇 ကျေးဇူးပြု၍ Proxy အမျိုးအစားကို ရွေးချယ်ပါ:", reply_markup=get_type_selection_keyboard())

# <--- MODIFIED (Broadcast message လက်ခံရန်) --->
async def message_handler(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    if status_data.get(chat_id, {}).get('running', False):
        await update.message.reply_text("⏳ ကျေးဇူးပြု၍ လက်ရှိလုပ်ဆောင်နေသော လုပ်ငန်းစဉ်ပြီးဆုံးသည်အထိ စောင့်ဆိုင်းပေးပါ။")
        return

    next_action = context.user_data.get('next_action')

    # --- Handle Broadcast Message ---
    if next_action == 'get_broadcast_message' and chat_id in ADMIN_IDS:
        context.user_data['broadcast_message_id'] = update.message.message_id
        user_count = len(load_user_ids())
        confirmation_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes, ပေးပို့မည်", callback_data='broadcast_confirm')],
            [InlineKeyboardButton("❌ No, ပယ်ဖျက်မည်", callback_data='broadcast_cancel')]
        ])
        await update.message.reply_text(f"👆 ဤ message ကို အသုံးပြုသူ {user_count} ဦးထံ ပေးပို့ရန် သေချာပါသလား?", reply_markup=confirmation_keyboard)
        return

    # --- Handle Proxy Count ---
    if next_action == 'get_proxy_count':
        try:
            count = int(update.message.text)
            if not 1 <= count <= 20000:
                await update.message.reply_text("❌ ကျေးဇူးပြု၍ 1 မှ 20,000 ကြား နံပါတ်တစ်ခု ထည့်ပါ။")
                return
            
            proxy_type = context.user_data.get('proxy_type_to_generate', 'all')
            del context.user_data['next_action']
            if 'proxy_type_to_generate' in context.user_data: del context.user_data['proxy_type_to_generate']
            
            await update.message.reply_text(f"✅ လက်ခံရရှိပါပြီ။ Proxy များကို ရင်းမြစ်မှ ရှာဖွေနေပါသည်။ ခဏစောင့်ဆိုင်းပေးပါ...")
            
            loop = asyncio.get_running_loop()
            with ThreadPoolExecutor() as pool:
                if proxy_type == 'all':
                    count_per_type, remainder = divmod(count, 3)
                    tasks = [
                        loop.run_in_executor(pool, get_proxies_from_api, 'http', count_per_type + remainder),
                        loop.run_in_executor(pool, get_proxies_from_api, 'socks4', count_per_type),
                        loop.run_in_executor(pool, get_proxies_from_api, 'socks5', count_per_type)
                    ]
                    results = await asyncio.gather(*tasks)
                    all_proxies = [p for sublist in results for p in sublist]
                else:
                    all_proxies = await loop.run_in_executor(pool, get_proxies_from_api, proxy_type, count)
            
            if not all_proxies:
                await update.message.reply_text("❌ Proxy များ ရှာမတွေ့ပါ။ အခြားအရေအတွက်ဖြင့် ထပ်မံကြိုးစားကြည့်ပါ။", reply_markup=get_main_menu_keyboard())
                return

            unique_proxies_to_check = list(set(all_proxies))
            await update.message.reply_text(f"ထပ်မနေသော proxy {len(unique_proxies_to_check)} ခုကို တွေ့ရှိခဲ့ပြီး စစ်ဆေးမှုကို စတင်ပါမည်...")
            asyncio.create_task(run_checker(context, chat_id, unique_proxies_to_check))

        except (ValueError, TypeError):
            await update.message.reply_text("❌ မှားယွင်းသောထည့်သွင်းမှု။ ကျေးဇူးပြု၍ နံပါတ်တစ်ခုတည်းသာ ရိုက်ထည့်ပါ။")

async def file_handler(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    if status_data.get(chat_id, {}).get('running', False):
        await update.message.reply_text("⏳ ကျေးဇူးပြု၍ လက်ရှိလုပ်ဆောင်နေသော လုပ်ငန်းစဉ်ပြီးဆုံးသည်အထိ စောင့်ဆိုင်းပေးပါ။")
        return
    if context.user_data.get('next_action') == 'get_proxy_file':
        del context.user_data['next_action']
        document = update.message.document
        file = await context.bot.get_file(document.file_id)
        proxies = (await file.download_as_bytearray()).decode('utf-8').strip().splitlines()
        if not proxies:
            await update.message.reply_text("❌ သင်ပေးပို့သောဖိုင်သည် ဗလာဖြစ်နေပါသည်။", reply_markup=get_main_menu_keyboard())
            return
        await update.message.reply_text(f"✅ ဖိုင်ကို လက်ခံရရှိပါပြီ။ စစ်ဆေးရန် proxy {len(proxies)} ခု တွေ့ရှိပါသည်။")
        asyncio.create_task(run_checker(context, chat_id, proxies))

def main() -> None:
    """Start the bot."""
    print("Bot is running...")
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    application.add_handler(MessageHandler(filters.Document.TXT, file_handler))
    # This is a catch-all for any message types (photo, video, etc.) for the broadcast
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))
    
    application.run_polling()

if __name__ == '__main__':
    main()
