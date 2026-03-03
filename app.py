"""
🌸 GENERAL PURPOSE AI SMART EXAM PORTAL
This application works for Schools, Colleges, Coaching Centers - Any Educational Institution
"""

import streamlit as st
import google.generativeai as genai
import PIL.Image
import json
import hashlib
import pandas as pd
import re
import os
import time
import io
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
from dotenv import load_dotenv
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.units import inch
import PyPDF2
from pdf2image import convert_from_bytes
import platform
import traceback
from datetime import datetime, timedelta
import random
import string
import pytz

# ============================================
# 1. ENVIRONMENT CONFIGURATION
# ============================================

# Load environment variables
load_dotenv()

# Institution Configuration
INSTITUTION_NAME = "DAFFODILS HIGH SCHOOL"
INSTITUTION_SECRET = os.getenv("INSTITUTION_SECRET", os.getenv("SCHOOL_SECRET", "1234"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", os.getenv("SCHOOL_ADMIN_PASSWORD", "2109"))

# IST Timezone
IST = pytz.timezone('Asia/Kolkata')

# Streamlit Page Config
st.set_page_config(
    page_title=f"📚 DAFFODILS HIGH SCHOOL AI Exam Portal", 
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ============================================
# ✅ SESSION STATE INITIALIZATION - MUST BE FIRST
# ============================================

# Initialize all session state variables
if 'user' not in st.session_state:
    st.session_state.user = None
if 'active_exam' not in st.session_state:
    st.session_state.active_exam = None
if 'shuffled_qs' not in st.session_state:
    st.session_state.shuffled_qs = []
if 'auto_submitted' not in st.session_state:
    st.session_state.auto_submitted = False
if 'exam_answers' not in st.session_state:
    st.session_state.exam_answers = {}
if 'exam_result' not in st.session_state:
    st.session_state.exam_result = None
if 'answer_saved' not in st.session_state:
    st.session_state.answer_saved = {}
if 'last_refresh' not in st.session_state:
    st.session_state.last_refresh = time.time()
if 'dashboard_initialized' not in st.session_state:
    st.session_state.dashboard_initialized = False
if 'exam_timer_started' not in st.session_state:
    st.session_state.exam_timer_started = False
if 'exam_start_time' not in st.session_state:
    st.session_state.exam_start_time = None
if 'exam_submitted' not in st.session_state:
    st.session_state.exam_submitted = False
if 'cache_timestamp' not in st.session_state:
    st.session_state.cache_timestamp = 0
if 'cached_data' not in st.session_state:
    st.session_state.cached_data = {}
if 'exam_questions_loaded' not in st.session_state:
    st.session_state.exam_questions_loaded = False
if 'exam_end_time' not in st.session_state:
    st.session_state.exam_end_time = None
if 'exam_auto_submitted' not in st.session_state:
    st.session_state.exam_auto_submitted = False
if 'timer_initialized' not in st.session_state:
    st.session_state.timer_initialized = False
if 'select_all_state' not in st.session_state:
    st.session_state.select_all_state = False
if 'selected_pending_users' not in st.session_state:
    st.session_state.selected_pending_users = set()
if 'exam_session_id' not in st.session_state:
    st.session_state.exam_session_id = None
if 'exam_logged' not in st.session_state:
    st.session_state.exam_logged = False
if 'processing_submit' not in st.session_state:
    st.session_state.processing_submit = False
if 'show_expired_exams' not in st.session_state:
    st.session_state.show_expired_exams = False
if 'selected_approved_users' not in st.session_state:
    st.session_state.selected_approved_users = set()

# ============================================
# ✅ NEW: CASE-INSENSITIVE NORMALIZATION HELPER
# ============================================

def normalize_text(value):
    """Normalize text for case-insensitive comparison"""
    return value.strip().lower() if value and isinstance(value, str) else ""

# ============================================
# 2. GEMINI AI CONFIGURATION
# ============================================

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    st.error("❌ GEMINI_API_KEY not found in .env file!")
    st.stop()

try:
    genai.configure(api_key=API_KEY)
except Exception as e:
    st.error(f"❌ Failed to configure Gemini API: {e}")
    st.stop()

# ============================================
# ✅ OPTIMIZED: DATABASE CONNECTION POOL
# ============================================

@st.cache_resource
def init_connection_pool():
    """Initialize connection pool with minimal connections for free tier"""
    try:
        DATABASE_URL = os.getenv("NEON_DATABASE_URL")
        if not DATABASE_URL:
            st.error("❌ NEON_DATABASE_URL not found in .env file!")
            return None
        
        if '?' in DATABASE_URL:
            base_url = DATABASE_URL.split('?')[0]
        else:
            base_url = DATABASE_URL
        
        connection_pool = psycopg2.pool.SimpleConnectionPool(
            minconn=1,
            maxconn=5,
            dsn=base_url,
            sslmode='require',
            connect_timeout=10
        )
        return connection_pool
    except Exception as e:
        st.error(f"❌ Database connection failed: {e}")
        return None

connection_pool = init_connection_pool()

# ✅ OPTIMIZED: Reusable execute_query function with caching
def execute_query(query, params=None, fetch=True, commit=False, retry=2, use_cache=False, cache_key=None):
    """Execute query with automatic connection management and optional caching"""
    conn = None
    cur = None
    
    # Check cache first if requested
    if use_cache and cache_key and cache_key in st.session_state.cached_data:
        cache_time, cache_result = st.session_state.cached_data[cache_key]
        if time.time() - cache_time < 30:  # 30 second cache
            return cache_result
    
    if not connection_pool:
        st.error("❌ Database connection pool not initialized")
        return None if fetch else False
    
    for attempt in range(retry):
        try:
            conn = connection_pool.getconn()
            if conn is None:
                if attempt == retry - 1:
                    st.error("❌ Could not get database connection")
                    return None if fetch else False
                time.sleep(0.5)
                continue
                
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(query, params or ())
            
            result = None
            if fetch:
                result = cur.fetchall()
            
            if commit:
                conn.commit()
            
            # Cache result if requested
            if use_cache and cache_key and fetch:
                st.session_state.cached_data[cache_key] = (time.time(), result)
            
            return result
            
        except Exception as e:
            if conn:
                conn.rollback()
            if attempt == retry - 1:
                st.error(f"❌ Database error: {str(e)}")
                return None if fetch else False
            time.sleep(0.1 * (attempt + 1))
            
        finally:
            if cur:
                cur.close()
            if conn:
                try:
                    connection_pool.putconn(conn)
                except:
                    pass

# ============================================
# ✅ OPTIMIZED: Database initialization
# ============================================

def init_database():
    """Create database tables with approval column"""
    if not connection_pool:
        st.warning("⚠️ Database connection not available. Running in limited mode.")
        return
    
    conn = None
    cur = None
    
    try:
        conn = connection_pool.getconn()
        cur = conn.cursor()
        
        # Create tables if they don't exist
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username VARCHAR(255) PRIMARY KEY,
                password VARCHAR(255) NOT NULL,
                role VARCHAR(50) NOT NULL,
                batch_name VARCHAR(255),
                institution_name VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Check if is_approved column exists
        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='users' AND column_name='is_approved'
        """)
        column_exists = cur.fetchone()
        
        if not column_exists:
            cur.execute("""
                ALTER TABLE users 
                ADD COLUMN is_approved BOOLEAN DEFAULT FALSE
            """)
            print("✅ Database updated: Added approval column")
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS teacher_batches (
                teacher_username VARCHAR(255) REFERENCES users(username) ON DELETE CASCADE,
                batch_name VARCHAR(255) NOT NULL,
                institution_name VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (teacher_username, batch_name)
            )
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS exams (
                id SERIAL PRIMARY KEY,
                teacher VARCHAR(255) NOT NULL,
                batch_name VARCHAR(255) NOT NULL,
                subject VARCHAR(255) NOT NULL,
                quiz_json TEXT NOT NULL,
                exam_date DATE,
                start_time TIME,
                end_time TIME,
                institution_name VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS results (
                id SERIAL PRIMARY KEY,
                student VARCHAR(255) NOT NULL,
                exam_id INTEGER NOT NULL,
                score INTEGER NOT NULL,
                total INTEGER NOT NULL,
                subject VARCHAR(255) NOT NULL,
                review_json TEXT NOT NULL,
                institution_name VARCHAR(255) NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS exam_sessions (
                id SERIAL PRIMARY KEY,
                student VARCHAR(255) NOT NULL,
                exam_id INTEGER NOT NULL,
                session_id VARCHAR(100) NOT NULL,
                start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status VARCHAR(50) DEFAULT 'in_progress',
                UNIQUE(student, exam_id)
            )
        """)
        
        conn.commit()
        print("✅ Database initialized successfully")
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ Database Error: {e}")
    finally:
        if cur:
            cur.close()
        if conn:
            try:
                connection_pool.putconn(conn)
            except:
                pass

# Initialize database
try:
    init_database()
except Exception as e:
    st.warning(f"⚠️ Database initialization warning: {e}")

# ============================================
# 5. PASSWORD HASH FUNCTION
# ============================================

def make_hash(password):
    """Hash password using SHA256"""
    if not password:
        return None
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

# ============================================
# 6. PASSWORD GENERATION FUNCTION
# ============================================

def generate_random_password(length=8):
    """Generate a random password"""
    characters = string.ascii_letters + string.digits + "!@#$%"
    return ''.join(random.choice(characters) for i in range(length))

# ============================================
# 7. GEMINI MODEL INITIALIZATION
# ============================================

@st.cache_resource
def get_gemini_model():
    """Initialize Gemini AI model with correct model names - without sidebar messages"""
    try:
        model_names = [
            'models/gemini-1.5-flash-001',
            'models/gemini-1.5-flash',
            'gemini-1.5-flash',
            'models/gemini-pro',
            'gemini-pro'
        ]
        
        for model_name in model_names:
            try:
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(
                    "Say 'OK'", 
                    generation_config={
                        "max_output_tokens": 10,
                        "temperature": 0.1
                    }
                )
                if response and hasattr(response, 'text'):
                    return model
            except Exception as e:
                continue
        
        models = genai.list_models()
        available_models = []
        for m in models:
            if hasattr(m, 'supported_generation_methods') and 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
        
        if available_models:
            return genai.GenerativeModel(available_models[0])
        else:
            st.error("❌ No suitable Gemini models found")
            return None
            
    except Exception as e:
        st.error(f"❌ Gemini AI Error: {str(e)}")
        return None

gemini_model = get_gemini_model()

# ============================================
# 8. PDF GENERATION FUNCTIONS
# ============================================

def register_indic_fonts():
    """Register fonts for Indian languages to fix box issue"""
    registered_fonts = {'telugu': None, 'hindi': None, 'fallback': 'Helvetica'}
    
    if platform.system() == 'Windows':
        windows_fonts_dir = "C:\\Windows\\Fonts"
        
        font_candidates = [
            {'file': 'gautami.ttf', 'name': 'Gautami', 'lang': 'telugu'},
            {'file': 'GautamiB.ttf', 'name': 'GautamiB', 'lang': 'telugu'},
            {'file': 'Nirmala.ttf', 'name': 'Nirmala', 'lang': 'both'},
            {'file': 'NirmalaB.ttf', 'name': 'NirmalaB', 'lang': 'both'},
            {'file': 'mangal.ttf', 'name': 'Mangal', 'lang': 'hindi'},
            {'file': 'MangalB.ttf', 'name': 'MangalB', 'lang': 'hindi'},
            {'file': 'arial.ttf', 'name': 'Arial', 'lang': 'fallback'},
        ]
        
        for candidate in font_candidates:
            font_path = os.path.join(windows_fonts_dir, candidate['file'])
            if os.path.exists(font_path):
                try:
                    font_name = candidate['name']
                    pdfmetrics.registerFont(TTFont(font_name, font_path))
                    
                    if candidate['lang'] in ['telugu', 'both'] and registered_fonts['telugu'] is None:
                        registered_fonts['telugu'] = font_name
                    
                    if candidate['lang'] in ['hindi', 'both'] and registered_fonts['hindi'] is None:
                        registered_fonts['hindi'] = font_name
                    
                    if candidate['lang'] == 'fallback' and registered_fonts['fallback'] == 'Helvetica':
                        registered_fonts['fallback'] = font_name
                        
                except Exception as e:
                    continue
    
    return registered_fonts

INDIC_FONTS = register_indic_fonts()

def is_telugu_text(text):
    if not isinstance(text, str):
        return False
    telugu_pattern = re.compile(r'[\u0C00-\u0C7F]')
    return bool(telugu_pattern.search(text))

def is_hindi_text(text):
    if not isinstance(text, str):
        return False
    hindi_pattern = re.compile(r'[\u0900-\u097F]')
    return bool(hindi_pattern.search(text))

def get_appropriate_font(text):
    if not isinstance(text, str):
        return 'Helvetica'
    
    if is_telugu_text(text):
        return INDIC_FONTS.get('telugu', 'Helvetica') or 'Helvetica'
    elif is_hindi_text(text):
        return INDIC_FONTS.get('hindi', 'Helvetica') or 'Helvetica'
    else:
        return 'Helvetica'

class MultiLingualParagraph(Paragraph):
    def __init__(self, text, style, **kw):
        font_name = get_appropriate_font(text)
        
        custom_style = ParagraphStyle(
            style.name + '_custom',
            parent=style,
            fontName=font_name,
            wordWrap='CJK',
            encoding='utf-8'
        )
        
        if isinstance(text, str):
            text = re.sub(r'■', '', text)
            text = text.encode('utf-8', 'ignore').decode('utf-8')
        
        super().__init__(text, custom_style, **kw)

def generate_student_pdf(student_name, exam_subject, score, total, review_data, batch_name="", institution_name=INSTITUTION_NAME):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    
    elements = []
    
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=20,
        textColor=colors.HexColor('#FF6B35'),
        alignment=1,
        spaceAfter=20,
        fontName='Helvetica-Bold'
    )
    
    elements.append(MultiLingualParagraph(f"{institution_name}", title_style))
    elements.append(MultiLingualParagraph(f"Student Exam Report", styles['Heading2']))
    elements.append(Spacer(1, 10))
    
    details = [
        ["Student Name:", student_name],
        ["Batch/Class:", batch_name],
        ["Subject:", exam_subject],
        ["Date:", datetime.now().strftime('%Y-%m-%d %H:%M')],
        ["Score:", f"{score}/{total} ({int((score/total)*100)}%)"]
    ]
    
    detail_table = Table(details, colWidths=[1.5*inch, 4*inch])
    detail_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 11),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
    ]))
    elements.append(detail_table)
    elements.append(Spacer(1, 20))
    
    for i, item in enumerate(review_data, 1):
        q_style = ParagraphStyle(
            'Question',
            parent=styles['Normal'],
            fontSize=10,
            leading=14,
            spaceAfter=6,
            fontName='Helvetica'
        )
        
        elements.append(MultiLingualParagraph(f"<b>Q{i}:</b> {item['question']}", q_style))
        
        if item.get('options') and len(item['options']) > 0:
            options_text = "<b>Options:</b> "
            for idx, opt in enumerate(item['options']):
                options_text += f"{chr(65+idx)}. {opt}  "
            elements.append(MultiLingualParagraph(options_text, q_style))
        
        is_correct = str(item['user_ans']).lower().strip() == str(item['correct_ans']).lower().strip()
        answer_color = 'green' if is_correct else 'red'
        
        user_answer_display = item['user_ans'] if item['user_ans'] and str(item['user_ans']).strip() != "" else "Not Attempted"
        if user_answer_display == "Not Attempted":
            answer_color = 'red'
        
        elements.append(MultiLingualParagraph(f"<b>Your Answer:</b> <font color='{answer_color}'>{user_answer_display}</font>", q_style))
        elements.append(MultiLingualParagraph(f"<b>Correct Answer:</b> <font color='green'>{item['correct_ans']}</font>", q_style))
        elements.append(MultiLingualParagraph(f"<b>Explanation:</b> {item['explanation']}", q_style))
        elements.append(MultiLingualParagraph(f"<b>Status:</b> {'✓ Correct' if is_correct else '✗ Incorrect' if item['user_ans'] and str(item['user_ans']).strip() != '' else '✗ Not Attempted'}", q_style))
        elements.append(Spacer(1, 10))
    
    footer_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontSize=8,
        textColor=colors.grey,
        alignment=1
    )
    elements.append(Spacer(1, 20))
    elements.append(MultiLingualParagraph("This is a computer-generated report. No signature required.", footer_style))
    elements.append(MultiLingualParagraph(f"Generated on: {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}", footer_style))
    
    doc.build(elements)
    return buffer.getvalue()

