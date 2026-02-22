import streamlit as st
import json
import os
import random
from youtube_transcript_api import YouTubeTranscriptApi
import sqlite3
import PyPDF2

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import google.generativeai as genai

# --- Setup & Config ---
st.set_page_config(page_title="Kavram Eşleştirme Oyunu", page_icon="🧠", layout="centered")

# Initialize Gemini API Keys
raw_keys = []
# 1. From Environment Variables
if os.getenv("GEMINI_API_KEYS"):
    raw_keys.extend(os.getenv("GEMINI_API_KEYS").split(","))
if os.getenv("GEMINI_API_KEY"):
    raw_keys.append(os.getenv("GEMINI_API_KEY"))

# 2. From Streamlit Secrets
try:
    if "GEMINI_API_KEYS" in st.secrets:
        raw_keys.extend(st.secrets["GEMINI_API_KEYS"].split(","))
    if "GEMINI_API_KEY" in st.secrets:
        raw_keys.append(st.secrets["GEMINI_API_KEY"])
except Exception:
    pass

# Remove duplicates and empty strings while preserving order
API_KEYS = list(dict.fromkeys([k.strip() for k in raw_keys if k.strip()]))

if not API_KEYS:
    st.warning("⚠️ Hiçbir GEMINI_API_KEY bulunamadı. Lütfen .env dosyasına veya Secrets içerisine ekleyin.")

# --- Database Layout ---
DB_NAME = "sources.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS sources
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  title TEXT,
                  content TEXT,
                  type TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

def save_source(title, content, type):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO sources (title, content, type) VALUES (?, ?, ?)", (title, content, type))
    conn.commit()
    conn.close()

def get_sources():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, title, content, type FROM sources ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1], "content": r[2], "type": r[3]} for r in rows]

# --- Core Logic ---
def extract_youtube_video_id(url: str):
    import re
    pattern = r'(?:v=|\/)([0-9A-Za-z_-]{11}).*'
    match = re.search(pattern, url)
    return match.group(1) if match else None

def get_youtube_transcript(url: str) -> str:
    video_id = extract_youtube_video_id(url)
    if not video_id:
        raise ValueError("Geçersiz YouTube URL'si")
    
    api = YouTubeTranscriptApi()
    
    try:
        transcript = api.fetch(video_id, languages=['tr', 'en'])
        text = " ".join([snippet.text for snippet in transcript])
        return text
    except Exception as e:
        error_msg = str(e)
        if "YouTube is blocking requests from your IP" in error_msg or "blocked" in error_msg.lower():
             raise ValueError("⚠️ YouTube, Streamlit Cloud sunucularının otomatik altyazı çekmesini (bot koruması nedeniyle) engelliyor. Lütfen videonun altyazısını YouTube'dan kopyalayıp '📄 Metin Ekle' sekmesinden yapıştırarak kullanın veya uygulamayı yerel bilgisayarınızda çalıştırın.")
        raise ValueError(f"Altyazı alınamadı: {error_msg}")

def generate_quiz_pairs(input_text: str, count: int = 5) -> list:
    if not API_KEYS:
        raise ValueError("API Key eksik!")
        
    prompt = f"""
    Sen harika ve eğitici bir quiz hazırlayıcısısın. Türkçe dilinde yanıt ver.
    Aşağıdaki metni veya içeriği incele. Bu içerikten {count} adet benzersiz "Kavram" ve "Anlamı" (veya Soru/Cevap, Terim/Açıklama) çifti çıkar.
    Çıkaracağın çiftler bir eşleştirme (matching) oyununda kullanılacak. Kavramlar kısa (1-3 kelime), anlamlar ise açıklayıcı ama çok uzun olmasın (maksimum 1 cümle).
    
    İçerik:
    {input_text}
    
    Lütfen yanıtını SADECE geçerli bir JSON dizisi formatında ver. Başka hiçbir açıklama metni ekleme.
    Örnek Format:
    [
      {{"concept": "Kavram 1", "meaning": "Anlamı 1"}},
      {{"concept": "Kavram 2", "meaning": "Anlamı 2"}}
    ]
    """
    
    last_error = None
    for key in API_KEYS:
        try:
            genai.configure(api_key=key)
            model = genai.GenerativeModel('gemini-2.5-flash')
            response = model.generate_content(prompt)
            
            text = response.text.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.endswith("```"):
                text = text[:-3]
                
            return json.loads(text)
        except Exception as e:
            last_error = e
            error_str = str(e).lower()
            continue
            
    # If all fail, display debug info about the keys loaded
    masked_keys = [f"{k[:4]}...{k[-4:]}" if len(k) > 8 else "KEY_TOO_SHORT" for k in API_KEYS]
    raise ValueError(f"Toplam {len(API_KEYS)} API anahtarı denendi. Yüklenen Anahtarlar: {masked_keys}. Son hata: {str(last_error)}")

