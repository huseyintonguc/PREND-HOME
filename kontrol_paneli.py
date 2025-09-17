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
    
    # Otomasyon Ayarları
    AUTO_APPROVE_CLAIMS = st.secrets.get("AUTO_APPROVE_CLAIMS", False)
    AUTO_ANSWER_QUESTIONS = st.secrets.get("AUTO_ANSWER_QUESTIONS", False)
    DELAY_MINUTES = st.secrets.get("DELAY_MINUTES", 5)

    # Telegram Ayarları
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

# =========================
# CEVAP FİLTRE AYARLARI
# =========================

st.sidebar.header("Cevap Filtre Ayarları")
MIN_EXAMPLES = st.sidebar.number_input(
    "Otomatik cevap için gerekli minimum örnek sayısı",
    min_value=1, max_value=10, value=1, step=1,
    help="Excel'de ilgili ürüne ait en az bu kadar örnek bulunmazsa otomatik cevap gönderilmez."
)

# Yasaklı yönlendirme kalıpları (url, sosyal ağ, web vb.)
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

# --- FONKSİYONLAR: TELEGRAM BOT ---

def send_telegram_message(message, chat_id=None):
    """Telegram'a bildirim gönderir."""
    target_chat_id = chat_id if chat_id else TELEGRAM_CHAT_ID
    if not all([SEND_NOTIFICATIONS, TELEGRAM_BOT_TOKEN, target_chat_id]):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': target_chat_id, 'text': message, 'parse_mode': 'Markdown'}
    try:
        response = requests.post(url, json=payload, timeout=5)
        if response.status_code != 200:
            st.sidebar.warning(f"Telegram mesajı gönderilemedi: {response.text}")
    except Exception as e:
        st.sidebar.error(f"Telegram'a bağlanırken hata: {e}")

# <--- YENİ EKLENDİ: Telegram'dan gelen cevapları işleyen fonksiyon --->
def process_telegram_updates():
    """Telegram'dan gelen yeni mesajları kontrol eder ve cevapları işler."""
    if 'last_update_id' not in st.session_state:
        st.session_state.last_update_id = 0

    offset = st.session_state.last_update_id + 1
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={offset}&timeout=10"
    
    try:
        response = requests.get(url, timeout=15)
        if response.status_code != 200:
            return
        
        updates = response.json().get("result", [])
        if not updates:
            return

        for update in updates:
            st.session_state.last_update_id = update.get("update_id")
            
            # Sadece bizim chatimizden ve "yanıtla" formatındaki mesajları işle
            if 'message' in update and 'reply_to_message' in update['message']:
                message = update['message']
                original_message = message['reply_to_message']
                
                # Güvenlik: Sadece yetkili chat ID'sinden gelen cevapları kabul et
                if str(message['chat']['id']) != str(TELEGRAM_CHAT_ID):
                    continue

                original_text = original_message.get("text", "")
                reply_text = message.get("text", "")

                # Orijinal mesajdan Soru ID'sini yakala
                match = re.search(r"\(Soru ID: (\d+)\)", original_text)
                if match:
                    question_id = int(match.group(1))
                    
                    with st.spinner(f"Telegram'dan gelen cevap Trendyol'a gönderiliyor (ID: {question_id})..."):
                        # Cevabı göndermeden önce filtrele
                        is_safe, reason = passes_forbidden_filter(reply_text)
                        if not is_safe:
                            error_msg = f"‼️ Cevabınız gönderilmedi: {reason}"
                            st.error(error_msg)
                            send_telegram_message(error_msg, chat_id=message['chat']['id'])
                            continue

                        # Trendyol'a cevabı gönder
                        success, response_text = send_answer(question_id, reply_text)
                        
                        if success:
                            confirmation_message = f"✅ Cevabınız (Soru ID: {question_id}) Trendyol'a başarıyla gönderildi."
                            st.success(confirmation_message)
                            send_telegram_message(confirmation_message, chat_id=message['chat']['id'])
                            # İşlenen soruyu hafızadan temizleyebiliriz
                            if 'notified_question_ids' in st.session_state:
                                st.session_state.notified_question_ids.discard(question_id)
                            st.rerun()
                        else:
                            error_message = f"❌ Cevabınız (Soru ID: {question_id}) gönderilemedi: {response_text}"
                            st.error(error_message)
                            send_telegram_message(error_message, chat_id=message['chat']['id'])
    except Exception as e:
        st.sidebar.error(f"Telegram güncellemeleri alınırken hata: {e}")