def generate_teacher_pdf(teacher_name, class_name, subject, results_data, institution_name=INSTITUTION_NAME):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    
    elements = []
    
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=20,
        textColor=colors.HexColor('#FF6B35'),
        alignment=1,
        spaceAfter=20,
        fontName='Helvetica-Bold'
    )
    
    elements.append(MultiLingualParagraph(f"{institution_name}", title_style))
    elements.append(MultiLingualParagraph(f"Class Performance Report", styles['Heading2']))
    elements.append(Spacer(1, 10))
    
    details = [
        ["Teacher:", teacher_name],
        ["Class/Batch:", class_name],
        ["Subject:", subject],
        ["Report Date:", datetime.now().strftime('%Y-%m-%d %H:%M')],
        ["Total Students:", str(len(results_data))]
    ]
    
    detail_table = Table(details, colWidths=[1.5*inch, 4*inch])
    detail_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 11),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
    ]))
    elements.append(detail_table)
    elements.append(Spacer(1, 20))
    
    if results_data:
        table_data = [["S.No", "Student Name", "Subject", "Score", "Total", "Percentage", "Date"]]
        
        for idx, row in enumerate(results_data, 1):
            percentage = int((row['score']/row['total'])*100)
            table_data.append([
                str(idx),
                row['student'],
                row['subject'],
                str(row['score']),
                str(row['total']),
                f"{percentage}%",
                row['date'][:10] if row['date'] else "N/A"
            ])
        
        results_table = Table(table_data, colWidths=[0.4*inch, 1.2*inch, 1*inch, 0.6*inch, 0.6*inch, 0.7*inch, 1*inch])
        results_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
        ]))
        elements.append(results_table)
        
        avg_score = sum(r['score'] for r in results_data) / len(results_data)
        avg_percentage = int((avg_score / results_data[0]['total']) * 100)
        
        elements.append(Spacer(1, 20))
        elements.append(MultiLingualParagraph(f"<b>Class Average:</b> {avg_percentage}%", styles['Normal']))
    
    footer_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontSize=8,
        textColor=colors.grey,
        alignment=1
    )
    elements.append(Spacer(1, 20))
    elements.append(MultiLingualParagraph("This is a computer-generated report.", footer_style))
    
    doc.build(elements)
    return buffer.getvalue()

def generate_exam_pdf(teacher_name, batch_name, subject, questions_data, institution_name=INSTITUTION_NAME):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    
    elements = []
    
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=20,
        textColor=colors.HexColor('#FF6B35'),
        alignment=1,
        spaceAfter=20,
        fontName='Helvetica-Bold'
    )
    
    elements.append(MultiLingualParagraph(f"{institution_name}", title_style))
    elements.append(MultiLingualParagraph(f"Exam Questions - {subject}", styles['Heading2']))
    elements.append(Spacer(1, 10))
    
    details = [
        ["Teacher:", teacher_name],
        ["Batch/Class:", batch_name],
        ["Subject:", subject],
        ["Generated on:", datetime.now().strftime('%Y-%m-%d %H:%M')],
        ["Total Questions:", str(len(questions_data))]
    ]
    
    detail_table = Table(details, colWidths=[1.5*inch, 4*inch])
    detail_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 11),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
    ]))
    elements.append(detail_table)
    elements.append(Spacer(1, 20))
    
    for i, q in enumerate(questions_data, 1):
        q_style = ParagraphStyle(
            'Question',
            parent=styles['Normal'],
            fontSize=11,
            leading=14,
            spaceAfter=6,
            fontName='Helvetica'
        )
        
        elements.append(MultiLingualParagraph(f"<b>Q{i}:</b> {q.get('question', 'N/A')}", q_style))
        
        if q.get('options') and len(q['options']) > 0:
            options_text = "<b>Options:</b> "
            for idx, opt in enumerate(q['options']):
                options_text += f"{chr(65+idx)}. {opt}  "
            elements.append(MultiLingualParagraph(options_text, q_style))
        
        elements.append(MultiLingualParagraph(f"<b>Answer:</b> <font color='green'>{q.get('answer', 'N/A')}</font>", q_style))
        if q.get('explanation'):
            elements.append(MultiLingualParagraph(f"<b>Explanation:</b> {q['explanation']}", q_style))
        
        elements.append(Spacer(1, 15))
    
    footer_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontSize=8,
        textColor=colors.grey,
        alignment=1
    )
    elements.append(Spacer(1, 20))
    elements.append(MultiLingualParagraph("This is a computer-generated question paper.", footer_style))
    
    doc.build(elements)
    return buffer.getvalue()

# ============================================
# 9. PDF UTILITY FUNCTIONS
# ============================================

def extract_text_from_pdf(pdf_bytes):
    try:
        pdf_file = io.BytesIO(pdf_bytes)
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text() or ""
        return text
    except:
        return ""

def convert_pdf_to_images(pdf_bytes):
    try:
        return convert_from_bytes(pdf_bytes)
    except:
        return []

def process_uploaded_files(files):
    all_content = []
    all_text = ""
    all_images = []
    
    for idx, file in enumerate(files):
        try:
            st.info(f"Processing file {idx+1}: {file.name}")
            
            if file.type == "application/pdf":
                pdf_text = extract_text_from_pdf(file.read())
                file.seek(0)
                
                if pdf_text and len(pdf_text.strip()) > 100:
                    all_text += f"\n\n--- Content from PDF {idx+1}: {file.name} ---\n{pdf_text}"
                    st.success(f"✅ Extracted text from {file.name}")
                else:
                    images = convert_pdf_to_images(file.read())
                    file.seek(0)
                    if images:
                        all_images.extend(images[:2])
                        st.success(f"✅ Converted {file.name} to {len(images[:2])} images")
            else:
                img = PIL.Image.open(file)
                all_images.append(img)
                st.success(f"✅ Loaded image: {file.name}")
                
        except Exception as e:
            st.warning(f"⚠️ Could not process file {file.name}: {str(e)}")
    
    if all_text:
        all_content.append(all_text)
    if all_images:
        all_content.extend(all_images)
    
    return all_content

# ============================================
# ✅ OPTIMIZED: Caching helper functions with case-insensitive updates
# ============================================

@st.cache_data(ttl=60, max_entries=10)
def get_cached_users(institution_name):
    return execute_query(
        "SELECT username, role, batch_name, is_approved, created_at FROM users WHERE LOWER(institution_name)=LOWER(%s) ORDER BY created_at DESC",
        (institution_name,)
    )

@st.cache_data(ttl=60, max_entries=10)
def get_cached_teachers(institution_name):
    return execute_query(
        "SELECT username, role, batch_name, is_approved, created_at FROM users WHERE LOWER(institution_name)=LOWER(%s) AND LOWER(role)='teacher' ORDER BY created_at DESC",
        (institution_name,)
    )

@st.cache_data(ttl=60, max_entries=10)
def get_cached_students(institution_name):
    return execute_query(
        "SELECT username, role, batch_name, is_approved, created_at FROM users WHERE LOWER(institution_name)=LOWER(%s) AND LOWER(role)='student' ORDER BY created_at DESC",
        (institution_name,)
    )

@st.cache_data(ttl=120, max_entries=20)
def get_cached_exams(teacher_name, institution_name):
    return execute_query(
        "SELECT * FROM exams WHERE LOWER(teacher)=LOWER(%s) AND LOWER(institution_name)=LOWER(%s) ORDER BY created_at DESC",
        (teacher_name, institution_name)
    )

@st.cache_data(ttl=60, max_entries=10)
def get_cached_batches(teacher_name, institution_name):
    return execute_query(
        "SELECT batch_name FROM teacher_batches WHERE LOWER(teacher_username)=LOWER(%s) AND LOWER(institution_name)=LOWER(%s)",
        (teacher_name, institution_name)
    )

# ✅ OPTIMIZED: Student exams with case-insensitive batch matching
def get_student_exams(batch_name, institution_name):
    """Get exams for student with caching and case-insensitive batch matching"""
    cache_key = f"student_exams_{normalize_text(batch_name)}_{normalize_text(institution_name)}"
    today = datetime.now().date()
    
    result = execute_query(
        "SELECT * FROM exams WHERE LOWER(batch_name)=LOWER(%s) AND LOWER(institution_name)=LOWER(%s) AND exam_date >= %s ORDER BY exam_date, start_time",
        (batch_name, institution_name, today),
        use_cache=True,
        cache_key=cache_key
    )
    return result if result else []