def extract_text_from_pdf(pdf_file) -> str:
    pdf_reader = PyPDF2.PdfReader(pdf_file)
    text = ""
    for page in pdf_reader.pages:
        extracted = page.extract_text()
        if extracted:
            text += extracted + "\n"
    return text.strip()

# --- Session State ---
if 'game_active' not in st.session_state:
    st.session_state.game_active = False
if 'pairs' not in st.session_state:
    st.session_state.pairs = []
if 'shuffled_concepts' not in st.session_state:
    st.session_state.shuffled_concepts = []
if 'shuffled_meanings' not in st.session_state:
    st.session_state.shuffled_meanings = []
if 'selected_concept' not in st.session_state:
    st.session_state.selected_concept = None
if 'selected_meaning' not in st.session_state:
    st.session_state.selected_meaning = None
if 'matched_pairs' not in st.session_state:
    st.session_state.matched_pairs = []
if 'score' not in st.session_state:
    st.session_state.score = 0
if 'current_source_content' not in st.session_state:
    st.session_state.current_source_content = ""
if 'current_source_type' not in st.session_state:
    st.session_state.current_source_type = ""

# --- UI Layout ---
st.title("🧠 Kavram Eşleştirme Oyunu")

init_db()

# Main Menu vs Game Screen routing
if not st.session_state.game_active:
    st.markdown("Öğrenmek istediğiniz bir konuyu, metni veya YouTube videosunu girin. Yapay zeka size anında eşleştirmeli bir oyun hazırlasın!")
    
    tab1, tab2, tab3, tab4 = st.tabs(["📄 Metin Ekle", "🎥 YouTube Linki", "💾 Kayıtlı Kaynaklar", "📎 PDF Yükle"])
    
    with tab1:
        text_input = st.text_area("Metninizi buraya yapıştırın:", height=200)
        col1, col2 = st.columns([3,1])
        with col1:
            text_title = st.text_input("Bu kaynağı kaydetmek için başlık (İsteğe bağlı)", key="txt_title")
        with col2:
            st.write("")
            st.write("")
            save_txt = st.checkbox("Kaydet", key="save_txt_chk")
            
        if st.button("🚀 Quiz Oluştur (Metin)", use_container_width=True, type="primary"):
            if not text_input.strip():
                st.error("Lütfen bir metin girin!")
            else:
                if save_txt and text_title:
                    save_source(text_title, text_input, "text")
                
                with st.spinner("Sorular yapay zeka tarafından hazırlanıyor..."):
                    try:
                        pairs = generate_quiz_pairs(text_input)
                        st.session_state.current_source_content = text_input
                        st.session_state.current_source_type = "text"
                        
                        st.session_state.pairs = pairs
                        st.session_state.shuffled_concepts = random.sample([p["concept"] for p in pairs], len(pairs))
                        st.session_state.shuffled_meanings = random.sample([p["meaning"] for p in pairs], len(pairs))
                        
                        st.session_state.game_active = True
                        st.rerun()
                    except Exception as e:
                        st.error(f"Hata oluştu: {str(e)}")

    with tab2:
        yt_input = st.text_input("YouTube Video URL'sini yapıştırın:")
        col1, col2 = st.columns([3,1])
        with col1:
            yt_title = st.text_input("Bu kaynağı kaydetmek için başlık (İsteğe bağlı)", key="yt_title")
        with col2:
            st.write("")
            st.write("")
            save_yt = st.checkbox("Kaydet", key="save_yt_chk")
            
        if st.button("🚀 Quiz Oluştur (YouTube)", use_container_width=True, type="primary"):
            if not yt_input.strip() or "youtube.com" not in yt_input:
                st.error("Lütfen geçerli bir YouTube url'si girin!")
            else:
                if save_yt and yt_title:
                    save_source(yt_title, yt_input, "youtube")
                    
                with st.spinner("Videonun altyazıları çekiliyor ve sorular hazırlanıyor..."):
                    try:
                        transcript = get_youtube_transcript(yt_input)
                        pairs = generate_quiz_pairs(transcript)
                        st.session_state.current_source_content = transcript
                        st.session_state.current_source_type = "text" # Treat transcript as text internally
                        
                        st.session_state.pairs = pairs
                        st.session_state.shuffled_concepts = random.sample([p["concept"] for p in pairs], len(pairs))
                        st.session_state.shuffled_meanings = random.sample([p["meaning"] for p in pairs], len(pairs))
                        
                        st.session_state.game_active = True
                        st.rerun()
                    except Exception as e:
                        st.error(f"Hata oluştu: {str(e)}")

    with tab3:
        sources = get_sources()
        if not sources:
            st.info("Henüz kaydedilmiş bir kaynağınız yok.")
        else:
            for s in sources:
                with st.expander(f"{s['title']} ({s['type'].upper()})"):
                    if s['type'] == 'youtube':
                        st.write(f"Link: {s['content']}")
                    else:
                        st.write(f"Özet: {s['content'][:150]}...")
                        
                    if st.button(f"Oyna: {s['title']}", key=f"play_{s['id']}"):
                        with st.spinner("Sorular hazırlanıyor..."):
                            try:
                                content_to_process = s['content']
                                if s['type'] == 'youtube':
                                    content_to_process = get_youtube_transcript(s['content'])
                                    
                                pairs = generate_quiz_pairs(content_to_process)
                                st.session_state.current_source_content = content_to_process
                                st.session_state.current_source_type = "text"
                                
                                st.session_state.pairs = pairs
                                st.session_state.shuffled_concepts = random.sample([p["concept"] for p in pairs], len(pairs))
                                st.session_state.shuffled_meanings = random.sample([p["meaning"] for p in pairs], len(pairs))
                                
                                st.session_state.game_active = True
                                st.rerun()
                            except Exception as e:
                                st.error(f"Hata: {str(e)}")

    with tab4:
        uploaded_file = st.file_uploader("Bir PDF dosyası seçin", type="pdf")
        col1, col2 = st.columns([3,1])
        with col1:
            pdf_title = st.text_input("Bu kaynağı kaydetmek için başlık (İsteğe bağlı)", key="pdf_title")
        with col2:
            st.write("")
            st.write("")
            save_pdf = st.checkbox("Kaydet", key="save_pdf_chk")
            
        if st.button("🚀 Quiz Oluştur (PDF)", use_container_width=True, type="primary"):
            if uploaded_file is None:
                st.error("Lütfen bir PDF dosyası yükleyin!")
            else:
                with st.spinner("PDF okunuyor ve sorular hazırlanıyor..."):
                    try:
                        pdf_text = extract_text_from_pdf(uploaded_file)
                        if not pdf_text:
                            st.error("PDF'den metin çıkarılamadı. Dosya taranmış veya resim formatında olabilir.")
                            st.stop()
                            
                        # If the text is extremely long, Gemini might truncate or timeout.
                        # It's better to limit the AI request text length. Let's cap at approx 40,000 characters.
                        if len(pdf_text) > 40000:
                            pdf_text = pdf_text[:40000]
                            st.warning("⚠️ PDF çok uzundu, ilk kısmı baz alınarak quiz oluşturuldu.")
                            
                        if save_pdf and pdf_title:
                            # We save up to the first 5000 chars to avoid huge DB bloating per user action, or fully since it's sqlite.
                            save_source(pdf_title, pdf_text[:10000], "pdf")
                            
                        pairs = generate_quiz_pairs(pdf_text)
                        
                        st.session_state.current_source_content = pdf_text
                        st.session_state.current_source_type = "pdf"
                        
                        st.session_state.pairs = pairs
                        st.session_state.shuffled_concepts = random.sample([p["concept"] for p in pairs], len(pairs))
                        st.session_state.shuffled_meanings = random.sample([p["meaning"] for p in pairs], len(pairs))
                        
                        st.session_state.game_active = True
                        st.rerun()
                    except Exception as e:
                        st.error(f"Hata oluştu: {str(e)}")

