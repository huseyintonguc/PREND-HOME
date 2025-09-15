import streamlit as st
import pandas as pd
import requests
import base64
import openai
import re
from streamlit_autorefresh import st_autorefresh

# --- Streamlit Arayüzü ve Ayarları ---
st.set_page_config(layout="wide")
st.title("Trendyol Otomasyon Paneli (v3.0 - Hepsi Bir Arada)")

# --- API Bilgilerini ve Ayarları Güvenli Olarak Oku ---
try:
    SELLER_ID = st.secrets["SELLER_ID"]
    API_KEY = st.secrets["API_KEY"]
    API_SECRET = st.secrets["API_SECRET"]
    
    # Otomasyon Ayarları (Açık/Kapalı)
    AUTO_APPROVE_CLAIMS = st.secrets.get("AUTO_APPROVE_CLAIMS", False)
    AUTO_ANSWER_QUESTIONS = st.secrets.get("AUTO_ANSWER_QUESTIONS", False)
    SEND_NOTIFICATIONS = st.secrets.get("SEND_NOTIFICATIONS", True)

    # Gerekli API Anahtarları
    if AUTO_ANSWER_QUESTIONS:
        openai.api_key = st.secrets["OPENAI_API_KEY"]
    if SEND_NOTIFICATIONS:
        TELEGRAM_BOT_TOKEN = st.secrets["TELEGRAM_BOT_TOKEN"]
        TELEGRAM_CHAT_ID = st.secrets["TELEGRAM_CHAT_ID"]

except KeyError as e:
    st.error(f"'{e.args[0]}' adlı gizli bilgi (Secret) bulunamadı. Lütfen 'Manage app' -> 'Secrets' bölümünü kontrol edin.")
    st.stop()

# --- Trendyol API için kimlik bilgileri hazırlanıyor ---
credentials = f"{API_KEY}:{API_SECRET}"
encoded_credentials = base64.b64encode(credentials.encode()).decode()
HEADERS = {
    "Authorization": f"Basic {encoded_credentials}",
    "Content-Type": "application/json",
    "User-Agent": f"Seller__{SELLER_ID}"
}

# Sayfa otomatik yenileme (30 saniyede bir)
st_autorefresh(interval=30 * 1000, key="data_fetch_refresher")

# --- YASAKLI KELİME FİLTRESİ (Yapay Zeka için) ---
FORBIDDEN_PATTERNS = [
    r"http[s]?://", r"\bwww\.", r"\.com\b", r"\.net\b", r"\.org\b",
    r"\blink\b", r"\bsite\b", r"\bweb\w*\b", r"\binstagram\b",
    r"\bwhats?app\b", r"\bdm\b", r"\btelegram\b"
]

def passes_forbidden_filter(text: str) -> bool:
    for pat in FORBIDDEN_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            return False
    return True

# --- FONKSİYONLAR ---

# --- TELEGRAM FONKSİYONLARI ---
def send_telegram_message(chat_id, text):
    """Belirtilen chat_id'ye Telegram mesajı gönderir."""
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'Markdown'}
    try:
        requests.post(base_url, json=payload)
    except Exception as e:
        print(f"Telegram mesajı gönderilemedi: {e}")

def send_question_notification(questions_list):
    """Yeni sorular için ana bildirim mesajını gönderir."""
    question_count = len(questions_list)
    message = f"📢 **Trendyol Bildirimi** 📢\n\nMağazanızda cevap bekleyen **{question_count}** yeni soru var:\n\n"
    for q in questions_list[:5]: # İlk 5 soruyu özet olarak göster
         q_id = q.get('id', 'ID Yok')
         q_text = q.get('text', '')[:50]
         message += f"Soru ID: `{q_id}`\nSoru: *{q_text}...*\n\n"
    message += "Cevaplamak için Telegram'dan `/cevap <SoruID> <Metin>` komutunu kullanabilirsiniz."
    send_telegram_message(TELEGRAM_CHAT_ID, message)

