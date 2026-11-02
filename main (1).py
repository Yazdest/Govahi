import asyncio, logging, os, re, html, json, base64
from io import BytesIO
from urllib.parse import quote
from collections import defaultdict, deque

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, MenuButtonWebApp, BufferedInputFile
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from openai import AsyncOpenAI

BOT_TOKEN = os.getenv('BOT_TOKEN')
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
POLLINATIONS_API_KEY = (os.getenv('POLLINATIONS_API_KEY') or '').strip().strip('\\"').strip("'")
MINI_APP_URL = os.getenv('MINI_APP_URL', '').strip()
GROQ_TEXT_MODEL = os.getenv('GROQ_TEXT_MODEL', 'openai/gpt-oss-120b')
OPENROUTER_TEXT_MODEL = os.getenv('OPENROUTER_TEXT_MODEL', 'openrouter/free')
GROQ_VISION_MODEL = os.getenv('GROQ_VISION_MODEL', 'qwen/qwen3.8-27b')
OPENROUTER_VISION_MODEL = os.getenv('OPENROUTER_VISION_MODEL', 'nvidia/nemotron-3.5-super-vl:free')
IMAGE_MODEL = os.getenv('IMAGE_MODEL', 'black-forest-labs/flux.1-schnell')
HISTORY_LIMIT = 30
MAX_UPLOAD = 20 * 1024 * 1024
SYSTEM = 'تو هوشمند هستی؛ دستیار فارسی‌زبان دقیق، خوش‌برخورد و مختصر. پاسخ را به زبان کاربر بده.'
MARKET_URL = 'https://raw.githubusercontent.com/iran-market/iran-market.github.io/main/data/popular.json'

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
log = logging.getLogger('hooshmand')
if not BOT_TOKEN:
    raise RuntimeError('BOT_TOKEN در Environment Variables تنظیم نشده است.')

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
groq = AsyncOpenAI(api_key=GROQ_API_KEY, base_url='https://api.groq.com/openai/v1', timeout=50) if GROQ_API_KEY else None
router = AsyncOpenAI(api_key=OPENROUTER_API_KEY, base_url='https://openrouter.ai/api/v1', timeout=65,
    default_headers={'HTTP-Referer':'https://github.com/Yazdest/Hooshmand','X-Title':'Hooshmand Toolbox'}) if OPENROUTER_API_KEY else None
histories = defaultdict(lambda: deque(maxlen=HISTORY_LIMIT))
states = {}
tts_voice_preferences = {}
TTS_VOICES = {
    '👩 صدای زنانه (دیلا)': 'fa-IR-DilaraNeural',
    '👨 صدای مردانه (فرید)': 'fa-IR-FaridNeural',
}

TOOLS = {
 'summary': '📚 خلاصه‌ساز متن و فایل',
 'stt': '🎙 تبدیل صدا به متن',
 'translator': '🌐 مترجم هوشمند',
 'tts': '🔊 تولید صوت از متن',
 'image': '🎨 تولید تصویر',
 'ocr': '📷 استخراج متن از عکس',
 'market': '💰 قیمت طلا و ارز'
}

def main_keyboard():
    b = ReplyKeyboardBuilder()
    b.row(KeyboardButton(text=TOOLS['summary']), KeyboardButton(text=TOOLS['stt']))
    b.row(KeyboardButton(text=TOOLS['translator']), KeyboardButton(text=TOOLS['tts']))
    b.row(KeyboardButton(text=TOOLS['image']), KeyboardButton(text=TOOLS['ocr']))
    b.row(KeyboardButton(text=TOOLS['market']))
    b.row(KeyboardButton(text='🏠 منوی اصلی'))
    return b.as_markup(resize_keyboard=True, is_persistent=True, input_field_placeholder='یک ابزار انتخاب کن…')

def esc(s):
    return html.escape(str(s or ''))