# ✅ FIXED: Get student results with case-insensitive matching
def get_student_results(student_name, institution_name):
    """Get student results with caching and case-insensitive matching"""
    cache_key = f"student_results_{normalize_text(student_name)}_{normalize_text(institution_name)}"
    
    result = execute_query(
        "SELECT * FROM results WHERE LOWER(student)=LOWER(%s) AND LOWER(institution_name)=LOWER(%s) ORDER BY timestamp DESC",
        (student_name, institution_name),
        use_cache=True,
        cache_key=cache_key
    )
    return result if result else []

# ✅ FIXED: Get attempted exam IDs with case-insensitive student name
def get_attempted_exam_ids(student_name):
    """Get set of exam IDs that student has already taken with case-insensitive matching"""
    cache_key = f"attempted_{normalize_text(student_name)}"
    
    result = execute_query(
        "SELECT exam_id FROM results WHERE LOWER(student)=LOWER(%s)",
        (student_name,),
        use_cache=True,
        cache_key=cache_key
    )
    return set(row['exam_id'] for row in result) if result else set()

def clear_cache():
    st.cache_data.clear()
    st.session_state.cache_timestamp = time.time()
    st.session_state.cached_data = {}

# ============================================
# ✅ FIXED: Timer Management with Power Failure Handling
# ============================================

def log_exam_session(student_name, exam_id):
    """Log exam session for power failure recovery with case-insensitive student name"""
    if st.session_state.exam_logged:
        return
    
    session_id = f"{student_name}_{exam_id}_{int(time.time())}"
    
    execute_query(
        """
        INSERT INTO exam_sessions (student, exam_id, session_id, status)
        VALUES (%s, %s, %s, 'in_progress')
        ON CONFLICT (student, exam_id) 
        DO UPDATE SET session_id = EXCLUDED.session_id, last_active = CURRENT_TIMESTAMP, status = 'in_progress'
        """,
        (student_name, exam_id, session_id),
        fetch=False,
        commit=True
    )
    
    st.session_state.exam_session_id = session_id
    st.session_state.exam_logged = True

def check_exam_session(student_name, exam_id):
    """Check if exam session exists and is valid with case-insensitive student name"""
    result = execute_query(
        "SELECT status FROM exam_sessions WHERE LOWER(student)=LOWER(%s) AND exam_id=%s",
        (student_name, exam_id)
    )
    return result[0]['status'] if result else None

def complete_exam_session(student_name, exam_id):
    """Mark exam session as completed with case-insensitive student name"""
    execute_query(
        "UPDATE exam_sessions SET status='completed' WHERE LOWER(student)=LOWER(%s) AND exam_id=%s",
        (student_name, exam_id),
        fetch=False,
        commit=True
    )

def auto_submit_exam():
    """
    Core auto-submit logic with power failure handling
    Returns True if auto-submit was triggered, False otherwise
    """
    if not st.session_state.active_exam or st.session_state.exam_auto_submitted or st.session_state.exam_result:
        return False
    
    if st.session_state.exam_end_time is None:
        return False
    
    remaining_seconds = int(st.session_state.exam_end_time - time.time())
    if remaining_seconds > 0:
        return False
    
    # Prevent duplicate processing
    if st.session_state.processing_submit:
        return True
    
    st.session_state.processing_submit = True
    
    try:
        # Check if result already exists (prevents duplicate on power failure) with case-insensitive matching
        existing = execute_query(
            "SELECT id FROM results WHERE LOWER(student)=LOWER(%s) AND exam_id=%s",
            (st.session_state.user['name'], st.session_state.active_exam['id'])
        )
        
        if existing:
            # Result already exists - just load it
            result_data = execute_query(
                "SELECT * FROM results WHERE LOWER(student)=LOWER(%s) AND exam_id=%s",
                (st.session_state.user['name'], st.session_state.active_exam['id'])
            )[0]
            
            st.session_state.exam_result = {
                "score": result_data['score'],
                "total": result_data['total'],
                "subject": result_data['subject'],
                "review": json.loads(result_data['review_json'])
            }
            
            st.session_state.exam_auto_submitted = True
            return True
        
        st.session_state.exam_auto_submitted = True
        
        score = 0
        review = []
        
        for i, q in enumerate(st.session_state.shuffled_qs):
            user_ans = str(st.session_state.exam_answers.get(str(i), "")).strip()
            correct_ans = str(q.get('answer', '')).strip()
            
            if user_ans and user_ans != "" and user_ans.lower() == correct_ans.lower():
                score += 1
            
            review.append({
                "question": q['question'],
                "user_ans": user_ans if user_ans else "",
                "correct_ans": correct_ans,
                "options": q.get('options', []),
                "explanation": q.get('explanation', 'No explanation available')
            })
        
        execute_query(
            "INSERT INTO results (student, exam_id, score, total, subject, review_json, institution_name) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (st.session_state.user['name'], st.session_state.active_exam['id'], score, len(review), 
             st.session_state.active_exam['subject'], json.dumps(review), INSTITUTION_NAME),
            fetch=False,
            commit=True
        )
        
        # Mark session as completed
        complete_exam_session(st.session_state.user['name'], st.session_state.active_exam['id'])
        
        # Clear cache for this student
        cache_key = f"attempted_{normalize_text(st.session_state.user['name'])}"
        if cache_key in st.session_state.cached_data:
            del st.session_state.cached_data[cache_key]
        
        st.session_state.exam_result = {
            "score": score,
            "total": len(review),
            "subject": st.session_state.active_exam['subject'],
            "review": review
        }
        
        return True
    finally:
        st.session_state.processing_submit = False

def initialize_exam_timer():
    if st.session_state.exam_end_time is not None or st.session_state.exam_auto_submitted:
        return
    
    exam = st.session_state.active_exam
    exam_duration = 3600
    
    if exam.get('start_time') and exam.get('end_time') and exam.get('exam_date'):
        try:
            if isinstance(exam['exam_date'], str):
                exam_date = datetime.strptime(exam['exam_date'], '%Y-%m-%d').date()
            else:
                exam_date = exam['exam_date']
            
            if isinstance(exam['end_time'], str):
                end_time = datetime.strptime(exam['end_time'], '%H:%M:%S').time()
            else:
                end_time = exam['end_time']
            
            end_datetime = datetime.combine(exam_date, end_time)
            # Localize to IST
            end_datetime = IST.localize(end_datetime)
            st.session_state.exam_end_time = end_datetime.timestamp()
        except Exception as e:
            st.session_state.exam_end_time = time.time() + exam_duration
    else:
        st.session_state.exam_end_time = time.time() + exam_duration

# ============================================
# ✅ NEW: Time input dropdown function for IST
# ============================================

def get_time_from_dropdown(hour, minute, am_pm):
    """Convert dropdown selections to time object in IST"""
    try:
        time_str = f"{hour}:{minute:02d} {am_pm}"
        time_obj = datetime.strptime(time_str, "%I:%M %p").time()
        return time_obj
    except Exception as e:
        return None

# ============================================
# 10. STYLING
# ============================================

st.markdown("""
    <style>
    .main-header {
        font-size: 42px; 
        font-weight: bold; 
        color: #FF6B35; 
        text-align: center; 
        margin-top: -20px;
    }
    .institution-name {
        font-size: 24px;
        color: #4ECDC4;
        text-align: center;
        margin-bottom: 20px;
        font-weight: bold;
    }
    .sub-header {
        font-size: 18px; 
        color: #1E3A8A; 
        text-align: center; 
        margin-bottom: 20px;
    }
    .footer {
        position: fixed; 
        left: 0; 
        bottom: 0; 
        width: 100%; 
        background: linear-gradient(135deg, #667eea 0%, #764ba2 50%, #ff6b35 100%);
        color: white; 
        text-align: center; 
        padding: 12px; 
        font-size: 15px; 
        font-weight: 500;
        z-index: 100;
        border-top: 2px solid #FFD700;
        box-shadow: 0 -4px 10px rgba(0,0,0,0.2);
    }
    .exam-info {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 20px;
        border-radius: 15px;
        margin-bottom: 20px;
    }
    .timer-box {
        font-size: 32px; 
        font-weight: bold; 
        color: #FF6B35; 
        text-align: center; 
        padding: 20px; 
        border-radius: 15px; 
        background: linear-gradient(135deg, #fff5f0 0%, #ffe6d5 100%);
        margin: 20px 0; 
        border: 2px solid #FF6B35;
    }
    .countdown-box {
        font-size: 28px; 
        font-weight: bold; 
        color: #1976d2; 
        text-align: center; 
        padding: 20px; 
        border-radius: 15px; 
        background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
        margin: 15px 0; 
        border: 2px solid #1976d2;
    }
    .success-message {
        background: linear-gradient(135deg, #84fab0 0%, #8fd3f4 100%);
        color: #155724;
        padding: 20px;
        border-radius: 15px;
        margin: 20px 0;
        font-weight: bold;
        text-align: center;
    }
    .info-box {
        background-color: #e3f2fd;
        padding: 15px;
        border-radius: 10px;
        margin-bottom: 15px;
        border-left: 5px solid #1976d2;
    }
    .review-box {
        background-color: #f8f9fa;
        padding: 15px;
        border-radius: 10px;
        margin-bottom: 10px;
        border-left: 5px solid #28a745;
    }
    .review-box-incorrect {
        background-color: #f8f9fa;
        padding: 15px;
        border-radius: 10px;
        margin-bottom: 10px;
        border-left: 5px solid #dc3545;
    }
    .upcoming-exam {
        background-color: #e3f2fd;
        padding: 15px;
        border-radius: 10px;
        margin-bottom: 10px;
        border-left: 5px solid #1976d2;
    }
    .available-exam {
        background-color: #f1f8e9;
        padding: 15px;
        border-radius: 10px;
        margin-bottom: 10px;
        border-left: 5px solid #4caf50;
    }
    .soon-exam {
        background-color: #fff3e0;
        padding: 15px;
        border-radius: 10px;
        margin-bottom: 10px;
        border-left: 5px solid #ff9800;
    }
    .expired-exam {
        background-color: #ffebee;
        padding: 15px;
        border-radius: 10px;
        margin-bottom: 10px;
        border-left: 5px solid #f44336;
        opacity: 0.8;
    }
    .completed-exam {
        background-color: #e8f5e8;
        padding: 15px;
        border-radius: 10px;
        margin-bottom: 10px;
        border-left: 5px solid #4caf50;
    }
    .start-button {
        background-color: #4CAF50;
        color: white;
        padding: 10px 20px;
        border-radius: 5px;
        border: none;
        cursor: pointer;
        font-size: 16px;
        font-weight: bold;
        width: 100%;
    }
    .start-button:hover {
        background-color: #45a049;
    }
    .start-button:disabled {
        background-color: #cccccc;
        cursor: not-allowed;
    }
    .scrollable-content {
        max-height: 500px;
        overflow-y: auto;
        padding-right: 10px;
        scrollbar-width: thin;
        scrollbar-color: #FF6B35 #f1f1f1;
    }
    .scrollable-content::-webkit-scrollbar {
        width: 8px;
    }
    .scrollable-content::-webkit-scrollbar-track {
        background: #f1f1f1;
        border-radius: 10px;
    }
    .scrollable-content::-webkit-scrollbar-thumb {
        background: #FF6B35;
        border-radius: 10px;
    }
    .scrollable-content::-webkit-scrollbar-thumb:hover {
        background: #ff8c42;
    }
    .support-message {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 15px;
        border-radius: 10px;
        margin-top: 20px;
        text-align: center;
        font-size: 16px;
        border: 1px solid #FFD700;
    }
    .support-message a {
        color: #FFD700;
        text-decoration: none;
        font-weight: bold;
    }
    .support-message a:hover {
        text-decoration: underline;
    }
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .users-table {
        font-size: 14px;
        margin-bottom: 20px;
    }
    .password-info {
        background-color: #fff3cd;
        border-left: 5px solid #ffc107;
        padding: 10px;
        margin: 10px 0;
        border-radius: 5px;
    }
    .approval-badge-approved {
        background-color: #d4edda;
        color: #155724;
        padding: 5px 10px;
        border-radius: 20px;
        font-weight: bold;
        display: inline-block;
    }
    .approval-badge-pending {
        background-color: #fff3cd;
        color: #856404;
        padding: 5px 10px;
        border-radius: 20px;
        font-weight: bold;
        display: inline-block;
    }
    .teacher-table {
        background-color: #e3f2fd;
        padding: 15px;
        border-radius: 10px;
        margin-bottom: 20px;
    }
    .student-table {
        background-color: #f1f8e9;
        padding: 15px;
        border-radius: 10px;
        margin-bottom: 20px;
    }
    .selection-box {
        background-color: #fff;
        border: 2px solid #FF6B35;
        border-radius: 10px;
        padding: 15px;
        margin: 10px 0;
    }
    .batch-edit-box {
        background-color: #e8f4fd;
        border-left: 5px solid #2196f3;
        padding: 15px;
        border-radius: 10px;
        margin: 15px 0;
    }
    .expired-exam-tick {
        color: #4caf50;
        font-weight: bold;
        margin-left: 10px;
    }
    .time-dropdown-container {
        background-color: #f8f9fa;
        padding: 15px;
        border-radius: 10px;
        border: 1px solid #dee2e6;
        margin-bottom: 10px;
    }
    </style>
    """, unsafe_allow_html=True)

