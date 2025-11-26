import streamlit as st
import pandas as pd
import requests
import base64
import openai
import re
import logging
from datetime import datetime, timedelta
from streamlit_autorefresh import st_autorefresh
from dataclasses import dataclass, field

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Konfigürasyon ---
@dataclass
class Config:
    min_examples: int = 0
    delay_minutes: int = 5
    openai_model: str = "gpt-4o-mini"
    openai_temperature: float = 0.4
    openai_max_tokens: int = 150

    def load_from_sidebar(self):
        st.sidebar.header("Genel Ayarlar")
        self.min_examples = st.sidebar.number_input(
            "Otomatik cevap için min. örnek sayısı (0 = Örneksiz çalışır)", 
            min_value=0, 
            value=self.min_examples
        )
        self.delay_minutes = st.sidebar.number_input(
            "Otomatik Cevap Gecikmesi (Dakika)", 
            min_value=1, 
            value=self.delay_minutes,
            help="Soru geldikten sonra bu süre kadar beklenir, siz cevaplamazsanız bot cevaplar."
        )
        
        st.sidebar.header("OpenAI Ayarları")
        self.openai_model = st.sidebar.text_input("OpenAI Modeli", value=self.openai_model)
        self.openai_temperature = st.sidebar.slider("Sıcaklık (Temperature)", 0.0, 1.0, self.openai_temperature)
        self.openai_max_tokens = st.sidebar.number_input("Max Tokens", min_value=50, value=self.openai_max_tokens)
        return self

# --- Streamlit Arayüzü ve Ayarları ---
st.set_page_config(layout="wide", page_title="Trendyol Panel")
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

def initialize_session_state():
    if 'processed_claims' not in st.session_state:
        st.session_state.processed_claims = set()
    if 'questions' not in st.session_state:
        st.session_state.questions = {}
    if 'last_update_id' not in st.session_state:
        st.session_state.last_update_id = 0
    if 'notified_question_ids' not in st.session_state:
        st.session_state.notified_question_ids = set()
    if 'metrics' not in st.session_state:
        st.session_state.metrics = {
            'questions_answered_auto': 0,
            'questions_answered_manual': 0,
            'claims_approved_auto': 0,
            'total_response_time_seconds': 0,
            'response_count': 0,
        }

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
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logging.error(f"Telegram mesajı gönderilemedi: {e}")

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
    offset = st.session_state.last_update_id + 1
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={offset}&timeout=10"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
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
                        
                        success, response_text = send_answer(store, question_id, final_answer)
                        if success:
                            msg = f"✅ `{store_name}` mağazası için (Soru ID: {question_id}) cevabı @{message.get('from', {}).get('username', chat_id)} tarafından gönderildi."
                            st.success(msg)
                            send_telegram_message(msg)
                            if question_id in st.session_state.questions:
                                st.session_state.questions[question_id]['handled'] = True
                            st.rerun()
                        else:
                            msg = f"❌ `{store_name}` için cevap gönderilemedi: {response_text}"
                            st.error(msg)
                            send_telegram_message(msg, chat_id=chat_id)
    except requests.exceptions.RequestException as e:
        logging.error(f"Telegram güncellemeleri alınamadı: {e}")
    except Exception as e:
        st.sidebar.error(f"Telegram güncellemeleri işlenirken hata: {e}")

# --- DİĞER FONKSİYONLAR ---
def get_pending_claims(store):
    url = f"https://apigw.trendyol.com/integration/order/sellers/{store['seller_id']}/claims?claimItemStatus=WaitingInAction&size=50&page=0"
    try:
        headers = get_headers(store['api_key'], store['api_secret'])
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        return response.json().get('content', [])
    except requests.exceptions.HTTPError as e:
        logging.error(f"{store['name']} için talepler alınamadı (HTTP Hatası): {e.response.status_code} - {e.response.text}")
    except requests.exceptions.RequestException as e:
        logging.error(f"{store['name']} için talepler alınamadı (Bağlantı Hatası): {e}")
    except Exception as e:
        logging.error(f"{store['name']} için talepler alınırken beklenmedik bir hata oluştu: {e}")
    return []