# --- FONKSİYONLAR: İADE/TALEP YÖNETİMİ ---
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

# --- FONKSİYONLAR: SORU-CEVAP YÖNETİMİ ---
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

def safe_generate_answer(product_name, question, past_df, min_examples=1, max_retries=3):
    if not openai.api_key: return None, "OpenAI API anahtarı bulunamadı."
    examples = pd.DataFrame()
    if past_df is not None:
        mask = past_df['Ürün İsmi'].astype(str).str.contains(str(product_name), case=False, na=False)
        examples = past_df[mask]
    if examples.empty or len(examples) < min_examples:
        return None, f"Örnek sayısı yetersiz ({len(examples)}/{min_examples})."
    for attempt in range(max_retries):
        prompt = (
            "Sen bir pazaryeri müşteri temsilcisisin. Aşağıdaki soruya, yalnızca verilen örnek cevapların bilgisi ve "
            "genel işleyiş kurallarını kullanarak KISA, NAZİK ve NET bir cevap ver. "
            "ASLA dış web sitesi, link, sosyal medya veya harici kanal yönlendirmesi yapma. "
            "Bilmiyorsan veya örneklerde cevap yoksa cevap üretme.\n\n"
            f"Ürün Adı: {product_name}\nMüşteri Sorusu: {question}\n\n"
            "--- Örnek Geçmiş Cevaplar ---\n"
        )
        for _, row in examples.head(5).iterrows():
            prompt += f"Soru: {row['Soru Detayı']}\nCevap: {row['Onaylanan Cevap']}\n---\n"
        prompt += "Oluşturulacak Cevap (harici yönlendirme YASAK):"
        try:
            client = openai.OpenAI(api_key=openai.api_key)
            response = client.chat.completions.create( model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}], max_tokens=150, temperature=0.4)
            answer = response.choices[0].message.content.strip()
            ok, reason = passes_forbidden_filter(answer)
            if ok: return answer, "" 
            else: st.warning(f"Yasaklı ifade tespit edildi, tekrar deneniyor... (Deneme {attempt+1}/{max_retries})"); continue
        except Exception as e: return None, f"OpenAI hata: {e}"
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

EXCEL_FILE_NAME = "soru_cevap_ornekleri.xlsx"
past_df = load_past_data(EXCEL_FILE_NAME)

st.sidebar.header("Otomasyon Durumu")
st.sidebar.markdown(f"**İade Onaylama:** `{'Aktif' if AUTO_APPROVE_CLAIMS else 'Pasif'}`")
st.sidebar.markdown(f"**Soru Cevaplama:** `{'Aktif' if AUTO_ANSWER_QUESTIONS else 'Pasif'}`")
st.sidebar.markdown(f"**Telegram Bildirim:** `{'Aktif' if SEND_NOTIFICATIONS else 'Pasif'}`")
if AUTO_ANSWER_QUESTIONS: st.sidebar.markdown(f"**Cevap Gecikmesi:** `{DELAY_MINUTES} dakika`")
if past_df is not None: st.sidebar.success("Soru-cevap örnekleri yüklendi.")
else: st.sidebar.warning("Soru-cevap örnek dosyası bulunamadı.")

# <--- YENİ EKLENDİ: Her döngüde Telegram'dan gelen cevapları kontrol et --->
process_telegram_updates()

col1, col2 = st.columns(2)

with col1:
    st.subheader("Onay Bekleyen İade/Talepler")
    # ... (Bu kısım aynı kaldı) ...
    try:
        claims = get_pending_claims()
        if not claims: st.info("Onay bekleyen iade/talep bulunamadı.")
        else:
            st.write(f"**{len(claims)}** adet onay bekleyen talep var.")
            for claim in claims:
                with st.expander(f"Sipariş No: {claim.get('orderNumber')} - Talep ID: {claim.get('id')}", expanded=True):
                    st.write(f"**Talep Nedeni:** {claim.get('claimType', {}).get('name', 'Belirtilmemiş')}")
                    st.write(f"**Durum:** {claim.get('status')}")
                    if AUTO_APPROVE_CLAIMS:
                        with st.spinner("Otomatik olarak onaylanıyor..."):
                            item_ids = [item.get('id') for batch in claim.get('items', []) for item in batch.get('claimItems', [])]
                            if item_ids:
                                success, message = approve_claim_items(claim.get('id'), item_ids)
                                if success: st.success("Talep başarıyla otomatik onaylandı."); st.rerun()
                                else: st.error(f"Otomatik onay başarısız: {message}")
                            else: st.warning("Onaylanacak ürün kalemi bulunamadı.")
    except Exception as e: st.error(f"İade/Talep bölümünde bir hata oluştu: {e}")


