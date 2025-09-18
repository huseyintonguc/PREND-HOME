import streamlit as st
import pandas as pd
import requests
import base64
import openai
import re
from datetime import datetime, timedelta
from streamlit_autorefresh import st_autorefresh
import time
import pytz
from collections import Counter

# --- Streamlit Arayüzü ve Ayarları ---
st.set_page_config(layout="wide")
st.title("Trendyol Multi-Store Otomasyon Paneli")

# --- Ortak API Bilgilerini Oku ---
try:
    openai.api_key = st.secrets["OPENAI_API_KEY"]
    TELEGRAM_BOT_TOKEN = st.secrets.get("TELEGRAM_BOT_TOKEN")
    if "AUTHORIZED_CHAT_IDS" in st.secrets:
        AUTHORIZED_CHAT_IDS = st.secrets.get("AUTHORIZED_CHAT_IDS", [])
    else:
        chat_id = st.secrets.get("TELEGRAM_CHAT_ID")
        AUTHORIZED_CHAT_IDS = [chat_id] if chat_id else []
        
    STORES = st.secrets.get("stores", [])
except KeyError as e:
    st.error(f"'{e.args[0]}' adlı gizli bilgi (Secret) bulunamadı. Lütfen 'Secrets' bölümünü kontrol edin.")
    st.stop()

if not STORES:
    st.error("Yapılandırılmış herhangi bir mağaza bulunamadı. Lütfen secrets dosyanızı `[[stores]]` formatına göre düzenleyin.")
    st.stop()
if not AUTHORIZED_CHAT_IDS:
    st.error("`TELEGRAM_CHAT_ID` veya `AUTHORIZED_CHAT_IDS` listesinde yetkili bir kullanıcı bulunamadı.")
    st.stop()


# Sayfa otomatik yenileme
st_autorefresh(interval=60 * 1000, key="data_fetch_refresher")

# --- Ortak Fonksiyonlar ---

def get_headers(api_key, api_secret):
    credentials = f"{api_key}:{api_secret}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    return {"Authorization": f"Basic {encoded_credentials}", "Content-Type": "application/json", "User-Agent": "MultiStorePanel/1.0"}

def send_telegram_message(message, chat_id=None):
    if not TELEGRAM_BOT_TOKEN: return
    
    recipients = []
    if chat_id:
        recipients.append(chat_id)
    else:
        recipients.extend(AUTHORIZED_CHAT_IDS)

    for recipient_id in set(recipients):
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {'chat_id': recipient_id, 'text': message, 'parse_mode': 'Markdown'}
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception:
            pass

@st.cache_data(ttl=600)
def load_templates(file_path="cevap_sablonlari.xlsx"):
    try:
        df = pd.read_excel(file_path)
        return pd.Series(df.sablon_metni.values, index=df.keyword).to_dict()
    except FileNotFoundError:
        return {}
    except Exception as e:
        st.sidebar.error(f"Şablon dosyası okunurken hata: {e}")
        return {}

