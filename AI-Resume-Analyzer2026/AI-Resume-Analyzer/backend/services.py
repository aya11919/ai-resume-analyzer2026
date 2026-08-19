import io
import os
import re
import json
import zlib
import time
import base64
import logging
import urllib.request
import urllib.error
import zipfile
import xml.etree.ElementTree as ET
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

def _decode_pdf_literal_string(raw: str) -> str:
    out = []
    i = 0
    n = len(raw)
    while i < n:
        c = raw[i]
        if c == '\\' and i + 1 < n:
            nxt = raw[i + 1]
            if nxt == 'n':
                out.append('\n'); i += 2; continue
            if nxt == 'r':
                out.append('\n'); i += 2; continue
            if nxt == 't':
                out.append('\t'); i += 2; continue
            if nxt in ('(', ')', '\\'):
                out.append(nxt); i += 2; continue
            if nxt.isdigit():
                j = i + 1
                digits = ''
                while j < n and len(digits) < 3 and raw[j].isdigit():
                    digits += raw[j]; j += 1
                try:
                    out.append(chr(int(digits, 8) & 0xFF))
                except ValueError:
                    pass
                i = j; continue
            out.append(nxt); i += 2; continue
        out.append(c)
        i += 1
    return ''.join(out)

def _decode_pdf_hex_string(raw: str) -> str:
    hex_digits = re.sub(r'\s+', '', raw)
    if len(hex_digits) % 2 != 0:
        hex_digits += '0'
    chars = []
    for i in range(0, len(hex_digits), 2):
        try:
            chars.append(chr(int(hex_digits[i:i + 2], 16)))
        except ValueError:
            continue
    return ''.join(chars)