with col2:
    st.subheader("Cevap Bekleyen Müşteri Soruları")
    try:
        questions = get_waiting_questions()

        # <--- DEĞİŞTİ: Her yeni soru için ayrı Telegram bildirimi gönder --->
        if questions:
            if 'notified_question_ids' not in st.session_state:
                st.session_state.notified_question_ids = set()

            for q in questions:
                q_id = q.get("id")
                if q_id not in st.session_state.notified_question_ids:
                    product_name = q.get('productName', 'Bilinmeyen Ürün')
                    question_text = q.get('text', '')
                    message = (
                        f"🔔 *Yeni Müşteri Sorusu!* (Soru ID: {q_id})\n\n"
                        f"📦 *Ürün:* {product_name}\n\n"
                        f"❓ *Soru:* {question_text}\n\n"
                        f"👇 *Cevaplamak için bu mesaja yanıt verin.*"
                    )
                    send_telegram_message(message)
                    st.session_state.notified_question_ids.add(q_id)
        # <--- DEĞİŞİKLİK SONU --->

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
                    # ... (Otomatik ve Manuel cevaplama mantığı aynı kaldı) ...
                    if f"time_{q_id}" not in st.session_state: st.session_state[f"time_{q_id}"] = datetime.now()
                    elapsed = datetime.now() - st.session_state[f"time_{q_id}"]
                    if AUTO_ANSWER_QUESTIONS:
                        if DELAY_MINUTES == 0 or elapsed >= timedelta(minutes=DELAY_MINUTES):
                            with st.spinner(f"Soru ID {q_id}: Otomatik cevap kontrol ediliyor..."):
                                answer, reason = safe_generate_answer(q.get("productName", ""), q.get("text", ""), past_df, min_examples=MIN_EXAMPLES)
                                if answer is None: st.warning(f"Otomatik cevap gönderilmedi: {reason}"); continue
                                st.info(f"Otomatik gönderilecek cevap:\n\n> {answer}")
                                success, message = send_answer(q_id, answer)
                                if success: st.success("Cevap başarıyla otomatik gönderildi."); st.session_state.questions_handled.append(q_id); st.rerun()
                                else: st.error(f"Cevap gönderilemedi: {message}")
                        else:
                            remaining_seconds = (timedelta(minutes=DELAY_MINUTES) - elapsed).total_seconds()
                            st.warning(f"Bu soruya otomatik cevap yaklaşık **{int(remaining_seconds / 60)} dakika {int(remaining_seconds % 60)} saniye** içinde gönderilecek.")
                    else:
                        suggestion, reason = safe_generate_answer(q.get("productName", ""), q.get("text", ""), past_df, min_examples=MIN_EXAMPLES)
                        default_text = suggestion if suggestion is not None else ""
                        if suggestion is None: st.info(f"Öneri üretilmedi: {reason}")
                        cevap = st.text_area("Cevabınız:", value=default_text, key=f"manual_{q_id}")
                        if st.button(f"Cevabı Gönder (ID: {q_id})", key=f"btn_{q_id}"):
                            ok, why = passes_forbidden_filter(cevap)
                            if not ok: st.error(why)
                            elif not cevap.strip(): st.error("Boş cevap gönderilemez.")
                            else:
                                success, message = send_answer(q_id, cevap)
                                if success: st.success("Cevap başarıyla gönderildi."); st.session_state.questions_handled.append(q_id); st.rerun()
                                else: st.error(f"Cevap gönderilemedi: {message}")
    except Exception as e:
        st.error(f"Müşteri Soruları bölümünde bir hata oluştu: {e}")
