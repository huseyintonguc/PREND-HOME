import streamlit as st
import pandas as pd
import os
from datetime import datetime, timedelta

# Sayfa Ayarları
st.set_page_config(page_title="PREND Panel", layout="wide")

st.title("🤖 PREND - Instagram Otomasyon Kontrol Paneli")

# Dosya Yolları (Dosyaların aynı klasörde olduğu varsayılmıştır)
SORU_CEVAP_FILE = "soru_cevap_ornekleri.xlsx - Sorular.csv"
SABLON_FILE = "cevap_sablonlari.xlsx - Sayfa1.csv"
AYAR_DOSYASI = "zaman_ayarlari.csv" 

# Yan Menü
menu = st.sidebar.selectbox("Menü", ["Ana Sayfa", "Gecikmeli Otomatik Cevap", "Mesaj Şablonları"])

if menu == "Ana Sayfa":
    st.subheader("Genel Durum")
    st.info("Sistem şu anda aktif. Mesajlar izleniyor.")
    
    # Metrikler (Örnek veriler)
    col1, col2, col3 = st.columns(3)
    col1.metric(label="Bugün Gelen Mesaj", value="12")
    col2.metric(label="Sizin Cevapladığınız", value="8")
    col3.metric(label="Botun Devraldığı", value="2")

elif menu == "Gecikmeli Otomatik Cevap":
    st.subheader("⏳ Gecikmeli Otomatik Cevap Ayarları")
    st.markdown("""
    Sistem, gelen mesajları takip eder. Eğer bir mesaja **belirlediğiniz süre boyunca** (örneğin 15 dakika) 
    siz veya ekibiniz cevap vermezse, bot devreye girer ve cevabı kendisi verir.
    """)

    col1, col2 = st.columns(2)
    
    # Varsayılan Değerler
    default_timeout = 15
    default_mode = "Yapay Zeka Üretsin"
    default_fixed_msg = ""

    # Varsa Mevcut Ayarları Yükle
    if os.path.exists(AYAR_DOSYASI):
        try:
            df_ayar = pd.read_csv(AYAR_DOSYASI)
            if not df_ayar.empty:
                # Veri tiplerini güvenli çekme
                default_timeout = int(df_ayar.iloc[0].get('bekleme_suresi', 15))
                default_mode = str(df_ayar.iloc[0].get('mod', "Yapay Zeka Üretsin"))
                default_fixed_msg = str(df_ayar.iloc[0].get('sabit_mesaj', ""))
        except Exception as e:
            st.error(f"Ayarlar yüklenirken hata oluştu: {e}")

    with col1:
        st.markdown("### ⏱️ Zamanlayıcı")
        timeout_minutes = st.number_input(
            "Kaç dakika cevap verilmezse bot devreye girsin?", 
            min_value=1, 
            max_value=1440, 
            value=default_timeout,
            help="Mesaj geldikten sonra bu süre kadar beklersiniz. Siz cevap yazmazsanız bot yazar."
        )

    with col2:
        st.markdown("### 🤖 Cevaplama Yöntemi")
        response_mode = st.radio(
            "Bot devreye girdiğinde ne yapsın?", 
            ["Yapay Zeka Üretsin", "Sabit Mesaj Gönder"]
        )
        
        fixed_msg_input = ""
        if response_mode == "Sabit Mesaj Gönder":
            fixed_msg_input = st.text_area(
                "Gönderilecek Sabit Mesaj:", 
                value=default_fixed_msg,
                placeholder="Örn: Şu an müsait değiliz, en kısa sürede döneceğiz."
            )
            st.caption("Bot sadece bu metni gönderir.")
        else:
            st.info("💡 Bot, 'Soru-Cevap Örnekleri' dosyasındaki verileri ve yapay zekayı kullanarak mesaja uygun, akıllı bir cevap üretecektir.")
            fixed_msg_input = "" # AI modunda boş kaydedebiliriz

    if st.button("Ayarları Kaydet", type="primary"):
        # Verileri kaydetme işlemi
        data = {
            "bekleme_suresi": [timeout_minutes],
            "mod": [response_mode],
            "sabit_mesaj": [fixed_msg_input]
        }
        df_save = pd.DataFrame(data)
        df_save.to_csv(AYAR_DOSYASI, index=False)
        st.success(f"✅ Ayarlar güncellendi! Cevapsız geçen {timeout_minutes} dakikadan sonra bot devreye girecek.")

    # Simülasyon Alanı (Test etmek için)
    st.divider()
    st.subheader("🛠️ Mantık Testi")
    
    c1, c2 = st.columns(2)
    with c1:
        msg_arrival_minutes_ago = st.number_input("Mesaj kaç dakika önce geldi?", min_value=0, value=10)
    with c2:
        is_human_replied = st.checkbox("Ben bu arada cevap verdim mi?", value=False)
        
    if st.button("Bot ne yapardı?"):
        if is_human_replied:
            st.success("✅ Siz zaten cevap vermişsiniz. Bot devreye girmez.")
        else:
            if msg_arrival_minutes_ago >= timeout_minutes:
                if response_mode == "Yapay Zeka Üretsin":
                    st.warning(f"🤖 SÜRE DOLDU ({timeout_minutes} dk geçti). Bot mesajı analiz edip OTOMATİK CEVAP ÜRETİRDİ.")
                else:
                    st.warning(f"🤖 SÜRE DOLDU. Bot şu sabit mesajı atardı: '{fixed_msg_input}'")
            else:
                remaining = timeout_minutes - msg_arrival_minutes_ago
                st.info(f"⏳ Henüz süre dolmadı. Bot {remaining} dakika daha bekliyor.")

elif menu == "Mesaj Şablonları":
    st.subheader("📝 Hazır Mesaj Şablonları")
    st.markdown("Sık kullanılan cevap kalıplarınızı buradan yönetebilirsiniz.")
    
    if os.path.exists(SABLON_FILE):
        df_sablon = pd.read_csv(SABLON_FILE)
        edited_df = st.data_editor(df_sablon, num_rows="dynamic")
        
        if st.button("Şablonları Güncelle"):
            edited_df.to_csv(SABLON_FILE, index=False)
            st.success("Şablonlar güncellendi.")
    else:
        st.warning("Şablon dosyası bulunamadı. Yeni oluşturuluyor...")
        df_new = pd.DataFrame({"Baslik": ["Örnek Başlık"], "Icerik": ["Örnek İçerik"]})
        st.button("Dosyayı Oluştur", on_click=lambda: df_new.to_csv(SABLON_FILE, index=False))
