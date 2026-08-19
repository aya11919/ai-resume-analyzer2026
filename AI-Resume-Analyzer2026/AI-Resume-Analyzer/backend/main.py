import os
import re
import json
import logging
import datetime
import hashlib
import secrets
import binascii
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from typing import Any, Optional
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from dotenv import load_dotenv

# Load env variables
load_dotenv()

import database
import services

SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_APP_PASSWORD = os.getenv("SMTP_APP_PASSWORD")
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize DB on start
try:
    database.init_db()
    logger.info("Sqlite3 database tables checked and initialized.")
except Exception as e:
    logger.critical(f"Failed to initialize database: {e}")

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in separate threads to prevent request blocking."""
    daemon_threads = True

def parse_multipart(body: bytes, boundary: str) -> dict:
    """
    Parses multipart/form-data body and extracts fields/files.
    Each field name maps to a LIST of values (in appearance order), so that
    repeated fields (e.g. several files under the same 'files' name for a
    batch upload) are all preserved instead of the last one overwriting
    earlier ones.
    """
    parts = body.split(b'--' + boundary.encode('utf-8'))
    result: dict = {}
    for part in parts:
        # Filter empty parts or final boundaries
        if not part or part == b'--' or part == b'--\r\n' or part == b'\r\n' or part == b'\r\n--':
            continue
        
        # Split headers and body
        if b'\r\n\r\n' not in part:
            continue
            
        header_part, content_part = part.split(b'\r\n\r\n', 1)
        
        # Trim leading/trailing CRLF from the content part
        if content_part.startswith(b'\r\n'):
            content_part = content_part[2:]
        if content_part.endswith(b'\r\n'):
            content_part = content_part[:-2]
        if content_part.endswith(b'\r\n--'):
            content_part = content_part[:-4]
            
        header_text = header_part.decode('utf-8', errors='ignore')
        
        headers = {}
        for line in header_text.split('\r\n'):
            if ':' in line:
                k, v = line.split(':', 1)
                headers[k.strip().lower()] = v.strip()
                
        disp = headers.get('content-disposition', '')
        if 'form-data' in disp:
            name_match = re.search(r'name="([^"]+)"', disp)
            filename_match = re.search(r'filename="([^"]+)"', disp)
            
            if name_match:
                name = name_match.group(1)
                if filename_match:
                    filename = filename_match.group(1)
                    if not filename:
                        # An <input> left empty still sends an empty-filename part; skip it
                        continue
                    result.setdefault(name, []).append({
                        'filename': filename,
                        'content': content_part,
                        'content-type': headers.get('content-type', '')
                    })
                else:
                    result.setdefault(name, []).append(content_part.decode('utf-8', errors='ignore'))
    return result

def get_field(fields: dict, name: str, default=None):
    """Returns the first value for a (possibly repeated) multipart field name."""
    values = fields.get(name)
    return values[0] if values else default

def get_all(fields: dict, name: str) -> list:
    """Returns all values for a (possibly repeated) multipart field name."""
    return fields.get(name, [])

class AnalysisError(Exception):
    """Raised by analyze_and_store_resume() so callers can report a clean HTTP error."""
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message

def analyze_and_store_resume(filename: str, file_bytes: bytes, job_description: str, job_reference: str = "", job_title: str = "") -> dict:
    """
    Extracts text from a resume file, runs the AI analysis, persists both the
    resume and the analysis to SQLite, and returns the detailed response
    payload. Shared by the single-CV route (/api/analyze) and the batch route
    (/api/analyze-batch) to avoid duplicating this logic.
    """
    # 1. Parse text from document bytes
    try:
        resume_text = services.extract_text(file_bytes, filename)
        if not resume_text.strip():
            raise ValueError("Le document me semble vide ou ne contient aucun texte lisible.")
    except ValueError as ve:
        logger.error(f"Text extraction failed for '{filename}': {ve}")
        raise AnalysisError(422, str(ve))
    except Exception as e:
        logger.error(f"Unexpected parsing error for '{filename}': {e}")
        raise AnalysisError(500, f"Erreur d'extraction de texte : {e}")

    # 2. Save CV to DB
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO resumes (filename, parsed_text) VALUES (?, ?)",
            (filename, resume_text)
        )
        resume_id = cursor.lastrowid
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to save resume metadata to SQLite for '{filename}': {e}")
        raise AnalysisError(500, f"Erreur de sauvegarde base de données : {e}")

    # 3. Analyze CV with AI
    try:
        ai_result = services.analyze_resume_with_ai(resume_text, job_description)
    except Exception as e:
        logger.error(f"AI analysis computation failed for '{filename}': {e}")
        try:
            cursor.execute("DELETE FROM resumes WHERE id = ?", (resume_id,))
            conn.commit()
        finally:
            conn.close()
        raise AnalysisError(500, f"Erreur lors de l'analyse IA : {e}")

    # 4. Save Analysis details to DB
    jdm = ai_result.get("job_description_match")
    match_score = jdm.get("match_score") if isinstance(jdm, dict) else None

    # Déterminer le titre du poste
    final_job_title = job_title.strip()
    if not final_job_title and isinstance(jdm, dict):
        fitting_roles = jdm.get("fitting_roles", [])
        if fitting_roles and isinstance(fitting_roles, list) and fitting_roles[0]:
            final_job_title = str(fitting_roles[0]).strip()
    if not final_job_title and job_description:
        first_line = job_description.strip().split('\n')[0].strip()
        if first_line and len(first_line) < 80:
            final_job_title = re.sub(r'^(poste|intitulé|titre|offre)\s*:\s*', '', first_line, flags=re.IGNORECASE)

    try:
        cursor.execute("""
            INSERT INTO analyses (
                resume_id, overall_score, summary, skills, experience,
                education, strengths, weaknesses, recommendations,
                job_description_match, chat_history,
                projects, certifications, languages, qualities,
                candidate_name, status, match_score, email, phone, city, job_reference, job_title, education_level
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            resume_id,
            ai_result.get("overall_score", 0),
            ai_result.get("summary", ""),
            json.dumps(ai_result.get("skills", [])),
            json.dumps(ai_result.get("experience", [])),
            json.dumps(ai_result.get("education", [])),
            json.dumps(ai_result.get("strengths", [])),
            json.dumps(ai_result.get("weaknesses", [])),
            json.dumps(ai_result.get("recommendations", [])),
            json.dumps(jdm),
            json.dumps([]),
            json.dumps(ai_result.get("projects", [])),
            json.dumps(ai_result.get("certifications", [])),
            json.dumps(ai_result.get("languages", [])),
            json.dumps(ai_result.get("qualities", [])),
            ai_result.get("candidate_name", ""),
            "completed",
            match_score,
            ai_result.get("email", ""),
            ai_result.get("phone", ""),
            ai_result.get("city", ""),
            job_reference,
            final_job_title,
            ai_result.get("education_level", ""),
        ))
        analysis_id = cursor.lastrowid
        conn.commit()

        cursor.execute("SELECT created_at FROM analyses WHERE id = ?", (analysis_id,))
        created_at = cursor.fetchone()['created_at']
        conn.close()
    except Exception as e:
        logger.error(f"Failed to write analysis record to SQLite for '{filename}': {e}")
        raise AnalysisError(500, f"Erreur de stockage de l'analyse : {e}")

    return {
        "id": analysis_id,
        "resume_id": resume_id,
        "overall_score": ai_result.get("overall_score", 0),
        "candidate_name": ai_result.get("candidate_name", ""),
        "email": ai_result.get("email", ""),
        "phone": ai_result.get("phone", ""),
        "city": ai_result.get("city", ""),
        "job_reference": job_reference,
        "job_title": final_job_title,
        "summary": ai_result.get("summary", ""),
        "skills": ai_result.get("skills", []),
        "experience": ai_result.get("experience", []),
        "education": ai_result.get("education", []),
        "projects": ai_result.get("projects", []),
        "certifications": ai_result.get("certifications", []),
        "languages": ai_result.get("languages", []),
        "qualities": ai_result.get("qualities", []),
        "strengths": ai_result.get("strengths", []),
        "weaknesses": ai_result.get("weaknesses", []),
        "recommendations": ai_result.get("recommendations", []),
        "job_description_match": jdm,
        "match_score": match_score,
        "status": "completed",
        "chat_history": [],
        "created_at": created_at,
        "resume": {
            "id": resume_id,
            "filename": filename,
            "uploaded_at": created_at
        }
    }