def markdown_to_telegram_html(text):
    """Convert common Markdown formatting to safe Telegram-compatible HTML."""
    text = str(text or '')
    code_blocks = []

    def save_code_block(match):
        placeholder = f'@@CODEBLOCK{len(code_blocks)}@@'
        code_blocks.append('<pre><code>' + esc(match.group(1).strip('\\n')) + '</code></pre>')
        return placeholder

    text = re.sub(r'```(?:[a-zA-Z0-9_+-]+)?\\s*\\n(.*?)```', save_code_block, text, flags=re.S)
    inline_codes = []

    def save_inline_code(match):
        placeholder = f'@@INLINECODE{len(inline_codes)}@@'
        inline_codes.append('<code>' + esc(match.group(1)) + '</code>')
        return placeholder

    text = re.sub(r'`([^`\\n]+)`', save_inline_code, text)
    text = esc(text)

    def convert_link(match):
        label = match.group(1)
        url = html.unescape(match.group(2)).strip()
        if not url.startswith(('https://', 'http://', 'tg://')):
            return label
        return f'<a href="{html.escape(url, quote=True)}">{label}</a>'

    text = re.sub(r'\\[([^\\]]+)\\]\\((https?://[^)\\s]+|tg://[^)\\s]+)\\)', convert_link, text)
    text = re.sub(r'(?m)^#{1,6}\\s+(.+)$', r'<b>\\1</b>', text)
    text = re.sub(r'\\*\\*(.+?)\\*\\*', r'<b>\\1</b>', text, flags=re.S)
    text = re.sub(r'__(.+?)__', r'<b>\\1</b>', text, flags=re.S)
    text = re.sub(r'(?<!\\*)\\*([^*\\n]+)\\*(?!\\*)', r'<i>\\1</i>', text)
    text = re.sub(r'(?<!_)_([^_\\n]+)_(?!_)', r'<i>\\1</i>', text)
    text = re.sub(r'~~(.+?)~~', r'<s>\\1</s>', text, flags=re.S)

    for i, code in enumerate(code_blocks):
        text = text.replace(f'@@CODEBLOCK{i}@@', code)
    for i, code in enumerate(inline_codes):
        text = text.replace(f'@@INLINECODE{i}@@', code)
    return text

def fa_num(n): return f'{int(round(float(n))):,}'.translate(str.maketrans('0123456789','۰۱۲۳۴۵۶۷۸۹'))

async def chat_completion(prompt, user_id, image_data_url=None):
    msgs=[{'role':'system','content':SYSTEM}]
    msgs.extend(list(histories[user_id])[-8:])
    if image_data_url:
        msgs.append({'role':'user','content':[{'type':'text','text':prompt},{'type':'image_url','image_url':{'url':image_data_url}}]})
        providers=[(groq,GROQ_VISION_MODEL),(router,OPENROUTER_VISION_MODEL)]
    else:
        msgs.append({'role':'user','content':prompt})
        providers=[(groq,GROQ_TEXT_MODEL),(router,OPENROUTER_TEXT_MODEL)]
    errors=[]
    for client,model in providers:
        if not client: continue
        try:
            r=await asyncio.wait_for(client.chat.completions.create(model=model,messages=msgs,max_tokens=2200),timeout=65)
            out=r.choices[0].message.content
            if isinstance(out,list): out='\\n'.join(x.get('text','') for x in out if isinstance(x,dict))
            if out and out.strip(): return out.strip()
            raise RuntimeError('empty response')
        except Exception as e:
            log.exception('chat provider %s failed',model); errors.append(f'{model}:{type(e).__name__}')
    raise RuntimeError(' / '.join(errors) or 'کلید GROQ_API_KEY یا OPENROUTER_API_KEY تنظیم نشده است.')

async def download_file(file_id):
    f=await bot.get_file(file_id); buf=BytesIO(); await bot.download_file(f.file_path,destination=buf)
    data=buf.getvalue()
    if len(data)>MAX_UPLOAD: raise ValueError('حجم فایل بیشتر از ۲۰ مگابایت است.')
    return data, f.file_path or 'upload.bin'