def _parse_tounicode_cmap(cmap_text: str) -> (dict, int):
    mapping: Dict[bytes, str] = {}
    byte_width = 1

    space_m = re.search(r'begincodespacerange(.*?)endcodespacerange', cmap_text, re.DOTALL)
    if space_m:
        first_range = re.search(r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', space_m.group(1))
        if first_range:
            byte_width = max(1, len(first_range.group(1)) // 2)

    for block in re.finditer(r'beginbfchar(.*?)endbfchar', cmap_text, re.DOTALL):
        for pair in re.finditer(r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', block.group(1)):
            try:
                src = bytes.fromhex(pair.group(1))
                dst_hex = pair.group(2)
                if len(dst_hex) % 2 != 0:
                    dst_hex += '0'
                mapping[src] = bytes.fromhex(dst_hex).decode('utf-16-be', errors='ignore')
            except Exception:
                continue

    for block in re.finditer(r'beginbfrange(.*?)endbfrange', cmap_text, re.DOTALL):
        body = block.group(1)
        for m in re.finditer(r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]', body, re.DOTALL):
            lo_hex, hi_hex, arr = m.group(1), m.group(2), m.group(3)
            width = len(lo_hex) // 2
            lo_i, hi_i = int(lo_hex, 16), int(hi_hex, 16)
            dsts = re.findall(r'<([0-9A-Fa-f]+)>', arr)
            for offset, code in enumerate(range(lo_i, hi_i + 1)):
                if offset >= len(dsts):
                    break
                try:
                    src = code.to_bytes(width, 'big')
                    dst_hex = dsts[offset]
                    if len(dst_hex) % 2 != 0:
                        dst_hex += '0'
                    mapping[src] = bytes.fromhex(dst_hex).decode('utf-16-be', errors='ignore')
                except Exception:
                    continue
        body_no_arrays = re.sub(r'\[(.*?)\]', '', body, flags=re.DOTALL)
        for m in re.finditer(r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', body_no_arrays):
            lo_hex, hi_hex, dst_hex = m.group(1), m.group(2), m.group(3)
            width = len(lo_hex) // 2
            lo_i, hi_i = int(lo_hex, 16), int(hi_hex, 16)
            try:
                dst_start = int(dst_hex, 16)
                for offset, code in enumerate(range(lo_i, hi_i + 1)):
                    src = code.to_bytes(width, 'big')
                    mapping[src] = chr(dst_start + offset)
            except Exception:
                continue

    return mapping, byte_width

def _apply_tounicode(raw_code_str: str, tounicode_map: dict, byte_width: int) -> str:
    if not tounicode_map:
        return raw_code_str
    raw_bytes = raw_code_str.encode('latin-1', errors='ignore')
    out = []
    step = byte_width if byte_width > 0 else 1
    for i in range(0, len(raw_bytes) - step + 1, step):
        code = raw_bytes[i:i + step]
        out.append(tounicode_map.get(code, ''))
    return ''.join(out)

def _extract_text_from_content_stream(content: bytes, tounicode_map: dict, byte_width: int) -> str:
    try:
        text = content.decode('latin-1', errors='ignore')
    except Exception:
        return ''

    fragments = []

    for m in re.finditer(r'\((?:[^()\\]|\\.)*\)\s*Tj', text, re.DOTALL):
        literal = m.group(0)
        start = literal.find('(')
        end = literal.rfind(')')
        if start != -1 and end != -1 and end > start:
            raw_codes = _decode_pdf_literal_string(literal[start + 1:end])
            fragments.append(_apply_tounicode(raw_codes, tounicode_map, byte_width))
            fragments.append('\n')

    for m in re.finditer(r'\[((?:[^\[\]]|\\.)*)\]\s*TJ', text, re.DOTALL):
        array_body = m.group(1)
        for piece in re.finditer(r'\((?:[^()\\]|\\.)*\)|<[0-9A-Fa-f\s]+>', array_body, re.DOTALL):
            token = piece.group(0)
            if token.startswith('('):
                raw_codes = _decode_pdf_literal_string(token[1:-1])
            else:
                raw_codes = _decode_pdf_hex_string(token[1:-1])
            fragments.append(_apply_tounicode(raw_codes, tounicode_map, byte_width))
        fragments.append('\n')

    for m in re.finditer(r'<([0-9A-Fa-f\s]+)>\s*Tj', text):
        raw_codes = _decode_pdf_hex_string(m.group(1))
        fragments.append(_apply_tounicode(raw_codes, tounicode_map, byte_width))
        fragments.append('\n')

    return ''.join(fragments)

def _ascii85_decode(data: bytes) -> bytes:
    import base64
    cleaned = data.strip()
    if cleaned.endswith(b'~>'):
        cleaned = cleaned[:-2]
    return base64.a85decode(cleaned)

def _iter_pdf_streams(file_bytes: bytes):
    for sm in re.finditer(rb'(?<!end)stream\r?\n', file_bytes):
        data_start = sm.end()
        data_end = file_bytes.find(b'endstream', data_start)
        if data_end == -1:
            continue
        data = file_bytes[data_start:data_end]
        if data.endswith(b'\r\n'):
            data = data[:-2]
        elif data.endswith(b'\n'):
            data = data[:-1]

        obj_idx = file_bytes.rfind(b'obj', 0, sm.start())
        dict_start = file_bytes.find(b'<<', obj_idx) if obj_idx != -1 else -1
        obj_dict = file_bytes[dict_start:sm.start()] if dict_start != -1 else b''

        if re.search(rb'/(Image|XObject)\b', obj_dict) and not re.search(rb'/Filter', obj_dict):
            continue

        filters = re.findall(rb'/(ASCII85Decode|ASCIIHexDecode|FlateDecode)\b', obj_dict)
        try:
            for f in filters:
                if f == b'ASCII85Decode':
                    data = _ascii85_decode(data)
                elif f == b'ASCIIHexDecode':
                    hex_clean = re.sub(rb'[^0-9A-Fa-f]', b'', data.split(b'>')[0])
                    data = bytes.fromhex(hex_clean.decode('ascii'))
                elif f == b'FlateDecode':
                    try:
                        data = zlib.decompress(data)
                    except Exception:
                        data = zlib.decompressobj().decompress(data)
        except Exception:
            continue

        yield obj_dict, data

def _extract_text_from_pdf_legacy(file_bytes: bytes) -> str:
    streams = list(_iter_pdf_streams(file_bytes))

    tounicode_map: Dict[bytes, str] = {}
    byte_width = 1
    for obj_dict, data in streams:
        if b'beginbfchar' in data or b'beginbfrange' in data:
            try:
                cmap_text = data.decode('latin-1', errors='ignore')
                m, w = _parse_tounicode_cmap(cmap_text)
                tounicode_map.update(m)
                if w > 1:
                    byte_width = w
            except Exception:
                continue

    pages_text = []
    for obj_dict, data in streams:
        if b'beginbfchar' in data or b'beginbfrange' in data or b'begincmap' in data:
            continue
        if b'Tj' not in data and b'TJ' not in data:
            continue
        extracted = _extract_text_from_content_stream(data, tounicode_map, byte_width)
        if extracted.strip():
            pages_text.append(extracted)

    full_text = '\n'.join(pages_text)
    full_text = re.sub(r'[ \t]+', ' ', full_text)
    full_text = re.sub(r'\n{3,}', '\n\n', full_text).strip()
    return full_text

def _extract_text_via_ocr(file_bytes: bytes) -> str:
    import fitz  # PyMuPDF
    import pytesseract
    from PIL import Image, ImageOps

    default_win_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    tesseract_cmd = os.getenv("TESSERACT_CMD", default_win_path)
    if os.path.exists(tesseract_cmd):
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    pages_text = []
    try:
        for page in doc:
            # Zoom x3 (au lieu de x2) : améliore nettement la lecture des
            # petits caractères, fréquents sur les CV designés (Canva, etc.).
            pix = page.get_pixmap(matrix=fitz.Matrix(3, 3))
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            # On essaie plusieurs versions de l'image et on garde le
            # meilleur résultat (le plus long), car le texte clair sur
            # bandeau coloré (zones de contact notamment) répond parfois
            # mieux à un traitement, parfois moins bien, selon les couleurs
            # utilisées — un seul traitement fixe n'est pas fiable partout.
            candidates = []

            try:
                candidates.append(pytesseract.image_to_string(img, lang="fra+eng"))
            except Exception as e:
                logger.warning(f"OCR: échec (image couleur originale) : {e}")

            try:
                gray_auto = ImageOps.autocontrast(ImageOps.grayscale(img))
                candidates.append(pytesseract.image_to_string(gray_auto, lang="fra+eng"))
            except Exception as e:
                logger.warning(f"OCR: échec (niveaux de gris + autocontraste) : {e}")

            try:
                inverted = ImageOps.invert(ImageOps.grayscale(img))
                candidates.append(pytesseract.image_to_string(inverted, lang="fra+eng"))
            except Exception as e:
                logger.warning(f"OCR: échec (image inversée) : {e}")

            # Seuillage binaire automatique (méthode d'Otsu) : sépare le
            # texte du fond en noir/blanc pur en trouvant le seuil optimal
            # pour CETTE image précise — souvent plus efficace que le simple
            # autocontraste sur du texte clair posé sur un bandeau coloré.
            try:
                gray_img = ImageOps.grayscale(img)
                histogram = gray_img.histogram()
                total = sum(histogram)
                sum_total = sum(i * h for i, h in enumerate(histogram))
                sum_bg, weight_bg, max_variance, threshold = 0.0, 0, 0.0, 128
                for i in range(256):
                    weight_bg += histogram[i]
                    if weight_bg == 0:
                        continue
                    weight_fg = total - weight_bg
                    if weight_fg == 0:
                        break
                    sum_bg += i * histogram[i]
                    mean_bg = sum_bg / weight_bg
                    mean_fg = (sum_total - sum_bg) / weight_fg
                    variance = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
                    if variance > max_variance:
                        max_variance = variance
                        threshold = i
                binarized = gray_img.point(lambda p, t=threshold: 255 if p > t else 0)
                candidates.append(pytesseract.image_to_string(binarized, lang="fra+eng"))
                # Et sa version inversée, au cas où le fond serait plus clair que le texte
                candidates.append(pytesseract.image_to_string(ImageOps.invert(binarized), lang="fra+eng"))
            except Exception as e:
                logger.warning(f"OCR: échec (seuillage binaire Otsu) : {e}")

            # Fusionne toutes les tentatives : ça maximise les chances de
            # capturer une information (ex: email dans un bandeau coloré)
            # qui ne ressortirait que dans une seule des versions testées.
            seen_lines = set()
            merged_lines = []
            for candidate in candidates:
                for line in candidate.split('\n'):
                    key = line.strip().lower()
                    if key and key not in seen_lines:
                        seen_lines.add(key)
                        merged_lines.append(line)

            page_text = '\n'.join(merged_lines)
            if page_text.strip():
                pages_text.append(page_text)
    finally:
        doc.close()

    full_text = '\n'.join(pages_text)
    full_text = re.sub(r'[ \t]+', ' ', full_text)
    full_text = re.sub(r'\n{3,}', '\n\n', full_text).strip()
    return full_text

def _extract_text_via_fitz(file_bytes: bytes) -> str:
    import fitz
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    pages_text = []
    try:
        for page in doc:
            blocks = page.get_text("blocks")
            blocks.sort(key=lambda b: (b[1], b[0]))
            page_blocks = [b[4].strip() for b in blocks if len(b) >= 5 and b[4] and b[4].strip()]
            if page_blocks:
                pages_text.append("\n".join(page_blocks))
    finally:
        doc.close()

    full_text = "\n\n".join(pages_text)
    full_text = re.sub(r'[ \t]+', ' ', full_text)
    return re.sub(r'\n{3,}', '\n\n', full_text).strip()

def clean_extracted_text(text: str) -> str:
    if not text:
        return ""
    
    ocr_fixes = [
        (r'\bHudionte\b', 'Étudiante'),
        (r'\bHudiont\b', 'Étudiant'),
        (r'\bdingnieur\b', "d'ingénieur"),
        (r'\bdingnieurs\b', "d'ingénieurs"),
        (r'\bploteforme\b', 'plateforme'),
        (r'\bOjango\b', 'Django'),
        (r'\bJevaserpt\b', 'JavaScript'),
        (r'\bever\b', 'avec'),
        (r'\bJans c\'ducation\b', "Ans d'éducation"),
        (r'\bPrporatoire\b', 'Préparatoire'),
        (r'\bPrporatoires\b', 'Préparatoires'),
        (r'\bEMS!\b', 'EMSI'),
        (r'\bsurvi\b', 'suivi'),
        (r'\bfrangais\b', 'français'),
        (r'\bcune\b', "d'une"),
        (r'\bCrection\b', 'Création'),
        (r'\bDeveloppement\b', 'Développement'),
        (r'\bOptimization\b', 'Optimisation'),
    ]
    
    for pattern, replacement in ocr_fixes:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    lines = []
    for line in text.split('\n'):
        l = re.sub(r'^[+\•\*\-\–\—\>\s]+', '', line).strip()
        if l:
            lines.append(l)
            
    return '\n'.join(lines)

def extract_text_from_pdf(file_bytes: bytes) -> str:
    fitz_text = ""
    pypdf_text = ""

    try:
        fitz_text = _extract_text_via_fitz(file_bytes)
    except Exception as e:
        logger.warning(f"PyMuPDF fitz layout extraction failed: {e}")

    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        pages_text = [page.extract_text() or "" for page in reader.pages]
        pypdf_text = '\n'.join([t for t in pages_text if t.strip()])
    except Exception as e:
        logger.warning(f"pypdf extraction failed: {e}")

    native_text = fitz_text if len(fitz_text) >= len(pypdf_text) else pypdf_text

    if len(native_text.strip()) < 100:
        logger.info("PDF has no native text layer (< 100 chars). Executing OCR extraction...")
        try:
            ocr_text = _extract_text_via_ocr(file_bytes)
            full_text = ocr_text if len(ocr_text.strip()) > len(native_text.strip()) else native_text
        except Exception as e:
            logger.warning(f"OCR extraction failed: {e}")
            full_text = native_text
    else:
        full_text = native_text

    cleaned = clean_extracted_text(full_text)

    if not cleaned.strip():
        raise ValueError(
            "Aucun texte lisible n'a pu être extrait de ce PDF. "
            "Veuillez téléverser un fichier PDF valide ou au format Word (.docx)."
        )
    return cleaned

def extract_text_from_docx(file_bytes: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as docx_zip:
            doc_xml = docx_zip.read("word/document.xml")
            root = ET.fromstring(doc_xml)

            paragraphs = []
            for elem in root.iter():
                if elem.tag.endswith("}t") and elem.text:
                    paragraphs.append(elem.text)
                elif elem.tag.endswith("}p"):
                    paragraphs.append("\n")

            text = "".join(paragraphs)
            return text.strip()
    except Exception as e:
        logger.error(f"Error parsing DOCX via pure-python parser: {e}")
        raise ValueError(f"Échec de l'analyse du document DOCX (sans DLL): {e}")

def extract_text_from_pptx(file_bytes: bytes) -> str:
    """
    Extrait le texte d'un fichier PowerPoint (.pptx). Comme un .docx, un
    .pptx est en réalité une archive ZIP contenant des fichiers XML — un
    par diapositive (ppt/slides/slide1.xml, slide2.xml...). Utile en repli
    pour les CV designés sur Canva/Google Slides dont l'export PDF est
    "aplati" en image (sans texte extractible), quand un export PDF/DOCX
    propre n'est pas disponible mais qu'un export PPTX l'est.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as pptx_zip:
            slide_files = sorted(
                [n for n in pptx_zip.namelist() if re.match(r'^ppt/slides/slide\d+\.xml$', n)],
                key=lambda n: int(re.search(r'\d+', n).group())
            )
            if not slide_files:
                raise ValueError("Aucune diapositive trouvée dans ce fichier PPTX.")

            all_text = []
            for slide_name in slide_files:
                slide_xml = pptx_zip.read(slide_name)
                root = ET.fromstring(slide_xml)
                slide_lines = []
                for elem in root.iter():
                    if elem.tag.endswith("}t") and elem.text:
                        slide_lines.append(elem.text)
                if slide_lines:
                    all_text.append("\n".join(slide_lines))

            text = "\n\n".join(all_text).strip()
            if not text:
                raise ValueError(
                    "Aucun texte lisible n'a pu être extrait de ce PPTX. "
                    "Le texte a peut-être été converti en image/forme lors de l'export."
                )
            return text
    except ValueError:
        raise
    except Exception as e:
        logger.error(f"Error parsing PPTX via pure-python parser: {e}")
        raise ValueError(f"Échec de l'analyse du document PPTX : {e}")

def extract_text(file_bytes: bytes, filename: str) -> str:
    ext = filename.split(".")[-1].lower()
    if ext == "pdf":
        return extract_text_from_pdf(file_bytes)
    elif ext in ["docx", "doc"]:
        return extract_text_from_docx(file_bytes)
    elif ext == "pptx":
        return extract_text_from_pptx(file_bytes)
    else:
        raise ValueError("Format de fichier non supporté. Veuillez téléverser un fichier PDF, DOCX ou PPTX.")

def call_gemini_api(prompt: str, system_instruction: str = "", response_format_json: bool = False) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not configured in the environment.")

    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    parts = []
    if system_instruction:
        parts.append({"text": system_instruction + "\n\n" + prompt})
    else:
        parts.append({"text": prompt})

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.2 if response_format_json else 0.7,
            "maxOutputTokens": 4096
        }
    }

    if response_format_json:
        payload["generationConfig"]["responseMimeType"] = "application/json"

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except urllib.error.HTTPError as he:
        err_body = he.read().decode("utf-8", errors="ignore")
        logger.error(f"Gemini API HTTPError {he.code}: {err_body}")
        raise ValueError(f"Erreur API Gemini ({he.code}) : {err_body}")
    except Exception as e:
        logger.error(f"Gemini connection error: {e}")
        raise ValueError(f"Impossible de se connecter à l'API Gemini : {e}")

# ── Groq Key Pool : rotation round-robin + fallback sur 429 ──────────────────
# On charge TOUTES les clés disponibles au premier appel (thread-safe).
import threading as _threading

_groq_keys: List[str] = []
_groq_key_idx: int = 0
_groq_key_lock = _threading.Lock()

def _load_groq_keys() -> List[str]:
    """Charge toutes les clés Groq définies dans le .env (GROQ_API_KEY, GROQ_API_KEY2, …)."""
    keys: List[str] = []
    # Clés nommées classiquement
    for var in ("GROQ_API_KEY", "groq_API_KEY", "GROQ_API_KEY2", "GROQ_API_KEY3",
                "GROQ_API_KEY4", "GROQ_API_KEY5"):
        k = os.getenv(var, "").strip()
        if k and k not in keys:
            keys.append(k)
    return keys

def _get_next_groq_key() -> str:
    """Retourne la prochaine clé Groq en rotation round-robin."""
    global _groq_keys, _groq_key_idx
    with _groq_key_lock:
        if not _groq_keys:
            _groq_keys = _load_groq_keys()
        if not _groq_keys:
            raise ValueError("Aucune GROQ_API_KEY n'est configurée dans le fichier .env.")
        key = _groq_keys[_groq_key_idx % len(_groq_keys)]
        _groq_key_idx = (_groq_key_idx + 1) % len(_groq_keys)
        return key

def _rotate_away_from_key(bad_key: str) -> Optional[str]:
    """En cas de 429 sur bad_key, retourne la prochaine clé différente (ou None si une seule clé)."""
    global _groq_keys, _groq_key_idx
    with _groq_key_lock:
        if not _groq_keys:
            _groq_keys = _load_groq_keys()
        others = [k for k in _groq_keys if k != bad_key]
        if not others:
            return None
        # Choisir la suivante dans la liste globale
        for i in range(len(_groq_keys)):
            candidate = _groq_keys[(_groq_key_idx + i) % len(_groq_keys)]
            if candidate != bad_key:
                _groq_key_idx = (_groq_keys.index(candidate) + 1) % len(_groq_keys)
                return candidate
        return None

def call_groq_api(
    prompt: str,
    system_instruction: str = "",
    response_format_json: bool = False,
    image_bytes: Optional[bytes] = None,
    image_mime_type: str = "image/png"
) -> str:
    """Appelle l'API Groq avec rotation automatique des clés et fallback sur 429."""

    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    if image_bytes:
        model = os.getenv("GROQ_VISION_MODEL", "llama-3.3-70b-versatile")

    url = "https://api.groq.com/openai/v1/chat/completions"

    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})

    if image_bytes:
        b64_img = base64.b64encode(image_bytes).decode("utf-8")
        user_content: List[Dict[str, Any]] = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{image_mime_type};base64,{b64_img}"
                }
            }
        ]
        messages.append({"role": "user", "content": user_content})
    else:
        messages.append({"role": "user", "content": prompt})

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.2 if response_format_json else 0.7,
        "max_tokens": 2048
    }

    is_reasoning_model = any(k in model.lower() for k in ["qwen", "qwq", "think"])
    if response_format_json and not is_reasoning_model:
        payload["response_format"] = {"type": "json_object"}

    # ── Stratégie : essaie chaque clé du pool, au maximum 2 × len(pool) tentatives ──
    keys = _load_groq_keys()
    if not keys:
        raise ValueError("Aucune GROQ_API_KEY n'est configurée dans le fichier .env.")

    total_attempts = max(6, len(keys) * 2)  # au moins 6 tentatives
    current_key = _get_next_groq_key()
    last_error: Exception = RuntimeError("Aucune tentative effectuée.")

    for attempt in range(total_attempts):
        headers = {
            "Authorization": f"Bearer {current_key}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                content = res_data["choices"][0]["message"]["content"].strip()
                if "<think>" in content:
                    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                # Succès — log si on a dû changer de clé
                key_display = f"…{current_key[-6:]}"
                logger.info(f"Groq API succès (tentative {attempt+1}, clé {key_display})")
                return content

        except urllib.error.HTTPError as he:
            err_body = he.read().decode("utf-8", errors="ignore")
            last_error = ValueError(f"Erreur API Groq ({he.code}) : {err_body}")

            if he.code == 429:
                # Rate-limited sur cette clé → on bascule sur la suivante
                next_key = _rotate_away_from_key(current_key)
                key_display = f"…{current_key[-6:]}"
                if next_key:
                    logger.warning(
                        f"Groq 429 Rate Limit (clé {key_display}, tentative {attempt+1})"
                        f" → bascule sur une autre clé."
                    )
                    current_key = next_key
                    time.sleep(1)  # pause courte entre changement de clé
                else:
                    # Une seule clé dans le pool — attente classique
                    wait = min(30, 5 * (attempt + 1))
                    logger.warning(
                        f"Groq 429 Rate Limit (clé {key_display}, tentative {attempt+1})"
                        f" — 1 seule clé disponible, attente {wait}s."
                    )
                    time.sleep(wait)
                continue

            elif he.code in (500, 502, 503, 504) and attempt < total_attempts - 1:
                # Erreur serveur transitoire — on retente avec une courte pause
                logger.warning(f"Groq API erreur serveur {he.code}, tentative {attempt+1}, retry...")
                time.sleep(2)
                continue

            else:
                logger.error(f"Groq API HTTPError {he.code}: {err_body}")
                raise last_error

        except Exception as e:
            last_error = e
            logger.error(f"Groq connexion error (tentative {attempt+1}): {e}")
            if attempt < total_attempts - 1:
                time.sleep(2)
                continue
            raise ValueError(f"Impossible de se connecter à l'API Groq : {e}")

    raise last_error

def call_ollama_api(prompt: str, system_instruction: str = "", response_format_json: bool = False) -> str:
    model = os.getenv("OLLAMA_MODEL", "llama3.2")
    url = "http://localhost:11434/api/generate"

    full_prompt = f"{system_instruction}\n\n{prompt}" if system_instruction else prompt

    payload = {
        "model": model,
        "prompt": full_prompt,
        "stream": False,
        "options": {"temperature": 0.2 if response_format_json else 0.7}
    }
    if response_format_json:
        payload["format"] = "json"

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=300) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return res_data["response"].strip()
    except urllib.error.URLError as e:
        logger.error(f"Ollama connection error: {e}")
        raise ValueError(
            "Impossible de se connecter à Ollama. Vérifiez qu'il tourne bien "
            "(ouvrez un terminal et lancez : ollama serve)."
        )
    except Exception as e:
        logger.error(f"Ollama error: {e}")
        raise ValueError(f"Erreur Ollama : {e}")