def extract_job_description(form_fields: dict) -> str:
    """Combines a pasted job_description text field with an optional job_description_file (PDF/DOCX)."""
    job_description = get_field(form_fields, 'job_description', '') or ''
    jd_file_data = get_field(form_fields, 'job_description_file')
    if jd_file_data and isinstance(jd_file_data, dict) and jd_file_data.get('content'):
        jd_from_file = services.extract_text(jd_file_data['content'], jd_file_data['filename'])
        job_description = (job_description + "\n\n" + jd_from_file).strip() if job_description else jd_from_file
    return job_description

# ═══════════════════════════════════════════════════════
#  AUTHENTICATION HELPERS
# ═══════════════════════════════════════════════════════
PBKDF2_ITERATIONS = 100_000
CODE_VALIDITY_MINUTES = 10

def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    """Retourne (hash_hex, salt_hex). Génère un sel aléatoire si non fourni."""
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), PBKDF2_ITERATIONS)
    return binascii.hexlify(dk).decode('utf-8'), salt

def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), PBKDF2_ITERATIONS)
    return secrets.compare_digest(binascii.hexlify(dk).decode('utf-8'), expected_hash)

def generate_verification_code() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(6))

def code_expiry_timestamp() -> str:
    return (datetime.datetime.now() + datetime.timedelta(minutes=CODE_VALIDITY_MINUTES)).isoformat()

def is_code_expired(expiry_iso: Optional[str]) -> bool:
    if not expiry_iso:
        return True
    try:
        return datetime.datetime.now() > datetime.datetime.fromisoformat(expiry_iso)
    except ValueError:
        return True

# ═══════════════════════════════════════════════════════
#  SESSIONS — protège les routes du tableau de bord (données
#  candidats) pour qu'un visiteur non connecté ne puisse pas
#  les appeler directement, même sans passer par l'interface.
#  Stockage en mémoire : les sessions sont perdues au redémarrage
#  du serveur, ce qui est acceptable pour ce projet.
# ═══════════════════════════════════════════════════════
ACTIVE_SESSIONS: dict = {}
SESSION_VALIDITY_HOURS = 24

def create_session(email: str, name: str) -> str:
    token = secrets.token_urlsafe(32)
    ACTIVE_SESSIONS[token] = {
        "email": email,
        "name": name,
        "expiry": (datetime.datetime.now() + datetime.timedelta(hours=SESSION_VALIDITY_HOURS)).isoformat(),
    }
    return token

def get_current_user(handler) -> Optional[dict]:
    """Lit l'en-tête 'Authorization: Bearer <token>' et renvoie la session
    correspondante ({'email', 'name'}) si elle est valide, sinon None."""
    auth_header = handler.headers.get('Authorization', '') or ''
    if not auth_header.startswith('Bearer '):
        return None
    token = auth_header[len('Bearer '):].strip()
    session = ACTIVE_SESSIONS.get(token)
    if not session:
        return None
    if is_code_expired(session.get('expiry')):
        ACTIVE_SESSIONS.pop(token, None)
        return None
    return session

def require_auth(handler) -> Optional[dict]:
    """Vérifie la session et écrit directement une erreur 401 si absente/expirée.
    Utilisation : `user = require_auth(self)` puis `if not user: return`."""
    user = get_current_user(handler)
    if not user:
        handler.write_error(401, "Authentification requise. Veuillez vous connecter.")
    return user