def process_telegram_updates(stores_map, templates):
    if 'last_update_id' not in st.session_state: st.session_state.last_update_id = 0
    offset = st.session_state.last_update_id + 1
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={offset}&timeout=10"
    try:
        response = requests.get(url, timeout=15)
        if response.status_code != 200: return
        updates = response.json().get("result", [])
        if not updates: return

        for update in updates:
            st.session_state.last_update_id = update.get("update_id")
            
            if 'message' not in update: continue
            message = update['message']
            chat_id = str(message['chat']['id'])
            
            if chat_id not in AUTHORIZED_CHAT_IDS:
                continue

            reply_text = message.get("text", "").strip()

            if reply_text == "/sablonlar":
                if templates:
                    template_list_message = "📋 *Kullanılabilir Cevap Şablonları:*\n\n"
                    for keyword in templates.keys():
                        template_list_message += f"`#{keyword}`\n"
                    template_list_message += "\n_(Bir soruya cevap verirken bu anahtar kelimeleri kullanabilirsiniz.)_"
                else:
                    template_list_message = "❌ Hiç cevap şablonu bulunamadı. Lütfen `cevap_sablonlari.xlsx` dosyasını kontrol edin."
                send_telegram_message(template_list_message, chat_id=chat_id)
                continue
            
            if 'reply_to_message' in message:
                original_message = message['reply_to_message']
                original_text = original_message.get("text", "")
                
                match_id = re.search(r"\(Soru ID: (\d+)\)", original_text)
                match_store = re.search(r"🏪 Mağaza: (.+?)\n", original_text)

                if match_id and match_store:
                    question_id = int(match_id.group(1))
                    store_name = match_store.group(1).strip()
                    
                    if store_name in stores_map:
                        store = stores_map[store_name]
                        final_answer = ""

                        if reply_text.startswith("#"):
                            keyword = reply_text[1:].lower()
                            if keyword in templates:
                                final_answer = templates[keyword]
                            else:
                                send_telegram_message(f"‼️ `{store_name}` için `#{keyword}` adında bir şablon bulunamadı.", chat_id=chat_id)
                                continue
                        else:
                            final_answer = reply_text

                        is_safe, reason = passes_forbidden_filter(final_answer)
                        if not is_safe:
                            send_telegram_message(f"‼️ `{store_name}` için cevap gönderilmedi: {reason}", chat_id=chat_id)
                            continue
                        
                        success, response_text = send_answer(store, question_id, final_answer)
                        if success:
                            msg = f"✅ `{store_name}` mağazası için (Soru ID: {question_id}) cevabı @{message.get('from', {}).get('username', chat_id)} tarafından gönderildi."
                            st.success(msg)
                            send_telegram_message(msg)
                            st.rerun()
                        else:
                            msg = f"❌ `{store_name}` için cevap gönderilemedi: {response_text}"
                            st.error(msg)
                            send_telegram_message(msg, chat_id=chat_id)
    except Exception as e:
        st.sidebar.error(f"Telegram güncellemeleri alınırken hata: {e}")

# --- RAPORLAMA FONKSİYONLARI ---

def get_and_filter_orders_for_report(store, target_date, api_query_status, final_filter_status):
    headers = get_headers(store['api_key'], store['api_secret'])
    turkey_tz = pytz.timezone("Europe/Istanbul")

    api_start_date = target_date - timedelta(days=14)
    start_timestamp = int(turkey_tz.localize(datetime.combine(api_start_date, datetime.min.time())).timestamp() * 1000)
    end_timestamp = int(turkey_tz.localize(datetime.combine(target_date, datetime.max.time())).timestamp() * 1000)
    
    all_packages = []
    page = 0
    size = 200
    
    while True:
        base_url = f"https://apigw.trendyol.com/integration/order/sellers/{store['seller_id']}/orders"
        params = f"startDate={start_timestamp}&endDate={end_timestamp}&status={api_query_status}&page={page}&size={size}&orderByField=PackageLastModifiedDate&orderByDirection=DESC"
        url = f"{base_url}?{params}"
        
        try:
            response = requests.get(url, headers=headers, timeout=20)
            if response.status_code == 404:
                st.sidebar.error(f"{store['name']} için {api_query_status} sorgusu başarısız (404).")
                return None
            response.raise_for_status()
            data = response.json()
            packages = data.get("content", [])
            all_packages.extend(packages)
            
            if not packages or len(packages) < size:
                break
            page += 1
            time.sleep(0.5)
        except requests.exceptions.RequestException as e:
            st.sidebar.error(f"{store['name']} için {api_query_status} raporu alınamadı: {e}")
            return None
    
    if not all_packages:
        return []

    start_of_target_day_ts = int(turkey_tz.localize(datetime.combine(target_date, datetime.min.time())).timestamp() * 1000)
    end_of_target_day_ts = int(turkey_tz.localize(datetime.combine(target_date, datetime.max.time())).timestamp() * 1000)

    filtered_packages = []
    for pkg in all_packages:
        if isinstance(pkg, dict) and pkg.get("status") == final_filter_status:
            modified_date_ts = pkg.get("packageLastModifiedDate")
            if modified_date_ts and start_of_target_day_ts <= modified_date_ts <= end_of_target_day_ts:
                filtered_packages.append(pkg)
            
    return filtered_packages