def _guess_candidate_name(resume_text: str, lines: List[str]) -> str:
    for line in lines[:5]:
        candidate = line.strip()
        if not candidate or len(candidate) > 60:
            continue
        if '@' in candidate or re.search(r'\d{3,}', candidate):
            continue
        word_count = len(candidate.split())
        if 1 <= word_count <= 5:
            return candidate.title() if candidate.isupper() else candidate
    return ""

# ═══════════════════════════════════════════════════════
#  COMPARAISON DE DIPLÔME — détecté (CV) vs requis (offre)
# ═══════════════════════════════════════════════════════

def _normalize_diploma_level(text: str) -> Optional[int]:
    """
    Convertit un texte de diplôme en un niveau numérique comparable
    (ex: "Bac+5" -> 5, "Master" -> 5, "Licence" -> 3, "Doctorat" -> 8).
    Retourne None si aucun niveau reconnaissable n'est trouvé.
    """
    if not text:
        return None
    t = text.lower()

    # "bac+N" ou "bac N" ou "bac + N"
    m = re.search(r'bac\s*\+?\s*(\d)', t)
    if m:
        return int(m.group(1))

    if re.search(r'\bdoctorat\b|\bphd\b', t):
        return 8
    if re.search(r'\bmaster\b|\bing[ée]nieur\b|\bmba\b', t):
        return 5
    if re.search(r'\blicence\b|\bbachelor\b', t):
        return 3
    if re.search(r'\bdut\b|\bbts\b|\bdeug\b', t):
        return 2
    if re.search(r'\bbac\b', t):
        return 0

    # Repli : n'importe quel "+N" isolé (ex: "niveau +5")
    m2 = re.search(r'\+\s*(\d)\b', t)
    if m2:
        return int(m2.group(1))

    return None

def _extract_required_diploma_level(job_description: str) -> (Optional[int], str):
    """Cherche un niveau de diplôme requis dans le texte de l'offre d'emploi.
    Retourne (niveau_numerique_ou_None, libellé_brut_trouvé)."""
    if not job_description:
        return None, ""

    patterns = [
        r'bac\s*\+?\s*\d',
        r'\bmaster\b[^\n,]*',
        r'\blicence\b[^\n,]*',
        r'\bing[ée]nieur\b[^\n,]*',
        r'\bdoctorat\b[^\n,]*',
        r'\bbts\b[^\n,]*',
        r'\bdut\b[^\n,]*',
    ]
    for pat in patterns:
        m = re.search(pat, job_description, re.IGNORECASE)
        if m:
            raw = m.group(0).strip()
            level = _normalize_diploma_level(raw)
            if level is not None:
                return level, raw
    return None, ""

