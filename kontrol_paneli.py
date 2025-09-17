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
st.title("Trendyol Otomasyon Kontrol Paneli (7/24 Aktif)")

# --- API Bilgilerini ve Ayarları Güvenli Olarak Oku ---
try:
    SELLER_ID = st.secrets["SELLER_ID"]
    API_KEY = st.secrets["API_KEY"]
    API_SECRET = st.secrets["API_SECRET"]
    openai.api_key = st.secrets["OPENAI_API_KEY"]
    
    AUTO_APPROVE_CLAIMS = st.secrets.get("AUTO_APPROVE_CLAIMS", False)
    AUTO_ANSWER_QUESTIONS = st.secrets.get("AUTO_ANSWER_QUESTIONS", False)
    DELAY_MINUTES = st.secrets.get("DELAY_MINUTES", 5)

    SEND_NOTIFICATIONS = st.secrets.get("SEND_NOTIFICATIONS", False)
    TELEGRAM_BOT_TOKEN = st.secrets.get("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID")

except KeyError as e:
    st.error(f"'{e.args[0]}' adlı gizli bilgi (Secret) bulunamadı. Lütfen 'Manage app' -> 'Secrets' bölümünü kontrol edin.")
    st.stop()

# --- Trendyol API için kimlik bilgileri hazırlanıyor ---
credentials = f"{API_KEY}:{API_SECRET}"
encoded_credentials = base64.b64encode(credentials.encode()).decode()
HEADERS = {
    "Authorization": f"Basic {encoded_credentials}",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
}

# Sayfa otomatik yenileme
st_autorefresh(interval=30 * 1000, key="data_fetch_refresher")

# --- Ortak Fonksiyonlar ---

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
    if not all([SEND_NOTIFICATIONS, TELEGRAM_BOT_TOKEN, target_chat_id]): return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': target_chat_id, 'text': message, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception:
        pass

# <--- YENİ EKLENDİ: Cevap şablonlarını Excel'den yükleyen fonksiyon --->
@st.cache_data(ttl=600) # Şablonları 10 dakikada bir yeniden yükle
def load_templates(file_path="cevap_sablonlari.xlsx"):
    try:
        df = pd.read_excel(file_path)
        return pd.Series(df.sablon_metni.values, index=df.keyword).to_dict()
    except FileNotFoundError:
        return {} # Dosya yoksa uyarı verme, sadece boş döndür
    except Exception as e:
        st.sidebar.error(f"Şablon dosyası okunurken hata: {e}")
        return {}

# <--- GÜNCELLENDİ: Telegram güncellemelerini işleyen fonksiyon --- >
def process_telegram_updates(templates):
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
            if str(message['chat']['id']) != str(TELEGRAM_CHAT_ID): continue
            
            reply_text = message.get("text", "").strip()

            # /sablonlar komutunu işle
            if reply_text == "/sablonlar":
                if templates:
                    template_list_message = "📋 *Kullanılabilir Cevap Şablonları:*\n\n"
                    for keyword in templates.keys():
                        template_list_message += f"`#{keyword}`\n"
                    template_list_message += "\n_(Bir soruya cevap verirken bu kodları kullanabilirsiniz.)_"
                else:
                    template_list_message = "❌ Hiç cevap şablonu bulunamadı. Lütfen `cevap_sablonlari.xlsx` dosyasını kontrol edin."
                send_telegram_message(template_list_message)
                continue # Komutu işledik, devam etme

            # Yanıtlama (reply) formatındaki mesajları işle
            if 'reply_to_message' in message:
                original_message = message['reply_to_message']
                original_text = original_message.get("text", "")
                match = re.search(r"\(Soru ID: (\d+)\)", original_text)
                if match:
                    question_id = int(match.group(1))
                    final_answer = ""

                    if reply_text.startswith("#"):
                        keyword = reply_text[1:].lower()
                        if keyword in templates:
                            final_answer = templates[keyword]
                        else:
                            send_telegram_message(f"‼️ `#{keyword}` adında bir şablon bulunamadı.")
                            continue
                    else:
                        final_answer = reply_text

                    is_safe, reason = passes_forbidden_filter(final_answer)
                    if not is_safe:
                        send_telegram_message(f"‼️ Cevabınız gönderilmedi: {reason}")
                        continue

                    success, response_text = send_answer(question_id, final_answer)
                    if success:
                        send_telegram_message(f"✅ Cevabınız (Soru ID: {question_id}) Trendyol'a başarıyla gönderildi.")
                        st.rerun()
                    else:
                        send_telegram_message(f"❌ Cevabınız (Soru ID: {question_id}) gönderilemedi: {response_text}")
    except Exception as e:
        st.sidebar.error(f"Telegram güncellemeleri alınırken hata: {e}")