def generate_report_message(stores, target_date, api_query_status, final_filter_status, title="Rapor"):
    report_date_str = target_date.strftime("%Y-%m-%d")
    report_message = f"📊 *{title} ({report_date_str})*\n\n"
    any_data_found = False

    for store in stores:
        packages = get_and_filter_orders_for_report(store, target_date, api_query_status, final_filter_status)
        
        if packages is None:
            report_message += f"*{store['name']}*: Veri alınamadı. ❌\n"
            continue

        if not packages:
            report_message += f"*{store['name']}*: Bu kriterde sipariş bulunamadı.\n"
            continue
            
        cargo_counts = Counter(pkg.get('cargoProviderName', 'Diğer') for pkg in packages)
        any_data_found = True
        total_packages = len(packages)
        report_message += f"*{store['name']}* (Toplam: {total_packages} adet):\n"
        for cargo_name, count in cargo_counts.items():
            report_message += f" - {cargo_name}: *{count} adet*\n"
        report_message += "\n"

    if not any_data_found:
        return f"📊 *{title} ({report_date_str})*\n\nTüm mağazalarda bu kriterde sipariş bulunamadı."

    return report_message

def check_and_send_daily_shipped_report(stores):
    turkey_tz = pytz.timezone("Europe/Istanbul")
    now = datetime.now(turkey_tz)
    
    if now.hour < 18:
        return

    today_str = now.strftime("%Y-%m-%d")
    report_sent_key = f"report_sent_shipped_{today_str}"

    if st.session_state.get(report_sent_key, False):
        return
    
    report_message = generate_report_message(stores, now.date(), "Shipped", "Shipped", title="Günlük Kargoya Verilenler Raporu")
    send_telegram_message(report_message)
    st.session_state[report_sent_key] = True

# --- DİĞER FONKSİYONLAR ---
def get_pending_claims(store):
    url = f"https://apigw.trendyol.com/integration/order/sellers/{store['seller_id']}/claims?claimItemStatus=WaitingInAction&size=50&page=0"
    try:
        headers = get_headers(store['api_key'], store['api_secret'])
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json().get('content', [])
    except Exception: return []

def approve_claim_items(store, claim_id, claim_item_ids):
    url = f"https://apigw.trendyol.com/integration/order/sellers/{store['seller_id']}/claims/{claim_id}/items/approve"
    data = {"claimLineItemIdList": claim_item_ids, "params": {}}
    try:
        headers = get_headers(store['api_key'], store['api_secret'])
        response = requests.put(url, headers=headers, json=data)
        return response.status_code == 200, response.text
    except Exception as e: return False, str(e)

def load_past_data(file_path="soru_cevap_ornekleri.xlsx"):
    try:
        df = pd.read_excel(file_path)
        return df[['Ürün İsmi', 'Soru Detayı', 'Onaylanan Cevap']]
    except FileNotFoundError: return None
    except Exception: return None

def get_waiting_questions(store):
    url = f"https://apigw.trendyol.com/integration/qna/sellers/{store['seller_id']}/questions/filter?status=WAITING_FOR_ANSWER"
    try:
        headers = get_headers(store['api_key'], store['api_secret'])
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json().get("content", [])
    except Exception: return []

def send_answer(store, question_id, answer_text):
    url = f"https://apigw.trendyol.com/integration/qna/sellers/{store['seller_id']}/questions/{question_id}/answers"
    data = {"text": answer_text}
    try:
        headers = get_headers(store['api_key'], store['api_secret'])
        response = requests.post(url, headers=headers, json=data)
        return response.status_code == 200, response.text
    except Exception as e: return False, str(e)

def safe_generate_answer(product_name, question, past_df, min_examples=1):
    if not openai.api_key: return None, "OpenAI API anahtarı bulunamadı."
    if past_df is None or past_df.empty: return None, "Örnek veri dosyası bulunamadı."
    
    mask = past_df['Ürün İsmi'].astype(str).str.contains(str(product_name), case=False, na=False)
    examples = past_df[mask]
    if len(examples) < min_examples:
        return None, f"Örnek sayısı yetersiz ({len(examples)}/{min_examples})."
    
    prompt = ("Sen bir pazaryeri müşteri temsilcisisin...")

    try:
        client = openai.OpenAI(api_key=openai.api_key)
        response = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}], max_tokens=150, temperature=0.4)
        answer = response.choices[0].message.content.strip()
        ok, reason = passes_forbidden_filter(answer)
        return (answer, "") if ok else (None, "Güvenli cevap üretilemedi.")
    except Exception as e: return None, f"OpenAI hata: {e}"