def get_telegram_updates(offset):
    """Telegram'dan yeni mesajları (güncellemeleri) çeker."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {'offset': offset, 'timeout': 10}
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json().get('result', [])
    except Exception:
        return []

# --- TRENDYOL FONKSİYONLARI ---
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

def get_waiting_questions():
    url = f"https://apigw.trendyol.com/integration/qna/sellers/{SELLER_ID}/questions/filter?status=WAITING_FOR_ANSWER"
    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        return response.json().get("content", [])
    except Exception as e: 
        st.error(f"Sorular çekilirken bir hata oluştu: {e}")
        return []

def send_answer_to_trendyol(question_id, answer_text):
    """Verilen cevabı Trendyol API'sine gönderir."""
    url = f"https://apigw.trendyol.com/integration/qna/sellers/{SELLER_ID}/questions/{question_id}/answers"
    data = {"text": answer_text}
    try:
        response = requests.post(url, headers=HEADERS, json=data)
        response.raise_for_status()
        return True, "Cevap başarıyla Trendyol'a gönderildi."
    except requests.exceptions.RequestException as e:
        error_message = f"Hata: {e.response.status_code} - {e.response.text}"
        return False, error_message

# --- OTOMATİK CEVAPLAMA FONKSİYONLARI ---
def load_past_data(file_path):
    try:
        df = pd.read_excel(file_path)
        return df[['Ürün İsmi', 'Soru Detayı', 'Onaylanan Cevap']]
    except FileNotFoundError:
        st.sidebar.warning(f"'{file_path}' dosyası bulunamadı.")
        return None
    except Exception as e:
        st.sidebar.error(f"Excel dosyası okunurken hata: {e}")
        return None

def generate_answer_with_ai(product_name, question, past_df):
    if not openai.api_key: return None, "OpenAI API anahtarı bulunamadı."
    if past_df is None: return None, "Örnek veri dosyası yüklenemedi."

    examples = past_df[past_df['Ürün İsmi'].astype(str).str.contains(str(product_name), case=False, na=False)]
    if examples.empty:
        return None, "Bu ürün için yeterli örnek bulunamadı."

    for _ in range(3): # Güvenli cevap için 3 deneme
        prompt = (
            "Sen bir pazaryeri müşteri temsilcisisin. Aşağıdaki soruya, yalnızca verilen örnek cevapların bilgisiyle "
            "KISA, NAZİK ve NET bir cevap ver. ASLA dış web sitesi, link veya sosyal medya yönlendirmesi yapma.\n\n"
            f"Ürün Adı: {product_name}\nMüşteri Sorusu: {question}\n\n"
            "--- Örnek Geçmiş Cevaplar ---\n"
        )
        for _, row in examples.head(5).iterrows():
            prompt += f"Soru: {row['Soru Detayı']}\nCevap: {row['Onaylanan Cevap']}\n---\n"
        prompt += "Oluşturulacak Cevap (yönlendirme YASAK):"

        try:
            client = openai.OpenAI(api_key=openai.api_key)
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150, temperature=0.4
            )
            answer = response.choices[0].message.content.strip()
            if passes_forbidden_filter(answer): 
                return answer, "AI tarafından üretildi."
        except Exception as e:
            return None, f"OpenAI hatası: {e}"
    return None, "Güvenli cevap üretilemedi."

# --- TELEGRAM KOMUT İŞLEME VE ANA UYGULAMA AKIŞI ---

def process_telegram_commands():
    if not SEND_NOTIFICATIONS: return

    if 'last_update_id' not in st.session_state:
        st.session_state.last_update_id = 0

    offset = st.session_state.last_update_id + 1
    updates = get_telegram_updates(offset)

    for update in updates:
        st.session_state.last_update_id = update['update_id']
        if "message" in update and "text" in update["message"]:
            chat_id = update['message']['chat']['id']
            message_text = update['message']['text'].strip()

            if message_text.lower().startswith('/cevap'):
                parts = message_text.split(maxsplit=2)
                if len(parts) < 3:
                    send_telegram_message(chat_id, "❌ Hatalı format!\nKullanım: `/cevap <SoruID> <Cevabınız>`")
                    continue
                
                _, question_id, answer = parts
                if not question_id.isdigit():
                    send_telegram_message(chat_id, f"❌ Geçersiz Soru ID'si: '{question_id}'.")
                    continue

                success, message = send_answer_to_trendyol(question_id, answer)
                feedback = f"✅ Başarılı!\nSoru ID'si `{question_id}` olan soruya cevabınız gönderildi." if success else f"❌ Hata!\nSoru `{question_id}` cevaplanamadı.\nSebep: {message}"
                send_telegram_message(chat_id, feedback)