def tts_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text='👩 صدای زنانه (دیلا)'), KeyboardButton(text='👨 صدای مردانه (فرید)')],
            [KeyboardButton(text='🏠 منوی اصلی')],
        ],
        resize_keyboard=True, is_persistent=True,
        input_field_placeholder='ابتدا صدای گوینده را انتخاب کن…'
    )

async def set_tool(m, tool):
    states[m.from_user.id]=tool
    if tool == 'tts':
        await m.answer(tool_prompt(tool), reply_markup=tts_keyboard())
    else:
        await m.answer(tool_prompt(tool),reply_markup=main_keyboard())

async def send_menu(m):
    states.pop(m.from_user.id,None)
    await m.answer('هوشمند | جعبه‌ابزار هوشمند\\nابزار موردنظرت را انتخاب کن:',reply_markup=main_keyboard())

@dp.message(CommandStart())
async def start(m): await send_menu(m)

@dp.message(Command('help'))
async def help_cmd(m): await send_menu(m)

@dp.message(Command('clear'))
async def clear(m):
    histories[m.from_user.id].clear(); await m.answer('حافظه موقت پاک شد.',reply_markup=main_keyboard())

@dp.message(Command('imagecheck'))
async def imagecheck(m):
    if POLLINATIONS_API_KEY:
        prefix = 'sk_' if POLLINATIONS_API_KEY.startswith('sk_') else 'نامشخص'
        await m.answer(
            '🔎 بررسی تنظیمات تولید تصویر\\n'
            'متغیر POLLINATIONS_API_KEY در پردازش فعلی ربات دریافت شده است.\\n'
            f'نوع پیشوند: {prefix}\\n'
            f'مدل تنظیم‌شده: {esc(IMAGE_MODEL)}\\n'
            'مقدار کلید نمایش داده نمی‌شود.'
        )
    else:
        await m.answer(
            '❌ پردازش فعلی ربات متغیر POLLINATIONS_API_KEY را دریافت نکرده است.\\n'
            'نام متغیر را دقیقاً همین‌طور بررسی کن، مقدار را در Environment Variables همان سرویس اجرا ذخیره کن و سپس Deploy/Restart انجام بده.'
        )

@dp.message(F.web_app_data)
async def webapp_data(m):
    try:
        data=json.loads(m.web_app_data.data); tool=data.get('tool')
        if tool in TOOLS: await set_tool(m,tool)
    except Exception: await m.answer('در دریافت انتخاب ابزار مشکلی پیش آمد.')

def tool_prompt(tool):
    return {
      'stt':'ویس یا فایل صوتی را ارسال کن تا گفتار آن به متن تبدیل شود.',
      'summary':'متن را همین‌جا بفرست یا فایل PDF، Word با پسوند DOCX یا TXT ارسال کن تا خلاصه شود. PDF اسکن‌شده در این نسخه پشتیبانی نمی‌شود.',
      'translator':'متن را بفرست و زبان مقصد را هم مشخص کن؛ اگر ننویسی، زبان مقصد را از درخواست تشخیص می‌دهم.',
      'tts':'ابتدا صدای گوینده را انتخاب کن. سبک پیش‌فرض صمیمی و پرانرژی است؛ سپس متن فارسی را بفرست. (حداکثر ۳۰۰۰ نویسه در هر بار)',
      'image':'توضیح دقیق تصویری را که می‌خواهی بسازم بنویس؛ هرچه جزئیات سوژه، ظاهر و محیط را روشن‌تر توضیح بدهی، نتیجه به خواسته‌ات نزدیک‌تر می‌شود.',
      'ocr':'عکس حاوی نوشته یا دست‌خط را ارسال کن تا متن آن استخراج شود.',
      'market':'برای دریافت آخرین داده‌های منتشرشده بازار، روی «دریافت قیمت‌ها» بزن یا همین گزینه را دوباره انتخاب کن.'
    }.get(tool,'یک ابزار انتخاب کن.')