# --- ANA UYGULAMA MANTIĞI ---

# --- SIDEBAR (KENAR ÇUBUĞU) ---
st.sidebar.header("Genel Ayarlar")
MIN_EXAMPLES = st.sidebar.number_input("Otomatik cevap için min. örnek sayısı", min_value=1, value=1)

st.sidebar.header("Manuel Raporlama")
selected_date = st.sidebar.date_input("Rapor için bir tarih seçin", datetime.now())
send_report_button = st.sidebar.button("Seçili Günün Teslimat Raporunu Gönder")

# --- VERİ YÜKLEME VE ARKA PLAN İŞLEMLERİ ---
templates = load_templates()
past_df = load_past_data()
stores_map = {store['name']: store for store in STORES}

if not templates:
    st.sidebar.warning("`cevap_sablonlari.xlsx` dosyası bulunamadı veya boş.")
if past_df is not None:
    st.sidebar.success("Soru-cevap örnekleri yüklendi.")
else:
    st.sidebar.warning("`soru_cevap_ornekleri.xlsx` dosyası bulunamadı.")

# --- DÜZELTME: Raporlama butonu işlemini ana gövde çizilmeden önce yap ---
if send_report_button:
    with st.sidebar.spinner("Teslimat raporu oluşturuluyor..."):
        report_text = generate_report_message(STORES, selected_date, "Shipped", "Delivered", title="Tarihli Teslimat Raporu")
        send_telegram_message(report_text)
        st.sidebar.success(f"{selected_date.strftime('%d-%m-%Y')} tarihli rapor gönderildi!")

# Telegram ve otomatik raporları kontrol et
process_telegram_updates(stores_map, templates)
check_and_send_daily_shipped_report(STORES)

# --- ANA SAYFA GÖVDESİ ---
store_tabs = st.tabs([s['name'] for s in STORES])