def approve_claim_items(store, claim_id, claim_item_ids):
    url = f"https://apigw.trendyol.com/integration/order/sellers/{store['seller_id']}/claims/{claim_id}/items/approve"
    data = {"claimLineItemIdList": claim_item_ids, "params": {}}
    try:
        headers = get_headers(store['api_key'], store['api_secret'])
        response = requests.put(url, headers=headers, json=data, timeout=15)
        response.raise_for_status()
        return True, response.text
    except requests.exceptions.HTTPError as e:
        error_message = f"HTTP Hatası: {e.response.status_code} - {e.response.text}"
        logging.error(f"{store['name']} için talep onayı başarısız: {error_message}")
        return False, error_message
    except requests.exceptions.RequestException as e:
        error_message = f"Bağlantı Hatası: {e}"
        logging.error(f"{store['name']} için talep onayı başarısız: {error_message}")
        return False, error_message
    except Exception as e:
        error_message = f"Beklenmedik bir hata oluştu: {e}"
        logging.error(f"{store['name']} için talep onayı başarısız: {error_message}")
        return False, error_message

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
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        return response.json().get("content", [])
    except requests.exceptions.HTTPError as e:
        logging.error(f"{store['name']} için bekleyen sorular alınamadı (HTTP Hatası): {e.response.status_code} - {e.response.text}")
    except requests.exceptions.RequestException as e:
        logging.error(f"{store['name']} için bekleyen sorular alınamadı (Bağlantı Hatası): {e}")
    except Exception as e:
        logging.error(f"{store['name']} için bekleyen sorular alınırken beklenmedik bir hata oluştu: {e}")
    return []

def send_answer(store, question_id, answer_text):
    url = f"https://apigw.trendyol.com/integration/qna/sellers/{store['seller_id']}/questions/{question_id}/answers"
    data = {"text": answer_text}
    try:
        headers = get_headers(store['api_key'], store['api_secret'])
        response = requests.post(url, headers=headers, json=data, timeout=15)
        response.raise_for_status()
        return True, response.text
    except requests.exceptions.HTTPError as e:
        error_message = f"HTTP Hatası: {e.response.status_code} - {e.response.text}"
        logging.error(f"{store['name']} için cevap gönderilemedi: {error_message}")
        return False, error_message
    except requests.exceptions.RequestException as e:
        error_message = f"Bağlantı Hatası: {e}"
        logging.error(f"{store['name']} için cevap gönderilemedi: {error_message}")
        return False, error_message
    except Exception as e:
        error_message = f"Beklenmedik bir hata oluştu: {e}"
        logging.error(f"{store['name']} için cevap gönderilemedi: {error_message}")
        return False, error_message

def safe_generate_answer(product_name, question, past_df, config: Config):
    if not openai.api_key: 
        logging.error("OpenAI API anahtarı bulunamadı.")
        return None, "OpenAI API anahtarı bulunamadı."
    
    examples = pd.DataFrame()
    if past_df is not None and not past_df.empty:
        mask = past_df['Ürün İsmi'].astype(str).str.contains(str(product_name), case=False, na=False)
        examples = past_df[mask]
    
    if config.min_examples > 0 and len(examples) < config.min_examples:
        return None, f"Örnek sayısı yetersiz ({len(examples)}/{config.min_examples})."
    
    prompt = f"""
    Sen Trendyol'da satış yapan bir mağazanın profesyonel müşteri temsilcisisin.
    Müşteriden gelen soruyu, ürün bilgisine dayanarak nazik ve açıklayıcı bir şekilde cevapla.
    
    Ürün: {product_name}
    Soru: {question}
    """
    
    if not examples.empty:
        prompt += "\n\nBenzer Geçmiş Sorular ve Cevaplarımız:\n"
        for idx, row in examples.head(3).iterrows():
            prompt += f"- Soru: {row['Soru Detayı']}\n  Cevap: {row['Onaylanan Cevap']}\n"
    else:
        prompt += "\n\nLütfen genel e-ticaret nezaket kurallarına uygun, yardımsever bir cevap üret."

    try:
        client = openai.OpenAI(api_key=openai.api_key)
        response = client.chat.completions.create(
            model=config.openai_model, 
            messages=[{"role": "user", "content": prompt}], 
            max_tokens=config.openai_max_tokens, 
            temperature=config.openai_temperature
        )
        answer = response.choices[0].message.content.strip()
        return (answer, "")
    except openai.APIError as e:
        logging.error(f"OpenAI API Hatası: {e}")
        return None, f"OpenAI API Hatası: {e}"
    except Exception as e:
        logging.error(f"OpenAI'den cevap üretilirken beklenmedik bir hata oluştu: {e}")
        return None, f"Beklenmedik bir hata: {e}"