# ... (Diğer fonksiyonlar aynı) ...
def get_pending_claims():
    url = f"https://apigw.trendyol.com/integration/order/sellers/{SELLER_ID}/claims?claimItemStatus=WaitingInAction&size=50&page=0"
    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        return response.json().get('content', [])
    except Exception as e:
        st.error(f"İade/Talep Talepleri çekilirken bir hata oluştu: {e}")
        return []

def approve_claim_items(claim_id, claim_item_ids):
    url = f"https://apigw.trendyol.com/integration/order/sellers/{SELLER_ID}/claims/{claim_id}/items/approve"
    data = {"claimLineItemIdList": claim_item_ids, "params": {}}
    try:
        response = requests.put(url, headers=HEADERS, json=data)
        return response.status_code == 200, response.text
    except Exception as e:
        return False, str(e)

def load_past_data(file_path):
    try:
        df = pd.read_excel(file_path)
        return df[['Ürün İsmi', 'Soru Detayı', 'Onaylanan Cevap']]
    except FileNotFoundError:
        st.warning(f"'{file_path}' dosyası bulunamadı. Lütfen GitHub deponuza bu isimde bir Excel dosyası yükleyin.")
        return None
    except Exception as e:
        st.error(f"Excel dosyası okunurken bir hata oluştu: {e}")
        return None

def get_waiting_questions():
    url = f"https://apigw.trendyol.com/integration/qna/sellers/{SELLER_ID}/questions/filter?status=WAITING_FOR_ANSWER"
    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        return response.json().get("content", [])
    except Exception as e:
        st.error(f"Sorular çekilirken bir hata oluştu: {e}")
        return []

def safe_generate_answer(product_name, question, past_df, min_examples=1):
    # ...
    return None, "Güvenli cevap üretilemedi."

def send_answer(question_id, answer_text):
    url = f"https://apigw.trendyol.com/integration/qna/sellers/{SELLER_ID}/questions/{question_id}/answers"
    data = {"text": answer_text}
    try:
        response = requests.post(url, headers=HEADERS, json=data)
        return response.status_code == 200, response.text
    except Exception as e:
        return False, str(e)


# --- ANA KONTROL PANELİ ARAYÜZÜ ---

# <--- YENİ EKLENDİ: Şablonları yükle --->
templates = load_templates()

EXCEL_FILE_NAME = "soru_cevap_ornekleri.xlsx"
past_df = load_past_data(EXCEL_FILE_NAME)

st.sidebar.header("Otomasyon Durumu")
# ... (sidebar aynı)

# <--- GÜNCELLENDİ: Telegram işlemlerini başlatırken şablonları da gönder --->
process_telegram_updates(templates)


col1, col2 = st.columns(2)

with col1:
    st.subheader("Onay Bekleyen İade/Talepler")
    # ... (İade/Talep mantığı aynı)

with col2:
    st.subheader("Cevap Bekleyen Müşteri Soruları")
    try:
        questions = get_waiting_questions()
        if questions:
            if 'notified_question_ids' not in st.session_state:
                st.session_state.notified_question_ids = set()

            for q in questions:
                q_id = q.get("id")
                if q_id not in st.session_state.notified_question_ids:
                    product_name = q.get('productName', 'Bilinmeyen Ürün')
                    question_text = q.get('text', '')
                    # <--- GÜNCELLENDİ: Bildirim mesajına /sablonlar komutu eklendi --->
                    message = (
                        f"🔔 *Yeni Müşteri Sorusu!* (Soru ID: {q_id})\n\n"
                        f"📦 *Ürün:* {product_name}\n\n"
                        f"❓ *Soru:* {question_text}\n\n"
                        f"👇 *Cevaplamak için bu mesaja yanıt verin veya `#keyword` kullanın. Tüm şablonları görmek için `/sablonlar` yazın.*"
                    )
                    send_telegram_message(message)
                    st.session_state.notified_question_ids.add(q_id)
        
        # ... (Soruları listeleme mantığı aynı)

    except Exception as e:
        st.error(f"Müşteri Soruları bölümünde bir hata oluştu: {e}")