def send_verification_email(to_email: str, to_name: str, code: str):
    """Envoie le code de vérification directement depuis le backend, via Gmail.
    Lève une exception si SMTP_EMAIL/SMTP_APP_PASSWORD ne sont pas configurés
    ou si l'envoi échoue, pour que l'appelant puisse réagir proprement."""
    if not SMTP_EMAIL or not SMTP_APP_PASSWORD:
        raise RuntimeError(
            "SMTP_EMAIL / SMTP_APP_PASSWORD manquants dans le fichier .env. "
            "Voir myaccount.google.com/apppasswords pour générer un mot de passe d'application."
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Votre code de vérification"
    msg["From"] = SMTP_EMAIL
    msg["To"] = formataddr((to_name, to_email)) if to_name else to_email

    text_body = (
        f"Bonjour {to_name},\n\n"
        f"Votre code de vérification est : {code}\n\n"
        f"Ce code est valide 10 minutes.\n\n"
        f"Si vous n'êtes pas à l'origine de cette demande, ignorez cet email."
    )
    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:480px;margin:auto">
      <p>Bonjour <strong>{to_name}</strong>,</p>
      <p>Votre code de vérification est :</p>
      <p style="font-size:28px;font-weight:bold;letter-spacing:4px;color:#ea580c">{code}</p>
      <p style="color:#78614a;font-size:13px">Ce code est valide 10 minutes.</p>
      <p style="color:#78614a;font-size:13px">Si vous n'êtes pas à l'origine de cette demande, ignorez cet email.</p>
    </div>
    """

    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(SMTP_EMAIL, SMTP_APP_PASSWORD)
        server.sendmail(SMTP_EMAIL, to_email, msg.as_string())


def send_generic_email(to_email: str, subject: str, body_text: str, to_name: str = ""):
    """
    Envoie un email générique (sujet + corps en texte brut) via la même
    configuration Gmail que send_verification_email. Utilisé notamment pour
    les convocations d'entretien générées par l'IA. Si to_name est fourni,
    l'en-tête "À" affiche "Nom Complet <email>" au lieu de la seule adresse.
    """
    if not SMTP_EMAIL or not SMTP_APP_PASSWORD:
        raise RuntimeError(
            "SMTP_EMAIL / SMTP_APP_PASSWORD manquants dans le fichier .env."
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_EMAIL
    msg["To"] = formataddr((to_name, to_email)) if to_name else to_email

    html_body = "<div style=\"font-family:Arial,sans-serif;max-width:520px;margin:auto;white-space:pre-wrap\">" \
        + body_text.replace("\n", "<br>") + "</div>"

    msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(SMTP_EMAIL, SMTP_APP_PASSWORD)
        server.sendmail(SMTP_EMAIL, to_email, msg.as_string())


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Override to log via standard logger instead of stderr
        logger.info("%s - - %s" % (self.address_string(), format%args))

    def end_headers(self):
        # CORS Headers
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS, DELETE')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        super().end_headers()
        
    def do_OPTIONS(self):
        """Respond to preflight CORS checks."""
        self.send_response(200)
        self.end_headers()
        
    def write_json(self, status_code: int, data: Any):
        """Helper to serialize data and write JSON response."""
        try:
            body = json.dumps(data).encode('utf-8')
            self.send_response(status_code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            logger.error(f"Error writing json response: {e}")
            
    def write_error(self, status_code: int, detail: str):
        """Helper to send JSON formatted error message."""
        self.write_json(status_code, {"detail": detail})

    def write_html(self, status_code: int, html_content: str):
        """Helper to send HTML response."""
        try:
            body = html_content.encode('utf-8')
            self.send_response(status_code)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            logger.error(f"Error writing html response: {e}")

    def read_json_body(self) -> dict:
        """Reads and parses a JSON request body. Raises ValueError on invalid JSON."""
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length else b''
        if not body:
            return {}
        return json.loads(body.decode('utf-8'))

    def do_GET(self):
        # Route 1: Main Server Status
        if self.path == "/" or self.path == "":
            self.write_json(200, {
                "message": "AI Resume Analyzer API is running successfully using pure Python server.",
                "status": "online"
            })
            return

        # Route 1.5: Offer Preview Page with Open Graph tags for LinkedIn
        if self.path.startswith("/offer-preview"):
            try:
                from urllib.parse import urlparse, parse_qs, unquote
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                
                title = unquote(params.get("title", ["Développeur Full Stack"])[0])
                diploma = unquote(params.get("diploma", ["Licence Informatique"])[0])
                skills = unquote(params.get("skills", ["Python, SQL, React"])[0])
                ref = unquote(params.get("ref", [""])[0])
                
                html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nous recrutons : {title}</title>

  <!-- Open Graph Meta Tags pour LinkedIn -->
  <meta property="og:title" content="Nous recrutons : {title}" />
  <meta property="og:description" content="Nous renforçons nos équipes ! Formation : {diploma} | Compétences clés : {skills}" />
  <meta property="og:type" content="article" />
  <meta property="og:site_name" content="Recrutement" />
  
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap" rel="stylesheet">
  
  <style>
    body {{
      margin: 0;
      padding: 40px 20px;
      background: #0f172a;
      font-family: 'Inter', sans-serif;
      display: flex;
      justify-content: center;
      align-items: center;
      min-height: 100vh;
    }}
    .poster-card {{
      max-width: 600px;
      width: 100%;
      background: #ffffff;
      border-radius: 20px;
      overflow: hidden;
      box-shadow: 0 20px 40px rgba(0,0,0,0.4);
    }}
  </style>
</head>
<body>
  <div class="poster-card">
    <div style="background: linear-gradient(135deg, #0e0d12 0%, #17151e 100%); padding: 44px 38px 36px; color: #ffffff; position: relative; overflow: hidden;">
      <div style="color: #ea580c; font-size: 0.88rem; font-weight: 800; letter-spacing: 3.5px; text-transform: uppercase; margin-bottom: 14px;">
        NOUS RECRUTONS
      </div>
      <h1 style="font-size: 2.6rem; line-height: 1.1; font-weight: 900; text-transform: uppercase; margin: 0 0 20px 0; color: #f97316;">
        {title}
      </h1>
      <p style="font-size: 1.05rem; color: #e2e8f0; line-height: 1.5; margin: 0;">
        Nous renforçons nos équipes et recherchons un(e) {title} pour nous accompagner dans nos projets.
      </p>
    </div>

    <div style="padding: 32px 38px 28px; background: #ffffff;">
      <h2 style="font-size: 1.1rem; font-weight: 800; text-transform: uppercase; letter-spacing: 2px; color: #0f172a; border-bottom: 4px solid #f97316; display: inline-block; padding-bottom: 4px; margin-bottom: 20px;">
        PROFIL RECHERCHÉ
      </h2>
      <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 24px;">
        <div style="background: #f1f5f9; border-left: 5px solid #ea580c; border-radius: 10px; padding: 14px;">
          <div style="font-size: 0.68rem; font-weight: 800; color: #ea580c;">DIPLÔME</div>
          <div style="font-size: 0.9rem; font-weight: 700; color: #0f172a;">{diploma}</div>
        </div>
        <div style="background: #f1f5f9; border-left: 5px solid #ea580c; border-radius: 10px; padding: 14px;">
          <div style="font-size: 0.68rem; font-weight: 800; color: #ea580c;">COMPÉTENCES CLÉS</div>
          <div style="font-size: 0.9rem; font-weight: 700; color: #0f172a;">{skills}</div>
        </div>
        <div style="background: #f1f5f9; border-left: 5px solid #ea580c; border-radius: 10px; padding: 14px;">
          <div style="font-size: 0.68rem; font-weight: 800; color: #ea580c;">LIEU</div>
          <div style="font-size: 0.9rem; font-weight: 700; color: #0f172a;">Casablanca, Maroc</div>
        </div>
      </div>
      <p style="font-size: 0.9rem; color: #475569;">
        Si cette opportunité vous correspond, n'hésitez pas à nous transmettre votre candidature. {f'(Réf : {ref})' if ref else ''}
      </p>
    </div>

    <div style="background: #0e0d12; border-top: 4px solid #ea580c; padding: 24px; text-align: center; color: #ffffff;">
      <div style="font-size: 1.05rem; font-weight: 900; color: #f97316; letter-spacing: 2px;">
        POSTULEZ MAINTENANT
      </div>
      <div style="font-size: 0.85rem; color: #94a3b8;">recrutement@votre-entreprise.com</div>
    </div>
  </div>
</body>
</html>"""
                self.write_html(200, html)
            except Exception as e:
                logger.error(f"Error serving offer preview: {e}")
                self.write_error(500, f"Erreur d'affichage de l'offre : {e}")
            return
            
        # Route 2: Get History List -> /api/history
        if self.path == "/api/history":
            if not require_auth(self):
                return
            try:
                conn = database.get_db_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT a.id, a.resume_id, r.filename, a.overall_score, 
                           a.job_description_match, a.created_at, a.summary,
                           a.candidate_name, a.skills, a.education, a.education_level, a.review_status,
                           a.email, a.phone, a.city, a.job_reference, a.job_title
                    FROM analyses a
                    JOIN resumes r ON a.resume_id = r.id
                    ORDER BY a.created_at DESC
                """)
                rows = cursor.fetchall()
                conn.close()
                
                history = []
                for row in rows:
                    jdm = json.loads(row['job_description_match']) if row['job_description_match'] else None
                    match_score = jdm.get('match_score') if jdm else None
                    try:
                        skills = json.loads(row['skills']) if row['skills'] else []
                    except (TypeError, ValueError, json.JSONDecodeError):
                        skills = []
                    try:
                        education = json.loads(row['education']) if row['education'] else []
                    except (TypeError, ValueError, json.JSONDecodeError):
                        education = []
                    
                    history.append({
                        "id": row['id'],
                        "resume_id": row['resume_id'],
                        "filename": row['filename'],
                        "candidate_name": row['candidate_name'] or row['filename'],
                        "overall_score": row['overall_score'],
                        "match_score": match_score,
                        "created_at": row['created_at'],
                        "summary": row['summary'],
                        "skills": skills,
                        "education": education,
                        "education_level": row['education_level'] if 'education_level' in row.keys() else "",
                        "job_description_match": jdm,
                        "review_status": row['review_status'] or 'a_etudier',
                        "email": row['email'] if 'email' in row.keys() else "",
                        "phone": row['phone'] if 'phone' in row.keys() else "",
                        "city": row['city'] if 'city' in row.keys() else "",
                        "job_reference": row['job_reference'] if 'job_reference' in row.keys() else "",
                        "status": "completed",
                        "job_title": row['job_title'] if 'job_title' in row.keys() else None
                    })
                self.write_json(200, history)
            except Exception as e:
                logger.error(f"Failed to fetch history: {e}")
                self.write_error(500, f"Erreur de base de données : {e}")
            return

        # Route 3: Dashboard KPIs -> /api/dashboard/kpis
        if self.path == "/api/dashboard/kpis":
            if not require_auth(self):
                return
            try:
                conn = database.get_db_connection()
                cursor = conn.cursor()

                cursor.execute("SELECT COUNT(*) as total_resumes FROM resumes")
                total_resumes = cursor.fetchone()['total_resumes']

                cursor.execute("""
                    SELECT COUNT(*) as total_analyses,
                           AVG(overall_score) as avg_score
                    FROM analyses
                """)
                row = cursor.fetchone()
                total_analyses = row['total_analyses'] or 0
                avg_score = round(row['avg_score'], 1) if row['avg_score'] is not None else 0

                # Average match score, computed from the JSON-stored job_description_match
                cursor.execute("SELECT job_description_match FROM analyses WHERE job_description_match IS NOT NULL")
                match_rows = cursor.fetchall()
                match_scores = []
                for r in match_rows:
                    try:
                        parsed = json.loads(r['job_description_match']) if r['job_description_match'] else None
                        if parsed and parsed.get('match_score') is not None:
                            match_scores.append(parsed['match_score'])
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                avg_match = round(sum(match_scores) / len(match_scores), 1) if match_scores else None

                conn.close()

                self.write_json(200, {
                    "total_resumes": total_resumes,
                    "analyzed_resumes": total_analyses,
                    "average_ats_score": avg_score,
                    "average_match_score": avg_match if avg_match is not None else 0
                })
            except Exception as e:
                logger.error(f"Failed to fetch dashboard KPIs: {e}")
                self.write_error(500, f"Erreur de base de données : {e}")
            return

        # Route 4: Get Detailed Analysis -> /api/history/{id}
        match_detail = re.match(r"^/api/history/(\d+)$", self.path)
        if match_detail:
            if not require_auth(self):
                return
            analysis_id = int(match_detail.group(1))
            try:
                conn = database.get_db_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT a.*, r.filename, r.parsed_text, r.uploaded_at
                    FROM analyses a
                    JOIN resumes r ON a.resume_id = r.id
                    WHERE a.id = ?
                """, (analysis_id,))
                row = cursor.fetchone()
                conn.close()
                
                if not row:
                    self.write_error(404, "Enregistrement introuvable.")
                    return
                    
                def jl(col):
                    try:
                        return json.loads(col) if col else []
                    except Exception:
                        return []
                jdm_raw = row['job_description_match'] if 'job_description_match' in row.keys() else None
                response = {
                    "id": row['id'],
                    "resume_id": row['resume_id'],
                    "overall_score": row['overall_score'],
                    "candidate_name": row['candidate_name'] if 'candidate_name' in row.keys() else "",
                    "summary": row['summary'],
                    "skills": jl(row['skills']),
                    "experience": jl(row['experience']),
                    "education": jl(row['education']),
                    "projects": jl(row['projects'] if 'projects' in row.keys() else None),
                    "certifications": jl(row['certifications'] if 'certifications' in row.keys() else None),
                    "languages": jl(row['languages'] if 'languages' in row.keys() else None),
                    "qualities": jl(row['qualities'] if 'qualities' in row.keys() else None),
                    "strengths": jl(row['strengths']),
                    "weaknesses": jl(row['weaknesses']),
                    "recommendations": jl(row['recommendations']),
                    "job_description_match": json.loads(jdm_raw) if jdm_raw else None,
                    "match_score": row['match_score'] if 'match_score' in row.keys() else None,
                    "status": row['status'] if 'status' in row.keys() else 'completed',
                    "chat_history": jl(row['chat_history'] if 'chat_history' in row.keys() else None),
                    "created_at": row['created_at'],
                    "resume": {
                        "id": row['resume_id'],
                        "filename": row['filename'],
                        "uploaded_at": row['uploaded_at']
                    }
                }
                self.write_json(200, response)
            except Exception as e:
                logger.error(f"Error fetching detailed analysis: {e}")
                self.write_error(500, f"Erreur serveur : {e}")
            return
            
        # Route: Liste des entretiens planifiés -> /api/interviews
        if self.path == "/api/interviews":
            if not require_auth(self):
                return
            try:
                conn = database.get_db_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT i.id, i.analysis_id, i.interview_date, i.interview_time,
                           i.format, i.location_link, i.notes, i.created_at,
                           i.job_title as intv_job_title, i.candidate_name as intv_candidate_name,
                           a.candidate_name, a.job_title, a.job_reference, a.job_description_match
                    FROM interviews i
                    JOIN analyses a ON i.analysis_id = a.id
                    ORDER BY i.interview_date ASC, i.interview_time ASC
                """)
                rows = cursor.fetchall()
                conn.close()
                interviews = []
                for row in rows:
                    cand_name = row['candidate_name'] or row['intv_candidate_name'] or "Candidat Anonyme"
                    j_title = row['job_title'] or row['intv_job_title']
                    if not j_title and row['job_description_match']:
                        try:
                            jdata = json.loads(row['job_description_match'])
                            roles = jdata.get('fitting_roles', [])
                            if roles and len(roles) > 0 and roles[0]:
                                j_title = roles[0]
                        except Exception:
                            pass
                    j_title = j_title or "Poste non spécifié"

                    j_ref = row['job_reference'] if ('job_reference' in row.keys() and row['job_reference']) else ""

                    interviews.append({
                        "id": row['id'],
                        "analysis_id": row['analysis_id'],
                        "interview_date": row['interview_date'],
                        "interview_time": row['interview_time'],
                        "format": row['format'],
                        "location_link": row['location_link'],
                        "notes": row['notes'],
                        "created_at": row['created_at'],
                        "candidate_name": cand_name,
                        "job_title": j_title,
                        "job_reference": j_ref,
                        "reference": j_ref,
                    })
                self.write_json(200, interviews)
            except Exception as e:
                logger.error(f"Failed to fetch interviews: {e}")
                self.write_error(500, f"Erreur de base de données : {e}")
            return

        # Path not found
        self.write_error(404, "Ressource introuvable.")

    def do_POST(self):
        # Route 1: Upload & Analyze -> /api/analyze
        if self.path == "/api/analyze":
            if not require_auth(self):
                return
            content_type = self.headers.get('Content-Type', '')
            if 'multipart/form-data' not in content_type:
                self.write_error(400, "Content-Type doit être multipart/form-data")
                return
                
            boundary_match = re.search(r'boundary=([^;]+)', content_type)
            if not boundary_match:
                self.write_error(400, "Paramètre boundary manquant dans le Content-Type")
                return
                
            boundary = boundary_match.group(1)
            content_length = int(self.headers.get('Content-Length', 0))
            
            try:
                body = self.rfile.read(content_length)
                form_fields = parse_multipart(body, boundary)
            except Exception as e:
                logger.error(f"Failed to read post body or parse multipart: {e}")
                self.write_error(400, f"Échec de lecture des données de formulaire : {e}")
                return
                
            file_data = get_field(form_fields, 'file')
            if not file_data or not isinstance(file_data, dict):
                self.write_error(400, "Fichier CV manquant dans la requête.")
                return
                
            filename = file_data['filename']
            file_bytes = file_data['content']

            # Optional: job description supplied as pasted text and/or a PDF/DOCX file.
            try:
                job_description = extract_job_description(form_fields)
            except Exception as e:
                logger.error(f"Failed to extract text from job description file: {e}")
                self.write_error(422, f"Impossible de lire le fichier de fiche de poste : {e}")
                return
            
            logger.info(f"Uploading file '{filename}' ({len(file_bytes)} bytes)")
            job_reference = (get_field(form_fields, 'job_reference', '') or '').strip()
            job_title = (get_field(form_fields, 'job_title', '') or '').strip()

            try:
                response_payload = analyze_and_store_resume(filename, file_bytes, job_description, job_reference, job_title)
            except AnalysisError as ae:
                self.write_error(ae.status_code, ae.message)
                return

            self.write_json(201, response_payload)
            return

        # Route 1b: Batch upload & analyze several CVs at once -> /api/analyze-batch
        if self.path == "/api/analyze-batch":
            if not require_auth(self):
                return
            content_type = self.headers.get('Content-Type', '')
            if 'multipart/form-data' not in content_type:
                self.write_error(400, "Content-Type doit être multipart/form-data")
                return

            boundary_match = re.search(r'boundary=([^;]+)', content_type)
            if not boundary_match:
                self.write_error(400, "Paramètre boundary manquant dans le Content-Type")
                return

            boundary = boundary_match.group(1)
            content_length = int(self.headers.get('Content-Length', 0))

            try:
                body = self.rfile.read(content_length)
                form_fields = parse_multipart(body, boundary)
            except Exception as e:
                logger.error(f"Failed to read post body or parse multipart: {e}")
                self.write_error(400, f"Échec de lecture des données de formulaire : {e}")
                return

            files = [f for f in get_all(form_fields, 'files') if isinstance(f, dict)]
            if not files:
                self.write_error(400, "Aucun CV fourni. Sélectionnez un ou plusieurs fichiers PDF/DOCX.")
                return
            if len(files) > 20:
                self.write_error(400, "Trop de fichiers en une seule fois (maximum 20 CV par lot).")
                return

            try:
                job_description = extract_job_description(form_fields)
            except Exception as e:
                logger.error(f"Failed to extract text from job description file: {e}")
                self.write_error(422, f"Impossible de lire le fichier de fiche de poste : {e}")
                return

            logger.info(f"Batch analyzing {len(files)} resumes")
            job_reference = (get_field(form_fields, 'job_reference', '') or '').strip()
            job_title = (get_field(form_fields, 'job_title', '') or '').strip()

            results = []
            errors = []
            for f in files:
                fname = f.get('filename') or 'CV sans nom'
                try:
                    payload = analyze_and_store_resume(fname, f['content'], job_description, job_reference, job_title)
                    results.append(payload)
                except AnalysisError as ae:
                    errors.append({"filename": fname, "detail": ae.message})
                except Exception as e:
                    logger.error(f"Unexpected batch analysis error for '{fname}': {e}")
                    errors.append({"filename": fname, "detail": str(e)})

            self.write_json(201, {
                "results": results,
                "errors": errors,
                "total": len(files),
                "succeeded": len(results)
            })
            return

        # Route 2: Chat with AI -> /api/history/{id}/chat
        match_chat = re.match(r"^/api/history/(\d+)/chat$", self.path)
        if match_chat:
            if not require_auth(self):
                return
            analysis_id = int(match_chat.group(1))
            content_length = int(self.headers.get('Content-Length', 0))
            try:
                body = self.rfile.read(content_length)
                payload = json.loads(body.decode('utf-8'))
                user_msg = payload.get('message', '').strip()
                if not user_msg:
                    self.write_error(400, "Le message ne peut pas être vide.")
                    return

                conn = database.get_db_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT a.chat_history, r.parsed_text
                    FROM analyses a
                    JOIN resumes r ON a.resume_id = r.id
                    WHERE a.id = ?
                """, (analysis_id,))
                row = cursor.fetchone()
                if not row:
                    conn.close()
                    self.write_error(404, "Analyse introuvable.")
                    return

                chat_history = json.loads(row['chat_history']) if row['chat_history'] else []
                resume_text = row['parsed_text']

                reply = services.chat_with_resume_ai(resume_text, chat_history, user_msg)

                now_str = datetime.datetime.now().isoformat()
                chat_history.append({"sender": "user", "message": user_msg, "timestamp": now_str})
                chat_history.append({"sender": "ai", "message": reply, "timestamp": now_str})

                cursor.execute("""
                    UPDATE analyses SET chat_history = ? WHERE id = ?
                """, (json.dumps(chat_history), analysis_id))
                conn.commit()
                conn.close()

                self.write_json(200, {
                    "response": reply,
                    "chat_history": chat_history
                })
            except Exception as e:
                logger.error(f"Chat failed for analysis {analysis_id}: {e}")
                self.write_error(500, f"Erreur lors du chat : {e}")
            return

        # Route: Inscription -> /api/auth/signup
        if self.path == "/api/auth/signup":
            try:
                payload = self.read_json_body()
            except (ValueError, json.JSONDecodeError):
                self.write_error(400, "Corps de requête JSON invalide.")
                return

            name = (payload.get('name') or '').strip()
            email = (payload.get('email') or '').strip().lower()
            password = payload.get('password') or ''

            if not name or not email or not password:
                self.write_error(400, "Veuillez remplir tous les champs.")
                return
            if len(password) < 4:
                self.write_error(400, "Mot de passe trop court (4 caractères minimum).")
                return

            try:
                conn = database.get_db_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT id, verified FROM users WHERE email = ?", (email,))
                existing = cursor.fetchone()

                code = generate_verification_code()
                expiry = code_expiry_timestamp()
                pw_hash, pw_salt = hash_password(password)

                if existing:
                    if existing['verified']:
                        conn.close()
                        self.write_error(409, "Un compte existe déjà avec cet email.")
                        return
                    # Compte existant mais jamais vérifié : on réutilise la ligne,
                    # on met à jour les infos et on relance un nouveau code.
                    cursor.execute("""
                        UPDATE users
                        SET name = ?, password_hash = ?, password_salt = ?,
                            verification_code = ?, code_expiry = ?
                        WHERE email = ?
                    """, (name, pw_hash, pw_salt, code, expiry, email))
                else:
                    cursor.execute("""
                        INSERT INTO users (name, email, password_hash, password_salt, verified, verification_code, code_expiry)
                        VALUES (?, ?, ?, ?, 0, ?, ?)
                    """, (name, email, pw_hash, pw_salt, code, expiry))

                conn.commit()
                conn.close()

                try:
                    send_verification_email(email, name, code)
                except Exception as email_err:
                    logger.error(f"Failed to send verification email to '{email}': {email_err}")
                    self.write_error(500, f"Compte créé, mais l'envoi de l'email a échoué : {email_err}")
                    return

                self.write_json(201, {"email": email, "name": name})
            except Exception as e:
                logger.error(f"Signup failed for '{email}': {e}")
                self.write_error(500, f"Erreur lors de l'inscription : {e}")
            return

        # Route: Connexion -> /api/auth/login
        if self.path == "/api/auth/login":
            try:
                payload = self.read_json_body()
            except (ValueError, json.JSONDecodeError):
                self.write_error(400, "Corps de requête JSON invalide.")
                return

            email = (payload.get('email') or '').strip().lower()
            password = payload.get('password') or ''

            if not email or not password:
                self.write_error(400, "Veuillez remplir tous les champs.")
                return

            try:
                conn = database.get_db_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
                user = cursor.fetchone()
                conn.close()

                if not user or not verify_password(password, user['password_salt'], user['password_hash']):
                    self.write_error(401, "Email ou mot de passe incorrect.")
                    return

                if not user['verified']:
                    self.write_error(403, "Ce compte n'est pas encore vérifié. Vérifiez votre email.")
                    return

                token = create_session(user['email'], user['name'])
                self.write_json(200, {"name": user['name'], "email": user['email'], "token": token})
            except Exception as e:
                logger.error(f"Login failed for '{email}': {e}")
                self.write_error(500, f"Erreur lors de la connexion : {e}")
            return

        # Route: Vérification du code -> /api/auth/verify
        if self.path == "/api/auth/verify":
            try:
                payload = self.read_json_body()
            except (ValueError, json.JSONDecodeError):
                self.write_error(400, "Corps de requête JSON invalide.")
                return

            email = (payload.get('email') or '').strip().lower()
            code = (payload.get('code') or '').strip()

            if not email or not code:
                self.write_error(400, "Email et code requis.")
                return

            try:
                conn = database.get_db_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
                user = cursor.fetchone()

                if not user:
                    conn.close()
                    self.write_error(404, "Compte introuvable.")
                    return

                if user['verified']:
                    conn.close()
                    token = create_session(user['email'], user['name'])
                    self.write_json(200, {"name": user['name'], "email": user['email'], "token": token})
                    return

                if is_code_expired(user['code_expiry']) or code != (user['verification_code'] or ''):
                    conn.close()
                    self.write_error(400, "Code incorrect ou expiré.")
                    return

                cursor.execute("""
                    UPDATE users SET verified = 1, verification_code = NULL, code_expiry = NULL
                    WHERE email = ?
                """, (email,))
                conn.commit()
                conn.close()

                token = create_session(user['email'], user['name'])
                self.write_json(200, {"name": user['name'], "email": user['email'], "token": token})
            except Exception as e:
                logger.error(f"Verification failed for '{email}': {e}")
                self.write_error(500, f"Erreur lors de la vérification : {e}")
            return

        # Route: Planifier un entretien -> /api/history/{id}/schedule-interview
        match_interview = re.match(r"^/api/history/(\d+)/schedule-interview$", self.path)
        if match_interview:
            if not require_auth(self):
                return
            print("*" * 60, flush=True)
            print("*** ROUTE SCHEDULE-INTERVIEW ATTEINTE ***", flush=True)
            print("*" * 60, flush=True)
            analysis_id = int(match_interview.group(1))
            try:
                payload = self.read_json_body()
            except (ValueError, json.JSONDecodeError):
                self.write_error(400, "Corps de requête JSON invalide.")
                return

            interview_date = (payload.get('interview_date') or '').strip()
            interview_time = (payload.get('interview_time') or '').strip()
            interview_format = (payload.get('interview_format') or '').strip()
            interview_location = (payload.get('interview_location') or '').strip()
            interview_notes = (payload.get('interview_notes') or '').strip()

            if not interview_date or not interview_time:
                self.write_error(400, "La date et l'heure de l'entretien sont requises.")
                return

            try:
                conn = database.get_db_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT candidate_name, email, job_title, job_reference FROM analyses WHERE id = ?
                """, (analysis_id,))
                row = cursor.fetchone()
                if not row:
                    conn.close()
                    self.write_error(404, "Analyse introuvable.")
                    return

                candidate_name = row['candidate_name'] or "Candidat(e)"
                candidate_email = row['email'] if 'email' in row.keys() else ""
                existing_job_title = row['job_title'] if ('job_title' in row.keys() and row['job_title']) else ""
                existing_job_reference = row['job_reference'] if ('job_reference' in row.keys() and row['job_reference']) else ""

                # Génère l'email de convocation via l'IA (avec repli automatique)
                invitation = services.generate_interview_invitation(
                    candidate_name=candidate_name,
                    job_title=existing_job_title,
                    interview_date=interview_date,
                    interview_time=interview_time,
                    interview_format=interview_format,
                    interview_location=interview_location,
                    interview_notes=interview_notes,
                    reference=existing_job_reference,
                )

                # Enregistre l'entretien dans la table dédiée "interviews"
                cursor.execute("""
                    INSERT INTO interviews (analysis_id, interview_date, interview_time, format, location_link, notes, job_title, candidate_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (analysis_id, interview_date, interview_time, interview_format, interview_location, interview_notes, existing_job_title, candidate_name))

                # Passe le statut de suivi RH à "En entretien"
                cursor.execute("UPDATE analyses SET review_status = 'en_entretien' WHERE id = ?", (analysis_id,))
                conn.commit()
                conn.close()

                email_sent = False
                email_error = None
                if candidate_email:
                    try:
                        send_generic_email(candidate_email, invitation["subject"], invitation["body"], to_name=candidate_name)
                        email_sent = True
                    except Exception as email_err:
                        logger.error(f"Échec de l'envoi de la convocation à '{candidate_email}': {email_err}")
                        email_error = str(email_err)
                else:
                    email_error = "Aucun email détecté pour ce candidat — convocation générée mais non envoyée."

                self.write_json(200, {
                    "id": analysis_id,
                    "review_status": "en_entretien",
                    "email_sent": email_sent,
                    "email_error": email_error,
                    "subject": invitation["subject"],
                    "body": invitation["body"],
                })
            except Exception as e:
                logger.error(f"Failed to schedule interview for analysis {analysis_id}: {e}")
                self.write_error(500, f"Erreur lors de la planification de l'entretien : {e}")
            return

        # Route: Mettre à jour le statut de suivi RH -> /api/history/{id}/status
        match_status = re.match(r"^/api/history/(\d+)/status$", self.path)
        if match_status:
            if not require_auth(self):
                return
            analysis_id = int(match_status.group(1))
            try:
                payload = self.read_json_body()
            except (ValueError, json.JSONDecodeError):
                self.write_error(400, "Corps de requête JSON invalide.")
                return

            new_status = (payload.get('review_status') or '').strip()
            allowed = {'a_etudier', 'en_entretien', 'retenu', 'refuse'}
            if new_status not in allowed:
                self.write_error(400, f"Statut invalide. Valeurs autorisées : {', '.join(sorted(allowed))}.")
                return

            try:
                conn = database.get_db_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT id, candidate_name, email, job_title, job_reference FROM analyses WHERE id = ?", (analysis_id,))
                row = cursor.fetchone()
                if not row:
                    conn.close()
                    self.write_error(404, "Analyse introuvable.")
                    return
                cursor.execute("UPDATE analyses SET review_status = ? WHERE id = ?", (new_status, analysis_id))
                conn.commit()
                conn.close()

                email_sent = False
                email_error = None

                # Si le statut passe à "Refusé", on génère et envoie
                # automatiquement l'email de refus au candidat.
                if new_status == 'refuse':
                    candidate_name = row['candidate_name'] or "Candidat(e)"
                    candidate_email = row['email'] if 'email' in row.keys() else ""
                    job_title = row['job_title'] if 'job_title' in row.keys() and row['job_title'] else ""
                    job_reference = row['job_reference'] if 'job_reference' in row.keys() and row['job_reference'] else ""

                    rejection = services.generate_rejection_email(candidate_name, job_title, job_reference)
                    if candidate_email:
                        try:
                            send_generic_email(candidate_email, rejection["subject"], rejection["body"], to_name=candidate_name)
                            email_sent = True
                            logger.info(f"Email de refus envoyé avec succès à {candidate_email}")
                        except Exception as email_err:
                            logger.error(f"Échec de l'envoi de l'email de refus à '{candidate_email}': {email_err}")
                            email_error = str(email_err)
                    else:
                        email_error = "Aucun email détecté pour ce candidat."

                self.write_json(200, {
                    "id": analysis_id,
                    "review_status": new_status,
                    "email_sent": email_sent,
                    "email_error": email_error,
                })
            except Exception as e:
                logger.error(f"Failed to update review_status for analysis {analysis_id}: {e}")
                self.write_error(500, f"Erreur lors de la mise à jour du statut : {e}")
            return

        # Route: Renvoyer un nouveau code -> /api/auth/resend-code
        if self.path == "/api/auth/resend-code":
            try:
                payload = self.read_json_body()
            except (ValueError, json.JSONDecodeError):
                self.write_error(400, "Corps de requête JSON invalide.")
                return

            email = (payload.get('email') or '').strip().lower()
            if not email:
                self.write_error(400, "Email requis.")
                return

            try:
                conn = database.get_db_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
                user = cursor.fetchone()

                if not user:
                    conn.close()
                    self.write_error(404, "Compte introuvable.")
                    return
                if user['verified']:
                    conn.close()
                    self.write_error(400, "Ce compte est déjà vérifié.")
                    return

                code = generate_verification_code()
                expiry = code_expiry_timestamp()
                cursor.execute("""
                    UPDATE users SET verification_code = ?, code_expiry = ? WHERE email = ?
                """, (code, expiry, email))
                conn.commit()
                conn.close()

                try:
                    send_verification_email(email, user['name'], code)
                except Exception as email_err:
                    logger.error(f"Failed to resend verification email to '{email}': {email_err}")
                    self.write_error(500, f"L'envoi de l'email a échoué : {email_err}")
                    return

                self.write_json(200, {"name": user['name'], "email": user['email']})
            except Exception as e:
                logger.error(f"Resend code failed for '{email}': {e}")
                self.write_error(500, f"Erreur lors du renvoi du code : {e}")
            return

        # Route: Planifier un entretien -> /api/interviews
        if self.path == "/api/interviews":
            if not require_auth(self):
                return
            try:
                payload = self.read_json_body()
            except (ValueError, json.JSONDecodeError):
                self.write_error(400, "Corps de requête JSON invalide.")
                return

            analysis_id = payload.get('analysis_id')
            interview_date = (payload.get('interview_date') or '').strip()
            interview_time = (payload.get('interview_time') or '').strip()
            interview_format = (payload.get('format') or '').strip()
            interview_location = (payload.get('location_link') or '').strip()
            interview_notes = (payload.get('notes') or '').strip()
            job_title_input = (payload.get('job_title') or '').strip()
            reference_input = (payload.get('reference') or '').strip()

            if not analysis_id or not interview_date or not interview_time:
                self.write_error(400, "analysis_id, la date et l'heure sont requis.")
                return

            try:
                analysis_id = int(analysis_id)
            except (TypeError, ValueError):
                self.write_error(400, "analysis_id invalide.")
                return

            try:
                conn = database.get_db_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT candidate_name, email, job_title, job_reference FROM analyses WHERE id = ?", (analysis_id,))
                row = cursor.fetchone()
                if not row:
                    conn.close()
                    self.write_error(404, "Analyse introuvable.")
                    return

                candidate_name = row['candidate_name'] or "Candidat(e)"
                candidate_email = row['email'] if 'email' in row.keys() else ""
                # Si un nouveau poste est fourni dans le formulaire, on le
                # sauvegarde ; sinon on garde celui déjà enregistré (s'il existe).
                existing_job_title = row['job_title'] if 'job_title' in row.keys() and row['job_title'] else ""
                final_job_title = job_title_input or existing_job_title
                existing_job_reference = row['job_reference'] if 'job_reference' in row.keys() and row['job_reference'] else ""

                logger.info(f"[DIAGNOSTIC ENTRETIEN] analysis_id={analysis_id} candidate_name={candidate_name!r} existing_job_reference={existing_job_reference!r} existing_job_title={existing_job_title!r}")

                # Référence : priorité à celle saisie manuellement dans CE
                # formulaire, sinon à la vraie référence de l'offre d'emploi
                # (remplie lors de l'analyse), sinon générée automatiquement
                # en dernier recours (ex: "DFS" + date de l'entretien).
                if reference_input:
                    reference = reference_input
                elif existing_job_reference:
                    reference = existing_job_reference
                else:
                    stop_words = {'de', 'du', 'des', 'le', 'la', 'les', 'en', 'et', 'un', 'une', 'à', 'a'}
                    abbr = ''.join(w[0].upper() for w in final_job_title.split() if w.lower() not in stop_words)[:6]
                    date_digits = ''.join(reversed(interview_date.split('-'))) if '-' in interview_date else interview_date.replace('-', '')
                    reference = f"{abbr}{date_digits}" if abbr else ""

                invitation = services.generate_interview_invitation(
                    candidate_name=candidate_name,
                    job_title=final_job_title,
                    interview_date=interview_date,
                    interview_time=interview_time,
                    interview_format=interview_format,
                    interview_location=interview_location,
                    interview_notes=interview_notes,
                    reference=reference,
                )

                cursor.execute("""
                    INSERT INTO interviews (analysis_id, interview_date, interview_time, format, location_link, notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (analysis_id, interview_date, interview_time, interview_format, interview_location, interview_notes))

                if job_title_input:
                    cursor.execute("UPDATE analyses SET job_title = ? WHERE id = ?", (job_title_input, analysis_id))

                cursor.execute("UPDATE analyses SET review_status = 'en_entretien' WHERE id = ?", (analysis_id,))
                conn.commit()
                conn.close()

                email_sent = False
                email_error = None
                if candidate_email:
                    try:
                        send_generic_email(candidate_email, invitation["subject"], invitation["body"], to_name=candidate_name)
                        email_sent = True
                    except Exception as email_err:
                        logger.error(f"Échec de l'envoi de la convocation à '{candidate_email}': {email_err}")
                        email_error = str(email_err)
                else:
                    email_error = "Aucun email détecté pour ce candidat — convocation générée mais non envoyée."

                self.write_json(201, {
                    "message": "Entretien planifié avec succès.",
                    "analysis_id": analysis_id,
                    "review_status": "en_entretien",
                    "email_sent": email_sent,
                    "email_error": email_error,
                    "subject": invitation["subject"],
                    "body": invitation["body"],
                })
            except Exception as e:
                logger.error(f"Failed to schedule interview for analysis {analysis_id}: {e}")
                self.write_error(500, f"Erreur lors de la planification de l'entretien : {e}")
            return

        # Path not found (FIX: sans ce fallback, toute requête POST vers une route
        # inconnue restait sans réponse et le navigateur bloquait jusqu'au timeout)
        self.write_error(404, "Ressource introuvable.")

    def do_DELETE(self):
        # Route 1: Delete analysis -> /api/history/{id}
        match_delete = re.match(r"^/api/history/(\d+)$", self.path)
        if match_delete:
            if not require_auth(self):
                return
            analysis_id = int(match_delete.group(1))
            try:
                conn = database.get_db_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT resume_id FROM analyses WHERE id = ?", (analysis_id,))
                row = cursor.fetchone()
                if not row:
                    conn.close()
                    self.write_json(200, {"message": "Analyse déjà supprimée ou introuvable.", "id": analysis_id})
                    return
                resume_id = row['resume_id']
                cursor.execute("DELETE FROM resumes WHERE id = ?", (resume_id,))
                conn.commit()
                conn.close()
                self.write_json(200, {"message": "Analyse supprimée avec succès.", "id": analysis_id})
            except Exception as e:
                logger.error(f"Failed to delete analysis {analysis_id}: {e}")
                self.write_error(500, f"Erreur de suppression : {e}")
            return

        # Route 2: Supprimer un entretien planifié -> /api/interviews/{id}
        match_delete_interview = re.match(r"^/api/interviews/(\d+)$", self.path)
        if match_delete_interview:
            if not require_auth(self):
                return
            interview_id = int(match_delete_interview.group(1))
            try:
                conn = database.get_db_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM interviews WHERE id = ?", (interview_id,))
                if not cursor.fetchone():
                    conn.close()
                    self.write_error(404, "Entretien introuvable.")
                    return
                cursor.execute("DELETE FROM interviews WHERE id = ?", (interview_id,))
                conn.commit()
                conn.close()
                self.write_json(200, {"message": "Entretien supprimé avec succès.", "id": interview_id})
            except Exception as e:
                logger.error(f"Failed to delete interview {interview_id}: {e}")
                self.write_error(500, f"Erreur de suppression : {e}")
            return

        self.write_error(404, "Ressource introuvable.")

def run(server_class=ThreadedHTTPServer, handler_class=RequestHandler, port=None):
    # Les plateformes d'hébergement (Render, Railway, Heroku...) imposent le
    # port d'écoute via la variable d'environnement PORT. On retombe sur 8000
    # en local si elle n'est pas définie.
    if port is None:
        port = int(os.getenv("PORT", 8000))
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    logger.info(f"Serveur démarré sur le port {port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()
    logger.info("Serveur arrêté.")

if __name__ == '__main__':
    run()