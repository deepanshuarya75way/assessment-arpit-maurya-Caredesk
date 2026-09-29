import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import date, datetime, time
from typing import Literal, get_args

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

import ai_service
import config
import database

Department = Literal[
    "General Medicine", "Cardiology", "Dermatology", "Neurology", "Orthopedics"
]

MAX_UPLOAD_MB = 10
FRONTEND = Path(__file__).resolve().parent / "frontend" / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.create_tables()
    database.seed_defaults()
    yield


app = FastAPI(title="AI Healthcare Automation API", lifespan=lifespan)

# allow any frontend to talk to the API while developing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- login guard: everything except these needs a valid token ----------

PUBLIC = {"/", "/auth/login", "/auth/signup", "/health", "/docs", "/openapi.json", "/redoc"}


def token_of(request):
    h = request.headers.get("authorization", "")
    return h[7:] if h.lower().startswith("bearer ") else ""


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path in PUBLIC:
        return await call_next(request)
    user = database.user_from_token(token_of(request))
    if not user:
        return JSONResponse({"detail": "Please log in."}, status_code=401)
    request.state.user = user
    return await call_next(request)


# ---------- request models ----------

class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=100)


class SignupIn(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=6, max_length=100)

    # remove spaces around the username only (the password is kept exactly as typed)
    @field_validator("username")
    @classmethod
    def clean_username(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Username cannot be empty")
        return value


class PasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=100)


class DoctorIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=100)
    department: Department


class PatientIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=100)
    age: int = Field(ge=1, le=120)
    gender: Literal["Male", "Female", "Other"]
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: str = Field(pattern=r"^\+?[0-9\s\-]{7,15}$")
    department: Department
    symptoms: str = Field(default="", max_length=1000)


class AppointmentIn(BaseModel):
    patient_id: int
    department: Department
    doctor: str = Field(default="", max_length=100)
    appointment_date: date
    appointment_time: time


class QuestionIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(min_length=3, max_length=1000)


class SymptomsIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    symptoms: str = Field(min_length=3, max_length=1000)


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=50000)


# ---------- helpers ----------

def run_ai(func, *args):
    """Call an AI function and turn its errors into proper HTTP errors."""
    try:
        return func(*args)
    except ai_service.AIServiceError as e:
        raise HTTPException(status_code=503, detail=str(e))


def read_upload(file: UploadFile, allowed_extensions):
    filename = (file.filename or "").lower()
    if not filename.endswith(tuple(allowed_extensions)):
        allowed = ", ".join(allowed_extensions)
        raise HTTPException(status_code=400, detail=f"Only these files are allowed: {allowed}")

    limit = MAX_UPLOAD_MB * 1024 * 1024
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=f"File is larger than {MAX_UPLOAD_MB} MB.")
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    return data


# ---------- general ----------

@app.get("/", include_in_schema=False)
def root():
    if FRONTEND.exists():
        return FileResponse(FRONTEND)
    return {"message": "AI Healthcare Automation API is running", "docs": "/docs"}


@app.get("/health")
def health():
    return ai_service.service_status()


@app.get("/departments")
def list_departments():
    return list(get_args(Department))


@app.get("/stats")
def stats():
    return database.get_stats()


# ---------- auth ----------

@app.post("/auth/login")
def do_login(body: LoginIn):
    token = database.login(body.username.strip(), body.password)
    if not token:
        raise HTTPException(status_code=401, detail="Wrong username or password.")
    return {"token": token, "username": body.username.strip()}


@app.post("/auth/signup", status_code=201)
def do_signup(body: SignupIn):
    try:
        user_id = database.create_user(body.username, body.password)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="This username is already taken.")
    return {"id": user_id, "message": "Account created. Now you can log in."}


@app.get("/admin/users")
def admin_users(request: Request):
    # only the admin account can see the list of users
    if request.state.user["username"] != config.ADMIN_USERNAME:
        raise HTTPException(status_code=403, detail="Only admin can view users.")
    return database.get_users()


@app.get("/auth/me")
def me(request: Request):
    user = request.state.user
    return {**user, "is_admin": user["username"] == config.ADMIN_USERNAME}


@app.post("/auth/logout")
def do_logout(request: Request):
    database.logout(token_of(request))
    return {"message": "Logged out"}


@app.post("/auth/password")
def change_password(body: PasswordIn, request: Request):
    if not database.change_password(request.state.user["id"], body.old_password, body.new_password):
        raise HTTPException(status_code=400, detail="Current password is wrong.")
    return {"message": "Password changed"}


# ---------- doctors ----------