def _format_diploma_label(level: Optional[int], raw_label: str = "") -> str:
    if raw_label:
        return raw_label.strip()
    if level is None:
        return "Non déterminé"
    if level == 0:
        return "Bac"
    return f"Bac+{level}"

def _detect_ongoing_study_level_from_text(resume_text: str) -> Optional[int]:
    """
    Détecte le niveau d'études ACTUEL (années déjà validées), pas le
    diplôme final visé, pour un candidat encore en formation — cas très
    fréquent avec le système marocain : classes préparatoires (2 ans après
    le bac) suivies d'un cycle ingénieur, avec le numéro d'année en cours
    (ex: "2ème année du cycle ingénieur" = seulement Bac+4, pas Bac+5, tant
    que le cycle n'est pas terminé). Sans cette distinction, un simple mot-
    clé "ingénieur" ferait croire à tort que le diplôme est déjà obtenu.
    Retourne le niveau Bac+N actuel, ou None si non détectable.
    """
    t = resume_text.lower()

    has_prepa = bool(re.search(r'classe[s]?\s+pr[ée]paratoire', t))

    m = re.search(r'(\d)\D{0,12}ann[ée]e\D{0,10}cycle\s+ing[ée]nieur', t)
    if not m:
        m = re.search(r'cycle\s+ing[ée]nieur.{0,40}?(\d)\D{0,12}ann[ée]e', t)

    if m:
        year_in_cycle = int(m.group(1))
        base = 2 if has_prepa else 0  # les classes prépa comptent déjà pour Bac+2
        return base + year_in_cycle

    return None

def _compute_diploma_check(analysis_data: Dict[str, Any], job_description: Optional[str], resume_text: str = "") -> Optional[Dict[str, Any]]:
    """
    Compare le niveau de diplôme détecté sur le CV avec celui exigé par
    l'offre d'emploi. Retourne None si l'offre ne précise aucun diplôme
    requis (rien à comparer), sinon un dict avec le résultat de la
    comparaison, prêt à être fusionné dans job_description_match.
    """
    if not job_description:
        return None

    required_level, required_raw = _extract_required_diploma_level(job_description)
    if required_level is None:
        return None  # l'offre ne précise pas de niveau de diplôme requis

    detected_label = str(analysis_data.get("education_level", "")).strip()
    detected_level = _normalize_diploma_level(detected_label)

    # Repli : si l'IA n'a pas rempli education_level, on tente de déduire le
    # niveau depuis la première formation listée dans "education".
    if detected_level is None:
        education_list = analysis_data.get("education", []) or []
        if education_list and isinstance(education_list[0], dict):
            detected_label = detected_label or education_list[0].get("degree", "")
            detected_level = _normalize_diploma_level(detected_label)

    # PRIORITÉ : si le CV montre clairement une formation EN COURS (ex:
    # "2ème année du cycle ingénieur"), ce niveau réel (années déjà
    # validées) prime sur une simple détection par mot-clé qui confondrait
    # à tort "ingénieur" avec un diplôme déjà obtenu (Bac+5).
    ongoing_level = _detect_ongoing_study_level_from_text(resume_text) if resume_text else None
    if ongoing_level is not None:
        detected_level = ongoing_level
        detected_label = f"Bac+{ongoing_level} (formation en cours)"

    conforms = (detected_level is not None) and (detected_level >= required_level)

    detected_display = _format_diploma_label(detected_level, detected_label)
    required_display = _format_diploma_label(required_level, required_raw)

    if detected_level is None:
        message = f"Diplôme non détecté sur le CV (Requis : {required_display})."
    elif conforms:
        message = f"Diplôme conforme (Niveau détecté : {detected_display}, Requis : {required_display})."
    else:
        message = (
            f"Compétences validées, mais diplôme insuffisant "
            f"(Niveau détecté : {detected_display}, Requis : {required_display})."
        )

    return {
        "conforms": bool(conforms) if detected_level is not None else False,
        "detected": detected_display,
        "required": required_display,
        "message": message,
    }

def _clean_extracted_email(email: str) -> str:
    if not email:
        return ""
    email = email.strip()
    # 1. Regex to extract email from potential garbage prefix/suffix
    match = re.search(r'([a-zA-Z0-9._%+-]+)@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', email)
    if not match:
        return email
    
    username = match.group(1)
    domain = match.group(2)
    
    # 2. Check for 8+ digit phone number sequence in username
    phone_match = re.search(r'\d{8,}', username)
    if phone_match:
        phone_str = phone_match.group(0)
        idx = username.find(phone_str) + len(phone_str)
        username = username[idx:]
        
    # 3. Clean leading non-alphanumeric chars
    username = re.sub(r'^[^a-zA-Z0-9]+', '', username)
    
    # 4. Strip common noise words that PDF extraction concatenates before the real email
    noise_words = [
        'maroc', 'morocco', 'casablanca', 'rabat', 'tanger', 'tangier', 'fes', 'fez',
        'agadir', 'meknes', 'oujda', 'kenitra', 'tetouan', 'safi', 'nador', 'settat',
        'khouribga', 'mohammedia', 'eljadida', 'benimlal', 'benimellal', 'taza',
        'france', 'paris', 'lyon', 'marseille', 'toulouse', 'bordeaux', 'lille',
        'canada', 'belgique', 'tunisie', 'algerie', 'suisse', 'espagne', 'spain',
    ]
    user_lower = username.lower()
    for word in noise_words:
        if user_lower.startswith(word) and len(username) > len(word):
            rest = re.sub(r'^[^a-zA-Z0-9]+', '', username[len(word):])
            if rest:
                username = rest
                break
    
    # 5. Clean domain suffix (e.g. gmail.comLANGUE or gmail.comHayAlQods)
    domain_match = re.match(r'^([a-zA-Z0-9.-]+\.(?:com|fr|ma|net|org|io|co|edu|gov|info|mil))([a-zA-Z]*)$', domain, re.IGNORECASE)
    if domain_match:
        domain = domain_match.group(1)
        
    return f"{username}@{domain}".lower()

def _extract_email_from_text(resume_text: str) -> str:
    # Standard email regex first
    normal_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', resume_text)
    if normal_match:
        return _clean_extracted_email(normal_match.group(0).strip())
    
    # Spaced email regex (handles PDFs where characters have spaces between them)
    spaced_match = re.search(r'[a-zA-Z0-9._%+\-\s]+@[a-zA-Z0-9.\-\s]+\.[a-zA-Z\s]{2,}', resume_text)
    if spaced_match:
        cleaned = re.sub(r'\s+', '', spaced_match.group(0))
        if re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', cleaned):
            return _clean_extracted_email(cleaned)

    return ""

