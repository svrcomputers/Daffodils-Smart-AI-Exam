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

# ============================================
# 1. ENVIRONMENT CONFIGURATION
# ============================================

# Load environment variables
load_dotenv()

# Institution Configuration - UPDATED to DAFFODILS HIGH SCHOOL
INSTITUTION_NAME = "DAFFODILS HIGH SCHOOL"
INSTITUTION_SECRET = os.getenv("INSTITUTION_SECRET", os.getenv("SCHOOL_SECRET", "1234"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", os.getenv("SCHOOL_ADMIN_PASSWORD", "2109"))

# Streamlit Page Config
st.set_page_config(
    page_title=f"📚 DAFFODILS HIGH SCHOOL AI Exam Portal", 
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ============================================
# ✅ SESSION STATE INITIALIZATION - MUST BE FIRST
# ============================================

# Initialize all session state variables BEFORE any other code
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
# ✅ OPTIMIZED: Cache for heavy queries
if 'cache_timestamp' not in st.session_state:
    st.session_state.cache_timestamp = 0
if 'cached_data' not in st.session_state:
    st.session_state.cached_data = {}
# ✅ OPTIMIZED: Exam questions cache
if 'exam_questions_loaded' not in st.session_state:
    st.session_state.exam_questions_loaded = False
# ✅ FIXED: ADD THESE NEW SESSION STATE VARIABLES FOR TIMER
if 'exam_end_time' not in st.session_state:
    st.session_state.exam_end_time = None
if 'exam_auto_submitted' not in st.session_state:
    st.session_state.exam_auto_submitted = False
if 'timer_initialized' not in st.session_state:
    st.session_state.timer_initialized = False

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
# ✅ OPTIMIZED FOR FREE TIER: DATABASE CONNECTION POOL
# ============================================

@st.cache_resource
def init_connection_pool():
    """Initialize connection pool with minimal connections for free tier"""
    try:
        DATABASE_URL = os.getenv("NEON_DATABASE_URL")
        if not DATABASE_URL:
            st.error("❌ NEON_DATABASE_URL not found in .env file!")
            return None
        
        # For Neon, just use the URL as-is - they handle pooling automatically
        # Remove any custom pooling parameters if present
        if '?' in DATABASE_URL:
            base_url = DATABASE_URL.split('?')[0]
        else:
            base_url = DATABASE_URL
        
        # ✅ OPTIMIZED: minconn=1, maxconn=5 for free tier
        connection_pool = psycopg2.pool.SimpleConnectionPool(
            minconn=1,
            maxconn=5,  # Reduced for free tier
            dsn=base_url,
            sslmode='require',
            connect_timeout=10
        )
        return connection_pool
    except Exception as e:
        st.error(f"❌ Database connection failed: {e}")
        return None

connection_pool = init_connection_pool()

# ✅ OPTIMIZED: Reusable execute_query function with better error handling
def execute_query(query, params=None, fetch=True, commit=False, retry=2):
    """Execute query with automatic connection management"""
    conn = None
    cur = None
    
    # Check if pool exists
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
            
            return result
            
        except Exception as e:
            if conn:
                conn.rollback()
            if attempt == retry - 1:
                st.error(f"❌ Database error: {str(e)}")
                return None if fetch else False
            time.sleep(0.1 * (attempt + 1))  # Exponential backoff
            
        finally:
            if cur:
                cur.close()
            if conn:
                try:
                    connection_pool.putconn(conn)
                except:
                    pass  # Connection might be already closed

# ============================================
# ✅ OPTIMIZED: Database initialization with better error handling
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
        # Try different model names that work in 2024
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
                # Test the model
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
        
        # If none work, list available models (no sidebar display)
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
    
    # On Windows, use system fonts
    if platform.system() == 'Windows':
        windows_fonts_dir = "C:\\Windows\\Fonts"
        
        # Map of font files to language and font name
        font_candidates = [
            # Telugu fonts
            {'file': 'gautami.ttf', 'name': 'Gautami', 'lang': 'telugu'},
            {'file': 'GautamiB.ttf', 'name': 'GautamiB', 'lang': 'telugu'},
            {'file': 'Nirmala.ttf', 'name': 'Nirmala', 'lang': 'both'},
            {'file': 'NirmalaB.ttf', 'name': 'NirmalaB', 'lang': 'both'},
            # Hindi/Devanagari fonts
            {'file': 'mangal.ttf', 'name': 'Mangal', 'lang': 'hindi'},
            {'file': 'MangalB.ttf', 'name': 'MangalB', 'lang': 'hindi'},
            # Fallback
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

# Register fonts
INDIC_FONTS = register_indic_fonts()

def is_telugu_text(text):
    """Check if text contains Telugu characters"""
    if not isinstance(text, str):
        return False
    telugu_pattern = re.compile(r'[\u0C00-\u0C7F]')
    return bool(telugu_pattern.search(text))

def is_hindi_text(text):
    """Check if text contains Hindi/Devanagari characters"""
    if not isinstance(text, str):
        return False
    hindi_pattern = re.compile(r'[\u0900-\u097F]')
    return bool(hindi_pattern.search(text))

def get_appropriate_font(text):
    """Return the appropriate font name based on text content"""
    if not isinstance(text, str):
        return 'Helvetica'
    
    if is_telugu_text(text):
        return INDIC_FONTS.get('telugu', 'Helvetica') or 'Helvetica'
    elif is_hindi_text(text):
        return INDIC_FONTS.get('hindi', 'Helvetica') or 'Helvetica'
    else:
        return 'Helvetica'

class MultiLingualParagraph(Paragraph):
    """Custom paragraph class that handles multilingual text"""
    def __init__(self, text, style, **kw):
        # Auto-detect language and set appropriate font
        font_name = get_appropriate_font(text)
        
        custom_style = ParagraphStyle(
            style.name + '_custom',
            parent=style,
            fontName=font_name,
            wordWrap='CJK',
            encoding='utf-8'
        )
        
        # Clean the text to remove any box characters
        if isinstance(text, str):
            text = re.sub(r'■', '', text)  # Remove box characters
            text = text.encode('utf-8', 'ignore').decode('utf-8')
        
        super().__init__(text, custom_style, **kw)

def generate_student_pdf(student_name, exam_subject, score, total, review_data, batch_name="", institution_name=INSTITUTION_NAME):
    """Generate PDF report for student"""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    
    elements = []
    
    # Title
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
    
    # Student Details
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
    
    # Questions and Answers
    for i, item in enumerate(review_data, 1):
        q_style = ParagraphStyle(
            'Question',
            parent=styles['Normal'],
            fontSize=10,
            leading=14,
            spaceAfter=6,
            fontName='Helvetica'
        )
        
        # Question
        elements.append(MultiLingualParagraph(f"<b>Q{i}:</b> {item['question']}", q_style))
        
        # Options if available
        if item.get('options') and len(item['options']) > 0:
            options_text = "<b>Options:</b> "
            for idx, opt in enumerate(item['options']):
                options_text += f"{chr(65+idx)}. {opt}  "
            elements.append(MultiLingualParagraph(options_text, q_style))
        
        # Answers
        is_correct = str(item['user_ans']).lower().strip() == str(item['correct_ans']).lower().strip()
        answer_color = 'green' if is_correct else 'red'
        
        elements.append(MultiLingualParagraph(f"<b>Your Answer:</b> <font color='{answer_color}'>{item['user_ans']}</font>", q_style))
        elements.append(MultiLingualParagraph(f"<b>Correct Answer:</b> <font color='green'>{item['correct_ans']}</font>", q_style))
        elements.append(MultiLingualParagraph(f"<b>Explanation:</b> {item['explanation']}", q_style))
        elements.append(MultiLingualParagraph(f"<b>Status:</b> {'✓ Correct' if is_correct else '✗ Incorrect'}", q_style))
        elements.append(Spacer(1, 10))
    
    # Footer
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
    """Generate PDF report for teacher"""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    
    elements = []
    
    # Title
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
    
    # Teacher Details
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
    
    # Results Table
    if results_data:
        table_data = [["S.No", "Student Name", "Score", "Total", "Percentage", "Date"]]
        
        for idx, row in enumerate(results_data, 1):
            percentage = int((row['score']/row['total'])*100)
            table_data.append([
                str(idx),
                row['student'],
                str(row['score']),
                str(row['total']),
                f"{percentage}%",
                row['date'][:10] if row['date'] else "N/A"
            ])
        
        results_table = Table(table_data, colWidths=[0.5*inch, 1.5*inch, 0.7*inch, 0.7*inch, 0.7*inch, 1*inch])
        results_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
        ]))
        elements.append(results_table)
        
        # Summary
        avg_score = sum(r['score'] for r in results_data) / len(results_data)
        avg_percentage = int((avg_score / results_data[0]['total']) * 100)
        
        elements.append(Spacer(1, 20))
        elements.append(MultiLingualParagraph(f"<b>Class Average:</b> {avg_percentage}%", styles['Normal']))
    
    # Footer
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
    """Generate PDF of exam questions and answers for teacher"""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    
    elements = []
    
    # Title
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
    
    # Exam Details
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
    
    # Questions and Answers
    for i, q in enumerate(questions_data, 1):
        q_style = ParagraphStyle(
            'Question',
            parent=styles['Normal'],
            fontSize=11,
            leading=14,
            spaceAfter=6,
            fontName='Helvetica'
        )
        
        # Question
        elements.append(MultiLingualParagraph(f"<b>Q{i}:</b> {q.get('question', 'N/A')}", q_style))
        
        # Options if available
        if q.get('options') and len(q['options']) > 0:
            options_text = "<b>Options:</b> "
            for idx, opt in enumerate(q['options']):
                options_text += f"{chr(65+idx)}. {opt}  "
            elements.append(MultiLingualParagraph(options_text, q_style))
        
        # Answer and Explanation
        elements.append(MultiLingualParagraph(f"<b>Answer:</b> <font color='green'>{q.get('answer', 'N/A')}</font>", q_style))
        if q.get('explanation'):
            elements.append(MultiLingualParagraph(f"<b>Explanation:</b> {q['explanation']}", q_style))
        
        elements.append(Spacer(1, 15))
    
    # Footer
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
    """Extract text from PDF"""
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
    """Convert PDF to images"""
    try:
        return convert_from_bytes(pdf_bytes)
    except:
        return []

def process_uploaded_files(files):
    """Process multiple uploaded files and return combined content for AI"""
    all_content = []
    all_text = ""
    all_images = []
    
    for idx, file in enumerate(files):
        try:
            st.info(f"Processing file {idx+1}: {file.name}")
            
            if file.type == "application/pdf":
                # Try text extraction first
                pdf_text = extract_text_from_pdf(file.read())
                file.seek(0)
                
                if pdf_text and len(pdf_text.strip()) > 100:
                    all_text += f"\n\n--- Content from PDF {idx+1}: {file.name} ---\n{pdf_text}"
                    st.success(f"✅ Extracted text from {file.name}")
                else:
                    # Fall back to images
                    images = convert_pdf_to_images(file.read())
                    file.seek(0)
                    if images:
                        all_images.extend(images[:2])  # Limit to 2 pages per PDF
                        st.success(f"✅ Converted {file.name} to {len(images[:2])} images")
            else:
                # Image file
                img = PIL.Image.open(file)
                all_images.append(img)
                st.success(f"✅ Loaded image: {file.name}")
                
        except Exception as e:
            st.warning(f"⚠️ Could not process file {file.name}: {str(e)}")
    
    # Combine all content
    if all_text:
        all_content.append(all_text)
    if all_images:
        all_content.extend(all_images)
    
    return all_content

# ============================================
# ✅ OPTIMIZED: Caching helper functions - MUST BE BEFORE LOGIN PAGE
# ============================================

@st.cache_data(ttl=60, max_entries=10)
def get_cached_users(institution_name):
    """Cache users data for 60 seconds"""
    return execute_query(
        "SELECT username, role, batch_name, is_approved, created_at FROM users WHERE institution_name=%s ORDER BY created_at DESC",
        (institution_name,)
    )

@st.cache_data(ttl=120, max_entries=20)
def get_cached_exams(teacher_name, institution_name):
    """Cache teacher exams for 120 seconds"""
    return execute_query(
        "SELECT * FROM exams WHERE teacher=%s AND institution_name=%s ORDER BY created_at DESC",
        (teacher_name, institution_name)
    )

@st.cache_data(ttl=60, max_entries=10)
def get_cached_batches(teacher_name, institution_name):
    """Cache teacher batches for 60 seconds"""
    return execute_query(
        "SELECT batch_name FROM teacher_batches WHERE teacher_username=%s AND institution_name=%s",
        (teacher_name, institution_name)
    )

@st.cache_data(ttl=30, max_entries=50)
def get_cached_student_exams(batch_name, institution_name, today):
    """Cache student exams for 30 seconds"""
    return execute_query(
        "SELECT * FROM exams WHERE batch_name=%s AND institution_name=%s AND exam_date >= %s ORDER BY exam_date, start_time",
        (batch_name, institution_name, today)
    )

@st.cache_data(ttl=60, max_entries=50)
def get_cached_results(student_name, institution_name):
    """Cache student results for 60 seconds"""
    return execute_query(
        "SELECT * FROM results WHERE student=%s AND institution_name=%s ORDER BY timestamp DESC",
        (student_name, institution_name)
    )

def clear_cache():
    """Clear all cached data"""
    st.cache_data.clear()
    st.session_state.cache_timestamp = time.time()
    st.session_state.cached_data = {}

# ============================================
# ✅ PATCH: Timer Management for Auto-Submit
# ============================================

def auto_submit_exam():
    """
    Core auto-submit logic - checks if time expired and processes submission
    Returns True if auto-submit was triggered, False otherwise
    """
    # Check if we're in an active exam and not already submitted
    if not st.session_state.active_exam or st.session_state.exam_auto_submitted or st.session_state.exam_result:
        return False
    
    # Get end time
    if st.session_state.exam_end_time is None:
        return False
    
    # Check if time expired
    remaining_seconds = int(st.session_state.exam_end_time - time.time())
    if remaining_seconds > 0:
        return False
    
    # TIME EXPIRED - Process auto-submit
    st.session_state.exam_auto_submitted = True
    
    # Calculate score
    score = 0
    review = []
    
    for i, q in enumerate(st.session_state.shuffled_qs):
        user_ans = str(st.session_state.exam_answers.get(str(i), "")).strip()
        correct_ans = str(q.get('answer', '')).strip()
        
        if user_ans and user_ans.lower() == correct_ans.lower():
            score += 1
        
        review.append({
            "question": q['question'],
            "user_ans": user_ans if user_ans else "Not answered",
            "correct_ans": correct_ans,
            "options": q.get('options', []),
            "explanation": q.get('explanation', 'No explanation available')
        })
    
    # Save to database
    execute_query(
        "INSERT INTO results (student, exam_id, score, total, subject, review_json, institution_name) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (st.session_state.user['name'], st.session_state.active_exam['id'], score, len(review), 
         st.session_state.active_exam['subject'], json.dumps(review), INSTITUTION_NAME),
        fetch=False,
        commit=True
    )
    
    # Set result - CRITICAL: Keep active_exam until result is displayed
    st.session_state.exam_result = {
        "score": score,
        "total": len(review),
        "subject": st.session_state.active_exam['subject'],
        "review": review
    }
    
    return True

