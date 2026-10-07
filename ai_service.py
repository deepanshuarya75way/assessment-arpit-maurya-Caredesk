from cmd import PROMPT
import os
from io import BytesIO
from shlex import join

import pytesseract
import requests
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

# very long reports can overflow the model's context, so we cut them off
MAX_PROMPT_CHARS = 12000

# Tesseract path: use the TESSERACT_CMD env variable if set,
# otherwise fall back to the default Windows install location
_WINDOWS_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

if os.getenv("TESSERACT_CMD"):
    pytesseract.pytesseract.tesseract_cmd = os.getenv("TESSERACT_CMD")
elif os.name == "nt" and os.path.exists(_WINDOWS_TESSERACT):
    pytesseract.pytesseract.tesseract_cmd = _WINDOWS_TESSERACT


class AIServiceError(Exception):
    """Ollama / Tesseract is unavailable or failed."""


class InvalidFileError(Exception):
    """The uploaded file could not be read."""


# ---------- Ollama ----------

def ask_ollama(prompt, temperature=0.2, max_tokens=700):
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "keep_alive": "10m",
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }

    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/generate", json=payload, timeout=180
        )
    except requests.exceptions.ConnectionError:
        raise AIServiceError(
            "Cannot connect to Ollama. Start it with 'ollama serve' and try again."
        )
    except requests.exceptions.Timeout:
        raise AIServiceError("Ollama took too long to respond. Please try again.")
    except requests.exceptions.RequestException as e:
        raise AIServiceError(f"Request to Ollama failed: {e}")

    if response.status_code != 200:
        raise AIServiceError(
            f"Ollama returned status {response.status_code}: {response.text[:200]}"
        )

    answer = response.json().get("response", "").strip()
    if not answer:
        raise AIServiceError("Ollama returned an empty response.")

    return answer


def healthcare_assistant(history):
    if not history:
         return""
         lines=[]
         for m in  history:
            speaker = "user" if m["role"]=="user"else "assistent"
            lines.append(f"{speaker}:"{,['content']}")
            text = "\".join(lines)
            return text[MAX_HISTORY_CHARS:]
            Def healthcare_assistant(question, histroy= None):
            history_text= _format_history(history)
            history_block =f"\conversation  so far:\n {history_text}\n"history_text else ""
            PROMPT =f""

- Provide general healthcare information.
- Do not diagnose a confirmed disease.
- Do not prescribe medicines.
- Do not provide medicine dosages.
- Give precautions relevant to the question.
- Mention when professional medical evaluation is appropriate.
- If the situation appears urgent, clearly recommend urgent medical care.
"""
    return ask_ollama(prompt, max_tokens=500)


def check_symptoms(symptoms):
    prompt = f"""
You are an AI healthcare information assistant.

Analyze the user's symptoms carefully.

USER SYMPTOMS:
{symptoms}

Return the answer in EXACTLY this structure:

Symptoms Identified:
- List the important symptoms understood from the user's input.

Possible Conditions:
- Give 2 to 4 possible conditions that can be associated with these symptoms.
- Do NOT claim that any condition is confirmed.
- Consider the combination of symptoms, not just one keyword.

Severity:
- Low / Moderate / High / Emergency
- Briefly explain why.

Recommended Department:
- Give the most relevant medical department.

Relevant Precautions:
- Give 3 to 5 precautions specifically related to these symptoms.
- Do NOT prescribe medicines or dosages.

Doctor Advice:
- Give advice specifically related to the symptoms.
- Mention when professional medical evaluation is appropriate.

Emergency Warning:
- Mention specific warning signs that require urgent medical attention.
- If there are no obvious emergency warning signs, say:
  "No specific emergency warning identified from the information provided."

IMPORTANT RULES:
1. Analyze ANY symptom or combination of symptoms provided by the user.
2. Provide preliminary health information in simple language.
3. Do not diagnose the patient.
4. Do not prescribe medicines or provide dosages.
"""
    return ask_ollama(prompt, max_tokens=800)


def summarize_report(report_text):
    report_text = report_text[:MAX_PROMPT_CHARS]
    prompt = f"""
You are an AI healthcare report summarization assistant.

Summarize the following medical report in simple language.

Important:
- Do not diagnose the patient.
- Do not prescribe medicines.
- Clearly mention important findings only.
- If information is unclear, say that it should be verified by a qualified healthcare professional.

Medical Report:
{report_text}
"""
    return ask_ollama(prompt, max_tokens=700)


def explain_ocr_text(extracted_text):
    extracted_text = extracted_text[:MAX_PROMPT_CHARS]
    prompt = f"""
You are a healthcare document assistant.

Analyze the following text extracted from a prescription or medical document.

Provide:
1. Medicines or important terms detected
2. Dosage or instructions if clearly mentioned
3. A simple explanation of the extracted information

Important:
- Do not diagnose the patient.
- Do not prescribe or recommend medicines.
- If the text is unclear, mention that it should be verified with a qualified healthcare professional.

Extracted Text:
{extracted_text}
"""
    return ask_ollama(prompt, max_tokens=700)


# ---------- file reading ----------

def extract_text_from_txt(data):
    return data.decode("utf-8", errors="ignore").strip()


def extract_text_from_pdf(data):
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise InvalidFileError("This PDF is password protected.")

        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
    except InvalidFileError:
        raise
    except Exception:
        raise InvalidFileError("Could not read this PDF. The file may be corrupted.")

    return "\n".join(pages).strip()


def extract_text_from_image(data):
    try:
        image = Image.open(BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError):
        raise InvalidFileError("Could not read this image file.")

    try:
        return pytesseract.image_to_string(image).strip()
    except pytesseract.TesseractNotFoundError:
        raise AIServiceError(
            "Tesseract OCR is not installed or not found. Install it, or set "
            "the TESSERACT_CMD environment variable to its path."
        )


# ---------- health ----------

def service_status():
    """Used by the UI to show whether Ollama and Tesseract are ready."""
    ollama_ok, model_ok = False, False
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        ollama_ok = r.status_code == 200
        names = [m.get("name", "") for m in r.json().get("models", [])]
        model_ok = any(n.split(":")[0] == OLLAMA_MODEL.split(":")[0] for n in names)
    except Exception:
        pass
    try:
        pytesseract.get_tesseract_version()
        ocr_ok = True
    except Exception:
        ocr_ok = False
    return {"ollama": ollama_ok, "model": model_ok, "model_name": OLLAMA_MODEL, "ocr": ocr_ok}