def handle_claims(store):
    st.subheader("Onay Bekleyen İade/Talepler")
    claims = get_pending_claims(store)
    if not claims: 
        st.info("Onay bekleyen iade/talep bulunamadı.")
    else:
        st.write(f"**{len(claims)}** adet onay bekleyen talep var.")
        for claim in claims:
            if isinstance(claim, dict) and claim.get('id'):
                claim_id = claim.get('id')
                with st.expander(f"Sipariş No: {claim.get('orderNumber')} - Talep ID: {claim_id}", expanded=True):
                    st.write(f"**Talep Nedeni:** {claim.get('claimType', {}).get('name', 'Belirtilmemiş')}")
                    st.write(f"**Durum:** {claim.get('status')}")
                    
                    if store.get('auto_approve_claims'):
                        if claim_id in st.session_state.processed_claims:
                            st.warning("⚠️ Bu talep bu oturumda daha önce denendi. Döngüyü önlemek için otomatik onay pas geçiliyor. Lütfen manuel kontrol edin.")
                        else:
                            with st.spinner("Otomatik olarak onaylanıyor..."):
                                item_ids = [item.get('id') for batch in claim.get('items', []) for item in batch.get('claimItems', [])]
                                if item_ids:
                                    st.session_state.processed_claims.add(claim_id)
                                    success, message = approve_claim_items(store, claim_id, item_ids)
                                    if success:
                                        st.session_state.metrics['claims_approved_auto'] += 1
                                        st.success("Talep başarıyla otomatik onaylandı.")
                                        st.rerun()
                                    else: 
                                        st.error(f"Otomatik onay başarısız: {message}")
                                else:
                                    st.warning("Onaylanacak ürün kalemi bulunamadı.")

def handle_questions(store, past_df, config: Config):
    st.subheader("Cevap Bekleyen Müşteri Soruları")
    
    all_questions_raw = get_waiting_questions(store)
    questions_data = []
    seen_question_ids = set()
    if all_questions_raw:
        for q in all_questions_raw:
            if isinstance(q, dict) and q.get("id"):
                q_id = q["id"]
                if q_id not in seen_question_ids:
                    questions_data.append(q)
                    seen_question_ids.add(q_id)
    
    if questions_data and store.get('send_notifications'):
        for q in questions_data:
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
    
    if not questions_data: 
        st.info("Cevap bekleyen soru bulunamadı.")
    else:
        st.write(f"**{len(questions_data)}** adet cevap bekleyen soru var.")
        for q in questions_data:
            q_id = q.get("id")

            if q_id not in st.session_state.questions:
                st.session_state.questions[q_id] = {'handled': False, 'timestamp': datetime.now()}
            
            if st.session_state.questions[q_id]['handled']:
                continue

            with st.expander(f"Soru ID: {q_id} - Ürün: {q.get('productName', '')[:30]}...", expanded=True):
                st.markdown(f"**Soru:** *{q.get('text', '')}*")
                is_auto_answer_active = store.get('auto_answer_questions', False)
                
                elapsed = datetime.now() - st.session_state.questions[q_id]['timestamp']
                
                if is_auto_answer_active:
                    if config.delay_minutes == 0 or elapsed >= timedelta(minutes=config.delay_minutes):
                        with st.spinner(f"Soru ID {q_id}: Otomatik cevap kontrol ediliyor..."):
                            answer, reason = safe_generate_answer(q.get("productName", ""), q.get("text", ""), past_df, config=config)
                            if answer is None: 
                                st.warning(f"Otomatik cevap gönderilmedi: {reason}")
                                continue
                            st.info(f"Otomatik gönderilecek cevap:\n\n> {answer}")
                            success, message = send_answer(store, q_id, answer)
                            if success:
                                st.session_state.metrics['questions_answered_auto'] += 1
                                st.session_state.metrics['total_response_time_seconds'] += elapsed.total_seconds()
                                st.session_state.metrics['response_count'] += 1
                                st.success("Cevap başarıyla otomatik gönderildi.")
                                st.session_state.questions[q_id]['handled'] = True
                                st.rerun()
                            else: 
                                st.error(f"Cevap gönderilemedi: {message}")
                    else:
                        remaining_seconds = (timedelta(minutes=config.delay_minutes) - elapsed).total_seconds()
                        st.warning(f"Bu soruya otomatik cevap yaklaşık **{int(remaining_seconds / 60)} dakika {int(remaining_seconds % 60)} saniye** içinde gönderilecek.")
                else: 
                    suggestion, reason = safe_generate_answer(q.get("productName", ""), q.get("text", ""), past_df, config=config)
                    default_text = suggestion if suggestion is not None else ""
                    if reason: st.info(f"Öneri üretilmedi: {reason}")
                    
                    cevap = st.text_area("Cevabınız:", value=default_text, key=f"textarea_{store['name']}_{q_id}")
                    if st.button(f"Cevabı Gönder (ID: {q_id})", key=f"btn_{store['name']}_{q_id}"):
                        if not cevap.strip(): st.error("Boş cevap gönderilemez.")
                        else:
                            success, message = send_answer(store, q_id, cevap)
                            if success:
                                st.session_state.metrics['questions_answered_manual'] += 1
                                st.session_state.metrics['total_response_time_seconds'] += elapsed.total_seconds()
                                st.session_state.metrics['response_count'] += 1
                                st.success("Cevap başarıyla gönderildi.")
                                st.session_state.questions[q_id]['handled'] = True
                                st.rerun()
                            else: 
                                st.error(f"Cevap gönderilemedi: {message}")