# --- ANA KOD BAŞLANGICI ---

# Her yenilemede Telegram'dan gelen komutları kontrol et
process_telegram_commands()

# Arayüzü çiz
st.sidebar.header("Otomasyon Ayarları")
st.sidebar.markdown(f"**İade Onaylama:** `{'Aktif' if AUTO_APPROVE_CLAIMS else 'Pasif'}`")
st.sidebar.markdown(f"**Otomatik Cevaplama:** `{'Aktif' if AUTO_ANSWER_QUESTIONS else 'Pasif'}`")
st.sidebar.markdown(f"**Soru Bildirimi (Telegram):** `{'Aktif' if SEND_NOTIFICATIONS else 'Pasif'}`")

col1, col2 = st.columns(2)

# --- Sütun 1: İade/Talepler ---
with col1:
    st.subheader("Onay Bekleyen İade/Talepler")
    claims = get_pending_claims()
    if not claims:
        st.info("Onay bekleyen iade/talep bulunamadı.")
    else:
        st.write(f"**{len(claims)}** adet onay bekleyen talep var.")
        for claim in claims:
            with st.expander(f"Sipariş No: {claim.get('orderNumber')} - Talep ID: {claim.get('id')}", expanded=True):
                st.write(f"**Talep Nedeni:** {claim.get('claimType', {}).get('name', 'Belirtilmemiş')}")
                if AUTO_APPROVE_CLAIMS:
                    with st.spinner("Otomatik olarak onaylanıyor..."):
                        item_ids = [item.get('id') for batch in claim.get('items', []) for item in batch.get('claimItems', [])]
                        if item_ids:
                            success, message = approve_claim_items(claim.get('id'), item_ids)
                            if success:
                                st.success("Talep başarıyla otomatik onaylandı.")
                                st.rerun()
                            else:
                                st.error(f"Otomatik onay başarısız: {message}")

# --- Sütun 2: Müşteri Soruları ---
with col2:
    st.subheader("Cevap Bekleyen Müşteri Soruları")
    questions = get_waiting_questions()

    if not questions:
        st.info("Cevap bekleyen soru bulunamadı.")
        st.session_state.notification_sent = False
    else:
        st.write(f"**{len(questions)}** adet cevap bekleyen soru var.")

        # BİLDİRİM GÖNDERME KONTROLÜ
        if SEND_NOTIFICATIONS and not st.session_state.get('notification_sent', False):
            send_question_notification(questions)
            st.session_state.notification_sent = True
        
        # OTOMATİK CEVAPLAMA İŞLEMLERİ
        if AUTO_ANSWER_QUESTIONS:
            past_df = load_past_data("soru_cevap_ornekleri.xlsx")
            if 'questions_handled' not in st.session_state:
                st.session_state.questions_handled = []

            for q in questions:
                q_id = q.get("id")
                if q_id in st.session_state.questions_handled:
                    continue
                
                with st.spinner(f"Soru ID {q_id}: Otomatik cevap kontrol ediliyor..."):
                    answer, reason = generate_answer_with_ai(q.get("productName"), q.get("text"), past_df)
                    if answer:
                        st.info(f"Soru ID {q_id} için AI cevabı: '{answer}' gönderiliyor...")
                        success, message = send_answer_to_trendyol(q_id, answer)
                        if success:
                            st.success(f"Soru ID {q_id} başarıyla otomatik cevaplandı.")
                            st.session_state.questions_handled.append(q_id)
                        else:
                            st.error(f"Soru ID {q_id} gönderilemedi: {message}")
                    else:
                        st.warning(f"Soru ID {q_id} otomatik cevaplanamadı: {reason}")
            
            if st.session_state.questions_handled:
                st.rerun() # İşlenen soru varsa sayfayı yenile

        # Sadece Soruları Görüntüleme (Otomatik cevaplama kapalıysa)
        else:
            for q in questions:
                with st.expander(f"Ürün: {q.get('productName', '')[:40]}...", expanded=True):
                    st.markdown(f"**Soru ID:** `{q.get('id')}`")
                    st.markdown(f"**Soru:** *{q.get('text', '')}*")