# Display Header
st.markdown(f'<p class="main-header">📚 AI SMART EXAM PORTAL</p>', unsafe_allow_html=True)
st.markdown(f'<p class="institution-name">🏫 DAFFODILS HIGH SCHOOL</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-header">AI-Powered Examination System for All Educational Institutions</p>', unsafe_allow_html=True)

# ============================================
# 11. UTILITY FUNCTIONS
# ============================================

def parse_time_input(time_str):
    if not time_str:
        return None
    try:
        return datetime.strptime(time_str.strip(), '%I:%M %p').time()
    except:
        try:
            return datetime.strptime(time_str.strip(), '%H:%M').time()
        except:
            return None

def format_time(time_obj):
    if not time_obj:
        return "Not set"
    try:
        if isinstance(time_obj, str):
            return time_obj
        return time_obj.strftime('%I:%M %p')
    except:
        return str(time_obj)

def format_timestamp(ts):
    if ts is None:
        return "Unknown date"
    try:
        if isinstance(ts, str):
            return ts[:10]
        if hasattr(ts, 'strftime'):
            return ts.strftime('%Y-%m-%d')
        return str(ts)[:10]
    except:
        return "Unknown date"

def get_time_remaining_seconds(target_datetime):
    now = datetime.now(IST)
    if target_datetime > now:
        return int((target_datetime - now).total_seconds())
    return 0

# ✅ FIXED: Check if exam is expired using IST
def is_exam_expired(exam):
    """Check if exam end time has passed using IST"""
    try:
        if isinstance(exam['exam_date'], str):
            exam_date = datetime.strptime(exam['exam_date'], '%Y-%m-%d').date()
        else:
            exam_date = exam['exam_date']
        
        if isinstance(exam['end_time'], str):
            end_time = datetime.strptime(exam['end_time'], '%H:%M:%S').time()
        else:
            end_time = exam['end_time']
        
        end_datetime = datetime.combine(exam_date, end_time)
        end_datetime = IST.localize(end_datetime)
        return datetime.now(IST) > end_datetime
    except:
        return False

# ============================================
# 13. LOGIN PAGE (with case-insensitive username)
# ============================================

if st.session_state.user is None:
    
    st.markdown(f"""
        <div style="text-align: center; padding: 30px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 20px; margin-bottom: 30px;">
            <h1 style="color: white; font-size: 48px;">📚 DAFFODILS HIGH SCHOOL</h1>
            <p style="color: white; font-size: 20px;">Welcome to AI-Powered Smart Examination Portal</p>
        </div>
    """, unsafe_allow_html=True)
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.image("https://images.pexels.com/photos/5905700/pexels-photo-5905700.jpeg?auto=compress&cs=tinysrgb&w=1260&h=750&dpr=1", 
                 use_container_width=True)
    
    with col2:
        tab_login, tab_admin = st.tabs(["🔐", "👑"])
        
        with tab_login:
            st.markdown('<div class="scrollable-content">', unsafe_allow_html=True)
            
            st.markdown(f"### 📝 Login to DAFFODILS HIGH SCHOOL")
            mode = st.radio("Select Action", ["Login", "New Registration"], horizontal=True, key="login_mode")
            
            u_name = st.text_input("👤 Username", key="login_username")
            u_pass = st.text_input("🔑 Password", type='password', key="login_password")
            
            if mode == "New Registration":
                st.info(f"🏫 Institution: DAFFODILS HIGH SCHOOL")
                secret_code = st.text_input("🔐 Institution Secret Code", type="password", key="reg_secret")
                
                role = st.selectbox("👔 Role", ["Teacher", "Student"], key="reg_role")
                
                if role == "Student":
                    batch_name = st.text_input(
                        "📚 Your Batch/Class/Course", 
                        placeholder="Examples: 1, 2, 3, 10A, 10B, MPC, BPC",
                        key="reg_batch"
                    )
                else:
                    st.markdown("""
                    <div class="info-box">
                    👨‍🏫 Enter batches/classes you teach (comma separated)<br>
                    Examples: 1,2,3,4,5 or 10A,10B,MPC,BPC
                    </div>
                    """, unsafe_allow_html=True)
                    teacher_batches = st.text_input("🏫 Your Batches/Classes", placeholder="e.g., 1,2,3,4,5", key="reg_teacher_batches")
                
                if st.button("📝 Register New Account", use_container_width=True, key="register_btn"):
                    if secret_code != INSTITUTION_SECRET:
                        st.error("❌ Invalid Secret Code!")
                    elif role == "Student" and not batch_name:
                        st.error("❌ Batch/Class name is required")
                    elif role == "Teacher" and not teacher_batches:
                        st.error("❌ At least one batch/class is required")
                    else:
                        try:
                            # Check if username already exists (case-insensitive)
                            existing_user = execute_query(
                                "SELECT username FROM users WHERE LOWER(username)=LOWER(%s) AND LOWER(institution_name)=LOWER(%s)",
                                (u_name, INSTITUTION_NAME)
                            )
                            
                            if existing_user:
                                st.error("❌ Username already exists (case-insensitive)!")
                                st.stop()
                            
                            hashed_password = make_hash(u_pass)
                            
                            if role == "Student":
                                # Check if batch already exists for any teacher (case-insensitive)
                                # This is just for information, not blocking
                                existing_batch = execute_query(
                                    "SELECT batch_name FROM teacher_batches WHERE LOWER(batch_name)=LOWER(%s) AND LOWER(institution_name)=LOWER(%s)",
                                    (batch_name, INSTITUTION_NAME)
                                )
                                
                                if existing_batch:
                                    st.info(f"ℹ️ Note: Batch/Class '{batch_name}' already exists (case-insensitive).")
                                
                                execute_query(
                                    'INSERT INTO users (username, password, role, batch_name, institution_name, is_approved) VALUES (%s, %s, %s, %s, %s, %s)',
                                    (u_name, hashed_password, role, batch_name.strip(), INSTITUTION_NAME, False),
                                    fetch=False,
                                    commit=True
                                )
                            else:
                                execute_query(
                                    'INSERT INTO users (username, password, role, batch_name, institution_name, is_approved) VALUES (%s, %s, %s, %s, %s, %s)',
                                    (u_name, hashed_password, role, None, INSTITUTION_NAME, False),
                                    fetch=False,
                                    commit=True
                                )
                                
                                # Check for duplicate batches (case-insensitive) before inserting
                                for batch in [b.strip() for b in teacher_batches.split(',') if b.strip()]:
                                    # Case-insensitive duplicate check
                                    duplicate = execute_query(
                                        "SELECT 1 FROM teacher_batches WHERE LOWER(teacher_username)=LOWER(%s) AND LOWER(batch_name)=LOWER(%s) AND LOWER(institution_name)=LOWER(%s)",
                                        (u_name, batch, INSTITUTION_NAME)
                                    )
                                    
                                    if duplicate:
                                        st.warning(f"⚠️ Batch '{batch}' already exists (case-insensitive) for this teacher. Skipping duplicate.")
                                    else:
                                        execute_query(
                                            'INSERT INTO teacher_batches (teacher_username, batch_name, institution_name) VALUES (%s, %s, %s)',
                                            (u_name, batch, INSTITUTION_NAME),
                                            fetch=False,
                                            commit=True
                                        )
                            
                            st.success(f"✅ Account created! Please wait for admin approval before logging in.")
                            time.sleep(2)
                            st.rerun()
                            
                        except psycopg2.errors.UniqueViolation:
                            st.error("❌ Username already exists!")
                        except Exception as e:
                            st.error(f"❌ Error: {str(e)}")
            
            else:
                if st.button("🔓 Login", type="primary", use_container_width=True, key="login_btn"):
                    if not u_name or not u_pass:
                        st.error("❌ Please enter username and password")
                    else:
                        try:
                            # ✅ FIXED: Case-insensitive login
                            user = execute_query(
                                'SELECT * FROM users WHERE LOWER(username)=LOWER(%s) AND LOWER(institution_name)=LOWER(%s)', 
                                (u_name, INSTITUTION_NAME)
                            )
                            
                            if user and len(user) > 0:
                                user = user[0]
                                if user['password'] == make_hash(u_pass):
                                    if user.get('is_approved', True):
                                        st.session_state.user = {
                                            "name": user['username'],  # Store original case for display
                                            "role": user['role'],
                                            "batch": user['batch_name'],
                                            "institution": user['institution_name']
                                        }
                                        st.success(f"✅ Welcome {user['username']}!")
                                        time.sleep(1)
                                        st.rerun()
                                    else:
                                        st.error("❌ Your account is pending admin approval. Please wait for approval.")
                                else:
                                    st.error("❌ Invalid username or password")
                            else:
                                st.error("❌ Invalid username or password")
                        except Exception as e:
                            st.error(f"❌ Login error: {str(e)}")
            
            st.markdown("""
                <div class="support-message">
                    📞 For Application support, please call us @ 
                    <a href="tel:+918500172644">8500172644</a><br>
                    Our team will assist you.
                </div>
            """, unsafe_allow_html=True)
            
            st.markdown('</div>', unsafe_allow_html=True)
        
        with tab_admin:
            st.markdown(f"### 👑 Admin Access")
            admin_pw = st.text_input("🔑 Admin Password", type="password", key="admin_pass")
            
            if st.button("🚪 Admin Login", use_container_width=True, key="admin_btn"):
                if admin_pw == ADMIN_PASSWORD:
                    st.session_state.user = {
                        "name": "Admin",
                        "role": "Admin",
                        "batch": "Administration",
                        "institution": INSTITUTION_NAME
                    }
                    st.rerun()
                else:
                    st.error("❌ Invalid password")

# ============================================
# 14. MAIN APPLICATION
# ============================================