# --- UYGULAMA BAŞLANGIÇ NOKTASI ---

initialize_session_state()
config = Config().load_from_sidebar()

# --- VERİ YÜKLEME VE ARKA PLAN İŞLEMLERİ ---
templates = load_templates()
past_df = load_past_data()
stores_map = {store['name']: store for store in STORES}

if not templates:
    st.sidebar.warning("`cevap_sablonlari.xlsx` dosyası bulunamadı veya boş.")
if past_df is not None:
    st.sidebar.success(f"Soru-cevap örnekleri yüklendi ({len(past_df)} kayıt).")
else:
    st.sidebar.warning("`soru_cevap_ornekleri.xlsx` dosyası bulunamadı.")

process_telegram_updates(stores_map, templates)

# --- ANA SAYFA GÖVDESİ ---
tab_titles = ["Dashboard"] + [s['name'] for s in STORES]
tabs = st.tabs(tab_titles)

with tabs[0]:
    st.header("📊 Genel Bakış")
    
    metrics = st.session_state.metrics
    
    avg_response_time = (metrics['total_response_time_seconds'] / metrics['response_count']) if metrics['response_count'] > 0 else 0
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Otomatik Onaylanan Talep", metrics['claims_approved_auto'])
    with col2:
        st.metric("Otomatik Cevaplanan Soru", metrics['questions_answered_auto'])
    with col3:
        st.metric("Manuel Cevaplanan Soru", metrics['questions_answered_manual'])
    with col4:
        st.metric("Ortalama Cevap Süresi (sn)", f"{avg_response_time:.2f}")

    st.subheader("Cevaplanan Soru Dağılımı")
    chart_data = pd.DataFrame({
        'Cevap Türü': ['Otomatik', 'Manuel'],
        'Sayı': [metrics['questions_answered_auto'], metrics['questions_answered_manual']]
    })
    st.bar_chart(chart_data.set_index('Cevap Türü'))

for i, store in enumerate(STORES):
    with tabs[i+1]:
        st.header(f"🏪 {store['name']} Mağazası Paneli")
        st.markdown(
            f"**İade Onaylama:** `{'Aktif' if store.get('auto_approve_claims') else 'Pasif'}` | "
            f"**Soru Cevaplama:** `{'Aktif' if store.get('auto_answer_questions') else 'Pasif'}` | "
            f"**Telegram Bildirim:** `{'Aktif' if store.get('send_notifications') else 'Pasif'}`"
        )
        col1, col2 = st.columns(2)
        with col1:
            handle_claims(store)
        with col2:
            handle_questions(store, past_df, config)