def get_mock_analysis(resume_text: str, job_description: Optional[str] = None) -> Dict[str, Any]:
    """Analyse locale intelligente du CV — extraction réelle sans API externe."""
    resume_lower = resume_text.lower()
    lines = [l.strip() for l in resume_text.split('\n') if l.strip()]
    candidate_name = _guess_candidate_name(resume_text, lines)

    SKILL_DB = {
        "python": "Python", "javascript": "JavaScript", "typescript": "TypeScript",
        "react": "React", "angular": "Angular", "vue": "Vue.js", "node": "Node.js",
        "html": "HTML", "css": "CSS", "sass": "SASS", "bootstrap": "Bootstrap",
        "tailwind": "Tailwind CSS", "sql": "SQL", "mysql": "MySQL", "postgresql": "PostgreSQL",
        "mongodb": "MongoDB", "nosql": "NoSQL", "redis": "Redis",
        "java": "Java", "c++": "C++", "c#": "C#", "php": "PHP", "ruby": "Ruby",
        "swift": "Swift", "kotlin": "Kotlin", "go": "Go", "rust": "Rust",
        "docker": "Docker", "kubernetes": "Kubernetes", "aws": "AWS", "azure": "Azure",
        "gcp": "GCP", "git": "Git", "github": "GitHub", "gitlab": "GitLab",
        "linux": "Linux", "jenkins": "Jenkins", "ci/cd": "CI/CD", "terraform": "Terraform",
        "fastapi": "FastAPI", "django": "Django", "flask": "Flask", "spring": "Spring",
        "express": "Express.js", "laravel": "Laravel", ".net": ".NET",
        "figma": "Figma", "photoshop": "Photoshop", "illustrator": "Illustrator",
        "machine learning": "Machine Learning", "deep learning": "Deep Learning",
        "tensorflow": "TensorFlow", "pytorch": "PyTorch", "pandas": "Pandas",
        "numpy": "NumPy", "scikit": "Scikit-learn", "power bi": "Power BI",
        "tableau": "Tableau", "excel": "Excel", "word": "Word",
        "scrum": "Scrum", "agile": "Agile", "jira": "Jira",
        "api rest": "API REST", "rest api": "API REST", "graphql": "GraphQL",
        "microservices": "Microservices", "devops": "DevOps",
    }
    found_skills = []
    for key, label in SKILL_DB.items():
        pattern = r'\b' + re.escape(key) + r'\b'
        if re.search(pattern, resume_lower):
            if label not in found_skills:
                found_skills.append(label)

    SOFT_SKILLS = {
        "communication": "Communication", "leadership": "Leadership",
        "travail d'équipe": "Travail d'équipe", "teamwork": "Travail d'équipe",
        "problem solving": "Résolution de problèmes", "résolution de problèmes": "Résolution de problèmes",
        "autonomie": "Autonomie", "autonomous": "Autonomie",
        "créativité": "Créativité", "creativity": "Créativité",
        "gestion de projet": "Gestion de projet", "project management": "Gestion de projet",
        "adaptabilité": "Adaptabilité", "rigoureux": "Rigueur", "rigueur": "Rigueur",
    }
    soft_found = []
    for key, label in SOFT_SKILLS.items():
        if key in resume_lower and label not in soft_found:
            soft_found.append(label)

    all_skills = found_skills + soft_found

    email = _extract_email_from_text(resume_text)

    phone_match = re.search(r'(?:\+?\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]?\d{2,4}[\s.-]?\d{2,4}[\s.-]?\d{0,4}', resume_text)
    phone = phone_match.group(0).strip() if phone_match else None

    section_keywords = {
        'experience': ['expérience', 'experience', 'parcours professionnel', 'work experience', 'emploi'],
        'education': ['formation', 'education', 'études', 'diplôme', 'cursus', 'scolarité'],
        'skills': ['compétences', 'skills', 'technologies', 'outils', 'langages'],
        'projects': ['projets', 'projects', 'réalisations'],
        'languages': ['langues', 'languages'],
        'certifications': ['certifications', 'certificats'],
        'interests': ['centres d\'intérêt', 'loisirs', 'hobbies', 'interests'],
    }
    sections_found = []
    for section, keywords in section_keywords.items():
        for kw in keywords:
            if kw in resume_lower:
                sections_found.append(section)
                break

    experience_patterns = [
        r'(\d{4})\s*[-–—]\s*(\d{4}|[Pp]résent|[Aa]ctuel|[Cc]urrent|[Nn]ow)',
        r'((?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{4})\s*[-–—]',
    ]
    experience_dates = []
    for pat in experience_patterns:
        experience_dates.extend(re.findall(pat, resume_lower))

    experiences = []
    role_indicators = ['ingénieur', 'développeur', 'developer', 'manager', 'chef de projet',
                       'consultant', 'analyste', 'analyst', 'technicien', 'designer',
                       'architecte', 'administrateur', 'assistant', 'responsable', 'directeur',
                       'stagiaire', 'intern', 'stage', 'alternance', 'apprenti',
                       'engineer', 'lead', 'senior', 'junior', 'full stack', 'frontend', 'backend']

    for i, line in enumerate(lines):
        line_lower = line.lower()
        for indicator in role_indicators:
            if indicator in line_lower and len(line) < 120:
                company = ""
                duration = ""
                desc = ""
                for j in range(max(0, i-2), min(len(lines), i+4)):
                    lj = lines[j].lower()
                    if re.search(r'\d{4}', lj) and j != i:
                        duration = lines[j]
                    elif j > i and len(lines[j]) > 30:
                        desc = lines[j]

                experiences.append({
                    "role": line[:80],
                    "company": company if company else "—",
                    "duration": duration[:50] if duration else "—",
                    "description": desc[:200] if desc else "Voir le CV pour plus de détails."
                })
                break
        if len(experiences) >= 5:
            break

    if not experiences:
        experiences.append({
            "role": "Expérience professionnelle",
            "company": "—",
            "duration": f"{len(experience_dates)} période(s) détectée(s)" if experience_dates else "—",
            "description": "Le système a détecté du contenu professionnel mais n'a pas pu structurer les postes automatiquement."
        })

    edu_keywords = ['licence', 'master', 'ingénieur', 'bac', 'bts', 'dut', 'bachelor',
                    'mba', 'doctorat', 'phd', 'deug', 'diplôme', 'certificat',
                    'university', 'université', 'école', 'institute', 'faculty', 'faculté']
    education = []
    for i, line in enumerate(lines):
        line_lower = line.lower()
        for kw in edu_keywords:
            if kw in line_lower and len(line) < 150:
                dur = ""
                school = ""
                for j in range(max(0, i-1), min(len(lines), i+3)):
                    if re.search(r'\d{4}', lines[j]) and j != i:
                        dur = lines[j]
                        break
                education.append({
                    "degree": line[:100],
                    "school": school if school else "—",
                    "duration": dur[:50] if dur else "—"
                })
                break
        if len(education) >= 4:
            break

    if not education:
        education.append({
            "degree": "Formation non détectée automatiquement",
            "school": "—",
            "duration": "—"
        })

    cert_keywords = ['certif', 'certification', 'certificat', 'attestation', 'badge', 'aws certified',
                     'google certified', 'microsoft certified', 'cisco', 'pmp', 'scrum master']
    certifications = []
    for i, line in enumerate(lines):
        ll = line.lower()
        if any(kw in ll for kw in cert_keywords) and len(line) < 150:
            clean_cert = re.sub(r'^[+\-*•>\s]+', '', line).strip()
            if clean_cert and clean_cert not in certifications:
                certifications.append(clean_cert)
        if len(certifications) >= 5:
            break

    LANG_PATTERNS = {
        'français': 'Français', 'french': 'Français',
        'anglais': 'Anglais', 'english': 'Anglais',
        'arabe': 'Arabe', 'arabic': 'Arabe',
        'espagnol': 'Espagnol', 'spanish': 'Espagnol',
        'allemand': 'Allemand', 'german': 'Allemand',
        'italien': 'Italien', 'italian': 'Italien',
        'portugais': 'Portugais', 'portuguese': 'Portugais',
        'chinois': 'Chinois', 'chinese': 'Chinois',
        'japonais': 'Japonais', 'japanese': 'Japonais',
        'russe': 'Russe', 'russian': 'Russe',
    }
    languages = []
    for key, label in LANG_PATTERNS.items():
        if key in resume_lower and label not in languages:
            languages.append(label)

    QUALITY_KEYWORDS = {
        'autonome': "Autonomie", 'autonomy': "Autonomie",
        'rigoureux': "Rigueur", 'rigueur': "Rigueur",
        'ponctuel': "Ponctualité",
        'créatif': "Créativité", 'créativité': "Créativité",
        'curieux': "Curiosité intellectuelle",
        'organisé': "Organisation", 'organisation': "Organisation",
        'motivé': "Motivation",
        'adaptable': "Adaptabilité", 'adaptabilité': "Adaptabilité",
        'polyvalent': "Polyvalence",
        'dynamique': "Dynamisme", 'dynamisme': "Dynamisme",
        "esprit d'équipe": "Esprit d'équipe", 'teamwork': "Esprit d'équipe",
        'communication': "Communication",
        'leadership': "Leadership",
        'problem solving': "Résolution de problèmes",
    }
    qualities = []
    for key, label in QUALITY_KEYWORDS.items():
        if key in resume_lower and label not in qualities:
            qualities.append(label)

    projects = []
    in_proj_section = False
    proj_section_keywords = ['projet', 'project', 'réalisation', 'travaux']
    for i, line in enumerate(lines):
        ll = line.lower()
        if any(kw in ll for kw in proj_section_keywords) and len(line) < 60:
            in_proj_section = True
            continue
        if in_proj_section:
            clean = re.sub(r'^[+\-*•>\s]+', '', line).strip()
            if len(clean) > 15 and not re.match(r'^\d{4}', clean):
                tech_match = re.findall(r'\b(?:Python|Java|C\+\+|JavaScript|React|Angular|Django|Flask|SQL|MySQL|PostgreSQL|MongoDB|Node\.js|HTML|CSS|PHP|Laravel|Spring|Docker|Kubernetes|AWS|Azure|Git|GitHub|TensorFlow|PyTorch|Pandas|Scikit)\b', line, re.IGNORECASE)
                projects.append({
                    "title": clean[:80],
                    "technologies": ", ".join(set(t.title() for t in tech_match)) if tech_match else "",
                    "description": ""
                })
        if len(projects) >= 5:
            break

    score = 0

    char_count = len(resume_text)
    if char_count > 1500:
        score += 15
    elif char_count > 800:
        score += 10
    elif char_count > 300:
        score += 5

    if email:
        score += 5
    if phone:
        score += 5

    section_score = min(20, len(sections_found) * 4)
    score += section_score

    skill_score = min(25, len(found_skills) * 3)
    score += skill_score

    exp_score = min(15, len(experience_dates) * 5)
    score += exp_score

    edu_score = min(10, len(education) * 5)
    score += edu_score

    action_words = ['développé', 'conçu', 'géré', 'dirigé', 'créé', 'optimisé', 'implémenté',
                    'developed', 'designed', 'managed', 'led', 'created', 'optimized', 'implemented',
                    'réalisé', 'mis en place', 'amélioré', 'automatisé', 'déployé']
    action_count = sum(1 for w in action_words if w in resume_lower)
    action_score = min(5, action_count * 2)
    score += action_score

    score = min(100, max(10, score))

    strengths = []
    if len(found_skills) >= 5:
        strengths.append(f"Profil technique riche avec {len(found_skills)} compétences identifiées : {', '.join(found_skills[:5])}.")
    elif len(found_skills) >= 2:
        strengths.append(f"Compétences techniques présentes : {', '.join(found_skills)}.")
    if email:
        strengths.append("Informations de contact clairement mentionnées.")
    if len(sections_found) >= 3:
        strengths.append(f"CV bien structuré avec {len(sections_found)} sections identifiées ({', '.join(sections_found)}).")
    if len(experience_dates) >= 2:
        strengths.append(f"Parcours professionnel documenté avec {len(experience_dates)} expérience(s).")
    if action_count >= 3:
        strengths.append("Utilisation de verbes d'action forts dans les descriptions.")
    if not strengths:
        strengths.append("Le document a été correctement lu et analysé.")

    weaknesses = []
    if len(found_skills) < 3:
        weaknesses.append("Peu de compétences techniques explicitement mentionnées. Ajoutez une section dédiée.")
    if not email:
        weaknesses.append("Aucune adresse email détectée — essentiel pour les recruteurs.")
    if len(sections_found) < 3:
        weaknesses.append("Structure du CV peu claire. Ajoutez des en-têtes de section explicites (Expérience, Formation, Compétences).")
    if char_count < 800:
        weaknesses.append("Le CV semble trop court. Un CV efficace contient généralement 1 à 2 pages de contenu.")
    if len(experience_dates) == 0:
        weaknesses.append("Aucune date d'expérience détectée. Précisez les périodes de vos emplois.")
    if action_count < 2:
        weaknesses.append("Manque de verbes d'action (développé, conçu, géré, optimisé...) dans les descriptions.")
    if not weaknesses:
        weaknesses.append("Aucun défaut majeur détecté dans la structure du CV.")

    # ── Recommandations contextuelles basées sur le vrai CV ──────────────────
    recommendations = []

    # 1. Compétences manquantes — personnalisé selon ce qu'on a déjà trouvé
    if len(found_skills) < 3:
        recommendations.append(
            f"Le CV ne mentionne que {len(found_skills) or 'aucune'} compétence technique détectable. "
            "Créez une section 'Compétences' dédiée listant explicitement chaque technologie maîtrisée "
            "(langage, framework, outil, SGBD) — c'est le filtre n°1 des ATS."
        )
    elif len(found_skills) < 6:
        missing_common = [l for k, l in {
            'git': 'Git/GitHub', 'docker': 'Docker', 'linux': 'Linux',
            'sql': 'SQL', 'api rest': 'API REST', 'agile': 'Agile/Scrum'
        }.items() if k not in resume_lower and l not in found_skills]
        if missing_common:
            recommendations.append(
                f"Vous maîtrisez {', '.join(found_skills[:4])} — ajoutez également "
                f"{', '.join(missing_common[:3])} si vous les connaissez, ce sont des compétences "
                "très recherchées qui augmenteront votre score ATS."
            )

    # 2. Projets — si section absente ou vide
    if not projects:
        if experiences:
            role_names = ', '.join(e['role'] for e in experiences[:2] if e.get('role') and e['role'] != '—')
            recommendations.append(
                f"Aucun projet académique ou personnel n'est visible sur ce CV. "
                f"Si vous avez réalisé des applications, prototypes ou contributions open-source durant "
                f"{'vos stages' if role_names else 'votre formation'}, ajoutez-les avec les technologies utilisées "
                "et un lien GitHub si disponible."
            )
        else:
            recommendations.append(
                "Ajoutez une section 'Projets' avec au minimum 2-3 réalisations concrètes "
                "(titre, technologies, description courte et lien GitHub) — c'est l'élément le plus "
                "différenciant pour un profil junior sans longue expérience professionnelle."
            )
    elif projects and not any(p.get('technologies') for p in projects):
        proj_titles = ', '.join(p.get('title','') for p in projects[:2] if p.get('title'))
        recommendations.append(
            f"Les projets {'(' + proj_titles + ') ' if proj_titles else ''}ne mentionnent pas les technologies utilisées. "
            "Précisez pour chaque projet le stack technique complet (ex: Django + PostgreSQL + React) "
            "— les recruteurs vérifient en priorité cette information."
        )

    # 3. Expérience et verbes d'action
    if len(experience_dates) > 0 and action_count < 2:
        exp_roles = [e['role'] for e in experiences[:2] if e.get('role') and e['role'] != '—']
        exp_subject = ('de ' + exp_roles[0]) if exp_roles else "d'expérience"
        recommendations.append(
            f"Les descriptions {exp_subject} manquent de verbes d'action "
            "(développé, conçu, optimisé, déployé...) et de résultats quantifiés. "
            "Reformulez chaque bullet point sous la forme : Verbe + contexte + résultat mesurable "
            "(ex: 'Développé un module de gestion des stocks réduisant les erreurs de 30%')."
        )
    elif len(experience_dates) == 0 and len(lines) > 30:
        recommendations.append(
            "Aucune expérience professionnelle avec dates n'est détectable (stage, alternance, freelance, bénévolat). "
            "Même un stage court de 1 mois, un projet associatif ou une mission freelance renforce considérablement "
            "un CV étudiant face aux filtres ATS."
        )

    # 4. Contact
    if not email:
        recommendations.append(
            "Aucune adresse email n'est détectable sur ce CV — information indispensable. "
            "Placez-la en en-tête, sous votre nom, au format standard (prenom.nom@domaine.com)."
        )

    # 5. Certifications
    if not certifications and len(found_skills) >= 3:
        cert_suggestions = []
        if any(k in resume_lower for k in ['python', 'machine learning', 'tensorflow', 'data']):
            cert_suggestions.append('Google Data Analytics ou Microsoft Azure AI Fundamentals')
        if any(k in resume_lower for k in ['aws', 'cloud', 'docker', 'kubernetes']):
            cert_suggestions.append('AWS Cloud Practitioner (gratuit en version Foundational)')
        if any(k in resume_lower for k in ['scrum', 'agile', 'projet', 'management']):
            cert_suggestions.append('Professional Scrum Master I (PSM I) — certification reconnue mondialement')
        if cert_suggestions:
            recommendations.append(
                f"Aucune certification n'est mentionnée. Envisagez : {cert_suggestions[0]}. "
                "Les certifications validées par des organismes tiers (Google, Microsoft, AWS, PMI) "
                "sont des atouts décisifs qui distinguent les candidats à score ATS équivalent."
            )

    # 6. Longueur et densité
    if char_count < 800:
        recommendations.append(
            f"Ce CV est court ({char_count} caractères). Un CV efficace pour un poste technique "
            "contient en général entre 4 000 et 8 000 caractères (1 à 2 pages denses). "
            "Développez les descriptions de vos expériences et projets avec plus de détail technique."
        )

    # 7. Langues
    if not languages:
        recommendations.append(
            "Le niveau de maîtrise des langues n'est pas précisé. Ajoutez une section 'Langues' "
            "avec le niveau certifié ou auto-évalué (ex: Français (natif), Anglais (B2/Courant), Arabe (natif)). "
            "L'anglais technique est exigé dans la plupart des offres IT."
        )

    # Limite : 6 recommandations max, toujours au moins 2
    if not recommendations:
        recommendations.append(
            f"Profil globalement solide avec {len(found_skills)} compétences identifiées. "
            "Pour maximiser votre visibilité ATS, assurez-vous que chaque compétence technique "
            "est mentionnée explicitement dans la section Compétences ET dans les descriptions de projets/expériences."
        )
        recommendations.append(
            "Adaptez le titre et le résumé de votre CV à chaque offre d'emploi : "
            "reprenez les 3-5 mots-clés les plus importants de l'annonce dans votre en-tête ou profil."
        )
    recommendations = recommendations[:6]

    summary_parts = [f"Ce CV contient {char_count} caractères et {len(lines)} lignes."]
    if found_skills:
        summary_parts.append(f"Compétences clés détectées : {', '.join(found_skills[:6])}.")
    if len(experience_dates) > 0:
        summary_parts.append(f"{len(experience_dates)} expérience(s) professionnelle(s) identifiée(s).")
    if education and education[0]['degree'] != "Formation non détectée automatiquement":
        summary_parts.append(f"Formation : {education[0]['degree'][:60]}.")
    summary_parts.append(f"Score ATS global : {score}/100.")
    summary = " ".join(summary_parts)

    result = {
        "candidate_name": candidate_name,
        "email": email or "",
        "phone": phone or "",
        "city": "",
        "education_level": education[0]['degree'] if education else "",
        "overall_score": score,
        "summary": summary,
        "skills": all_skills if all_skills else ["Compétences non identifiées automatiquement"],
        "experience": experiences,
        "education": education,
        "projects": projects,
        "certifications": certifications,
        "languages": languages,
        "qualities": qualities,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "recommendations": recommendations,
    }

    if job_description:
        jd_lower = job_description.lower()
        jd_skills = []
        for key, label in SKILL_DB.items():
            if re.search(r'\b' + re.escape(key) + r'\b', jd_lower):
                jd_skills.append(label)

        matched = [s for s in jd_skills if s in found_skills]
        gaps = [s for s in jd_skills if s not in found_skills]

        if jd_skills:
            match_score = min(100, max(20, int(len(matched) / max(1, len(jd_skills)) * 100)))
        else:
            match_score = 50

        feedback = f"Votre CV correspond à {match_score}% de l'offre d'emploi. "
        if matched:
            feedback += f"Compétences en commun : {', '.join(matched[:5])}. "
        if gaps:
            feedback += f"Compétences manquantes : {', '.join(gaps[:5])}. "
            feedback += "Ajoutez ces mots-clés pour augmenter votre score."
        else:
            feedback += "Toutes les compétences demandées sont présentes dans votre CV !"

        result["job_description_match"] = {
            "match_score": match_score,
            "keyword_gaps": gaps if gaps else ["Aucun écart critique détecté"],
            "fitting_roles": ["Poste ciblé"],
            "custom_feedback": feedback
        }
    else:
        result["job_description_match"] = None

    # ── Comparaison du diplôme (détecté vs requis par l'offre) ──
    diploma_check = _compute_diploma_check(result, job_description, resume_text)
    if diploma_check is not None:
        if result["job_description_match"] is None:
            result["job_description_match"] = {}
        result["job_description_match"]["diploma_check"] = diploma_check

    return result

