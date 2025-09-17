import streamlit as st
import pandas as pd
import requests
import base64
import openai
import re
from datetime import datetime, timedelta
from streamlit_autorefresh import st_autorefresh

# --- Streamlit Arayüzü ve Ayarları ---
st.set_page_config(layout="wide")
st.title("Trendyol Multi-Store Otomasyon Paneli")

# --- Ortak API Bilgilerini Oku ---
try:
    openai.api_key = st.secrets["OPENAI_API_KEY"]
    TELEGRAM_BOT_TOKEN = st.secrets.get("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID")
    STORES = st.secrets.get("stores", [])
except KeyError as e:
    st.error(f"'{e.args[0]}' adlı gizli bilgi (Secret) bulunamadı. Lütfen 'Secrets' bölümünü kontrol edin.")
    st.stop()

if not STORES:
    st.error("Yapılandırılmış herhangi bir mağaza bulunamadı. Lütfen secrets dosyanızı `[[stores]]` formatına göre düzenleyin.")
    st.stop()

# Sayfa otomatik yenileme
st_autorefresh(interval=60 * 1000, key="data_fetch_refresher") # Çoklu mağaza için yenileme süresini 1 dakikaya çıkardık

# --- Ortak Fonksiyonlar ---

def get_headers(api_key, api_secret):
    """Mağazaya özel header oluşturur."""
    credentials = f"{api_key}:{api_secret}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    return {
        "Authorization": f"Basic {encoded_credentials}",
        "Content-Type": "application/json",
        "User-Agent": "MultiStorePanel/1.0"
    }

# ... (Diğer tüm fonksiyonlar - passes_forbidden_filter, load_past_data vb. - buraya eklenecek ve güncellenecek) ...
# ... (Fonksiyonların tam listesi aşağıda verilmiştir, bu sadece bir yer tutucudur) ...

# <================================================================================>
# <--- Tüm Fonksiyonların Güncellenmiş Halleri (Mağaza Bilgilerini Parametre Alan) --->
# <================================================================================>

FORBIDDEN_PATTERNS = [
    r"http[s]?://", r"\bwww\.", r"\.com\b", r"\.net\b", r"\.org\b",
    r"\blink\b", r"\bsite\b", r"\bweb\w*\b", r"\binstagram\b",
    r"\bwhats?app\b", r"\bdm\b", r"\btelegram\b"
]

def passes_forbidden_filter(text: str) -> (bool, str):
    for pat in FORBIDDEN_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            return False, f"YASAK: Cevap yönlendirme içeriyor ({pat})."
    return True, ""

def send_telegram_message(message, chat_id=None):
    target_chat_id = chat_id if chat_id else TELEGRAM_CHAT_ID
    if not all([TELEGRAM_BOT_TOKEN, target_chat_id]):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': target_chat_id, 'text': message, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception:
        pass # Hata durumunda arayüzü kirletme