def initialize_exam_timer():
    """Initialize exam end time based on exam schedule"""
    if st.session_state.exam_end_time is not None or st.session_state.exam_auto_submitted:
        return
    
    exam = st.session_state.active_exam
    exam_duration = 3600  # Default 1 hour
    
    if exam.get('start_time') and exam.get('end_time') and exam.get('exam_date'):
        try:
            # Parse exam date
            if isinstance(exam['exam_date'], str):
                exam_date = datetime.strptime(exam['exam_date'], '%Y-%m-%d').date()
            else:
                exam_date = exam['exam_date']
            
            # Parse end time
            if isinstance(exam['end_time'], str):
                end_time = datetime.strptime(exam['end_time'], '%H:%M:%S').time()
            else:
                end_time = exam['end_time']
            
            end_datetime = datetime.combine(exam_date, end_time)
            st.session_state.exam_end_time = end_datetime.timestamp()
        except Exception as e:
            # Fallback to default duration
            st.session_state.exam_end_time = time.time() + exam_duration
    else:
        st.session_state.exam_end_time = time.time() + exam_duration

# ============================================
# 10. STYLING - UPDATED WITH NEW REQUIREMENTS
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
    /* UPDATED FOOTER - More attractive gradient */
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
    /* NEW STYLES FOR SCROLLABLE LOGIN PAGE */
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
    /* Support message style */
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
    /* Hide Streamlit branding */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    /* Admin users table style */
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
    </style>
    """, unsafe_allow_html=True)

# Display Header - UPDATED with DAFFODILS HIGH SCHOOL
st.markdown(f'<p class="main-header">📚 AI SMART EXAM PORTAL</p>', unsafe_allow_html=True)
st.markdown(f'<p class="institution-name">🏫 DAFFODILS HIGH SCHOOL</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-header">AI-Powered Examination System for All Educational Institutions</p>', unsafe_allow_html=True)

# ============================================
# 11. UTILITY FUNCTIONS
# ============================================

def parse_time_input(time_str):
    """Parse time string"""
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
    """Format time for display"""
    if not time_obj:
        return "Not set"
    try:
        if isinstance(time_obj, str):
            return time_obj
        return time_obj.strftime('%I:%M %p')
    except:
        return str(time_obj)

def format_timestamp(ts):
    """Format timestamp safely"""
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
    """Get seconds remaining until target datetime"""
    now = datetime.now()
    if target_datetime > now:
        return int((target_datetime - now).total_seconds())
    return 0

# ============================================
# 13. LOGIN PAGE - UPDATED WITH APPROVAL CHECK
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
        # UPDATED: Admin tab now shows only icon without text
        tab_login, tab_admin = st.tabs(["🔐", "👑"])  # Only icons, no text
        
        with tab_login:
            # UPDATED: Added scrollable container
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
                            hashed_password = make_hash(u_pass)
                            
                            if role == "Student":
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
                                
                                for batch in [b.strip() for b in teacher_batches.split(',') if b.strip()]:
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
            
            else:  # Login
                if st.button("🔓 Login", type="primary", use_container_width=True, key="login_btn"):
                    if not u_name or not u_pass:
                        st.error("❌ Please enter username and password")
                    else:
                        try:
                            user = execute_query(
                                'SELECT * FROM users WHERE username=%s AND institution_name=%s', 
                                (u_name, INSTITUTION_NAME)
                            )
                            
                            if user and len(user) > 0:
                                user = user[0]
                                if user['password'] == make_hash(u_pass):
                                    if user.get('is_approved', True):
                                        st.session_state.user = {
                                            "name": user['username'],
                                            "role": user['role'],
                                            "batch": user['batch_name'],
                                            "institution": user['institution_name']
                                        }
                                        st.success(f"✅ Welcome {u_name}!")
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
            
            # UPDATED: Support message inside scrollable area
            st.markdown("""
                <div class="support-message">
                    📞 For Application support, please call us @ 
                    <a href="tel:+918500172644">8500172644</a><br>
                    Our team will assist you.
                </div>
            """, unsafe_allow_html=True)
            
            st.markdown('</div>', unsafe_allow_html=True)  # Close scrollable div
        
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
# 14. MAIN APPLICATION - OPTIMIZED WITH CACHING
# ============================================

else:
    # Header
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown(f"### 📚 DAFFODILS HIGH SCHOOL")
        st.markdown(f"👋 **{st.session_state.user['name']}** | Role: **{st.session_state.user['role']}**")
        if st.session_state.user['role'] == "Student" and st.session_state.user['batch']:
            st.markdown(f"📚 Batch: **{st.session_state.user['batch']}**")
    with col2:
        if st.button("🚪 Logout", key="logout_btn"):
            # Clear all session state on logout
            st.session_state.user = None
            st.session_state.active_exam = None
            st.session_state.exam_result = None
            st.session_state.exam_answers = {}
            st.session_state.dashboard_initialized = False
            st.session_state.exam_end_time = None
            st.session_state.exam_auto_submitted = False
            st.session_state.timer_initialized = False
            clear_cache()
            st.rerun()
    
    st.divider()
    
    user = st.session_state.user
    
    # ============================================
    # ✅ OPTIMIZED: ADMIN PANEL WITH CACHING
    # ============================================
    if user['role'] == "Admin":
        st.markdown(f"### 👑 Administration Panel")
        
        tab1, tab2, tab3, tab4, tab5 = st.tabs(["👥 Users", "✅ Approvals", "📝 Exams", "📊 Reports", "👨‍🏫 Teacher Batches"])
        
        with tab1:
            st.markdown("#### 👥 All Users - Complete Details")
            st.markdown("""
            <div class="password-info">
                ℹ️ Passwords are stored in encrypted format (SHA256 hash). 
                Use this section to manage user accounts and reset passwords.
            </div>
            """, unsafe_allow_html=True)
            
            # ✅ OPTIMIZED: Use cached users
            users_data = get_cached_users(INSTITUTION_NAME)
            
            if users_data:
                users_df = pd.DataFrame(users_data)
                if not users_df.empty:
                    # Add approval status column with badges
                    users_df['approval_status'] = users_df['is_approved'].apply(
                        lambda x: '✅ Approved' if x else '⏳ Pending'
                    )
                    
                    # Display complete user information
                    st.dataframe(
                        users_df[['username', 'role', 'batch_name', 'approval_status', 'created_at']],
                        use_container_width=True,
                        column_config={
                            "username": "Username",
                            "role": "Role",
                            "batch_name": "Batch/Class",
                            "approval_status": "Approval Status",
                            "created_at": "Registered On"
                        }
                    )
                    
                    # Password Management Section
                    st.markdown("#### 🔑 Password Management")
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        st.markdown("**Reset User Password**")
                        user_to_reset = st.selectbox(
                            "Select User", 
                            users_df['username'].tolist(), 
                            key="admin_reset_user"
                        )
                        
                        new_password = st.text_input("New Password", type="password", key="new_pass")
                        confirm_password = st.text_input("Confirm Password", type="password", key="confirm_pass")
                        
                        if st.button("🔄 Reset Password", key="reset_pass_btn"):
                            if not new_password:
                                st.error("❌ Please enter a new password")
                            elif new_password != confirm_password:
                                st.error("❌ Passwords do not match")
                            else:
                                try:
                                    hashed_password = make_hash(new_password)
                                    execute_query(
                                        "UPDATE users SET password=%s WHERE username=%s AND institution_name=%s",
                                        (hashed_password, user_to_reset, INSTITUTION_NAME),
                                        fetch=False,
                                        commit=True
                                    )
                                    st.success(f"✅ Password reset successfully for {user_to_reset}")
                                    clear_cache()
                                    time.sleep(1)
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"❌ Error resetting password: {str(e)}")
                    
                    with col2:
                        st.markdown("**Generate Random Password**")
                        if st.button("🎲 Generate Random Password"):
                            random_pass = generate_random_password()
                            st.info(f"Generated Password: `{random_pass}`")
                            st.code(f"Copy this password: {random_pass}")
                    
                    st.divider()
                    
                    # Delete user option
                    st.markdown("#### 🗑️ Delete User")
                    user_to_delete = st.selectbox(
                        "Select User to Delete", 
                        users_df['username'].tolist(), 
                        key="admin_delete_user"
                    )
                    
                    col1, col2 = st.columns([1, 3])
                    with col1:
                        if st.button("🗑️ Delete User", type="primary", key="admin_delete_btn"):
                            try:
                                execute_query("DELETE FROM users WHERE username=%s AND institution_name=%s", (user_to_delete, INSTITUTION_NAME), fetch=False, commit=True)
                                execute_query("DELETE FROM results WHERE student=%s AND institution_name=%s", (user_to_delete, INSTITUTION_NAME), fetch=False, commit=True)
                                execute_query("DELETE FROM teacher_batches WHERE teacher_username=%s AND institution_name=%s", (user_to_delete, INSTITUTION_NAME), fetch=False, commit=True)
                                st.success(f"✅ {user_to_delete} deleted successfully")
                                clear_cache()
                                time.sleep(1)
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ Error deleting user: {str(e)}")
            else:
                st.info("No users found")
        
        with tab2:
            st.markdown("#### ✅ User Approvals")
            st.markdown("Approve or reject user registrations")
            
            try:
                # Get pending users
                pending_users_data = execute_query("""
                    SELECT username, role, batch_name, created_at 
                    FROM users 
                    WHERE institution_name=%s AND is_approved=FALSE 
                    ORDER BY created_at ASC
                """, (INSTITUTION_NAME,))
                
                # Get approved users
                approved_users_data = execute_query("""
                    SELECT username, role, batch_name, created_at 
                    FROM users 
                    WHERE institution_name=%s AND is_approved=TRUE 
                    ORDER BY created_at DESC
                """, (INSTITUTION_NAME,))
                
                col1, col2 = st.columns(2)
                
                with col1:
                    st.markdown("**⏳ Pending Approvals**")
                    if pending_users_data:
                        pending_users = pd.DataFrame(pending_users_data)
                        
                        # ✅ FIXED: Select all checkbox with proper state management
                        select_all_key = "select_all_pending"
                        
                        # Initialize session state for select all if not exists
                        if 'select_all_state' not in st.session_state:
                            st.session_state.select_all_state = False
                        
                        select_all = st.checkbox(
                            "Select All Pending", 
                            key=select_all_key,
                            value=st.session_state.select_all_state
                        )
                        
                        # Update session state when select all changes
                        if select_all != st.session_state.select_all_state:
                            st.session_state.select_all_state = select_all
                            st.rerun()
                        
                        selected_users = []
                        # Create a container for checkboxes
                        for idx, user_row in pending_users.iterrows():
                            checkbox_key = f"pending_{user_row['username']}_{idx}"
                            
                            # If select all is checked, set default value to True
                            if select_all:
                                selected = st.checkbox(
                                    f"{user_row['username']} ({user_row['role']}) - {user_row['batch_name'] if user_row['batch_name'] else 'N/A'}",
                                    value=True,
                                    key=checkbox_key
                                )
                            else:
                                selected = st.checkbox(
                                    f"{user_row['username']} ({user_row['role']}) - {user_row['batch_name'] if user_row['batch_name'] else 'N/A'}",
                                    key=checkbox_key
                                )
                            if selected:
                                selected_users.append(user_row['username'])
                        
                        if selected_users:
                            if st.button("✅ Approve Selected Users", use_container_width=True, key="approve_selected_btn"):
                                try:
                                    for username in selected_users:
                                        execute_query(
                                            "UPDATE users SET is_approved=TRUE WHERE username=%s AND institution_name=%s",
                                            (username, INSTITUTION_NAME),
                                            fetch=False,
                                            commit=True
                                        )
                                    st.success(f"✅ Approved {len(selected_users)} user(s)")
                                    clear_cache()
                                    # Reset select all state
                                    st.session_state.select_all_state = False
                                    time.sleep(1)
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"❌ Error approving users: {str(e)}")
                    else:
                        st.info("No pending approvals")
                
                with col2:
                    st.markdown("**✅ Approved Users**")
                    if approved_users_data:
                        approved_users = pd.DataFrame(approved_users_data)
                        for _, user_row in approved_users.iterrows():
                            st.markdown(f"""
                                <div style="padding: 5px; margin: 2px; background-color: #d4edda; border-radius: 5px;">
                                    ✅ {user_row['username']} ({user_row['role']}) - {user_row['batch_name'] if user_row['batch_name'] else 'N/A'}
                                </div>
                            """, unsafe_allow_html=True)
                        
                        # Bulk revoke option
                        st.markdown("---")
                        st.markdown("**Revoke Approvals**")
                        revoke_user = st.selectbox(
                            "Select User to Revoke Approval",
                            approved_users['username'].tolist(),
                            key="revoke_user"
                        )
                        
                        if st.button("🔄 Revoke Approval", use_container_width=True, key="revoke_btn"):
                            try:
                                execute_query(
                                    "UPDATE users SET is_approved=FALSE WHERE username=%s AND institution_name=%s",
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
      
        with tab3:
            # ✅ OPTIMIZED: Cache exams with manual refresh
            exams_data = st.session_state.cached_data.get('admin_exams')
            
            col1, col2 = st.columns([3, 1])
            with col2:
                if st.button("🔄 Refresh Exams", key="refresh_admin_exams"):
                    exams_data = execute_query(
                        "SELECT * FROM exams WHERE institution_name=%s ORDER BY created_at DESC",
                        (INSTITUTION_NAME,)
                    )
                    st.session_state.cached_data['admin_exams'] = exams_data
            
            if exams_data is None:
                exams_data = execute_query(
                    "SELECT * FROM exams WHERE institution_name=%s ORDER BY created_at DESC",
                    (INSTITUTION_NAME,)
                )
                st.session_state.cached_data['admin_exams'] = exams_data
            
            if exams_data:
                exams_df = pd.DataFrame(exams_data)
                if not exams_df.empty:
                    st.dataframe(exams_df[['id', 'teacher', 'batch_name', 'subject', 'exam_date', 'start_time', 'end_time']], use_container_width=True)
                    
                    # Delete exam option
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
        
        with tab4:
            # ✅ OPTIMIZED: Cache results with manual refresh
            results_data = st.session_state.cached_data.get('admin_results')
            
            col1, col2 = st.columns([3, 1])
            with col2:
                if st.button("🔄 Refresh Results", key="refresh_admin_results"):
                    results_data = execute_query(
                        "SELECT * FROM results WHERE institution_name=%s ORDER BY timestamp DESC",
                        (INSTITUTION_NAME,)
                    )
                    st.session_state.cached_data['admin_results'] = results_data
            
            if results_data is None:
                results_data = execute_query(
                    "SELECT * FROM results WHERE institution_name=%s ORDER BY timestamp DESC",
                    (INSTITUTION_NAME,)
                )
                st.session_state.cached_data['admin_results'] = results_data
            
            if results_data:
                results_df = pd.DataFrame(results_data)
                if not results_df.empty:
                    st.dataframe(results_df[['student', 'subject', 'score', 'total', 'timestamp']], use_container_width=True)
                    
                    # Statistics
                    total_students = len(results_df['student'].unique())
                    avg_score = results_df['score'].mean() if not results_df.empty else 0
                    st.metric("Total Students Appeared", total_students)
                    st.metric("Average Score", f"{avg_score:.1f}")
                    
                    # Delete result option
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
        
        with tab5:
            st.markdown("### 👨‍🏫 Teacher Batches Management")
            # ✅ OPTIMIZED: Cache teacher batches with manual refresh
            teachers_data = st.session_state.cached_data.get('admin_teachers')
            
            col1, col2 = st.columns([3, 1])
            with col2:
                if st.button("🔄 Refresh Teachers", key="refresh_admin_teachers"):
                    teachers_data = execute_query(
                        "SELECT username FROM users WHERE role='Teacher' AND institution_name=%s",
                        (INSTITUTION_NAME,)
                    )
                    st.session_state.cached_data['admin_teachers'] = teachers_data
            
            if teachers_data is None:
                teachers_data = execute_query(
                    "SELECT username FROM users WHERE role='Teacher' AND institution_name=%s",
                    (INSTITUTION_NAME,)
                )
                st.session_state.cached_data['admin_teachers'] = teachers_data
            
            if teachers_data:
                teachers_df = pd.DataFrame(teachers_data)
                for teacher in teachers_df['username'].tolist():
                    with st.expander(f"👨‍🏫 {teacher}"):
                        # Get batches for this teacher
                        batches_data = execute_query(
                            "SELECT batch_name, created_at FROM teacher_batches WHERE teacher_username=%s AND institution_name=%s",
                            (teacher, INSTITUTION_NAME)
                        )
                        if batches_data:
                            batches_df = pd.DataFrame(batches_data)
                            st.dataframe(batches_df, use_container_width=True)
            else:
                st.info("No teachers found")
    
    # ============================================
    # ✅ OPTIMIZED: TEACHER PANEL WITH CACHING AND MANUAL REFRESH
    # ============================================
    elif user['role'] == "Teacher":
        st.markdown(f"### 👨‍🏫 Teacher Dashboard")
        
        tab1, tab2, tab3 = st.tabs(["📝 Create Exam", "📋 Published Exams", "📊 Class Reports"])
        
        with tab1:
            st.markdown("#### Create New Exam")
            
            # ✅ OPTIMIZED: Use cached batches
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
            
            col1, col2 = st.columns(2)
            with col1:
                start_time = st.text_input("⏰ Start Time (e.g., 9:00 AM)", "9:00 AM", key="teacher_start")
            with col2:
                end_time = st.text_input("⏰ End Time (e.g., 10:30 AM)", "10:30 AM", key="teacher_end")
            
            q_type = st.selectbox("📝 Question Type", ["Multiple Choice (MCQ)", "Fill in Blanks", "Mixed"], key="teacher_qtype")
            level = st.selectbox("📊 Difficulty", ["Easy", "Medium", "Hard"], key="teacher_level")
            
            # Multiple file uploader
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
                else:
                    with st.spinner(f"🤖 AI is generating {q_num} questions from {len(files)} files... This may take a moment."):
                        try:
                            # Process all uploaded files
                            content = process_uploaded_files(files)
                            
                            if not content:
                                st.error("❌ Could not process any of the uploaded files.")
                                st.stop()
                            
                            st.info(f"✅ Processed {len(content)} content items from {len(files)} files")
                            
                            # Create prompt based on question type
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
                            
                            # Prepare content for Gemini
                            ai_content = [prompt] + content
                            
                            # Generate questions
                            response = gemini_model.generate_content(
                                ai_content,
                                generation_config={
                                    "temperature": 0.7,
                                    "max_output_tokens": 8192,
                                }
                            )
                            
                            # Extract JSON from response
                            json_match = re.search(r'\[.*\]', response.text, re.DOTALL)
                            if json_match:
                                quiz_data = json.loads(json_match.group())
                                
                                # Validate quiz data
                                if not isinstance(quiz_data, list) or len(quiz_data) == 0:
                                    st.error("❌ Generated questions are not in the expected format")
                                    st.stop()
                                
                                # Save to database
                                execute_query(
                                    'INSERT INTO exams (teacher, batch_name, subject, quiz_json, exam_date, start_time, end_time, institution_name) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
                                    (user['name'], target_batch, subject, json.dumps(quiz_data), exam_date, parse_time_input(start_time), parse_time_input(end_time), INSTITUTION_NAME),
                                    fetch=False,
                                    commit=True
                                )
                                
                                # Clear cache
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
            
            # ✅ OPTIMIZED: Use cached exams with manual refresh
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
                        
                        # ✅ NEW: Download Exam PDF button
                        col1, col2 = st.columns([3, 1])
                        with col2:
                            if st.button("📥 Download Exam PDF", key=f"download_exam_{exam['id']}"):
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
                        
                        # Display all questions with answers
                        for i, q in enumerate(questions):
                            st.markdown(f"**Q{i+1}:** {q.get('question', 'N/A')}")
                            
                            # Show options for MCQ
                            if q.get('options') and len(q['options']) > 0:
                                options_text = ""
                                for idx, opt in enumerate(q['options']):
                                    options_text += f"{chr(65+idx)}. {opt}  "
                                st.markdown(f"*Options:* {options_text}")
                            
                            st.markdown(f"*✅ Answer:* {q.get('answer', 'N/A')}")
                            if q.get('explanation'):
                                st.markdown(f"*💡 Explanation:* {q['explanation']}")
                            st.markdown("---")
                        
                        # Delete button
                        if st.button("🗑️ Delete Exam", key=f"del_{exam['id']}"):
                            execute_query("DELETE FROM exams WHERE id=%s", (exam['id'],), fetch=False, commit=True)
                            clear_cache()
                            st.rerun()
            else:
                st.info("No exams published yet")
        
        with tab3:
            st.markdown("#### 📊 Class Reports")
            
            # ✅ OPTIMIZED: Use cached batches
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
                
                # Get results
                if selected_subject:
                    results_data = execute_query("""
                        SELECT * FROM results 
                        WHERE institution_name=%s
                        AND exam_id IN (
                            SELECT id FROM exams 
                            WHERE batch_name=%s AND subject=%s
                        )
                        ORDER BY timestamp DESC
                    """, (INSTITUTION_NAME, selected_batch, selected_subject))
                else:
                    results_data = execute_query("""
                        SELECT * FROM results 
                        WHERE institution_name=%s
                        AND exam_id IN (
                            SELECT id FROM exams 
                            WHERE batch_name=%s
                        )
                        ORDER BY timestamp DESC
                    """, (INSTITUTION_NAME, selected_batch))
                
                if results_data:
                    results = pd.DataFrame(results_data)
                    st.dataframe(results[['student', 'subject', 'score', 'total', 'timestamp']], use_container_width=True)
                    
                    # Prepare data for PDF
                    pdf_data = []
                    for _, r in results.iterrows():
                        pdf_data.append({
                            'student': r['student'],
                            'score': r['score'],
                            'total': r['total'],
                            'subject': r['subject'],
                            'date': format_timestamp(r['timestamp'])
                        })
                    
                    # Download PDF button
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
    # ✅ FIXED: STUDENT PANEL WITH REAL-TIME TIMER AND AUTO-SUBMIT
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
            
            # Download PDF only - NO QUESTIONS DISPLAYED
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
            
            # Clear result and return to dashboard
            if st.button("📊 Back to Dashboard", use_container_width=True):
                # Clean up exam state
                st.session_state.exam_result = None
                st.session_state.active_exam = None
                st.session_state.shuffled_qs = []
                st.session_state.exam_answers = {}
                st.session_state.exam_end_time = None
                st.session_state.exam_auto_submitted = False
                st.rerun()
        
        # ✅ FIXED: Active exam with real-time timer and auto-submit
        elif st.session_state.active_exam:
            exam = st.session_state.active_exam
            
            # Initialize timer if needed
            initialize_exam_timer()
            
            # Check for auto-submit FIRST - before any display
            auto_submitted = auto_submit_exam()
            if auto_submitted:
                # Result is now in st.session_state.exam_result
                # Clear exam state but keep result
                st.session_state.active_exam = None
                st.session_state.shuffled_qs = []
                st.rerun()
            
            # Calculate remaining time for display
            remaining_seconds = 0
            if st.session_state.exam_end_time:
                remaining_seconds = int(st.session_state.exam_end_time - time.time())
                if remaining_seconds < 0:
                    remaining_seconds = 0
            
            # Display timer
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
            
            # Questions with AUTO-SAVE
            st.markdown("### 📝 Answer Questions (Answers auto-saved)")
            
            for i, q in enumerate(st.session_state.shuffled_qs):
                st.markdown(f"**Q{i+1}:** {q['question']}")
                
                saved_answer = st.session_state.exam_answers.get(str(i), "")
                
                # Create a unique key for each question
                input_key = f"ans_{i}"
                
                if q.get('type') == 'blank' or 'fill' in str(q.get('type', '')).lower():
                    # Text input for fill-in-blanks
                    answer = st.text_input(
                        f"Your Answer for Q{i+1}", 
                        value=saved_answer, 
                        key=input_key,
                        label_visibility="collapsed",
                        placeholder="Type your answer here..."
                    )
                else:
                    # Radio buttons for MCQ
                    options = q.get('options', ['Option A', 'Option B', 'Option C', 'Option D'])
                    
                    # Find index of saved answer
                    idx = 0
                    if saved_answer in options:
                        idx = options.index(saved_answer)
                    
                    answer = st.radio(
                        f"Options for Q{i+1}", 
                        options, 
                        index=idx, 
                        key=input_key,
                        label_visibility="collapsed",
                        horizontal=True
                    )
                
                # AUTO-SAVE: Update session state when answer changes
                if answer != saved_answer:
                    st.session_state.exam_answers[str(i)] = answer
                    st.session_state.answer_saved[str(i)] = True
                
                # Show small indicator that answer is saved
                if st.session_state.exam_answers.get(str(i), ""):
                    st.caption("✓ Saved")
                
                st.divider()
            
            # Manual submit button (only show if time remaining)
            if remaining_seconds > 0 and not st.session_state.exam_auto_submitted:
                col1, col2, col3 = st.columns([1, 2, 1])
                with col2:
                    if st.button("📤 Submit Exam", type="primary", use_container_width=True, key="submit_exam"):
                        
                        # Calculate score
                        score = 0
                        review = []
                        
                        for i, q in enumerate(st.session_state.shuffled_qs):
                            user_ans = str(st.session_state.exam_answers.get(str(i), "")).strip()
                            correct_ans = str(q.get('answer', '')).strip()
                        
                            if user_ans and user_ans.lower() == correct_ans.lower():
                                score += 1
                            
                            review.append({
                                "question": q['question'],
                                "user_ans": user_ans if user_ans else "Not answered",
                                "correct_ans": correct_ans,
                                "options": q.get('options', []),
                                "explanation": q.get('explanation', 'No explanation available')
                            })
                        
                        # Save to database
                        execute_query(
                            "INSERT INTO results (student, exam_id, score, total, subject, review_json, institution_name) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                            (user['name'], exam['id'], score, len(review), exam['subject'], json.dumps(review), INSTITUTION_NAME),
                            fetch=False,
                            commit=True
                        )
                        
                        st.session_state.exam_result = {
                            "score": score,
                            "total": len(review),
                            "subject": exam['subject'],
                            "review": review
                        }
                        
                        # Clear exam state but keep result
                        st.session_state.active_exam = None
                        st.session_state.shuffled_qs = []
                        st.session_state.exam_answers = {}
                        st.session_state.answer_saved = {}
                        st.session_state.exam_end_time = None
                        st.session_state.exam_auto_submitted = False
                        
                        st.rerun()
            
            # Auto-refresh for timer update - runs every second
            if remaining_seconds > 0:
                time.sleep(1)
                st.rerun()
        
        # Show available exams - ✅ OPTIMIZED WITH CACHING
        else:
            st.markdown(f"### 🎓 Student Dashboard")
            
            if not user['batch']:
                st.error("❌ No batch assigned. Please contact admin.")
            else:
                # ✅ OPTIMIZED: Manual refresh button
                col1, col2 = st.columns([3, 1])
                with col2:
                    if st.button("🔄 Refresh Dashboard", key="refresh_student_dash"):
                        st.cache_data.clear()
                        st.rerun()
                
                # ✅ OPTIMIZED: Use cached exams
                today = datetime.now().date()
                exams_data = get_cached_student_exams(user['batch'], INSTITUTION_NAME, today)
                
                # Get attempted exams
                attempted_key = f"attempted_{user['name']}"
                if attempted_key not in st.session_state.cached_data:
                    attempted_data = execute_query(
                        "SELECT exam_id FROM results WHERE student=%s",
                        (user['name'],)
                    )
                    st.session_state.cached_data[attempted_key] = set(row['exam_id'] for row in attempted_data) if attempted_data else set()
                
                attempted_ids = st.session_state.cached_data[attempted_key]
                
                if exams_data:
                    exams = pd.DataFrame(exams_data)
                    current_time_obj = datetime.now().time()
                    
                    upcoming = []
                    available = []
                    soon_exams = []
                    
                    for _, exam in exams.iterrows():
                        if exam['id'] in attempted_ids:
                            continue
                        
                        if isinstance(exam['exam_date'], str):
                            exam_date = datetime.strptime(exam['exam_date'], '%Y-%m-%d').date()
                        else:
                            exam_date = exam['exam_date']
                        
                        if exam_date > today:
                            upcoming.append(exam)
                        elif exam_date == today:
                            start = exam['start_time']
                            end = exam['end_time']
                            
                            if start and end:
                                if isinstance(start, str):
                                    start_time_obj = datetime.strptime(start, '%H:%M:%S').time()
                                else:
                                    start_time_obj = start
                                
                                if isinstance(end, str):
                                    end_time_obj = datetime.strptime(end, '%H:%M:%S').time()
                                else:
                                    end_time_obj = end
                                
                                if current_time_obj < start_time_obj:
                                    start_datetime = datetime.combine(exam_date, start_time_obj)
                                    seconds_until = get_time_remaining_seconds(start_datetime)
                                    
                                    if seconds_until <= 900:  # 15 minutes
                                        soon_exams.append((exam, start_datetime, seconds_until))
                                    else:
                                        upcoming.append(exam)
                                elif start_time_obj <= current_time_obj <= end_time_obj:
                                    available.append(exam)
                    
                    # Display Available Exams
                    if available:
                        st.markdown("### 📝 Available Now")
                        for exam in available:
                            st.markdown(f"""
                                <div class="available-exam">
                                    <b>{exam['subject']}</b><br>
                                    📅 Date: {exam['exam_date']} | ⏰ Time: {format_time(exam['start_time'])} - {format_time(exam['end_time'])}<br>
                                    👨‍🏫 Teacher: {exam['teacher']}
                                </div>
                            """, unsafe_allow_html=True)
                            
                            if st.button("🚀 Start Exam", key=f"start_{exam['id']}"):
                                # Load questions and set end time properly
                                st.session_state.active_exam = dict(exam)
                                st.session_state.shuffled_qs = json.loads(exam['quiz_json'])
                                st.session_state.exam_answers = {}
                                st.session_state.answer_saved = {}
                                st.session_state.exam_auto_submitted = False
                                st.session_state.exam_end_time = None  # Will be set in exam page
                                
                                st.rerun()
                    
                    # Display Soon Exams
                    if soon_exams:
                        st.markdown("### ⏳ Starting Soon")
                        for exam, start_datetime, seconds_until in soon_exams:
                            current_seconds = get_time_remaining_seconds(start_datetime)
                            
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
                    if upcoming:
                        st.markdown("### 📅 Upcoming Exams")
                        for exam in upcoming:
                            st.markdown(f"""
                                <div class="upcoming-exam">
                                    <b>{exam['subject']}</b><br>
                                    📅 Date: {exam['exam_date']} | ⏰ Time: {format_time(exam['start_time'])} - {format_time(exam['end_time'])}<br>
                                    👨‍🏫 Teacher: {exam['teacher']}
                                </div>
                            """, unsafe_allow_html=True)
                    
                    if not upcoming and not available and not soon_exams:
                        st.info("🎉 No exams available at this time")
                else:
                    st.info("📚 No exams scheduled for your batch")
            
            # ✅ OPTIMIZED: Results - using cached data
            st.markdown("### 📊 My Results")
            
            results_data = get_cached_results(user['name'], INSTITUTION_NAME)
            
            if results_data:
                results = pd.DataFrame(results_data)
                for _, r in results.iterrows():
                    score_pct = int((r['score']/r['total'])*100)
                    timestamp_str = format_timestamp(r['timestamp'])
                    
                    # Simple display with only download button
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
# 15. FOOTER - UPDATED WITH DEVELOPER CREDIT
# ============================================

st.markdown(f"""
    <div class="footer">
        © {datetime.now().year} DAFFODILS HIGH SCHOOL AI Exam Portal | All Rights Reserved<br>
        Designed and Developed by <b>SVR COMPUTERS </b>
    </div>
""", unsafe_allow_html=True)