@dp.message(F.text)
async def text_handler(m):
    text=m.text.strip(); uid=m.from_user.id
    if text=='🏠 منوی اصلی': return await send_menu(m)
    if text in TTS_VOICES:
        tts_voice_preferences[uid] = TTS_VOICES[text]
        states[uid] = 'tts'
        voice_label = 'زنانه، دیلا' if 'Dilara' in TTS_VOICES[text] else 'مردانه، فرید'
        return await m.answer(f'✅ صدای {voice_label} انتخاب شد. حالا متن فارسی را بفرست؛ لحن پیش‌فرض صمیمی و پرانرژی است.', reply_markup=tts_keyboard())
    tool=next((k for k,v in TOOLS.items() if v==text),None)
    if tool:
        if tool=='market': return await market_prices(m)
        return await set_tool(m,tool)
    tool=states.get(uid)
    if tool=='translator':
        prompt='متن زیر را به زبان مقصدی که کاربر مشخص کرده ترجمه کن. اگر مقصد مشخص نشده، زبان مناسب را تشخیص بده. فقط ترجمه را ارائه بده:\\n'+text
    elif tool=='tts':
        return await text_to_speech(m,text)
    elif tool=='image':
        return await generate_image(m,text)
    elif tool=='summary':
        await summarize_text_message(m, text); return
    elif tool=='stt':
        await m.answer('برای تبدیل صدا به متن، ویس یا فایل صوتی بفرست.'); return
    elif tool=='ocr':
        await m.answer('برای استخراج متن، عکس را ارسال کن.'); return
    else:
        await m.answer('ابتدا یکی از ابزارهای منو را انتخاب کن.',reply_markup=main_keyboard()); return
    status=await m.answer('⏳ در حال پردازش…')
    try:
        answer=await chat_completion(prompt,uid)
        histories[uid].append({'role':'user','content':text}); histories[uid].append({'role':'assistant','content':answer})
        await status.edit_text(markdown_to_telegram_html(answer[:3500]))
    except Exception:
        log.exception('text tool failed'); await status.edit_text('فعلاً سرویس هوش مصنوعی پاسخ نداد. کمی بعد دوباره تلاش کن.')

async def _edge_tts_audio(text, voice):
    import edge_tts
    pitch = '+12Hz' if voice == 'fa-IR-DilaraNeural' else '+6Hz'
    audio = BytesIO()
    communicate = edge_tts.Communicate(text, voice, rate='+8%', pitch=pitch, volume='+0%', connect_timeout=15, receive_timeout=60)
    async for chunk in communicate.stream():
        if chunk.get('type') == 'audio': audio.write(chunk.get('data', b''))
    data = audio.getvalue()
    if len(data) < 256: raise RuntimeError('Edge TTS returned an empty or incomplete audio stream')
    return data