def process_telegram_updates(stores_map):
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
            if 'message' in update and 'reply_to_message' in update['message']:
                message = update['message']
                original_message = message['reply_to_message']
                if str(message['chat']['id']) != str(TELEGRAM_CHAT_ID): continue
                original_text = original_message.get("text", "")
                reply_text = message.get("text", "")
                
                match_id = re.search(r"\(Soru ID: (\d+)\)", original_text)
                match_store = re.search(r"🏪 Mağaza: (.+?)\n", original_text)

                if match_id and match_store:
                    question_id = int(match_id.group(1))
                    store_name = match_store.group(1).strip()
                    
                    if store_name in stores_map:
                        store = stores_map[store_name]
                        is_safe, reason = passes_forbidden_filter(reply_text)
                        if not is_safe:
                            send_telegram_message(f"‼️ `{store_name}` için cevap gönderilmedi: {reason}")
                            continue
                        
                        success, response_text = send_answer(store, question_id, reply_text)
                        if success:
                            msg = f"✅ `{store_name}` mağazası için cevabınız (Soru ID: {question_id}) gönderildi."
                            st.success(msg)
                            send_telegram_message(msg)
                            st.rerun()
                        else:
                            msg = f"❌ `{store_name}` için cevap gönderilemedi: {response_text}"
                            st.error(msg)
                            send_telegram_message(msg)

    except Exception as e:
        st.sidebar.error(f"Telegram güncellemeleri alınırken hata: {e}")

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
    # Bu fonksiyon mağazadan bağımsız olduğu için aynı kalabilir
    if not openai.api_key: return None, "OpenAI API anahtarı bulunamadı."
    if past_df is None or past_df.empty: return None, "Örnek veri dosyası bulunamadı."
    
    mask = past_df['Ürün İsmi'].astype(str).str.contains(str(product_name), case=False, na=False)
    examples = past_df[mask]
    if len(examples) < min_examples:
        return None, f"Örnek sayısı yetersiz ({len(examples)}/{min_examples})."
    
    prompt = "..." # (Prompt içeriği aynı kaldığı için kısalttım)
    try:
        client = openai.OpenAI(api_key=openai.api_key)
        response = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}], max_tokens=150, temperature=0.4)
        answer = response.choices[0].message.content.strip()
        ok, reason = passes_forbidden_filter(answer)
        return (answer, "") if ok else (None, "Güvenli cevap üretilemedi.")
    except Exception as e: return None, f"OpenAI hata: {e}"

# --- ANA UYGULAMA MANTIĞI ---

st.sidebar.header("Genel Ayarlar")
MIN_EXAMPLES = st.sidebar.number_input("Otomatik cevap için min. örnek sayısı", min_value=1, value=1)

past_df = load_past_data()
if past_df is not None:
    st.sidebar.success("Soru-cevap örnekleri yüklendi.")
else:
    st.sidebar.warning("`soru_cevap_ornekleri.xlsx` dosyası bulunamadı.")

# Store name'e göre arama yapabilmek için map oluştur
stores_map = {store['name']: store for store in STORES}
process_telegram_updates(stores_map)

# Mağazaları sekmeler halinde göster
store_tabs = st.tabs([s['name'] for s in STORES])

for i, store in enumerate(STORES):
    with store_tabs[i]:
        st.header(f"🏪 {store['name']} Mağazası Paneli")
        
        # Mağazaya özel ayarları göster
        st.markdown(
            f"**İade Onaylama:** `{'Aktif' if store.get('auto_approve_claims') else 'Pasif'}` | "
            f"**Soru Cevaplama:** `{'Aktif' if store.get('auto_answer_questions') else 'Pasif'}` | "
            f"**Telegram Bildirim:** `{'Aktif' if store.get('send_notifications') else 'Pasif'}`"
        )
        
        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Onay Bekleyen İade/Talepler")
            claims = get_pending_claims(store)
            if not claims: st.info("Onay bekleyen iade/talep bulunamadı.")
            else:
                for claim in claims:
                    # ... (İade/Talep gösterme ve işleme mantığı - store parametresi eklendi) ...
                    if store.get('auto_approve_claims'):
                        # Otomatik onaylama...
                        pass # Kodun kısalığı için bu kısmı çıkardım, isterseniz ekleyebiliriz

        with col2:
            st.subheader("Cevap Bekleyen Müşteri Soruları")
            questions = get_waiting_questions(store)

            # Her yeni soru için ayrı Telegram bildirimi
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
                            f"👇 *Cevaplamak için bu mesaja yanıt verin.*"
                        )
                        send_telegram_message(message)
                        st.session_state.notified_question_ids.add(q_id)

            if not questions: st.info("Cevap bekleyen soru bulunamadı.")
            else:
                # ... (Soruları gösterme ve işleme mantığı - store parametresi eklendi) ...
                for q in questions:
                    # Otomatik cevaplama...
                    pass # Kodun kısalığı için bu kısmı çıkardım, isterseniz ekleyebiliriz