for i, store in enumerate(STORES):
    with store_tabs[i]:
        st.header(f"🏪 {store['name']} Mağazası Paneli")
        st.markdown(
            f"**İade Onaylama:** `{'Aktif' if store.get('auto_approve_claims') else 'Pasif'}` | "
            f"**Soru Cevaplama:** `{'Aktif' if store.get('auto_answer_questions') else 'Pasif'}` | "
            f"**Telegram Bildirim:** `{'Aktif' if store.get('send_notifications') else 'Pasif'}`"
        )
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Onay Bekleyen İade/Talepler")
            claims = get_pending_claims(store)
            if not claims: 
                st.info("Onay bekleyen iade/talep bulunamadı.")
            else:
                st.write(f"**{len(claims)}** adet onay bekleyen talep var.")
                for claim in claims:
                    with st.expander(f"Sipariş No: {claim.get('orderNumber')} - Talep ID: {claim.get('id')}", expanded=True):
                        st.write(f"**Talep Nedeni:** {claim.get('claimType', {}).get('name', 'Belirtilmemiş')}")
                        st.write(f"**Durum:** {claim.get('status')}")
                        if store.get('auto_approve_claims'):
                            with st.spinner("Otomatik olarak onaylanıyor..."):
                                item_ids = [item.get('id') for batch in claim.get('items', []) for item in batch.get('claimItems', [])]
                                if item_ids:
                                    success, message = approve_claim_items(store, claim.get('id'), item_ids)
                                    if success: 
                                        st.success("Talep başarıyla otomatik onaylandı.")
                                        st.rerun()
                                    else: 
                                        st.error(f"Otomatik onay başarısız: {message}")
                                else:
                                    st.warning("Onaylanacak ürün kalemi bulunamadı.")
        with col2:
            st.subheader("Cevap Bekleyen Müşteri Soruları")
            
            all_questions_raw = get_waiting_questions(store)
            questions = []
            seen_question_ids = set()
            if all_questions_raw:
                for q in all_questions_raw:
                    if isinstance(q, dict) and q.get("id"):
                        q_id = q["id"]
                        if q_id not in seen_question_ids:
                            questions.append(q)
                            seen_question_ids.add(q_id)
            
            if questions and store.get('send_notifications'):
                if 'notified_question_ids' not in st.session_state:
                    st.session_state.notified_question_ids = set()
                
                for q in questions:
                    q_id = q.get("id")
                    if q_id not in st.session_state.notified_question_ids:
                        message = (
                            f"🔔 *Yeni Soru!*\n\n"
                            f"🏪 Mağaza: *{store['name']}*\n"
                            f"📦 Ürün: {q.get('productName', '')}\n"
                            f"❓ Soru: {q.get('text', '')}\n"
                            f"(Soru ID: {q_id})\n\n"
                            f"👇 *Cevaplamak için bu mesaja yanıt verin veya `#keyword` kullanın. Tüm şablonları görmek için `/sablonlar` yazın.*"
                        )
                        send_telegram_message(message)
                        st.session_state.notified_question_ids.add(q_id)
            
            if not questions: 
                st.info("Cevap bekleyen soru bulunamadı.")
            else:
                st.write(f"**{len(questions)}** adet cevap bekleyen soru var.")
                if 'questions_handled' not in st.session_state: st.session_state.questions_handled = []
                for q in questions:
                    q_id = q.get("id")
                    if q_id in st.session_state.questions_handled: continue
                    with st.expander(f"Soru ID: {q_id} - Ürün: {q.get('productName', '')[:30]}...", expanded=True):
                        st.markdown(f"**Soru:** *{q.get('text', '')}*")
                        is_auto_answer_active = store.get('auto_answer_questions', False)
                        delay_minutes = store.get('delay_minutes', 5)
                        if f"time_{q_id}" not in st.session_state: st.session_state[f"time_{q_id}"] = datetime.now()
                        elapsed = datetime.now() - st.session_state[f"time_{q_id}"]
                        if is_auto_answer_active:
                            if delay_minutes == 0 or elapsed >= timedelta(minutes=delay_minutes):
                                with st.spinner(f"Soru ID {q_id}: Otomatik cevap kontrol ediliyor..."):
                                    answer, reason = safe_generate_answer(q.get("productName", ""), q.get("text", ""), past_df, min_examples=MIN_EXAMPLES)
                                    if answer is None: 
                                        st.warning(f"Otomatik cevap gönderilmedi: {reason}")
                                        continue
                                    st.info(f"Otomatik gönderilecek cevap:\n\n> {answer}")
                                    success, message = send_answer(store, q_id, answer)
                                    if success: 
                                        st.success("Cevap başarıyla otomatik gönderildi.")
                                        st.session_state.questions_handled.append(q_id)
                                        st.rerun()
                                    else: 
                                        st.error(f"Cevap gönderilemedi: {message}")
                            else:
                                remaining_seconds = (timedelta(minutes=delay_minutes) - elapsed).total_seconds()
                                st.warning(f"Bu soruya otomatik cevap yaklaşık **{int(remaining_seconds / 60)} dakika {int(remaining_seconds % 60)} saniye** içinde gönderilecek.")
                        else: # Manuel mod
                            suggestion, reason = safe_generate_answer(q.get("productName", ""), q.get("text", ""), past_df, min_examples=MIN_EXAMPLES)
                            default_text = suggestion if suggestion is not None else ""
                            if suggestion is None: st.info(f"Öneri üretilmedi: {reason}")
                            
                            cevap = st.text_area("Cevabınız:", value=default_text, key=f"textarea_{store['name']}_{q_id}")
                            if st.button(f"Cevabı Gönder (ID: {q_id})", key=f"btn_{store['name']}_{q_id}"):
                                ok, why = passes_forbidden_filter(cevap)
                                if not ok: st.error(why)
                                elif not cevap.strip(): st.error("Boş cevap gönderilemez.")
                                else:
                                    success, message = send_answer(store, q_id, cevap)
                                    if success: 
                                        st.success("Cevap başarıyla gönderildi.")
                                        st.session_state.questions_handled.append(q_id)
                                        st.rerun()
                                    else: 
                                        st.error(f"Cevap gönderilemedi: {message}")