async def text_to_speech(m,text):
    text=(text or '').strip()
    if not text: return await m.answer('لطفاً متنی را برای تبدیل به صوت بفرست.')
    if len(text)>3000: return await m.answer('متن طولانی است؛ لطفاً آن را به بخش‌های حداکثر ۳۰۰۰ نویسه‌ای تقسیم کن.')
    selected_preference = tts_voice_preferences.get(m.from_user.id)
    if not selected_preference:
        return await m.answer('برای انتخاب گوینده، ابتدا یکی از دو گزینه صدای زنانه یا مردانه را از دکمه‌های پایین انتخاب کن.', reply_markup=tts_keyboard())
    status=await m.answer('🔊 در حال ساخت فایل صوتی با لحن صمیمی و پرانرژی…')
    other_voice = 'fa-IR-FaridNeural' if selected_preference == 'fa-IR-DilaraNeural' else 'fa-IR-DilaraNeural'
    voices=[selected_preference, other_voice]
    errors=[]
    try:
        audio_data=None; selected_voice=None
        for voice in voices:
            try:
                audio_data=await asyncio.wait_for(_edge_tts_audio(text,voice),timeout=100)
                selected_voice=voice; break
            except Exception as e:
                errors.append(f'{voice}: {type(e).__name__}: {str(e)[:180]}')
                log.exception('Edge TTS failed for voice %s',voice)
        if not audio_data: raise RuntimeError(' | '.join(errors) or 'No audio was returned by Edge TTS')
        await status.delete()
        await m.answer_audio(BufferedInputFile(audio_data,filename='hooshmand-tts.mp3'),
            caption=('🔊 صدای تولیدشده توسط هوشمند | گوینده: ' + ('دیلا' if selected_voice == 'fa-IR-DilaraNeural' else 'فرید') + ' | لحن: صمیمی و پرانرژی'))
        log.info('TTS completed successfully with voice=%s bytes=%s',selected_voice,len(audio_data))
    except Exception as e:
        log.exception('TTS failed'); detail=f'{type(e).__name__}: {str(e)[:260]}'
        try:
            await status.edit_text('❌ تولید صوت ناموفق بود. خطای فنی برای عیب‌یابی:\\n'+esc(detail)+'\\n\\nاگر مشکل تکرار شد، همین متن خطا را برای پشتیبانی ارسال کن.')
        except Exception: await m.answer('❌ تولید صوت ناموفق بود: '+esc(detail))

async def translate_image_prompt(prompt):
    prompt=(prompt or '').strip()
    if not prompt: raise ValueError('توضیح تصویر خالی است.')
    if not re.search(r'[\\u0600-\\u06FF\\u0750-\\u077F\\u08A0-\\u08FF]',prompt): return prompt
    system_prompt=('You are a precise Persian-to-English translator for image-generation prompts. '
        'Translate the user request into clear, natural, concise English. Preserve the exact main subject, '
        'actions, setting, relationships, quantities, and visual details. Do not reinterpret, embellish, '
        'replace, omit, or invent any subject or object. Do not answer the request. Do not add a castle, '
        'mountain, forest, person, animal, or other object unless the user explicitly requested it. '
        'Make the requested main subject explicit at the beginning. Return only the English image prompt, '
        'with no explanation, labels, quotation marks, or commentary.')
    messages=[{'role':'system','content':system_prompt},{'role':'user','content':prompt}]
    errors=[]
    for client,model in ((groq,GROQ_TEXT_MODEL),(router,OPENROUTER_TEXT_MODEL)):
        if not client: continue
        try:
            response=await asyncio.wait_for(client.chat.completions.create(model=model,messages=messages,temperature=0.1,max_tokens=700),timeout=55)
            translated=response.choices[0].message.content
            if isinstance(translated,list): translated=' '.join(x.get('text','') for x in translated if isinstance(x,dict))
            translated=(translated or '').strip().strip('"“”')
            if not translated: raise RuntimeError('empty translation')
            log.info('Image prompt translated successfully using %s',model); return translated
        except Exception as e:
            log.exception('Image prompt translation failed using %s',model); errors.append(f'{model}: {type(e).__name__}')
    raise RuntimeError('ترجمه دقیق متن فارسی به انگلیسی انجام نشد. کلید GROQ_API_KEY یا OPENROUTER_API_KEY و دسترسی مدل متنی را بررسی کن. '+('; '.join(errors) if errors else 'هیچ سرویس متنی تنظیم نشده است.'))