def _looks_like_placeholder_response(data: Dict[str, Any]) -> bool:
    placeholder_markers = {
        "compétence1", "compétence2", "titre", "entreprise", "diplôme", "école",
        "point fort 1 en français", "point fort 2",
        "axe d'amélioration 1 en français", "axe d'amélioration 2",
        "recommandation 1 en français", "recommandation 2", "mot manquant",
        "rôle suggéré", "nom complet réel du candidat tel qu'écrit sur le cv",
    }
    values_to_check: List[str] = []
    values_to_check.append(str(data.get("candidate_name", "")).strip().lower())
    values_to_check += [str(s).strip().lower() for s in data.get("skills", []) if isinstance(s, (str, int, float))]
    values_to_check += [str(s).strip().lower() for s in data.get("strengths", []) if isinstance(s, str)]
    values_to_check += [str(s).strip().lower() for s in data.get("weaknesses", []) if isinstance(s, str)]
    values_to_check += [str(s).strip().lower() for s in data.get("recommendations", []) if isinstance(s, str)]
    for exp in data.get("experience", []) or []:
        if isinstance(exp, dict):
            values_to_check.append(str(exp.get("role", "")).strip().lower())
            values_to_check.append(str(exp.get("company", "")).strip().lower())

    hits = sum(1 for v in values_to_check if v in placeholder_markers)
    return hits >= 2