@app.get("/doctors")
def list_doctors():
    return database.get_doctors()


@app.post("/doctors", status_code=201)
def create_doctor(doctor: DoctorIn):
    return {"id": database.add_doctor(doctor.name, doctor.department), "message": "Doctor added"}


@app.delete("/doctors/{doctor_id}")
def remove_doctor(doctor_id: int):
    if not database.delete_doctor(doctor_id):
        raise HTTPException(status_code=404, detail="Doctor not found.")
    return {"message": "Doctor removed"}


# ---------- patients ----------

@app.post("/patients", status_code=201)
def create_patient(patient: PatientIn):
    patient_id = database.add_patient(
        patient.name,
        patient.age,
        patient.gender,
        patient.email,
        patient.phone,
        patient.department,
        patient.symptoms,
    )
    return {"id": patient_id, "message": "Patient registered successfully"}


@app.get("/patients")
def list_patients():
    return database.get_patients()


@app.put("/patients/{patient_id}")
def edit_patient(patient_id: int, p: PatientIn):
    if not database.update_patient(patient_id, p.name, p.age, p.gender, p.email, p.phone, p.department, p.symptoms):
        raise HTTPException(status_code=404, detail="Patient not found.")
    return {"message": "Patient updated"}


@app.delete("/patients/{patient_id}")
def remove_patient(patient_id: int):
    if not database.delete_patient(patient_id):
        raise HTTPException(status_code=404, detail="Patient not found.")
    return {"message": "Patient and their appointments deleted"}


# ---------- appointments ----------

@app.post("/appointments", status_code=201)
def create_appointment(appointment: AppointmentIn):
    patient = database.get_patient(appointment.patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found.")

    if appointment.appointment_date < date.today():
        raise HTTPException(status_code=400, detail="Appointment date cannot be in the past.")

    if appointment.appointment_date == date.today():
        if appointment.appointment_time <= datetime.now().time():
            raise HTTPException(status_code=400, detail="Appointment time has already passed.")

    date_str = appointment.appointment_date.isoformat()
    time_str = appointment.appointment_time.strftime("%H:%M")

    if database.slot_taken(appointment.department, date_str, time_str):
        raise HTTPException(
            status_code=409,
            detail="This time slot is already booked for the selected department.",
        )

    try:
        appointment_id = database.add_appointment(
            patient, appointment.department, appointment.doctor, date_str, time_str
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="This time slot is already booked for the selected department.")
    return {"id": appointment_id, "message": "Appointment booked successfully"}


@app.get("/appointments")
def list_appointments():
    return database.get_appointments()


@app.patch("/appointments/{appointment_id}/cancel")
def cancel_appointment(appointment_id: int):
    if not database.cancel_appointment(appointment_id):
        raise HTTPException(status_code=404, detail="Booked appointment not found.")
    return {"message": "Appointment cancelled"}


# ---------- AI features ----------

@app.post("/ai/assistant")
def ask_assistant(body: QuestionIn):
    answer = run_ai(ai_service.healthcare_assistant, body.question)
    database.log_activity("ai_query")
    return {"answer": answer}


@app.post("/ai/symptoms")
def symptom_checker(body: SymptomsIn):
    answer = run_ai(ai_service.check_symptoms, body.symptoms)
    database.log_activity("ai_query")
    return {"result": answer}


@app.post("/ai/summarize")
def summarize(body: TextIn):
    summary = run_ai(ai_service.summarize_report, body.text)
    database.log_activity("report")
    return {"summary": summary}


@app.post("/ai/explain-ocr")
def explain_ocr(body: TextIn):
    explanation = run_ai(ai_service.explain_ocr_text, body.text)
    database.log_activity("ai_query")
    return {"explanation": explanation}


# ---------- file uploads ----------

@app.post("/reports/extract")
def extract_report_text(file: UploadFile = File(...)):
    data = read_upload(file, [".txt", ".pdf"])

    try:
        if file.filename.lower().endswith(".pdf"):
            text = ai_service.extract_text_from_pdf(data)
        else:
            text = ai_service.extract_text_from_txt(data)
    except ai_service.InvalidFileError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not text:
        raise HTTPException(status_code=422, detail="No text could be extracted from this file.")

    return {"filename": file.filename, "text": text}


@app.post("/ocr/extract")
def extract_image_text(file: UploadFile = File(...)):
    data = read_upload(file, [".jpg", ".jpeg", ".png"])

    try:
        text = ai_service.extract_text_from_image(data)
    except ai_service.InvalidFileError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ai_service.AIServiceError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return {"filename": file.filename, "text": text}
