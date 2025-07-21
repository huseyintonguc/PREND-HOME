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

# Sayfa otomatik yenileme
st_autorefresh(interval=30 * 1000, key="data_fetch_refresher")

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

def generate_answer(product_name, question, past_df):
    if not openai.api_key:
        return "OpenAI API anahtarı 'Secrets' içinde bulunamadı."
    examples = pd.DataFrame()
    if past_df is not None:
        mask = past_df['Ürün İsmi'].astype(str).str.contains(str(product_name), case=False, na=False)
        examples = past_df[mask]

    prompt = f"Sen bir müşteri temsilcisisin. Aşağıdaki soruya, geçmişte verilen cevapları örnek alarak, nazik, yardımsever ve kısa bir cevap ver.\n\n"
    prompt += f"Ürün Adı: {product_name}\nMüşteri Sorusu: {question}\n\n--- Örnek Geçmiş Cevaplar ---\n"
    for _, row in examples.head(5).iterrows():
        prompt += f"Soru: {row['Soru Detayı']}\nCevap: {row['Onaylanan Cevap']}\n---\n"
    prompt += "Oluşturulacak Cevap:"
    try:
        client = openai.OpenAI(api_key=openai.api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"OpenAI'den cevap üretilirken hata oluştu: {e}"

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
if AUTO_ANSWER_QUESTIONS:
    st.sidebar.markdown(f"**Cevap Gecikmesi:** `{DELAY_MINUTES} dakika`")

if past_df is not None:
    st.sidebar.success("Soru-cevap örnekleri başarıyla yüklendi.")
else:
    st.sidebar.warning("Soru-cevap örnek dosyası bulunamadı veya okunamadı.")

col1, col2 = st.columns(2)

with col1:
    st.subheader("Onay Bekleyen İade/Talepler")
    try:
        claims = get_pending_claims()
        if not claims:
            st.info("Onay bekleyen iade/talep bulunamadı.")
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
                                if success:
                                    st.success("Talep başarıyla otomatik onaylandı.")
                                    st.rerun()
                                else:
                                    st.error(f"Otomatik onay başarısız: {message}")
                            else:
                                st.warning("Onaylanacak ürün kalemi bulunamadı.")
    except Exception as e:
        st.error(f"İade/Talep bölümünde bir hata oluştu: {e}")

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

                    if f"time_{q_id}" not in st.session_state:
                        st.session_state[f"time_{q_id}"] = datetime.now()
                    
                    elapsed = datetime.now() - st.session_state[f"time_{q_id}"]

                    if AUTO_ANSWER_QUESTIONS:
                        if DELAY_MINUTES == 0 or elapsed >= timedelta(minutes=DELAY_MINUTES):
                            with st.spinner(f"Soru ID {q_id}: Otomatik cevap gönderiliyor..."):
                                gpt_answer = generate_answer(q.get("productName", ""), q.get("text", ""), past_df)
                                st.info(f"Otomatik gönderilen cevap:\n\n> {gpt_answer}")
                                
                                success, message = send_answer(q_id, gpt_answer)
                                if success:
                                    st.success("Cevap başarıyla otomatik gönderildi.")
                                    st.session_state.questions_handled.append(q_id)
                                    st.rerun()
                                else:
                                    st.error(f"Cevap gönderilemedi: {message}")
                        else:
                            remaining_seconds = (timedelta(minutes=DELAY_MINUTES) - elapsed).total_seconds()
                            remaining_minutes = int(remaining_seconds / 60)
                            remaining_sec = int(remaining_seconds % 60)
                            st.warning(f"Bu soruya otomatik cevap yaklaşık **{remaining_minutes} dakika {remaining_sec} saniye** içinde gönderilecek.")
                    
                    else: # Manuel mod
                        answer_suggestion = generate_answer(q.get("product_name", ""), q.get("text", ""), past_df)
                        cevap = st.text_area("Cevabınız:", value=answer_suggestion, key=f"manual_{q_id}")
                        if st.button(f"Cevabı Gönder (ID: {q_id})", key=f"btn_{q_id}"):
                            success, message = send_answer(q_id, cevap)
                            if success:
                                st.success("Cevap başarıyla gönderildi.")
                                st.session_state.questions_handled.append(q_id)
                                st.rerun()
                            else:
                                st.error(f"Cevap gönderilemedi: {message}")
    except Exception as e:
        st.error(f"Müşteri Soruları bölümünde bir hata oluştu: {e}")
