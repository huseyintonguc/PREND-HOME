import streamlit as st
import pandas as pd
import requests
import base64
import openai
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
    # DEĞİŞİKLİK: Otomasyon ayarları artık Secrets'tan okunuyor
    AUTO_APPROVE_CLAIMS = st.secrets.get("AUTO_APPROVE_CLAIMS", False)
    AUTO_ANSWER_QUESTIONS = st.secrets.get("AUTO_ANSWER_QUESTIONS", False)
    DELAY_MINUTES = st.secrets.get("DELAY_MINUTES", 5) # Varsayılan 5 dakika
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

# --- FONKSİYONLAR ---
# (Fonksiyonlarda bir değişiklik yok, aynı kalıyorlar)
def get_pending_claims():
    url = f"https://apigw.trendyol.com/integration/order/sellers/{SELLER_ID}/claims?claimItemStatus=WaitingInAction&size=100&page=0"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    return response.json().get('content', [])

def approve_claim_items(claim_id, claim_item_ids):
    url = f"https://apigw.trendyol.com/integration/order/sellers/{SELLER_ID}/claims/{claim_id}/items/approve"
    data = {"claimLineItemIdList": claim_item_ids, "params": {}}
    response = requests.put(url, headers=HEADERS, json=data)
    return response.status_code == 200, response.text

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
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    return response.json().get("content", [])

def generate_answer(product_name, question, past_df):
    # ... (generate_answer fonksiyonu aynı)
    pass
    
def send_answer(question_id, answer_text):
    # ... (send_answer fonksiyonu aynı)
    pass

# --- ANA KONTROL PANELİ ARAYÜZÜ ---

# DEĞİŞİKLİK: Excel dosyası artık manuel yüklenmiyor, direkt okunuyor.
# GitHub'a yükleyeceğiniz Excel dosyasının adının bu olduğundan emin olun.
EXCEL_FILE_NAME = "soru_cevap_ornekleri.xlsx"
past_df = load_past_data(EXCEL_FILE_NAME)

st.sidebar.header("Otomasyon Durumu")
st.sidebar.markdown(f"**İade Onaylama:** `{'Aktif' if AUTO_APPROVE_CLAIMS else 'Pasif'}`")
st.sidebar.markdown(f"**Soru Cevaplama:** `{'Aktif' if AUTO_ANSWER_QUESTIONS else 'Pasif'}`")
if AUTO_ANSWER_QUESTIONS:
    st.sidebar.markdown(f"**Cevap Gecikmesi:** `{DELAY_MINUTES} dakika`")

if past_df is not None:
    st.sidebar.success("Soru-cevap örnekleri başarıyla yüklendi.")

col1, col2 = st.columns(2)

# Bölüm 1 ve 2, artık butonlar yerine global ayarlara göre çalışacak
# (İçerikleri büyük ölçüde aynı, sadece auto_approve_claims gibi değişkenleri kullanıyorlar)
# ... (col1 ve col2 içindeki kodlar öncekiyle aynı, sadece değişken adları güncellendi)