def analyze_resume_with_ai(resume_text: str, job_description: Optional[str] = None) -> Dict[str, Any]:
    """Sends the resume text and optional job description to Groq for analysis, returning structured JSON."""

    system_instruction = (
        "You are an expert ATS (Applicant Tracking System) optimizer and professional recruiter. "
        "Analyze the provided resume text thoroughly and extract ALL sections without missing any detail. "
        "You must respond ONLY with a valid JSON object matching this structure exactly:\n"
        "{\n"
        "  \"candidate_name\": \"Nom complet réel du candidat (ex: Aya Ijenha)\",\n"
        "  \"email\": \"Adresse email du candidat telle qu'écrite sur le CV, ou chaîne vide si absente\",\n"
        "  \"phone\": \"Numéro de téléphone du candidat tel qu'écrit sur le CV, ou chaîne vide si absent\",\n"
        "  \"city\": \"Ville de résidence du candidat (ex: Casablanca), ou chaîne vide si non mentionnée\",\n"
        "  \"education_level\": \"Niveau du diplôme le plus élevé détecté, normalisé (ex: 'Bac+5', 'Master', 'Licence', 'Bac+3')\",\n"
        "  \"overall_score\": 85,\n"
        "  \"summary\": \"Résumé exécutif professionnel du candidat en 2-3 phrases parfaites en français.\",\n"
        "  \"skills\": [\"Python\", \"React\", \"C++\", \"Java\", \"SQL\"],\n"
        "  \"experience\": [{\"role\": \"Titre professionnel propre (ex: Stagiaire Développeuse Backend)\", \"company\": \"Entreprise ou Organisme\", \"duration\": \"Durée\", \"description\": \"Description claire sans symbole + ou -\"}],\n"
        "  \"projects\": [{\"title\": \"Titre du projet (ex: Application E-Commerce)\", \"technologies\": \"Technologies utilisées\", \"description\": \"Description du projet\"}],\n"
        "  \"education\": [{\"degree\": \"Diplôme ou Filière (ex: Cycle Ingénieur en Informatique)\", \"school\": \"École ou Établissement\", \"duration\": \"Dates ou Années\"}],\n"
        "  \"certifications\": [\"Certification ou Atelier\"],\n"
        "  \"languages\": [\"Français (Courant)\", \"Arabe (Langue maternelle)\", \"Anglais (Courant)\"],\n"
        "  \"qualities\": [\"Esprit d'équipe\", \"Sens de l'organisation\"],\n"
        "  \"strengths\": [\"Point fort SPÉCIFIQUE basé sur ce CV (ex: maîtrise réelle de Python/Django démontrée par les projets)\", \"Autre point fort réel\"],\n"
        "  \"weaknesses\": [\"Lacune SPÉCIFIQUE identifiée sur ce CV (ex: aucune expérience professionnelle réelle, seulement des stages)\", \"Autre faiblesse concrète\"],\n"
        "  \"recommendations\": [\"Conseil CONCRET et PERSONNALISÉ basé sur ce CV précis (ex: Ajouter des métriques de résultats aux projets E-commerce et IA, comme le nombre d'utilisateurs ou le taux de précision)\", \"Autre conseil actionnable ciblant une vraie lacune de ce CV\"],\n"
        "  \"job_description_match\": {\"match_score\": 78, \"keyword_gaps\": [\"Mot manquant\"], \"fitting_roles\": [\"Rôle suggéré\"], \"custom_feedback\": \"Feedback détaillé en français\"}\n"
        "}\n"
        "\n"
        "CRITICAL QUALITY INSTRUCTIONS:\n"
        "1. CORRECT ALL SPELLING & OCR TYPOS: Fix any French spelling errors, missing accents, or OCR typos in the source text. For example, convert 'Hudionte' to 'Étudiante', 'dingénieur' to 'd'ingénieur', 'ever' to 'avec', 'Ojango' to 'Django', 'cune' to 'd'une', 'utiizateur' to 'utilisateur'. Write 100% clean, professional French.\n"
        "2. CLEAN & PROFESSIONAL TITLES: Do NOT copy raw sentence lines or lines starting with '+' or '-' as role or project titles. Synthesize elegant, professional titles (e.g. 'Développeuse Web - Projet E-commerce', 'Stagiaire Développeuse Backend - CBI'). Strip any leading '+', '-', '*', or bullet symbols.\n"
        "3. EXPLICIT SEPARATION: Put formal work experiences and internships under 'experience', and put academic/personal projects under 'projects'.\n"
        "4. EXHAUSTIVE SKILLS EXTRACTION — THIS IS CRITICAL: You MUST extract EVERY SINGLE technical skill, tool, framework, library, "
        "database, methodology, platform, and technology mentioned ANYWHERE in the CV — not just the 'Compétences' section. "
        "Scan the entire resume including: the skills/competences section, project descriptions, experience descriptions, "
        "education coursework, certifications, and any other section. For example, if a project mentions 'MongoDB', 'Node.js', "
        "'TensorFlow', 'Scikit-learn', 'Bootstrap', 'Git', 'Docker', 'Linux', 'UML', 'Scrum', 'Agile', 'Power BI', 'Tableau', "
        "'Figma', 'Firebase', 'REST API', 'Spring Boot', 'Angular', 'TypeScript', 'PHP', 'Laravel', 'MySQL', 'PostgreSQL', "
        "'Oracle', 'PL/SQL', 'JEE', 'Hibernate', '.NET', 'MATLAB', 'R', 'Keras', 'PyTorch', etc. — ALL of them must appear "
        "in the 'skills' array. Do NOT limit to 10-15 skills; include every single one found. Also extract languages (programming and human), "
        "qualities, certifications, and education degrees.\n"
        "5. Set job_description_match to null if no job description is provided.\n"
        "6. education_level must reflect the candidate's CURRENT actual level, NOT the final diploma they are aiming for if still studying. "
        "If the resume shows an ONGOING program (e.g. \"2nd year of a 5-year engineering cycle\", dates ending in \"Présent\"/\"Actuel\"/\"En cours\"), "
        "count only the years already completed (e.g. 2 years of classes préparatoires + being in year 2 of the cycle ingénieur = Bac+4, "
        "NOT Bac+5 — the engineering degree is only Bac+5 once fully completed). Only use 'Master', 'Bac+5', 'Doctorat' etc. for diplomas "
        "that are clearly ALREADY OBTAINED (past dates, no 'in progress' indicators).\n"
        "7. PERSONALIZED RECOMMENDATIONS — THIS IS CRITICAL: Each item in 'strengths', 'weaknesses', and 'recommendations' MUST be 100% specific to THIS candidate's CV. "
        "Never write generic advice like 'Personnalisez votre CV' or 'Ajoutez des mots-clés'. Instead:\n"
        "   - Mention the candidate by name or refer to their actual skills, projects, companies, or school.\n"
        "   - strengths: highlight what is genuinely impressive in THIS CV (real technologies mastered, real projects, real experience).\n"
        "   - weaknesses: identify real gaps in THIS CV (e.g. no metrics in project descriptions, missing GitHub link, no internship experience, etc.).\n"
        "   - recommendations: give concrete, actionable next steps targeting the actual gaps found (e.g. 'Ajouter un lien GitHub vers le projet E-commerce', "
        "'Quantifier les résultats du stage chez CBI (ex: nombre de fonctionnalités livrées, délai respecté)', 'Obtenir une certification AWS ou Azure pour renforcer le profil Cloud').\n"
        "   Provide at least 4 recommendations. Each must be a complete, specific sentence in French.\n"
        "8. Do not include any markdown, backticks, or text outside the JSON object."
    )

    user_prompt = f"RESUME TEXT:\n{resume_text}\n\n"
    if job_description:
        user_prompt += f"JOB DESCRIPTION TO MATCH AGAINST:\n{job_description}\n"
    else:
        user_prompt += "No specific job description provided. Perform a general standalone evaluation."

    try:
        response_text = call_groq_api(user_prompt, system_instruction, response_format_json=True)

        clean = response_text.strip()
        if "```" in clean:
            clean = re.sub(r"^```[a-zA-Z]*\n?", "", clean)
            clean = re.sub(r"```$", "", clean).strip()

        match = re.search(r'\{.*\}', clean, re.DOTALL)
        json_str = match.group(0) if match else clean

        analysis_data = json.loads(json_str)

        if _looks_like_placeholder_response(analysis_data):
            logger.warning(
                "Groq API a renvoyé des valeurs d'exemple non substituées — bascule sur l'analyse locale."
            )
            mock_res = get_mock_analysis(resume_text, job_description)
            return mock_res

        if not str(analysis_data.get("candidate_name", "")).strip():
            lines = [l.strip() for l in resume_text.split('\n') if l.strip()]
            analysis_data["candidate_name"] = _guess_candidate_name(resume_text, lines)

        extracted_email = _extract_email_from_text(resume_text)
        if extracted_email:
            analysis_data["email"] = extracted_email
        else:
            analysis_data["email"] = _clean_extracted_email(analysis_data.get("email", ""))

        if not str(analysis_data.get("phone", "")).strip():
            phone_match = re.search(r'(?:\+?\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]?\d{2,4}[\s.-]?\d{2,4}[\s.-]?\d{0,4}', resume_text)
            analysis_data["phone"] = phone_match.group(0).strip() if phone_match else ""

        # ── Comparaison du diplôme (détecté vs requis par l'offre) ──
        diploma_check = _compute_diploma_check(analysis_data, job_description, resume_text)
        if diploma_check is not None:
            if analysis_data.get("job_description_match") is None:
                analysis_data["job_description_match"] = {}
            analysis_data["job_description_match"]["diploma_check"] = diploma_check

        return analysis_data
    except Exception as e:
        logger.error(f"Groq API analysis failed: {e}")
        try:
            if os.getenv("GEMINI_API_KEY") and os.getenv("GEMINI_API_KEY") != "your_gemini_api_key_here":
                logger.info("Tentative de repli sur l'API Gemini...")
                res = call_gemini_api(user_prompt, system_instruction, response_format_json=True)
                clean_g = re.sub(r"^```[a-zA-Z]*\n?", "", res.strip())
                clean_g = re.sub(r"```$", "", clean_g).strip()
                match_g = re.search(r'\{.*\}', clean_g, re.DOTALL)
                gemini_data = json.loads(match_g.group(0) if match_g else clean_g)

                extracted_email = _extract_email_from_text(resume_text)
                if extracted_email:
                    gemini_data["email"] = extracted_email
                else:
                    gemini_data["email"] = _clean_extracted_email(gemini_data.get("email", ""))

                diploma_check = _compute_diploma_check(gemini_data, job_description, resume_text)
                if diploma_check is not None:
                    if gemini_data.get("job_description_match") is None:
                        gemini_data["job_description_match"] = {}
                    gemini_data["job_description_match"]["diploma_check"] = diploma_check

                return gemini_data
        except Exception as gemini_err:
            logger.warning(f"Fallback Gemini échoué : {gemini_err}")

        mock_res = get_mock_analysis(resume_text, job_description)
        # Ensure mock email is cleaned
        if "email" in mock_res:
            mock_res["email"] = _clean_extracted_email(mock_res["email"])
        return mock_res