async def generate_image(m,prompt):
    if not POLLINATIONS_API_KEY:
        return await m.answer('❌ ربات در زمان اجرا کلید POLLINATIONS_API_KEY را دریافت نکرده است.\\nدر FadeHost نام متغیر را دقیقاً بررسی کن، سپس Deploy و Restart انجام بده. برای آزمایش دریافت کلید دستور /imagecheck را بفرست.')
    status=await m.answer('🌐 در حال آماده‌سازی توضیح فارسی و تولید تصویر…')
    try:
        english_prompt=await translate_image_prompt(prompt)
        log.info('Starting image generation | prompt_chars=%s translated_chars=%s',len(prompt),len(english_prompt))
        image_url='https://gen.pollinations.ai/image/'+quote(english_prompt, safe='')
        timeout=aiohttp.ClientTimeout(total=180, connect=25, sock_read=150)
        headers={'Authorization':f'Bearer {POLLINATIONS_API_KEY}', 'Accept':'image/*'}
        params={'model':IMAGE_MODEL, 'width':1024, 'height':1024}
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(image_url, headers=headers, params=params) as response:
                if response.status >= 400:
                    detail=(await response.text())[:700]
                    if response.status == 401: reason='کلید نامعتبر است یا به‌درستی ارسال نشده است (401).'
                    elif response.status == 402: reason='اعتبار حساب یا بودجه کلید کافی نیست (402).'
                    elif response.status == 403: reason='کلید اجازه استفاده از این مدل/سرویس را ندارد (403).'
                    elif response.status == 429: reason='محدودیت درخواست یا سهمیه موقتاً پر شده است (429).'
                    else: reason=f'سرویس Pollinations خطای {response.status} برگرداند.'
                    log.error('Pollinations image error %s: %s', response.status, detail); raise RuntimeError(reason)
                content_type=response.headers.get('Content-Type','').lower(); image_bytes=await response.read()
                if not content_type.startswith('image/') or not image_bytes:
                    preview=image_bytes[:300].decode('utf-8','ignore')
                    log.error('Unexpected Pollinations response content-type=%s body=%s',content_type,preview)
                    raise RuntimeError('پاسخ سرویس تصویر نبود یا خالی بود.')
        await status.delete()
        await m.answer_photo(BufferedInputFile(image_bytes,filename='hooshmand-image.png'),caption='🎨 تصویر ساخته شد.')
    except Exception as e:
        log.exception('image generation failed')
        message=str(e) if isinstance(e,RuntimeError) else 'ارتباط با سرویس تولید تصویر برقرار نشد.'
        try: await status.edit_text('❌ تولید تصویر انجام نشد.\\n'+esc(message)+'\\n\\nدر صورت نیاز، دریافت کلید را با /imagecheck بررسی کن.')
        except Exception: await m.answer('❌ تولید تصویر انجام نشد. '+esc(message))

async def market_prices(m):
    status=await m.answer('💹 در حال دریافت داده‌های بازار…')
    try:
        timeout=aiohttp.ClientTimeout(total=25)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(MARKET_URL,headers={'User-Agent':'HooshmandBot/1.0'}) as r:
                r.raise_for_status(); payload=await r.json(content_type=None)
        data=payload.get('data',[]) if isinstance(payload,dict) else []
        wanted={'USD_IRR_FREE':'دلار آزاد','EUR_IRR_FREE':'یورو آزاد','GOLD_18K_IRR':'طلای ۱۸ عیار (هر گرم)','COIN_EMAMI_IRR':'سکه امامی','USDT_IRR':'تتر'}
        by_symbol={x.get('symbol'):x for x in data if isinstance(x,dict)}
        lines=['💰 <b>قیمت‌های منتخب بازار</b>','<i>منبع: Iran Market Data</i>','']
        for sym,label in wanted.items():
            item=by_symbol.get(sym)
            if not item or item.get('price') is None: continue
            price=item['price']; unit=item.get('unit') or 'تومان'
            try: price_text=fa_num(price)
            except Exception: price_text=str(price)
            lines.append(f'• <b>{label}:</b> {price_text} {esc(unit)}')
        if len(lines)==3: raise RuntimeError('No expected symbols in source response')
        stamp=payload.get('updated_at') or payload.get('last_updated') or payload.get('timestamp')
        if stamp: lines += ['',f'<i>زمان داده: {esc(stamp)}</i>']
        lines += ['', '<i>به‌روزرسانی منبع حدوداً هر ۳۰ دقیقه است؛ قیمت‌ها ممکن است با بازار لحظه‌ای اختلاف داشته باشند. این اطلاعات توصیه خریدوفروش نیست.</i>']
        await status.edit_text('\\n'.join(lines))
    except Exception:
        log.exception('market request failed')
        await status.edit_text('دریافت قیمت‌ها از منبع بازار فعلاً ناموفق بود. ممکن است منبع موقتاً در دسترس نباشد؛ کمی بعد دوباره امتحان کن.')

