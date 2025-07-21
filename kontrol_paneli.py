import streamlit as st
import pandas as pd
import requests
import base64
import openai
from datetime import datetime, timedelta
from streamlit_autorefresh import st_autorefresh

# --- Streamlit Arayüzü ve Ayarları ---
st.set_page_config(layout="wide")
st.title("Trendyol Otomasyon Kontrol Paneli (HATA AYIKLAMA MODU)")

# --- API Bilgilerini Güvenli Olarak Oku ---
try:
    SELLER_ID = st.secrets["SELLER_ID"]
    API_KEY = st.secrets["API_KEY"]
    API_SECRET = st.secrets["API_SECRET"]
    openai.api_key = st.secrets["OPENAI_API_KEY"]
    AUTO_APPROVE_CLAIMS = st.secrets.get("AUTO_APPROVE_CLAIMS", False)
    AUTO_ANSWER_QUESTIONS = st.secrets.get("AUTO_ANSWER_QUESTIONS", False)
    DELAY_MINUTES = st.secrets.get("DELAY_MINUTES", 5)
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

st_autorefresh(interval=30 * 1000, key="data_fetch_refresher")

# --- FONKSİYONLAR ---

def get_pending_claims():
    url = f"https://apigw.trendyol.com/integration/order/sellers/{SELLER_ID}/claims?claimItemStatus=WaitingInAction&size=50&page=0"
    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        return response.json().get('content', [])
    except Exception as e:
        st.error(f"İade/Talep Talepleri çekilirken bir hata oluştu: {e}")
        return []

# ... (Diğer fonksiyonlar aynı, sadece generate_answer değişti) ...
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
        st.warning(f"'{file_path}' dosyası bulunamadı.")
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
        
def send_answer(question_id, answer_text):
    url = f"https://apigw.trendyol.com/integration/qna/sellers/{SELLER_ID}/questions/{question_id}/answers"
    data = {"text": answer_text}
    try:
        response = requests.post(url, headers=HEADERS, json=data)
        return response.status_code == 200, response.text
    except Exception as e:
        return False, str(e)
        
# --- HATA AYIKLAMA MODLU generate_answer FONKSİYONU ---
def generate_answer(product_name, question, past_df):
    st.info("1. `generate_answer` fonksiyonuna girildi.")
    
    if openai.api_key and openai.api_key.startswith("sk-"):
        st.info(f"2. Geçerli formatta bir OpenAI API anahtarı bulundu. Son 4 karakteri: ...{openai.api_key[-4:]}")
    else:
        st.error("HATA: Geçerli bir OpenAI API anahtarı 'Secrets' içinde bulunamadı veya formatı yanlış.")
        return "API Anahtarı Eksik veya Hatalı."

    st.info("3. OpenAI'ye gönderilecek prompt metni oluşturuldu.")
    prompt = "Test sorusu" # Gerçek prompt yerine basit bir test gönderiyoruz.

    try:
        st.info("4. OpenAI API'sine istek gönderiliyor...")
        client = openai.OpenAI(api_key=openai.api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10,
            temperature=0.7
        )
        st.success("5. OpenAI API'sinden başarıyla cevap alındı!")
        return response.choices[0].message.content.strip()
    except openai.AuthenticationError as e:
        st.error(f"OpenAI KIMLIK DOĞRULAMA HATASI! API Anahtarınız yanlış, geçersiz veya bloke edilmiş. Lütfen 'Secrets' bölümünü kontrol edin. Hata Detayı: {e}")
        return "Kimlik Doğrulama Hatası."
    except openai.RateLimitError as e:
        st.error(f"OpenAI KULLANIM LİMİTİ HATASI! Bakiyeniz bitmiş veya kullanım limitinizi aşmış olabilirsiniz. Lütfen OpenAI 'Usage' sayfasını kontrol edin. Hata Detayı: {e}")
        return "Kullanım Limiti Hatası."
    except openai.APIConnectionError as e:
        st.error(f"OpenAI BAĞLANTI HATASI! Sunucuya bağlanılamıyor. Hata Detayı: {e}")
        return "Bağlantı Hatası."
    except Exception as e:
        st.error(f"BEKLENMEDİK BİR HATA OLUŞTU! Hata Detayı: {type(e).__name__} - {e}")
        return "Bilinmeyen Hata."


# --- ANA KONTROL PANELİ ARAYÜZÜ ---
# (Bu bölüm aynı kalıyor)
EXCEL_FILE_NAME = "soru_cevap_ornekleri.xlsx"
past_df = load_past_data(EXCEL_FILE_NAME)
st.sidebar.header("Otomasyon Durumu")
st.sidebar.markdown(f"**İade Onaylama:** `{'Aktif' if AUTO_APPROVE_CLAIMS else 'Pasif'}`")
st.sidebar.markdown(f"**Soru Cevaplama:** `{'Aktif' if AUTO_ANSWER_QUESTIONS else 'Pasif'}`")
if AUTO_ANSWER_QUESTIONS:
    st.sidebar.markdown(f"**Cevap Gecikmesi:** `{DELAY_MINUTES} dakika`")
if past_df is not None:
    st.sidebar.success("Soru-cevap örnekleri başarıyla yüklendi.")
else:
    st.sidebar.warning("Soru-cevap örnek dosyası bulunamadı veya okunamadı.")
col1, col2 = st.columns(2)
with col1:
    # ... (iade bölümü aynı)
    pass
with col2:
    st.subheader("Cevap Bekleyen Müşteri Soruları")
    try:
        questions = get_waiting_questions()
        if not questions:
            st.info("Cevap bekleyen soru bulunamadı.")
        else:
            st.write(f"**{len(questions)}** adet cevap bekleyen soru var.")
            if 'questions_handled' not in st.session_state:
                st.session_state.questions_handled = []
            for q in questions:
                q_id = q.get("id")
                if q_id in st.session_state.questions_handled:
                    continue
                with st.expander(f"Soru ID: {q_id} - Ürün: {q.get('productName', '')[:30]}...", expanded=True):
                    st.markdown(f"**Soru:** *{q.get('text', '')}*")
                    # Artık otomatik/manuel ayrımı olmadan direkt debug fonksiyonunu çağırıyoruz
                    generate_answer(q.get("productName", ""), q.get("text", ""), past_df)
    except Exception as e:
        st.error(f"Müşteri Soruları bölümünde bir hata oluştu: {e}")