else:
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown(f"### 📚 DAFFODILS HIGH SCHOOL")
        st.markdown(f"👋 **{st.session_state.user['name']}** | Role: **{st.session_state.user['role']}**")
        if st.session_state.user['role'] == "Student" and st.session_state.user['batch']:
            st.markdown(f"📚 Batch: **{st.session_state.user['batch']}**")
    with col2:
        if st.button("🚪 Logout", key="logout_btn"):
            st.session_state.user = None
            st.session_state.active_exam = None
            st.session_state.exam_result = None
            st.session_state.exam_answers = {}
            st.session_state.dashboard_initialized = False
            st.session_state.exam_end_time = None
            st.session_state.exam_auto_submitted = False
            st.session_state.timer_initialized = False
            st.session_state.select_all_state = False
            st.session_state.selected_pending_users = set()
            st.session_state.exam_session_id = None
            st.session_state.exam_logged = False
            st.session_state.processing_submit = False
            st.session_state.show_expired_exams = False
            clear_cache()
            st.rerun()
    
    st.divider()
    
    user = st.session_state.user
    
    # ============================================
    # ✅ OPTIMIZED: ADMIN PANEL (with case-insensitive updates)
    # ============================================
    if user['role'] == "Admin":
        st.markdown(f"### 👑 Administration Panel")
        
        tab1, tab2, tab3, tab4, tab5 = st.tabs(["👥 Teachers", "👤 Students", "✅ Approvals", "📝 Exams", "📊 Reports"])
        
        with tab1:
            st.markdown("#### 👨‍🏫 Teachers - Complete Details")
            st.markdown("""
            <div class="teacher-table">
                ℹ️ All teacher accounts are listed below.
            </div>
            """, unsafe_allow_html=True)
            
            teachers_data = get_cached_teachers(INSTITUTION_NAME)
            
            if teachers_data:
                teachers_df = pd.DataFrame(teachers_data)
                if not teachers_df.empty:
                    teachers_df['approval_status'] = teachers_df['is_approved'].apply(
                        lambda x: '✅ Approved' if x else '⏳ Pending'
                    )
                    
                    st.dataframe(
                        teachers_df[['username', 'role', 'batch_name', 'approval_status', 'created_at']],
                        use_container_width=True,
                        column_config={
                            "username": "Username",
                            "role": "Role",
                            "batch_name": "Batches",
                            "approval_status": "Approval Status",
                            "created_at": "Registered On"
                        }
                    )
                    
                    # ✅ FIXED: Batch Edit Option for Teachers with case-insensitive duplicate check
                    st.markdown("#### ✏️ Edit Teacher Batches")
                    st.markdown('<div class="batch-edit-box">', unsafe_allow_html=True)
                    
                    teacher_to_edit = st.selectbox(
                        "Select Teacher to Edit Batches", 
                        teachers_df['username'].tolist(), 
                        key="admin_edit_teacher"
                    )
                    
                    # Get current batches (case-insensitive check not needed for display)
                    current_batches_data = execute_query(
                        "SELECT batch_name FROM teacher_batches WHERE LOWER(teacher_username)=LOWER(%s) AND LOWER(institution_name)=LOWER(%s)",
                        (teacher_to_edit, INSTITUTION_NAME)
                    )
                    current_batches = [b['batch_name'] for b in current_batches_data] if current_batches_data else []
                    
                    st.info(f"Current Batches: {', '.join(current_batches) if current_batches else 'None'}")
                    
                    new_batches = st.text_input(
                        "Enter New Batches (comma separated)", 
                        value=", ".join(current_batches) if current_batches else "",
                        placeholder="e.g., 10A,10B,MPC,BPC",
                        key="teacher_new_batches"
                    )
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("💾 Update Batches", use_container_width=True, key="update_teacher_batches"):
                            try:
                                # Delete old batches (case-insensitive)
                                execute_query(
                                    "DELETE FROM teacher_batches WHERE LOWER(teacher_username)=LOWER(%s) AND LOWER(institution_name)=LOWER(%s)",
                                    (teacher_to_edit, INSTITUTION_NAME),
                                    fetch=False,
                                    commit=True
                                )
                                
                                # Insert new batches with duplicate check
                                if new_batches.strip():
                                    duplicate_found = False
                                    for batch in [b.strip() for b in new_batches.split(',') if b.strip()]:
                                        # Check for duplicates among themselves (case-insensitive)
                                        normalized_batches = [normalize_text(b) for b in [b.strip() for b in new_batches.split(',') if b.strip()]]
                                        if len(normalized_batches) != len(set(normalized_batches)):
                                            st.error("❌ Duplicate batches found in your input (case-insensitive)!")
                                            duplicate_found = True
                                            break
                                        
                                        # Check against existing batches for this teacher (though we just deleted, but for safety)
                                        duplicate = execute_query(
                                            "SELECT 1 FROM teacher_batches WHERE LOWER(teacher_username)=LOWER(%s) AND LOWER(batch_name)=LOWER(%s) AND LOWER(institution_name)=LOWER(%s)",
                                            (teacher_to_edit, batch, INSTITUTION_NAME)
                                        )
                                        
                                        if duplicate:
                                            st.error(f"❌ Batch '{batch}' already exists (case-insensitive)!")
                                            duplicate_found = True
                                            break
                                        
                                        execute_query(
                                            'INSERT INTO teacher_batches (teacher_username, batch_name, institution_name) VALUES (%s, %s, %s)',
                                            (teacher_to_edit, batch, INSTITUTION_NAME),
                                            fetch=False,
                                            commit=True
                                        )
                                    
                                    if not duplicate_found:
                                        st.success(f"✅ Batches updated for {teacher_to_edit}")
                                        clear_cache()
                                        time.sleep(1)
                                        st.rerun()
                                else:
                                    st.success(f"✅ All batches removed for {teacher_to_edit}")
                                    clear_cache()
                                    time.sleep(1)
                                    st.rerun()
                            except Exception as e:
                                st.error(f"❌ Error updating batches: {str(e)}")
                    
                    with col2:
                        if st.button("🔄 Reset", use_container_width=True, key="reset_batches"):
                            st.rerun()
                    
                    st.markdown('</div>', unsafe_allow_html=True)
                    
                    # Password Management Section
                    st.markdown("#### 🔑 Teacher Password Management")
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        st.markdown("**Reset Teacher Password**")
                        teacher_to_reset = st.selectbox(
                            "Select Teacher", 
                            teachers_df['username'].tolist(), 
                            key="admin_reset_teacher"
                        )
                        
                        new_password = st.text_input("New Password", type="password", key="new_teacher_pass")
                        confirm_password = st.text_input("Confirm Password", type="password", key="confirm_teacher_pass")
                        
                        if st.button("🔄 Reset Teacher Password", key="reset_teacher_pass_btn"):
                            if not new_password:
                                st.error("❌ Please enter a new password")
                            elif new_password != confirm_password:
                                st.error("❌ Passwords do not match")
                            else:
                                try:
                                    hashed_password = make_hash(new_password)
                                    execute_query(
                                        "UPDATE users SET password=%s WHERE LOWER(username)=LOWER(%s) AND LOWER(institution_name)=LOWER(%s)",
                                        (hashed_password, teacher_to_reset, INSTITUTION_NAME),
                                        fetch=False,
                                        commit=True
                                    )
                                    st.success(f"✅ Password reset successfully for {teacher_to_reset}")
                                    clear_cache()
                                    time.sleep(1)
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"❌ Error resetting password: {str(e)}")
                    
                    with col2:
                        st.markdown("**Generate Random Password**")
                        if st.button("🎲 Generate Random Password", key="gen_teacher_pass"):
                            random_pass = generate_random_password()
                            st.info(f"Generated Password: `{random_pass}`")
                            st.code(f"Copy this password: {random_pass}")
                    
                    st.divider()
                    
                    st.markdown("#### 🗑️ Delete Teacher")
                    teacher_to_delete = st.selectbox(
                        "Select Teacher to Delete", 
                        teachers_df['username'].tolist(), 
                        key="admin_delete_teacher"
                    )
                    
                    col1, col2 = st.columns([1, 3])
                    with col1:
                        if st.button("🗑️ Delete Teacher", type="primary", key="admin_delete_teacher_btn"):
                            try:
                                execute_query("DELETE FROM users WHERE LOWER(username)=LOWER(%s) AND LOWER(institution_name)=LOWER(%s)", (teacher_to_delete, INSTITUTION_NAME), fetch=False, commit=True)
                                execute_query("DELETE FROM teacher_batches WHERE LOWER(teacher_username)=LOWER(%s) AND LOWER(institution_name)=LOWER(%s)", (teacher_to_delete, INSTITUTION_NAME), fetch=False, commit=True)
                                st.success(f"✅ {teacher_to_delete} deleted successfully")
                                clear_cache()
                                time.sleep(1)
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ Error deleting teacher: {str(e)}")
            else:
                st.info("No teachers found")
        
        with tab2:
            st.markdown("#### 👨‍🎓 Students - Complete Details")
            st.markdown("""
            <div class="student-table">
                ℹ️ All student accounts are listed below.
            </div>
            """, unsafe_allow_html=True)
            
            students_data = get_cached_students(INSTITUTION_NAME)
            
            if students_data:
                students_df = pd.DataFrame(students_data)
                if not students_df.empty:
                    students_df['approval_status'] = students_df['is_approved'].apply(
                        lambda x: '✅ Approved' if x else '⏳ Pending'
                    )
                    
                    st.dataframe(
                        students_df[['username', 'role', 'batch_name', 'approval_status', 'created_at']],
                        use_container_width=True,
                        column_config={
                            "username": "Username",
                            "role": "Role",
                            "batch_name": "Batch/Class",
                            "approval_status": "Approval Status",
                            "created_at": "Registered On"
                        }
                    )
                    
                    st.markdown("#### 🔑 Student Password Management")
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        st.markdown("**Reset Student Password**")
                        student_to_reset = st.selectbox(
                            "Select Student", 
                            students_df['username'].tolist(), 
                            key="admin_reset_student"
                        )
                        
                        new_password = st.text_input("New Password", type="password", key="new_student_pass")
                        confirm_password = st.text_input("Confirm Password", type="password", key="confirm_student_pass")
                        
                        if st.button("🔄 Reset Student Password", key="reset_student_pass_btn"):
                            if not new_password:
                                st.error("❌ Please enter a new password")
                            elif new_password != confirm_password:
                                st.error("❌ Passwords do not match")
                            else:
                                try:
                                    hashed_password = make_hash(new_password)
                                    execute_query(
                                        "UPDATE users SET password=%s WHERE LOWER(username)=LOWER(%s) AND LOWER(institution_name)=LOWER(%s)",
                                        (hashed_password, student_to_reset, INSTITUTION_NAME),
                                        fetch=False,
                                        commit=True
                                    )
                                    st.success(f"✅ Password reset successfully for {student_to_reset}")
                                    clear_cache()
                                    time.sleep(1)
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"❌ Error resetting password: {str(e)}")
                    
                    with col2:
                        st.markdown("**Generate Random Password**")
                        if st.button("🎲 Generate Random Password", key="gen_student_pass"):
                            random_pass = generate_random_password()
                            st.info(f"Generated Password: `{random_pass}`")
                            st.code(f"Copy this password: {random_pass}")
                    
                    st.divider()
                    
                    st.markdown("#### 🗑️ Delete Student")
                    student_to_delete = st.selectbox(
                        "Select Student to Delete", 
                        students_df['username'].tolist(), 
                        key="admin_delete_student"
                    )
                    
                    col1, col2 = st.columns([1, 3])
                    with col1:
                        if st.button("🗑️ Delete Student", type="primary", key="admin_delete_student_btn"):
                            try:
                                execute_query("DELETE FROM users WHERE LOWER(username)=LOWER(%s) AND LOWER(institution_name)=LOWER(%s)", (student_to_delete, INSTITUTION_NAME), fetch=False, commit=True)
                                execute_query("DELETE FROM results WHERE LOWER(student)=LOWER(%s) AND LOWER(institution_name)=LOWER(%s)", (student_to_delete, INSTITUTION_NAME), fetch=False, commit=True)
                                st.success(f"✅ {student_to_delete} deleted successfully")
                                clear_cache()
                                time.sleep(1)
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ Error deleting student: {str(e)}")
            else:
                st.info("No students found")
        
        with tab3:
            st.markdown("#### ✅ User Approvals")
            st.markdown("Approve or reject user registrations")
            
            try:
                pending_users_data = execute_query("""
                    SELECT username, role, batch_name, created_at 
                    FROM users 
                    WHERE LOWER(institution_name)=LOWER(%s) AND is_approved=FALSE 
                    ORDER BY created_at ASC
                """, (INSTITUTION_NAME,))
                
                approved_users_data = execute_query("""
                    SELECT username, role, batch_name, created_at 
                    FROM users 
                    WHERE LOWER(institution_name)=LOWER(%s) AND is_approved=TRUE 
                    ORDER BY created_at DESC
                """, (INSTITUTION_NAME,))
                
                col1, col2 = st.columns(2)
                
                with col1:
                    st.markdown("**⏳ Pending Approvals**")
                    if pending_users_data:
                        pending_users = pd.DataFrame(pending_users_data)
                        
                        # ✅ FIXED: Select all checkbox with proper state management
                        select_all_key = "select_all_pending"
                        
                        if 'select_all_state' not in st.session_state:
                            st.session_state.select_all_state = False
                        
                        st.markdown('<div class="selection-box">', unsafe_allow_html=True)
                        
                        # Simple checkbox that triggers on change
                        select_all = st.checkbox(
                            "✅ Select All Pending Users", 
                            key=select_all_key,
                            value=st.session_state.select_all_state
                        )
                        
                        # Update state without immediate rerun
                        if select_all != st.session_state.select_all_state:
                            st.session_state.select_all_state = select_all
                            if select_all:
                                # Select all users
                                st.session_state.selected_pending_users = set(pending_users['username'].tolist())
                            else:
                                # Deselect all
                                st.session_state.selected_pending_users = set()
                            st.rerun()
                        
                        selected_users = []
                        
                        for idx, user_row in pending_users.iterrows():
                            checkbox_key = f"pending_{user_row['username']}_{idx}"
                            
                            # Determine if checkbox should be checked
                            is_checked = user_row['username'] in st.session_state.selected_pending_users
                            
                            selected = st.checkbox(
                                f"👤 {user_row['username']} ({user_row['role']}) - {user_row['batch_name'] if user_row['batch_name'] else 'N/A'} [Registered: {format_timestamp(user_row['created_at'])}]",
                                value=is_checked,
                                key=checkbox_key
                            )
                            
                            if selected:
                                selected_users.append(user_row['username'])
                                if user_row['username'] not in st.session_state.selected_pending_users:
                                    st.session_state.selected_pending_users.add(user_row['username'])
                            else:
                                if user_row['username'] in st.session_state.selected_pending_users:
                                    st.session_state.selected_pending_users.remove(user_row['username'])
                        
                        st.markdown('</div>', unsafe_allow_html=True)
                        
                        if st.session_state.selected_pending_users:
                            st.info(f"📌 {len(st.session_state.selected_pending_users)} user(s) selected")
                            
                            col_a, col_b = st.columns(2)
                            with col_a:
                                if st.button("✅ Approve Selected Users", use_container_width=True, key="approve_selected_btn"):
                                    try:
                                        for username in st.session_state.selected_pending_users:
                                            execute_query(
                                                "UPDATE users SET is_approved=TRUE WHERE LOWER(username)=LOWER(%s) AND LOWER(institution_name)=LOWER(%s)",
                                                (username, INSTITUTION_NAME),
                                                fetch=False,
                                                commit=True
                                            )
                                        st.success(f"✅ Approved {len(st.session_state.selected_pending_users)} user(s)")
                                        st.session_state.select_all_state = False
                                        st.session_state.selected_pending_users = set()
                                        clear_cache()
                                        time.sleep(1)
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"❌ Error approving users: {str(e)}")
                            
                            with col_b:
                                if st.button("❌ Reject Selected Users", use_container_width=True, key="reject_selected_btn"):
                                    try:
                                        for username in st.session_state.selected_pending_users:
                                            execute_query(
                                                "DELETE FROM users WHERE LOWER(username)=LOWER(%s) AND LOWER(institution_name)=LOWER(%s)",
                                                (username, INSTITUTION_NAME),
                                                fetch=False,
                                                commit=True
                                            )
                                        st.warning(f"⚠️ Rejected {len(st.session_state.selected_pending_users)} user(s)")
                                        st.session_state.select_all_state = False
                                        st.session_state.selected_pending_users = set()
                                        clear_cache()
                                        time.sleep(1)
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"❌ Error rejecting users: {str(e)}")
                        else:
                            st.info("No users selected")
                    
                    else:
                        st.info("No pending approvals")
                
                with col2:
                    st.markdown("**✅ Approved Users**")
                    if approved_users_data:
                        approved_users = pd.DataFrame(approved_users_data)
                        
                        st.markdown('<div class="selection-box">', unsafe_allow_html=True)
                        
                        if 'selected_approved_users' not in st.session_state:
                            st.session_state.selected_approved_users = set()
                        
                        select_all_approved = st.checkbox(
                            "✅ Select All Approved Users", 
                            key="select_all_approved"
                        )
                        
                        if select_all_approved:
                            st.session_state.selected_approved_users = set(approved_users['username'].tolist())
                            st.rerun()
                        
                        selected_approved = []
                        
                        for idx, user_row in approved_users.iterrows():
                            checkbox_key = f"approved_{user_row['username']}_{idx}"
                            
                            is_checked = user_row['username'] in st.session_state.selected_approved_users
                            
                            selected = st.checkbox(
                                f"✅ {user_row['username']} ({user_row['role']}) - {user_row['batch_name'] if user_row['batch_name'] else 'N/A'}",
                                value=is_checked,
                                key=checkbox_key
                            )
                            
                            if selected:
                                selected_approved.append(user_row['username'])
                                st.session_state.selected_approved_users.add(user_row['username'])
                            else:
                                if user_row['username'] in st.session_state.selected_approved_users:
                                    st.session_state.selected_approved_users.remove(user_row['username'])
                        
                        st.markdown('</div>', unsafe_allow_html=True)
                        
                        if st.session_state.selected_approved_users:
                            st.info(f"📌 {len(st.session_state.selected_approved_users)} user(s) selected")
                            
                            if st.button("🔄 Revoke Selected Approvals", use_container_width=True, key="revoke_selected_btn"):
                                try:
                                    for username in st.session_state.selected_approved_users:
                                        execute_query(
                                            "UPDATE users SET is_approved=FALSE WHERE LOWER(username)=LOWER(%s) AND LOWER(institution_name)=LOWER(%s)",
                                            (username, INSTITUTION_NAME),
                                            fetch=False,
                                            commit=True
                                        )
                                    st.success(f"✅ Approval revoked for {len(st.session_state.selected_approved_users)} user(s)")
                                    st.session_state.selected_approved_users = set()
                                    clear_cache()
                                    time.sleep(1)
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"❌ Error revoking approvals: {str(e)}")
                        
                        st.markdown("---")
                        st.markdown("**Or Revoke Individually**")
                        revoke_user = st.selectbox(
                            "Select User to Revoke Approval",
                            approved_users['username'].tolist(),
                            key="revoke_user"
                        )
                        
                        if st.button("🔄 Revoke Individual Approval", use_container_width=True, key="revoke_btn"):
                            try:
                                execute_query(
                                    "UPDATE users SET is_approved=FALSE WHERE LOWER(username)=LOWER(%s) AND LOWER(institution_name)=LOWER(%s)",
                                    (revoke_user, INSTITUTION_NAME),
                                    fetch=False,
                                    commit=True
                                )
                                st.success(f"✅ Approval revoked for {revoke_user}")
                                clear_cache()
                                time.sleep(1)
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ Error revoking approval: {str(e)}")
                    else:
                        st.info("No approved users")
                    
            except Exception as e:
                st.error(f"❌ Error loading approvals: {str(e)}")
      
        with tab4:
            exams_data = st.session_state.cached_data.get('admin_exams')
            
            col1, col2 = st.columns([3, 1])
            with col2:
                if st.button("🔄 Refresh Exams", key="refresh_admin_exams"):
                    exams_data = execute_query(
                        "SELECT * FROM exams WHERE LOWER(institution_name)=LOWER(%s) ORDER BY created_at DESC",
                        (INSTITUTION_NAME,)
                    )
                    st.session_state.cached_data['admin_exams'] = exams_data
            
            if exams_data is None:
                exams_data = execute_query(
                    "SELECT * FROM exams WHERE LOWER(institution_name)=LOWER(%s) ORDER BY created_at DESC",
                    (INSTITUTION_NAME,)
                )
                st.session_state.cached_data['admin_exams'] = exams_data
            
            if exams_data:
                exams_df = pd.DataFrame(exams_data)
                if not exams_df.empty:
                    st.dataframe(exams_df[['id', 'teacher', 'batch_name', 'subject', 'exam_date', 'start_time', 'end_time']], use_container_width=True)
                    
                    exam_to_delete = st.selectbox("Select Exam to Delete", exams_df['id'].tolist(), key="admin_delete_exam", format_func=lambda x: f"Exam {x} - {exams_df[exams_df['id']==x]['subject'].iloc[0]}")
                    if st.button("🗑️ Delete Exam", type="primary", key="admin_delete_exam_btn"):
                        execute_query("DELETE FROM exams WHERE id=%s", (exam_to_delete,), fetch=False, commit=True)
                        st.session_state.cached_data['admin_exams'] = None
                        st.success(f"✅ Exam deleted successfully")
                        time.sleep(1)
                        st.rerun()
                else:
                    st.info("No exams found")
            else:
                st.info("No exams found")
        
        with tab5:
            results_data = st.session_state.cached_data.get('admin_results')
            
            col1, col2 = st.columns([3, 1])
            with col2:
                if st.button("🔄 Refresh Results", key="refresh_admin_results"):
                    results_data = execute_query(
                        "SELECT * FROM results WHERE LOWER(institution_name)=LOWER(%s) ORDER BY timestamp DESC",
                        (INSTITUTION_NAME,)
                    )
                    st.session_state.cached_data['admin_results'] = results_data
            
            if results_data is None:
                results_data = execute_query(
                    "SELECT * FROM results WHERE LOWER(institution_name)=LOWER(%s) ORDER BY timestamp DESC",
                    (INSTITUTION_NAME,)
                )
                st.session_state.cached_data['admin_results'] = results_data
            
            if results_data:
                results_df = pd.DataFrame(results_data)
                if not results_df.empty:
                    st.dataframe(results_df[['student', 'subject', 'score', 'total', 'timestamp']], use_container_width=True)
                    
                    total_students = len(results_df['student'].unique())
                    avg_score = results_df['score'].mean() if not results_df.empty else 0
                    st.metric("Total Students Appeared", total_students)
                    st.metric("Average Score", f"{avg_score:.1f}")
                    
                    result_to_delete = st.selectbox("Select Result to Delete", results_df['id'].tolist(), key="admin_delete_result", format_func=lambda x: f"Result {x}")
                    if st.button("🗑️ Delete Result", type="primary", key="admin_delete_result_btn"):
                        execute_query("DELETE FROM results WHERE id=%s", (result_to_delete,), fetch=False, commit=True)
                        st.session_state.cached_data['admin_results'] = None
                        st.success(f"✅ Result deleted successfully")
                        time.sleep(1)
                        st.rerun()
                else:
                    st.info("No results found")
            else:
                st.info("No results found")
    
    # ============================================
    # ✅ OPTIMIZED: TEACHER PANEL with dropdown time picker
    # ============================================
    elif user['role'] == "Teacher":
        st.markdown(f"### 👨‍🏫 Teacher Dashboard")
        
        tab1, tab2, tab3 = st.tabs(["📝 Create Exam", "📋 Published Exams", "📊 Class Reports"])
        
        with tab1:
            st.markdown("#### Create New Exam")
            
            batches_data = get_cached_batches(user['name'], INSTITUTION_NAME)
            batches = [row['batch_name'] for row in batches_data] if batches_data else []
            
            col1, col2 = st.columns(2)
            with col1:
                if batches:
                    target_batch = st.selectbox("🎯 Select Batch", batches, key="teacher_batch")
                else:
                    target_batch = st.text_input("🎯 Batch Name", key="teacher_batch_input")
                    st.warning("⚠️ No batches assigned. Contact admin.")
                
                subject = st.text_input("📚 Subject", key="teacher_subject")
            
            with col2:
                exam_date = st.date_input("📅 Exam Date", datetime.now(), key="teacher_date")
                q_num = st.number_input("❓ Number of Questions", 1, 30, 5, key="teacher_qnum")
            
            # ✅ NEW: Dropdown time picker for start time
            st.markdown("#### ⏰ Start Time")
            st.markdown('<div class="time-dropdown-container">', unsafe_allow_html=True)
            col_start1, col_start2, col_start3 = st.columns(3)
            with col_start1:
                start_hour = st.selectbox("Hour", list(range(1, 13)), index=8, key="start_hour")  # Default 9
            with col_start2:
                start_minute = st.selectbox("Minute", [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55], index=0, key="start_minute", format_func=lambda x: f"{x:02d}")
            with col_start3:
                start_am_pm = st.selectbox("AM/PM", ["AM", "PM"], index=0, key="start_am_pm")  # Default AM
            st.markdown('</div>', unsafe_allow_html=True)
            
            # ✅ NEW: Dropdown time picker for end time
            st.markdown("#### ⏰ End Time")
            st.markdown('<div class="time-dropdown-container">', unsafe_allow_html=True)
            col_end1, col_end2, col_end3 = st.columns(3)
            with col_end1:
                end_hour = st.selectbox("Hour", list(range(1, 13)), index=10, key="end_hour")  # Default 10
            with col_end2:
                end_minute = st.selectbox("Minute", [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55], index=0, key="end_minute", format_func=lambda x: f"{x:02d}")
            with col_end3:
                end_am_pm = st.selectbox("AM/PM", ["AM", "PM"], index=1 if start_hour == 10 else 0, key="end_am_pm")  # Default based on start
            st.markdown('</div>', unsafe_allow_html=True)
            
            # Convert dropdown to time objects
            start_time_obj = get_time_from_dropdown(start_hour, start_minute, start_am_pm)
            end_time_obj = get_time_from_dropdown(end_hour, end_minute, end_am_pm)
            
            # Display selected times
            if start_time_obj and end_time_obj:
                st.info(f"Selected Time: {start_time_obj.strftime('%I:%M %p')} - {end_time_obj.strftime('%I:%M %p')} (IST)")
            else:
                st.error("❌ Invalid time selection")
            
            q_type = st.selectbox("📝 Question Type", ["Multiple Choice (MCQ)", "Fill in Blanks", "Mixed"], key="teacher_qtype")
            level = st.selectbox("📊 Difficulty", ["Easy", "Medium", "Hard"], key="teacher_level")
            
            st.markdown("### 📎 Upload Study Materials")
            st.info("You can upload multiple files (images or PDFs). Questions will be generated from ALL uploaded content.")
            files = st.file_uploader(
                "Select files", 
                type=['jpg', 'png', 'jpeg', 'pdf'], 
                accept_multiple_files=True, 
                key="teacher_files"
            )
            
            if files:
                st.success(f"✅ {len(files)} file(s) uploaded successfully")
                for f in files:
                    st.caption(f"📄 {f.name}")
            
            if st.button("🚀 Generate & Publish Exam", type="primary", use_container_width=True, key="teacher_publish"):
                if not target_batch:
                    st.error("❌ Please select/enter batch name")
                elif not subject:
                    st.error("❌ Please enter subject")
                elif not files:
                    st.error("❌ Please upload at least one file")
                elif not gemini_model:
                    st.error("❌ Gemini AI not available. Please check your API key.")
                elif not start_time_obj or not end_time_obj:
                    st.error("❌ Please select valid start and end times")
                else:
                    with st.spinner(f"🤖 AI is generating {q_num} questions from {len(files)} files... This may take a moment."):
                        try:
                            content = process_uploaded_files(files)
                            
                            if not content:
                                st.error("❌ Could not process any of the uploaded files.")
                                st.stop()
                            
                            st.info(f"✅ Processed {len(content)} content items from {len(files)} files")
                            
                            if q_type == "Mixed":
                                mcq_count = q_num // 2
                                fill_count = q_num - mcq_count
                                prompt = f"""Generate a mix of {mcq_count} multiple choice questions and {fill_count} fill-in-the-blanks questions. 
                                Total {q_num} questions at {level} level based on ALL the uploaded content. Use content from all files.
                                Format as JSON list. For MCQ: use 'type':'mcq', 'question', 'options' (list of 4), 'answer', 'explanation'.
                                For Fill-in-blanks: use 'type':'blank', 'question' (with ____ for blank), 'answer', 'explanation'."""
                            elif "MCQ" in q_type:
                                prompt = f"Generate {q_num} {level} level multiple choice questions based on ALL the uploaded content from all files. Format as JSON list with 'type':'mcq', 'question', 'options' (list of 4), 'answer', 'explanation'."
                            else:
                                prompt = f"Generate {q_num} {level} level fill-in-the-blanks questions based on ALL the uploaded content from all files. Format as JSON list with 'type':'blank', 'question' (with ____ for blank), 'answer', 'explanation'."
                            
                            ai_content = [prompt] + content
                            
                            response = gemini_model.generate_content(
                                ai_content,
                                generation_config={
                                    "temperature": 0.7,
                                    "max_output_tokens": 8192,
                                }
                            )
                            
                            json_match = re.search(r'\[.*\]', response.text, re.DOTALL)
                            if json_match:
                                quiz_data = json.loads(json_match.group())
                                
                                if not isinstance(quiz_data, list) or len(quiz_data) == 0:
                                    st.error("❌ Generated questions are not in the expected format")
                                    st.stop()
                                
                                execute_query(
                                    'INSERT INTO exams (teacher, batch_name, subject, quiz_json, exam_date, start_time, end_time, institution_name) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
                                    (user['name'], target_batch, subject, json.dumps(quiz_data), exam_date, start_time_obj, end_time_obj, INSTITUTION_NAME),
                                    fetch=False,
                                    commit=True
                                )
                                
                                clear_cache()
                                
                                st.success(f"✅ Exam published successfully with {len(quiz_data)} questions from {len(files)} files!")
                                st.balloons()
                                time.sleep(2)
                                st.rerun()
                            else:
                                st.error("❌ Could not extract JSON from AI response")
                                st.code(response.text[:500])
                                
                        except Exception as e:
                            st.error(f"❌ Error generating exam: {str(e)}")
        
        with tab2:
            st.markdown("#### 📋 Published Exams")
            
            col1, col2 = st.columns([3, 1])
            with col2:
                if st.button("🔄 Refresh", key="refresh_teacher_exams"):
                    st.cache_data.clear()
                    st.rerun()
            
            exams_data = get_cached_exams(user['name'], INSTITUTION_NAME)
            
            if exams_data:
                exams = pd.DataFrame(exams_data)
                for _, exam in exams.iterrows():
                    with st.expander(f"📝 {exam['subject']} - {exam['batch_name']} ({exam['exam_date']})"):
                        st.write(f"⏰ Time: {format_time(exam['start_time'])} - {format_time(exam['end_time'])}")
                        questions = json.loads(exam['quiz_json'])
                        st.write(f"📊 Total Questions: {len(questions)}")
                        
                        if st.button("📥 Download Question Paper", key=f"download_exam_{exam['id']}"):
                            pdf = generate_exam_pdf(
                                teacher_name=exam['teacher'],
                                batch_name=exam['batch_name'],
                                subject=exam['subject'],
                                questions_data=questions,
                                institution_name=INSTITUTION_NAME
                            )
                            st.download_button(
                                "💾 Save PDF",
                                pdf,
                                f"{exam['subject']}_Exam_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                                "application/pdf",
                                key=f"save_exam_pdf_{exam['id']}"
                            )
                        
                        if st.button("🗑️ Delete Exam", key=f"del_{exam['id']}"):
                            execute_query("DELETE FROM exams WHERE id=%s", (exam['id'],), fetch=False, commit=True)
                            clear_cache()
                            st.rerun()
            else:
                st.info("No exams published yet")
        
        with tab3:
            st.markdown("#### 📊 Class Reports")
            
            batches_data = get_cached_batches(user['name'], INSTITUTION_NAME)
            batches = [row['batch_name'] for row in batches_data] if batches_data else []
            
            if batches:
                selected_batch = st.selectbox("Select Batch", batches, key="teacher_report_batch")
                selected_subject = st.text_input("Subject (optional)", key="teacher_report_subject")
                
                col1, col2 = st.columns([3, 1])
                with col2:
                    if st.button("🔄 Refresh Reports", key="refresh_reports"):
                        st.cache_data.clear()
                        st.rerun()
                
                if selected_subject:
                    results_data = execute_query("""
                        SELECT r.*, e.subject as exam_subject 
                        FROM results r
                        JOIN exams e ON r.exam_id = e.id
                        WHERE r.institution_name=%s
                        AND LOWER(e.batch_name)=LOWER(%s) AND LOWER(e.subject)=LOWER(%s)
                        ORDER BY r.timestamp DESC
                    """, (INSTITUTION_NAME, selected_batch, selected_subject))
                else:
                    results_data = execute_query("""
                        SELECT r.*, e.subject as exam_subject 
                        FROM results r
                        JOIN exams e ON r.exam_id = e.id
                        WHERE r.institution_name=%s
                        AND LOWER(e.batch_name)=LOWER(%s)
                        ORDER BY r.timestamp DESC
                    """, (INSTITUTION_NAME, selected_batch))
                
                if results_data:
                    results = pd.DataFrame(results_data)
                    
                    display_df = results[['student', 'exam_subject', 'score', 'total', 'timestamp']].copy()
                    display_df.columns = ['Student', 'Subject', 'Score', 'Total', 'Date']
                    
                    st.dataframe(display_df, use_container_width=True)
                    
                    pdf_data = []
                    for _, r in results.iterrows():
                        pdf_data.append({
                            'student': r['student'],
                            'score': r['score'],
                            'total': r['total'],
                            'subject': r['exam_subject'],
                            'date': format_timestamp(r['timestamp'])
                        })
                    
                    if st.button("📥 Download Class Report PDF", use_container_width=True):
                        pdf = generate_teacher_pdf(
                            teacher_name=user['name'],
                            class_name=selected_batch,
                            subject=selected_subject if selected_subject else "All Subjects",
                            results_data=pdf_data,
                            institution_name=INSTITUTION_NAME
                        )
                        st.download_button(
                            "💾 Save PDF",
                            pdf,
                            f"Class_Report_{selected_batch}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                            "application/pdf"
                        )
                else:
                    st.info("No results found for this batch")
            else:
                st.info("No batches assigned")
    
    # ============================================
    # ✅ FIXED: STUDENT PANEL WITH IMPROVED EXAM DISPLAY
    # ============================================
    elif user['role'] == "Student":
        
        # Check if exam result exists
        if st.session_state.exam_result:
            res = st.session_state.exam_result
            percentage = int((res['score']/res['total'])*100)
            
            st.markdown(f"""
                <div class="success-message">
                    ✅ Exam Completed!<br>
                    Your Score: {res['score']}/{res['total']} ({percentage}%)
                </div>
            """, unsafe_allow_html=True)
            
            st.markdown("### 📥 Download Your Exam Report")
            st.info("Click the button below to download your complete exam report with all questions and answers.")
            
            if st.button("📥 Download PDF Report", use_container_width=True):
                pdf = generate_student_pdf(
                    student_name=user['name'],
                    exam_subject=res['subject'],
                    score=res['score'],
                    total=res['total'],
                    review_data=res['review'],
                    batch_name=user['batch'],
                    institution_name=INSTITUTION_NAME
                )
                st.download_button(
                    "💾 Save PDF",
                    pdf,
                    f"{res['subject']}_Report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                    "application/pdf",
                    key="download_pdf"
                )
            
            if st.button("📊 Back to Dashboard", use_container_width=True):
                st.session_state.exam_result = None
                st.session_state.active_exam = None
                st.session_state.shuffled_qs = []
                st.session_state.exam_answers = {}
                st.session_state.exam_end_time = None
                st.session_state.exam_auto_submitted = False
                st.session_state.exam_session_id = None
                st.session_state.exam_logged = False
                st.rerun()
        
        # ✅ FIXED: Active exam with power failure handling
        elif st.session_state.active_exam:
            exam = st.session_state.active_exam
            
            # Check if this exam was already submitted (prevents duplicate on reload) with case-insensitive matching
            existing_result = execute_query(
                "SELECT * FROM results WHERE LOWER(student)=LOWER(%s) AND exam_id=%s",
                (user['name'], exam['id'])
            )
            
            if existing_result:
                # Already submitted - load result
                result_data = existing_result[0]
                st.session_state.exam_result = {
                    "score": result_data['score'],
                    "total": result_data['total'],
                    "subject": result_data['subject'],
                    "review": json.loads(result_data['review_json'])
                }
                st.session_state.active_exam = None
                st.rerun()
            
            # Log session for power failure recovery
            log_exam_session(user['name'], exam['id'])
            
            initialize_exam_timer()
            
            # Check for auto-submit
            auto_submitted = auto_submit_exam()
            if auto_submitted:
                st.session_state.active_exam = None
                st.session_state.shuffled_qs = []
                st.rerun()
            
            remaining_seconds = 0
            if st.session_state.exam_end_time:
                remaining_seconds = int(st.session_state.exam_end_time - time.time())
                if remaining_seconds < 0:
                    remaining_seconds = 0
            
            timer_placeholder = st.empty()
            timer_placeholder.markdown(f"""
                <div class="timer-box">
                    ⏰ Time Remaining: {remaining_seconds // 3600:02d}:{(remaining_seconds % 3600) // 60:02d}:{remaining_seconds % 60:02d}
                </div>
            """, unsafe_allow_html=True)
            
            st.markdown(f"""
                <div class="exam-info">
                    <h3>{exam['subject']}</h3>
                    <p>Teacher: {exam['teacher']} | Batch: {exam['batch_name']}</p>
                    <p>Time: {format_time(exam.get('start_time'))} - {format_time(exam.get('end_time'))}</p>
                </div>
            """, unsafe_allow_html=True)
            
            st.markdown("### 📝 Answer Questions (Answers auto-saved)")
            
            for i, q in enumerate(st.session_state.shuffled_qs):
                st.markdown(f"**Q{i+1}:** {q['question']}")
                
                saved_answer = st.session_state.exam_answers.get(str(i), "")
                
                input_key = f"ans_{i}"
                
                if q.get('type') == 'blank' or 'fill' in str(q.get('type', '')).lower():
                    answer = st.text_input(
                        f"Your Answer for Q{i+1}", 
                        value=saved_answer, 
                        key=input_key,
                        label_visibility="collapsed",
                        placeholder="Type your answer here..."
                    )
                else:
                    options = q.get('options', ['Option A', 'Option B', 'Option C', 'Option D'])
                    
                    idx = None
                    if saved_answer and saved_answer in options:
                        idx = options.index(saved_answer)
                    
                    answer = st.radio(
                        f"Options for Q{i+1}", 
                        options, 
                        index=idx,
                        key=input_key,
                        label_visibility="collapsed",
                        horizontal=True
                    )
                
                if answer != saved_answer:
                    if answer is None or answer == "":
                        st.session_state.exam_answers[str(i)] = ""
                    else:
                        st.session_state.exam_answers[str(i)] = answer
                    st.session_state.answer_saved[str(i)] = True
                
                if st.session_state.exam_answers.get(str(i), ""):
                    st.caption("✓ Saved")
                else:
                    st.caption("⚪ Not answered")
                
                st.divider()
            
            if remaining_seconds > 0 and not st.session_state.exam_auto_submitted:
                col1, col2, col3 = st.columns([1, 2, 1])
                with col2:
                    if st.button("📤 Submit Exam", type="primary", use_container_width=True, key="submit_exam"):
                        
                        score = 0
                        review = []
                        
                        for i, q in enumerate(st.session_state.shuffled_qs):
                            user_ans = str(st.session_state.exam_answers.get(str(i), "")).strip()
                            correct_ans = str(q.get('answer', '')).strip()
                        
                            if user_ans and user_ans != "" and user_ans.lower() == correct_ans.lower():
                                score += 1
                            
                            review.append({
                                "question": q['question'],
                                "user_ans": user_ans if user_ans else "",
                                "correct_ans": correct_ans,
                                "options": q.get('options', []),
                                "explanation": q.get('explanation', 'No explanation available')
                            })
                        
                        execute_query(
                            "INSERT INTO results (student, exam_id, score, total, subject, review_json, institution_name) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                            (user['name'], exam['id'], score, len(review), exam['subject'], json.dumps(review), INSTITUTION_NAME),
                            fetch=False,
                            commit=True
                        )
                        
                        # Mark session as completed
                        complete_exam_session(user['name'], exam['id'])
                        
                        # Clear cache
                        cache_key = f"attempted_{normalize_text(user['name'])}"
                        if cache_key in st.session_state.cached_data:
                            del st.session_state.cached_data[cache_key]
                        
                        st.session_state.exam_result = {
                            "score": score,
                            "total": len(review),
                            "subject": exam['subject'],
                            "review": review
                        }
                        
                        st.session_state.active_exam = None
                        st.session_state.shuffled_qs = []
                        st.session_state.exam_answers = {}
                        st.session_state.answer_saved = {}
                        st.session_state.exam_end_time = None
                        st.session_state.exam_auto_submitted = False
                        st.session_state.exam_session_id = None
                        st.session_state.exam_logged = False
                        
                        st.rerun()
            
            if remaining_seconds > 0:
                time.sleep(1)
                st.rerun()
        
        # ✅ FIXED: Student Dashboard with proper exam categorization and case-insensitive matching
        else:
            st.markdown(f"### 🎓 Student Dashboard")
            
            if not user['batch']:
                st.error("❌ No batch assigned. Please contact admin.")
            else:
                col1, col2 = st.columns([3, 1])
                with col2:
                    if st.button("🔄 Refresh Dashboard", key="refresh_student_dash"):
                        clear_cache()
                        st.rerun()
                
                # Get exams with case-insensitive batch matching
                exams_data = get_student_exams(user['batch'], INSTITUTION_NAME)
                
                # Get attempted exams (completed) with case-insensitive matching
                attempted_ids = get_attempted_exam_ids(user['name'])
                
                # ✅ NEW: Toggle for showing expired exams
                st.session_state.show_expired_exams = st.checkbox(
                    "Show Expired Exams", 
                    value=st.session_state.show_expired_exams,
                    key="show_expired_toggle",
                    help="Check to display expired exams with ✓ tick mark"
                )
                
                if exams_data:
                    exams = pd.DataFrame(exams_data)
                    current_time = datetime.now(IST)
                    
                    available_exams = []
                    soon_exams = []
                    upcoming_exams = []
                    expired_exams = []
                    
                    # Filter out completed exams completely
                    for _, exam in exams.iterrows():
                        # ✅ FIXED: Skip completed exams entirely - they won't appear in dashboard
                        if exam['id'] in attempted_ids:
                            continue
                        
                        # Check if expired
                        if is_exam_expired(exam):
                            expired_exams.append(exam)
                            continue
                        
                        # Parse exam date and times
                        if isinstance(exam['exam_date'], str):
                            exam_date = datetime.strptime(exam['exam_date'], '%Y-%m-%d').date()
                        else:
                            exam_date = exam['exam_date']
                        
                        if exam_date > current_time.date():
                            upcoming_exams.append(exam)
                        elif exam_date == current_time.date():
                            if isinstance(exam['start_time'], str):
                                start_time = datetime.strptime(exam['start_time'], '%H:%M:%S').time()
                            else:
                                start_time = exam['start_time']
                            
                            if isinstance(exam['end_time'], str):
                                end_time = datetime.strptime(exam['end_time'], '%H:%M:%S').time()
                            else:
                                end_time = exam['end_time']
                            
                            start_datetime = datetime.combine(exam_date, start_time)
                            start_datetime = IST.localize(start_datetime)
                            
                            if current_time < start_datetime:
                                seconds_until = (start_datetime - current_time).total_seconds()
                                if seconds_until <= 900:  # 15 minutes
                                    soon_exams.append((exam, start_datetime, seconds_until))
                                else:
                                    upcoming_exams.append(exam)
                            elif start_time <= current_time.time() <= end_time:
                                available_exams.append(exam)
                            elif current_time.time() > end_time:
                                expired_exams.append(exam)
                    
                    # Display Available Exams
                    if available_exams:
                        st.markdown("### 📝 Available Now")
                        for exam in available_exams:
                            st.markdown(f"""
                                <div class="available-exam">
                                    <b>{exam['subject']}</b><br>
                                    📅 Date: {exam['exam_date']} | ⏰ Time: {format_time(exam['start_time'])} - {format_time(exam['end_time'])}<br>
                                    👨‍🏫 Teacher: {exam['teacher']}
                                </div>
                            """, unsafe_allow_html=True)
                            
                            if st.button("🚀 Start Exam", key=f"start_{exam['id']}"):
                                st.session_state.active_exam = dict(exam)
                                st.session_state.shuffled_qs = json.loads(exam['quiz_json'])
                                st.session_state.exam_answers = {}
                                st.session_state.answer_saved = {}
                                st.session_state.exam_auto_submitted = False
                                st.session_state.exam_end_time = None
                                st.session_state.exam_session_id = None
                                st.session_state.exam_logged = False
                                st.session_state.processing_submit = False
                                st.rerun()
                    
                    # Display Soon Exams
                    if soon_exams:
                        st.markdown("### ⏳ Starting Soon")
                        for exam, start_datetime, seconds_until in soon_exams:
                            current_seconds = (start_datetime - datetime.now(IST)).total_seconds()
                            
                            if current_seconds <= 0:
                                st.markdown(f"""
                                    <div class="soon-exam">
                                        <b>{exam['subject']}</b><br>
                                        📅 Date: {exam['exam_date']} | ⏰ Time: {format_time(exam['start_time'])} - {format_time(exam['end_time'])}<br>
                                        👨‍🏫 Teacher: {exam['teacher']}<br>
                                        <span style="color: #4CAF50; font-weight: bold;">✅ Exam is ready to start!</span>
                                    </div>
                                """, unsafe_allow_html=True)
                                
                                if st.button("🚀 Click to Start Exam", key=f"start_soon_{exam['id']}"):
                                    st.session_state.active_exam = dict(exam)
                                    st.session_state.shuffled_qs = json.loads(exam['quiz_json'])
                                    st.session_state.exam_answers = {}
                                    st.session_state.answer_saved = {}
                                    st.session_state.exam_auto_submitted = False
                                    st.session_state.exam_end_time = None
                                    st.session_state.exam_session_id = None
                                    st.session_state.exam_logged = False
                                    st.session_state.processing_submit = False
                                    st.rerun()
                            else:
                                mins = int(current_seconds // 60)
                                secs = int(current_seconds % 60)
                                
                                st.markdown(f"""
                                    <div class="soon-exam">
                                        <b>{exam['subject']}</b><br>
                                        📅 Date: {exam['exam_date']} | ⏰ Time: {format_time(exam['start_time'])} - {format_time(exam['end_time'])}<br>
                                        👨‍🏫 Teacher: {exam['teacher']}<br>
                                        <span style="color: #ff9800; font-weight: bold;">⏰ Your exam will start in {mins} minute(s) {secs} second(s)!</span>
                                    </div>
                                """, unsafe_allow_html=True)
                    
                    # Display Upcoming Exams
                    if upcoming_exams:
                        st.markdown("### 📅 Upcoming Exams")
                        for exam in upcoming_exams:
                            st.markdown(f"""
                                <div class="upcoming-exam">
                                    <b>{exam['subject']}</b><br>
                                    📅 Date: {exam['exam_date']} | ⏰ Time: {format_time(exam['start_time'])} - {format_time(exam['end_time'])}<br>
                                    👨‍🏫 Teacher: {exam['teacher']}
                                </div>
                            """, unsafe_allow_html=True)
                    
                    # ✅ FIXED: Display Expired Exams only if toggle is checked
                    if expired_exams and st.session_state.show_expired_exams:
                        st.markdown("### ⏰ Expired Exams")
                        for exam in expired_exams:
                            st.markdown(f"""
                                <div class="expired-exam">
                                    <b>{exam['subject']}</b><br>
                                    📅 Date: {exam['exam_date']} | ⏰ Time: {format_time(exam['start_time'])} - {format_time(exam['end_time'])}<br>
                                    👨‍🏫 Teacher: {exam['teacher']}<br>
                                    <span style="color: #f44336;">⚠️ Exam period has ended</span>
                                    <span class="expired-exam-tick">✔</span>
                                </div>
                            """, unsafe_allow_html=True)
                    
                    # ✅ FIXED: Check if there are any exams to display (excluding expired if toggle off)
                    has_displayable_exams = (
                        available_exams or 
                        soon_exams or 
                        upcoming_exams or 
                        (expired_exams and st.session_state.show_expired_exams)
                    )
                    
                    if not has_displayable_exams:
                        st.info("📭 No Available Exams Now")
                else:
                    st.info("📭 No Available Exams Now")
            
            # ✅ OPTIMIZED: Results display with case-insensitive matching
            st.markdown("### 📊 My Results")
            
            results_data = get_student_results(user['name'], INSTITUTION_NAME)
            
            if results_data:
                results = pd.DataFrame(results_data)
                for _, r in results.iterrows():
                    score_pct = int((r['score']/r['total'])*100)
                    timestamp_str = format_timestamp(r['timestamp'])
                    
                    col1, col2, col3 = st.columns([2, 1, 1])
                    with col1:
                        st.markdown(f"**{r['subject']}** - Score: {r['score']}/{r['total']} ({score_pct}%)")
                    with col2:
                        st.markdown(f"📅 {timestamp_str}")
                    with col3:
                        if st.button(f"📥 PDF", key=f"dl_{r['id']}"):
                            review = json.loads(r['review_json'])
                            pdf = generate_student_pdf(
                                student_name=user['name'],
                                exam_subject=r['subject'],
                                score=r['score'],
                                total=r['total'],
                                review_data=review,
                                batch_name=user['batch'],
                                institution_name=INSTITUTION_NAME
                            )
                            st.download_button(
                                "💾 Save",
                                pdf,
                                f"{r['subject']}_Report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                                "application/pdf",
                                key=f"save_pdf_{r['id']}"
                            )
                    st.divider()
            else:
                st.info("No exam history found")

# ============================================
# 15. FOOTER
# ============================================

st.markdown(f"""
    <div class="footer">
        © {datetime.now().year} DAFFODILS HIGH SCHOOL AI Exam Portal | All Rights Reserved<br>
        Designed and Developed by <b> SVR COMPUTERS </b>
    </div>
""", unsafe_allow_html=True)