@dp.message(F.photo)
async def photo_handler(m):
    tool=states.get(m.from_user.id)
    if tool not in ('ocr',): return await m.answer('برای استخراج متن از عکس، ابتدا ابزار «استخراج متن از عکس» را انتخاب کن.')
    status=await m.answer('📷 در حال خواندن نوشته‌های تصویر…')
    try:
        data,_=await download_file(m.photo[-1].file_id)
        mime='image/jpeg'; data_url=f'data:{mime};base64,{base64.b64encode(data).decode()}'
        answer=await chat_completion('تمام متن قابل مشاهده در تصویر را دقیقاً استخراج کن. زبان و املای متن را حفظ کن. اگر بخشی ناخواناست آن را با [ناخوانا] مشخص کن؛ توضیح اضافه نده.',m.from_user.id,data_url)
        await status.edit_text('<b>متن استخراج‌شده:</b>\\n'+markdown_to_telegram_html(answer[:3500]))
    except Exception:
        log.exception('OCR failed'); await status.edit_text('استخراج متن ناموفق بود. کلید سرویس بینایی (Groq یا OpenRouter) و اندازه عکس را بررسی کن.')

async def summarize_text_message(m, raw):
    raw=(raw or '').strip()
    if not raw: return await m.answer('متن خالی است. متن موردنظر را بفرست یا فایل PDF، DOCX یا TXT ارسال کن.')
    if len(raw)>50000: raw=raw[:50000]; note='\\n\\n[برای پردازش، متن پس از ۵۰ هزار نویسه کوتاه شده است.]'
    else: note=''
    status=await m.answer('📝 در حال خلاصه‌سازی متن…')
    try:
        answer=await chat_completion('متن زیر را به فارسی، منظم و دقیق خلاصه کن. عنوان مناسب، موضوع اصلی، نکات کلیدی و نتیجه‌گیری را حفظ کن. از افزودن اطلاعاتی که در متن نیست خودداری کن:\\n'+raw+note,m.from_user.id)
        await status.edit_text(markdown_to_telegram_html(answer[:3500]))
    except Exception:
        log.exception('text summary failed'); await status.edit_text('خلاصه‌سازی متن ناموفق بود. اتصال سرویس هوش مصنوعی و کلیدهای API را بررسی کن.')