def chat_with_resume_ai(resume_text: str, chat_history: List[Dict[str, Any]], user_message: str) -> str:
    system_instruction = (
        "Tu es un assistant recrutement expert. L'utilisateur va te poser des questions sur son CV. "
        "Tu as accès au texte complet du CV ci-dessous. Réponds avec précision, de manière professionnelle et directement basée sur le CV. "
        "Réponds toujours en français.\n\n"
        f"TEXTE DU CV:\n{resume_text}"
    )

    history_text = ""
    for msg in chat_history[-10:]:
        role = "Assistant" if msg.get("sender") == "ai" else "Utilisateur"
        history_text += f"{role}: {msg.get('message')}\n"

    full_prompt = history_text + f"Utilisateur: {user_message}\nAssistant:"

    try:
        return call_groq_api(full_prompt, system_instruction, response_format_json=False)
    except Exception as e:
        logger.error(f"Groq Chat Error: {e}")
        return f"Désolé, je n'ai pas pu générer de réponse via Groq : {e}"


def chat_about_resume(resume_text: str, chat_history: List[Dict[str, Any]], user_message: str) -> str:
    return chat_with_resume_ai(resume_text, chat_history, user_message)

def generate_interview_invitation(
    candidate_name: str,
    job_title: str,
    interview_date: str,
    interview_time: str,
    interview_format: str,
    interview_location: str,
    interview_notes: str = "",
    reference: str = "",
) -> dict:
    """
    Génère un email de convocation à un entretien avec une structure FIXE et
    toujours identique (garantie par le code, pas par l'IA) : accroche
    personnalisée, puis la liste claire des détails pratiques (référence,
    date, heure, format, lieu), puis une formule de clôture standard. Seule
    l'accroche d'ouverture est confiée à l'IA (une ou deux phrases courtes),
    ce qui évite le problème d'un email parfois bien structuré et parfois
    pas — la mise en page ne dépend jamais du modèle.
    """
    first_name = candidate_name.split()[0] if candidate_name else "Bonjour"

    # ── Phrase d'ouverture personnalisée générée par l'IA (avec repli fixe) ──
    opening_sentence = f"Nous avons le plaisir de vous convier à un entretien{' pour le poste de ' + job_title if job_title else ''} au sein de notre équipe."
    try:
        system_instruction = (
            "Tu es un(e) chargé(e) de recrutement professionnel(le). Rédige UNIQUEMENT une phrase "
            "d'introduction chaleureuse et professionnelle (1 à 2 phrases maximum, en français, en vouvoyant), qui "
            "annonce au candidat qu'il est convié à un entretien pour le poste concerné. "
            "Ne mentionne PAS la date, l'heure, le format ou le lieu (ces informations seront ajoutées "
            "séparément). Réponds UNIQUEMENT avec cette phrase, sans guillemets, sans markdown, sans JSON."
        )
        user_prompt = f"Candidat(e) : {first_name}\nPoste concerné : {job_title or 'Non précisé'}"
        ai_opening = call_groq_api(user_prompt, system_instruction, response_format_json=False).strip()
        if ai_opening and 10 < len(ai_opening) < 400 and not re.search(r'\d{1,2}[:/h]\d{2}|présentiel|visio', ai_opening.lower()):
            opening_sentence = ai_opening
    except Exception as e:
        logger.warning(f"Échec de la génération IA de l'accroche, repli sur la phrase standard : {e}")

    # ── Structure fixe, toujours identique, peu importe l'IA ──
    subject = f"Convocation à un entretien{' — ' + job_title if job_title else ''}"
    reference_line = f"• Référence : {reference}\n" if reference else ""
    notes_block = f"\nInformations complémentaires : {interview_notes}\n" if interview_notes else ""

    body = (
        f"Bonjour {first_name},\n\n"
        f"{opening_sentence}\n\n"
        f"Voici les détails de votre entretien :\n"
        f"{reference_line}"
        f"• Date : {interview_date}\n"
        f"• Heure : {interview_time}\n"
        f"• Format : {interview_format}\n"
        f"• Lieu / Lien : {interview_location}\n"
        f"{notes_block}\n"
        f"Nous vous recommandons de vous connecter (ou de vous présenter) quelques minutes avant "
        f"l'heure prévue.\n\n"
        f"N'hésitez pas à nous contacter pour toute question.\n\n"
        f"Cordialement,\n"
        f"L'équipe Recrutement"
    )

    return {"subject": subject, "body": body}

def generate_rejection_email(candidate_name: str, job_title: str = "", reference: str = "") -> dict:
    """
    Génère un email de refus de candidature avec une structure FIXE et
    professionnelle (garantie par le code, pas par l'IA) : accroche
    personnalisée, puis les informations de la candidature (référence,
    poste visé), puis le message de refus, puis une formule de clôture
    bienveillante. Seule l'accroche est confiée à l'IA, avec repli fixe.
    """
    first_name = candidate_name.split()[0] if candidate_name else "Bonjour"

    # ── Phrase d'ouverture personnalisée générée par l'IA (avec repli fixe) ──
    opening_sentence = (
        f"Nous vous remercions vivement pour l'intérêt que vous avez porté à notre entreprise"
        f"{' et pour votre candidature au poste de ' + job_title if job_title else ''}."
    )
    try:
        system_instruction = (
            "Tu es un(e) chargé(e) de recrutement professionnel(le). Rédige UNIQUEMENT une phrase "
            "de remerciement chaleureuse et professionnelle (1 à 2 phrases maximum, en français, en vouvoyant), "
            "qui remercie le candidat pour sa candidature et l'intérêt porté à l'entreprise. "
            "Ne mentionne PAS que la candidature est refusée (cette information sera ajoutée séparément). "
            "Réponds UNIQUEMENT avec cette phrase, sans guillemets, sans markdown, sans JSON."
        )
        user_prompt = f"Candidat(e) : {first_name}\nPoste concerné : {job_title or 'Non précisé'}"
        ai_opening = call_groq_api(user_prompt, system_instruction, response_format_json=False).strip()
        if ai_opening and 10 < len(ai_opening) < 400 and not re.search(r'refus|retenu|regret', ai_opening.lower()):
            opening_sentence = ai_opening
    except Exception as e:
        logger.warning(f"Échec de la génération IA de l'accroche (refus), repli sur la phrase standard : {e}")

    # ── Structure fixe, toujours identique, peu importe l'IA ──
    subject = f"Mise à jour concernant votre candidature{' — ' + job_title if job_title else ''}"

    info_lines = []
    if reference:
        info_lines.append(f"• Référence : {reference}\n")
    if job_title:
        info_lines.append(f"• Poste concerné : {job_title}\n")
    info_block = ""
    if info_lines:
        info_block = "Rappel de votre candidature :\n" + "".join(info_lines) + "\n"

    body = (
        f"Bonjour {first_name},\n\n"
        f"{opening_sentence}\n\n"
        f"{info_block}"
        f"Après étude attentive de votre profil, nous sommes au regret de vous informer que nous n'avons "
        f"pas retenu votre candidature pour ce poste.\n\n"
        f"Cette décision ne remet pas en cause la qualité de votre parcours, et nous vous invitons à "
        f"consulter nos futures opportunités qui pourraient mieux correspondre à votre profil.\n\n"
        f"Nous vous souhaitons une excellente continuation dans vos projets professionnels.\n\n"
        f"Cordialement,\n"
        f"L'équipe Recrutement"
    )

    return {"subject": subject, "body": body}