# --- THE GAME SCREEN ---
else:
    # Game Header
    col1, col2, col3 = st.columns([1, 2, 1])
    with col1:
        st.metric("Doğru Eşleşme", f"{st.session_state.score}")
    with col2:
        st.write("")
    with col3:
        if st.button("🔙 Menüye Dön"):
            st.session_state.game_active = False
            st.session_state.selected_concept = None
            st.session_state.selected_meaning = None
            st.session_state.matched_pairs = []
            st.session_state.score = 0
            st.rerun()

    st.markdown("---")
    st.subheader("Doğru çiftleri bulmaya çalışın!")

    # Check for win condition
    if len(st.session_state.matched_pairs) == len(st.session_state.pairs) and len(st.session_state.pairs) > 0:
        st.success("Tebrikler! Tüm çiftleri buldunuz! 🎉 Yeni sorular yükleniyor...")
        
        if st.button("Yeni Soruları Getir", type="primary"):
             with st.spinner("Yenileri hazırlanıyor..."):
                try:
                    new_pairs = generate_quiz_pairs(st.session_state.current_source_content)
                    st.session_state.pairs = new_pairs
                    st.session_state.shuffled_concepts = random.sample([p["concept"] for p in new_pairs], len(new_pairs))
                    st.session_state.shuffled_meanings = random.sample([p["meaning"] for p in new_pairs], len(new_pairs))
                    st.session_state.matched_pairs = []
                    st.session_state.selected_concept = None
                    st.session_state.selected_meaning = None
                    st.rerun()
                except Exception as e:
                    st.error("Hata oluştu!")

    else:
        # Match Logic
        if st.session_state.selected_concept and st.session_state.selected_meaning:
            # Check if they match in original pairs
            match_found = False
            for p in st.session_state.pairs:
                if p["concept"] == st.session_state.selected_concept and p["meaning"] == st.session_state.selected_meaning:
                    match_found = True
                    st.session_state.matched_pairs.append(st.session_state.selected_concept)
                    st.session_state.score += 1
                    break
            
            if not match_found:
                st.error(f"❌ '{st.session_state.selected_concept}' ve '{st.session_state.selected_meaning}' eşleşmiyor!")
                
            st.session_state.selected_concept = None
            st.session_state.selected_meaning = None
            st.rerun()

        # Display Grid
        col_c, col_m = st.columns(2)
        
        with col_c:
            st.markdown("### Kavramlar")
            for concept in st.session_state.shuffled_concepts:
                if concept in st.session_state.matched_pairs:
                    st.success(f"✅ {concept}")
                else:
                    btn_type = "primary" if st.session_state.selected_concept == concept else "secondary"
                    if st.button(concept, key=f"c_{concept}", type=btn_type, use_container_width=True):
                        st.session_state.selected_concept = concept if st.session_state.selected_concept != concept else None
                        st.rerun()
                        
        with col_m:
            st.markdown("### Anlamları")
            for meaning in st.session_state.shuffled_meanings:
                # Find if this meaning belongs to a matched concept
                is_matched = False
                for p in st.session_state.pairs:
                    if p["meaning"] == meaning and p["concept"] in st.session_state.matched_pairs:
                        is_matched = True
                        break
                        
                if is_matched:
                    st.success(f"✅ {meaning}")
                else:
                    btn_type = "primary" if st.session_state.selected_meaning == meaning else "secondary"
                    if st.button(meaning, key=f"m_{meaning}", type=btn_type, use_container_width=True):
                        st.session_state.selected_meaning = meaning if st.session_state.selected_meaning != meaning else None
                        st.rerun()