@dp.message(F.document)
async def document_handler(m):
    doc=m.document; uid=m.from_user.id
    if doc.file_size and doc.file_size>MAX_UPLOAD: return await m.answer('حجم فایل بیش از ۲۰ مگابایت است.')
    if states.get(uid)!='summary': return await m.answer('برای خلاصه‌سازی، ابتدا ابزار «خلاصه‌ساز متن و فایل» را انتخاب کن و سپس متن یا فایل را بفرست.')
    status=await m.answer('📄 در حال خواندن فایل و آماده‌سازی خلاصه…')
    try:
        data,path=await download_file(doc.file_id); filename=(doc.file_name or path or '').lower()
        if filename.endswith('.pdf') or doc.mime_type=='application/pdf':
            from pypdf import PdfReader
            reader=PdfReader(BytesIO(data)); raw='\\n'.join(page.extract_text() or '' for page in reader.pages)
            if not raw.strip(): return await status.edit_text('این PDF اسکن‌شده یا تصویری است و متن قابل استخراج ندارد. در این نسخه PDF متنی پشتیبانی می‌شود.')
        elif filename.endswith('.docx') or doc.mime_type=='application/vnd.openxmlformats-officedocument.wordprocessingml.document':
            from docx import Document
            document=Document(BytesIO(data)); parts=[p.text for p in document.paragraphs if p.text.strip()]
            for table in document.tables:
                for row in table.rows:
                    cells=[cell.text.strip().replace('\\n',' / ') for cell in row.cells]
                    if any(cells): parts.append(' | '.join(cells))
            raw='\\n'.join(parts)
            if not raw.strip(): return await status.edit_text('در فایل Word متن قابل استخراج پیدا نشد.')
        elif filename.endswith('.txt') or doc.mime_type.startswith('text/'):
            raw=None
            for encoding in ('utf-8-sig','utf-16','cp1256','windows-1252'):
                try: raw=data.decode(encoding); break
                except UnicodeDecodeError: continue
            if raw is None: raise ValueError('Unsupported text encoding')
            if not raw.strip(): return await status.edit_text('فایل TXT خالی است.')
        else: return await status.edit_text('این نوع فایل پشتیبانی نمی‌شود. لطفاً PDF، Word با پسوند DOCX یا فایل TXT بفرست.')
        if len(raw)>50000: raw=raw[:50000]+'\\n[ادامه فایل به علت محدودیت پردازش وارد خلاصه نشده است.]'
        answer=await chat_completion('محتوای استخراج‌شده از فایل را به فارسی، منظم و دقیق خلاصه کن. عنوان، موضوع اصلی، نکات کلیدی و نتیجه‌گیری را حفظ کن و هیچ اطلاعاتی خارج از متن اضافه نکن:\\n'+raw,uid)
        await status.edit_text(markdown_to_telegram_html(answer[:3500]))
    except Exception:
        log.exception('document summary failed'); await status.edit_text('خواندن یا خلاصه‌سازی فایل ناموفق بود. مطمئن شو فایل سالم است و از PDF متنی، DOCX یا TXT استفاده می‌کنی.')

@dp.message(F.voice | F.audio)
async def audio_handler(m):
    if states.get(m.from_user.id)!='stt': return await m.answer('برای تبدیل صدا به متن، ابتدا ابزار «تبدیل صدا به متن» را انتخاب کن.')
    status=await m.answer('🎧 در حال تبدیل صدا به متن…')
    try:
        if not groq: raise RuntimeError('GROQ_API_KEY missing')
        media=m.voice or m.audio; data,_=await download_file(media.file_id)
        filename='voice.ogg' if m.voice else (media.file_name or 'audio.mp3')
        result=await groq.audio.transcriptions.create(file=(filename,data),model='whisper-large-v3',response_format='text',language='fa')
        transcript=result if isinstance(result,str) else result.text
        await status.edit_text('🎙 <b>متن پیاده‌سازی‌شده:</b>\\n'+esc(transcript[:3800]))
    except Exception:
        log.exception('STT failed'); await status.edit_text('تبدیل صدا به متن ناموفق بود. کلید Groq، سهمیه Whisper و حداکثر حجم ۲۰ مگابایت را بررسی کن.')

@dp.message()
async def other(m): await m.answer('برای شروع، یکی از ابزارهای منو را انتخاب کن.',reply_markup=main_keyboard())

async def main():
    if MINI_APP_URL:
        try: await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text='ابزارها',web_app=WebAppInfo(url=MINI_APP_URL)))
        except Exception: log.exception('Could not set Mini App menu button')
    log.info('Hooshmand toolbox started | Groq=%s OpenRouter=%s Pollinations_key_loaded=%s Pollinations_prefix=%s MiniApp=%s',bool(groq),bool(router),bool(POLLINATIONS_API_KEY),('sk_' if POLLINATIONS_API_KEY.startswith('sk_') else 'other' if POLLINATIONS_API_KEY else 'missing'),bool(MINI_APP_URL))
    await dp.start_polling(bot)

if __name__=='__main__': asyncio.run(main())
