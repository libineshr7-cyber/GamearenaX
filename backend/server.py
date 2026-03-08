from fastapi import FastAPI, APIRouter, HTTPException, Depends, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict, EmailStr
from typing import List, Optional
import uuid
from datetime import datetime, timezone, timedelta
import jwt
import smtplib
from email.message import EmailMessage
import json
import asyncio
import requests as http_requests

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  STORAGE SETUP
#  Priority:
#    1. Google Sheets (Cloud Persistent) — Set GOOGLE_SHEET_URL (Web App URL)
#    2. Local data.json (Fallback for local dev)
# ─────────────────────────────────────────────────────────────────────────────

GOOGLE_SHEET_URL = os.environ.get('GOOGLE_SHEET_URL', '').strip()
USE_GOOGLE_SHEETS = bool(GOOGLE_SHEET_URL)
DATA_FILE  = ROOT_DIR / "data.json"
DATA_RETENTION_DAYS = 60

DATA_RETENTION_DAYS = 60   # registrations older than 60 days are auto-removed

data_lock  = asyncio.Lock()
_local_db_cache: Optional[dict] = None

# ── Google Sheets helpers ───────────────────────────────────────────────────

def _gs_load() -> dict:
    """Read data from Google Sheets via Web App."""
    if not GOOGLE_SHEET_URL:
        raise Exception("GOOGLE_SHEET_URL missing from environment.")
    try:
        r = http_requests.get(GOOGLE_SHEET_URL, timeout=15)
        r.raise_for_status()
        data = r.json()
        # Fallback empty structures
        data.setdefault("tournaments", [])
        data.setdefault("registrations", [])
        data.setdefault("contacts", [])
        return data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Google Sheet read failed: {e}")
        # Return empty data instead of crashing on read
        return {"tournaments": [], "registrations": [], "contacts": []}

def _gs_save(data: dict):
    """Write data to Google Sheets via Web App."""
    if not GOOGLE_SHEET_URL:
        raise Exception("GOOGLE_SHEET_URL missing from environment.")
    try:
        r = http_requests.post(GOOGLE_SHEET_URL, json=data, timeout=20)
        r.raise_for_status()
    except Exception as e:
        logger.error(f"Google Sheet write failed: {e}")
        raise HTTPException(status_code=500, detail=f"Google Sheet write failed: {str(e)}")

# ── Local JSON helpers ────────────────────────────────────────────────────────

def _local_load() -> dict:
    global _local_db_cache
    if _local_db_cache is not None:
        return _local_db_cache
    if not DATA_FILE.exists():
        _local_db_cache = {"tournaments": [], "registrations": [], "contacts": []}
        return _local_db_cache
    try:
        with open(DATA_FILE, "r") as f:
            _local_db_cache = json.load(f)
        _local_db_cache.setdefault("tournaments", [])
        _local_db_cache.setdefault("registrations", [])
        _local_db_cache.setdefault("contacts", [])
        return _local_db_cache
    except Exception:
        _local_db_cache = {"tournaments": [], "registrations": [], "contacts": []}
        return _local_db_cache

def _local_save(data: dict):
    global _local_db_cache
    _local_db_cache = data
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

# ── Unified DB access ─────────────────────────────────────────────────────────

def load_db() -> dict:
    """Load database from Cloud (Google Sheets) or fallback to local JSON."""
    if USE_GOOGLE_SHEETS:
        return _gs_load()
    return _local_load()

def save_db(data: dict):
    """Save database to Cloud (Google Sheets) and purge old data."""
    data = _purge_old_registrations(data)
    if USE_GOOGLE_SHEETS:
        _gs_save(data)
    else:
        _local_save(data)

def _purge_old_registrations(data: dict) -> dict:
    """Remove registrations older than DATA_RETENTION_DAYS. Returns updated data."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=DATA_RETENTION_DAYS)).isoformat()
    before = len(data["registrations"])
    data["registrations"] = [
        r for r in data["registrations"]
        if r.get("registered_at", "9999") >= cutoff
    ]
    removed = before - len(data["registrations"])
    if removed:
        logger.info(f"Auto-purged {removed} registrations older than {DATA_RETENTION_DAYS} days.")
    return data

# ─────────────────────────────────────────────────────────────────────────────

JWT_SECRET = os.environ.get('JWT_SECRET', 'your-secret-key-change-in-production')
JWT_ALGORITHM = 'HS256'
security = HTTPBearer()

app = FastAPI()
api_router = APIRouter(prefix="/api")

# ── Pydantic models ───────────────────────────────────────────────────────────

class TournamentCreate(BaseModel):
    name: str = "Free Fire Tournament"
    date: str
    max_slots: int = 50

class Tournament(TournamentCreate):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    is_active: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class Registration(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tournament_id: str
    player_name: str
    email: EmailStr
    phone: str
    freefire_uid: str
    team_name: str
    payment_screenshot: str
    slot_number: Optional[int] = None
    kills: int = 0
    tournament_rank: int = 0
    total_prize: float = 0.0
    registered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "pending"

class RegistrationCreate(BaseModel):
    tournament_id: Optional[str] = None
    player_name: str
    email: EmailStr
    phone: str
    freefire_uid: str
    team_name: str
    payment_screenshot: str
    slot_number: int

class ContactForm(BaseModel):
    name: str
    email: EmailStr
    subject: str
    message: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_read: bool = False
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))

class UpdateKills(BaseModel): kills: int
class UpdateRank(BaseModel): rank: int
class UpdateStatus(BaseModel): status: str
class AdminLogin(BaseModel): password: str

class TournamentStats(BaseModel):
    total_registrations: int
    max_registrations: int
    total_prize_pool: float
    pending_count: int
    approved_count: int

# ── Auth ──────────────────────────────────────────────────────────────────────

def create_jwt_token(data: dict):
    to_encode = data.copy()
    to_encode.update({"exp": datetime.now(timezone.utc) + timedelta(hours=24)})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)

def verify_jwt_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        return jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

# ── Email helpers ─────────────────────────────────────────────────────────────

def send_approval_email(player_email: str, player_name: str, tournament_name: str):
    smtp_user = os.environ.get('SMTP_USER')
    smtp_pass = os.environ.get('SMTP_PASS')
    if not smtp_user or not smtp_pass:
        logger.warning("SMTP not configured. Skipping confirmation email.")
        return
    try:
        msg = EmailMessage()
        msg['Subject'] = f"Registration Approved - {tournament_name}"
        msg['From'] = smtp_user
        msg['To'] = player_email
        msg.set_content(f"Hello {player_name},\n\nYour registration for {tournament_name} has been APPROVED! Your slot is now confirmed.\n\nGood luck!")
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
    except Exception as e:
        logger.error(f"Email failed: {e}")

def send_registration_email(player_email: str, player_name: str, tournament_name: str, slot_number: int):
    smtp_user = os.environ.get('SMTP_USER')
    smtp_pass = os.environ.get('SMTP_PASS')
    if not smtp_user or not smtp_pass:
        return
    try:
        msg = EmailMessage()
        msg['Subject'] = f"Registration Received - {tournament_name}"
        msg['From'] = smtp_user
        msg['To'] = player_email
        msg.set_content(
            f"Hello {player_name},\n\n"
            f"We have received your registration for {tournament_name} (Requested Slot #{slot_number}).\n\n"
            f"Your registration is PENDING. You will receive another email once an admin approves your slot.\n\nThank you!"
        )
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
    except Exception as e:
        logger.error(f"Email failed: {e}")

def send_confirmation_email_with_rules(player_email: str, player_name: str, tournament_name: str, slot_number: int):
    smtp_user = os.environ.get('SMTP_USER')
    smtp_pass = os.environ.get('SMTP_PASS')
    if not smtp_user or not smtp_pass:
        logger.warning("SMTP not configured. Skipping confirmation email with rules.")
        return
    try:
        msg = EmailMessage()
        msg['Subject'] = f"✅ Registration Confirmed - {tournament_name} | Slot #{slot_number}"
        msg['From'] = smtp_user
        msg['To'] = player_email

        plain_text = f"""
Hello {player_name},

🎮 Your registration for {tournament_name} has been CONFIRMED!

SLOT NUMBER: #{slot_number}
TOURNAMENT: {tournament_name}
ENTRY FEE: ₹20/slot

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GAMEARENAX TOURNAMENT RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. REGISTRATION RULES
   - Players must register before match start time.
   - Entry fee must be paid to confirm registration.
   - Players must provide their correct in-game ID during registration.

2. PAYMENT RULES
   - Entry fee is non-refundable after successful registration.
   - Payment screenshot must be clear and valid.
   - Fake payment proofs will result in permanent disqualification.

3. MATCH TIMING RULES
   - All matches will start exactly at the scheduled time.
   - Players must join the room at least 15 minutes before the match start time.
   - No delay will be made for late players.

4. GAMEPLAY RULES
   - Hacks, cheats, or modded applications are strictly prohibited.
   - Any player found using unfair advantages will be immediately banned.

5. RESULT RULES
   - Winners will be decided based on kills and final match placement.
   - In case of disputes, the admin decision will be final.

6. PRIZE DISTRIBUTION RULES
   - Prize money will be sent within 24-48 hours after result verification.
   - Winners must provide correct payment details.
   - Prize: ₹120 (1st) | ₹60 (2nd) | ₹30 (3rd) | ₹10 per kill

7. BEHAVIOUR RULES
   - Abusive language, harassment, or toxic behaviour is not allowed.
   - Any attempt to scam or exploit the system will lead to a permanent ban.

8. TECHNICAL ISSUES
   - Organizers are not responsible for internet issues, device lag, or game crashes.
   - Players must ensure a stable internet connection before joining.

9. ACCOUNT RESPONSIBILITY
   - Players are responsible for their own game account security.

10. ADMIN AUTHORITY
    - The admin reserves the right to change rules, cancel matches, or take decisions in special situations.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Good luck, {player_name}! See you on the battlefield! 🔥
GameArenaX Team
"""

        html_content = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0a0a0a;font-family:'Segoe UI',Arial,sans-serif;color:#ffffff;">
  <div style="max-width:600px;margin:0 auto;background:#111111;border:1px solid #1f1f1f;border-radius:12px;overflow:hidden;">
    <div style="background:linear-gradient(135deg,#1a1a00,#2a2000);padding:32px 32px 24px;border-bottom:2px solid #FFD700;text-align:center;">
      <div style="font-size:13px;font-weight:700;letter-spacing:4px;color:#FFD700;text-transform:uppercase;margin-bottom:8px;">GameArenaX</div>
      <h1 style="margin:0;font-size:28px;font-weight:800;color:#FFD700;text-transform:uppercase;letter-spacing:2px;">Registration Confirmed!</h1>
      <p style="margin:8px 0 0;color:#a0a0a0;font-size:14px;">You're officially in the arena.</p>
    </div>
    <div style="padding:28px 32px;">
      <p style="color:#d0d0d0;font-size:15px;margin:0 0 20px;">Hello <strong style="color:#ffffff;">{player_name}</strong>,</p>
      <div style="background:#1a1a1a;border:1px solid #FFD700;border-radius:10px;padding:20px;margin-bottom:20px;text-align:center;">
        <div style="font-size:13px;color:#a0a0a0;letter-spacing:3px;text-transform:uppercase;margin-bottom:6px;">Your Slot Number</div>
        <div style="font-size:52px;font-weight:800;color:#FFD700;line-height:1;">#{slot_number}</div>
      </div>
      <div style="background:#1a0000;border:1px solid #ff4444;border-radius:8px;padding:12px 16px;">
        <div style="font-size:12px;font-weight:700;color:#ff4444;text-transform:uppercase;letter-spacing:2px;margin-bottom:4px;">⚠️ Important</div>
        <p style="margin:0;font-size:13px;color:#ffaaaa;">Join the match room at least <strong>15 minutes</strong> before start time. Late players will not be accommodated.</p>
      </div>
    </div>
    <div style="background:#0d0d0d;padding:20px 32px;border-top:1px solid #1f1f1f;text-align:center;">
      <p style="margin:0 0 4px;font-size:16px;font-weight:700;color:#FFD700;">Good Luck, {player_name}! 🔥</p>
      <p style="margin:0;font-size:12px;color:#555;">GameArenaX Team &bull; Free Fire Tournaments</p>
    </div>
  </div>
</body>
</html>
"""
        msg.set_content(plain_text)
        msg.add_alternative(html_content, subtype='html')
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        logger.info(f"Confirmation email with rules sent to {player_email}")
    except Exception as e:
        logger.error(f"Confirmation email failed: {e}")
        raise

# ── API Routes ────────────────────────────────────────────────────────────────

@api_router.get("/")
async def root():
    return {
        "message": "GameArenaX API",
        "storage": "Google Sheets (Cloud Persistent)" if USE_GOOGLE_SHEETS else "Local JSON (Ephemeral)",
        "data_retention_days": DATA_RETENTION_DAYS
    }

@api_router.get("/storage-status")
async def storage_status(payload: dict = Depends(verify_jwt_token)):
    data = load_db()
    return {
        "storage_backend": "Google Sheets" if USE_GOOGLE_SHEETS else "Local JSON",
        "is_persistent": USE_GOOGLE_SHEETS,
        "sheet_connected": USE_GOOGLE_SHEETS,
        "tournaments_count": len(data["tournaments"]),
        "registrations_count": len(data["registrations"]),
    }

@api_router.post("/public/contact")
async def create_contact(input: ContactForm):
    async with data_lock:
        data = load_db()
        doc = input.model_dump()
        doc['created_at'] = doc['created_at'].isoformat()
        data["contacts"].append(doc)
        save_db(data)
    return {"message": "Contact form submitted"}

@api_router.get("/contacts")
async def get_contacts(payload: dict = Depends(verify_jwt_token)):
    data = load_db()
    contacts = sorted(data["contacts"], key=lambda x: x.get("created_at", ""), reverse=True)
    for c in contacts:
        if isinstance(c.get('created_at'), str):
            try:
                c['created_at'] = datetime.fromisoformat(c['created_at'])
            except Exception:
                pass
    return contacts

@api_router.put("/contacts/{contact_id}/read")
async def mark_contact_read(contact_id: str, payload: dict = Depends(verify_jwt_token)):
    async with data_lock:
        data = load_db()
        for c in data["contacts"]:
            if c["id"] == contact_id:
                c["is_read"] = True
        save_db(data)
    return {"message": "Success"}

@api_router.get("/settings")
async def get_settings():
    # Simple read-only — never auto-creates to avoid JSONBin write failures crashing this endpoint
    try:
        data = load_db()
    except Exception as e:
        logger.error(f"Settings load failed: {e}")
        return {"tournament_name": "GameArenaX Tournament", "tournament_date": "TBD", "max_slots": 50, "id": None}
    t = next((t for t in data["tournaments"] if t.get("is_active")), None)
    if not t:
        t = data["tournaments"][0] if data["tournaments"] else None
    if not t:
        return {"tournament_name": "GameArenaX Tournament", "tournament_date": "TBD", "max_slots": 50, "id": None}
    return {"tournament_name": t["name"], "tournament_date": t["date"], "max_slots": t["max_slots"], "id": t["id"]}

@api_router.post("/tournaments", response_model=Tournament)
async def create_tournament(input: TournamentCreate, payload: dict = Depends(verify_jwt_token)):
    async with data_lock:
        data = load_db()
        t = Tournament(**input.model_dump())
        if not data["tournaments"]:
            t.is_active = True
        doc = t.model_dump()
        doc['created_at'] = doc['created_at'].isoformat()
        data["tournaments"].append(doc)
        save_db(data)
    return t

@api_router.get("/tournaments", response_model=List[Tournament])
async def get_tournaments(payload: dict = Depends(verify_jwt_token)):
    data = load_db()
    return sorted(data["tournaments"], key=lambda x: x.get("created_at", ""), reverse=True)

@api_router.get("/public/tournaments", response_model=List[Tournament])
async def get_public_tournaments():
    data = load_db()
    return sorted(data["tournaments"], key=lambda x: x.get("created_at", ""), reverse=True)

@api_router.put("/tournaments/{t_id}/activate")
async def activate_tournament(t_id: str, payload: dict = Depends(verify_jwt_token)):
    async with data_lock:
        data = load_db()
        for t in data["tournaments"]:
            t["is_active"] = (t["id"] == t_id)
        save_db(data)
    return {"message": "Tournament activated"}

@api_router.delete("/tournaments/{t_id}")
async def delete_tournament(t_id: str, payload: dict = Depends(verify_jwt_token)):
    async with data_lock:
        data = load_db()
        data["tournaments"] = [t for t in data["tournaments"] if t["id"] != t_id]
        data["registrations"] = [r for r in data["registrations"] if r.get("tournament_id") != t_id]
        save_db(data)
    return {"message": "Tournament deleted"}

@api_router.post("/register", response_model=Registration)
async def create_registration(input: RegistrationCreate, background_tasks: BackgroundTasks):
    async with data_lock:
        data = load_db()

        # Auto-purge registrations older than 60 days
        data = _purge_old_registrations(data)

        t = None
        if input.tournament_id:
            t = next((x for x in data["tournaments"] if x["id"] == input.tournament_id), None)
        else:
            t = next((x for x in data["tournaments"] if x.get("is_active")), None)

        if not t:
            raise HTTPException(status_code=400, detail="Tournament not found or no active tournament.")

        regs = [r for r in data["registrations"] if str(r.get("tournament_id")) == str(t["id"])]
        
        try:
            m_slots = int(t.get("max_slots", 50))
        except:
            m_slots = 50
            
        if len(regs) >= m_slots:
            raise HTTPException(status_code=400, detail="Tournament full.")

        target_slot = int(input.slot_number)
        slot_occupied = False
        for r in regs:
            try:
                if int(r.get("slot_number")) == target_slot and r.get("status") in ["pending", "approved"]:
                    slot_occupied = True
                    break
            except:
                continue
                
        if slot_occupied:
            raise HTTPException(status_code=400, detail="Slot occupied.")

        registration_dict = input.model_dump()
        registration_dict['tournament_id'] = t["id"]
        registration_obj = Registration(**registration_dict)
        doc = registration_obj.model_dump()
        doc['registered_at'] = doc['registered_at'].isoformat()

        data["registrations"].append(doc)
        save_db(data)

    background_tasks.add_task(send_registration_email, input.email, input.player_name, t["name"], input.slot_number)
    return registration_obj

@api_router.post("/admin/login")
async def admin_login(input: AdminLogin):
    if input.password != os.environ.get('ADMIN_PASSWORD', 'admin123'):
        raise HTTPException(status_code=401, detail="Invalid password")
    return {"token": create_jwt_token({"role": "admin"})}

@api_router.get("/registrations")
async def get_registrations(tournament_id: Optional[str] = None, payload: dict = Depends(verify_jwt_token)):
    data = load_db()
    regs = data["registrations"]
    if tournament_id:
        regs = [r for r in regs if r.get("tournament_id") == tournament_id]
    for reg in regs:
        if isinstance(reg.get('registered_at'), str):
            try:
                reg['registered_at'] = datetime.fromisoformat(reg['registered_at'])
            except Exception:
                pass
    return regs

@api_router.post("/registrations/{registration_id}/send-mail")
async def send_registration_confirmation(registration_id: str, payload: dict = Depends(verify_jwt_token)):
    data = load_db()
    reg = next((r for r in data["registrations"] if r["id"] == registration_id), None)
    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found")
    smtp_user = os.environ.get('SMTP_USER', '').strip()
    smtp_pass = os.environ.get('SMTP_PASS', '').strip().replace(' ', '')
    if not smtp_user or not smtp_pass:
        raise HTTPException(status_code=500, detail="Email not configured. Add SMTP_USER and SMTP_PASS in Render Environment Variables.")
    t = next((t for t in data["tournaments"] if t["id"] == reg.get("tournament_id")), None)
    tournament_name = t["name"] if t else "GameArenaX Tournament"
    try:
        send_confirmation_email_with_rules(reg["email"], reg["player_name"], tournament_name, reg.get("slot_number", "?"))
        return {"message": f"Email sent successfully to {reg['email']}"}
    except smtplib.SMTPAuthenticationError:
        raise HTTPException(status_code=500, detail="Gmail authentication failed. Make sure SMTP_PASS is a Gmail App Password (16 chars). Get one at: myaccount.google.com/apppasswords")
    except smtplib.SMTPException as e:
        raise HTTPException(status_code=500, detail=f"SMTP error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Email failed: {str(e)}")



@api_router.get("/public/leaderboard")
async def get_leaderboard(tournament_id: Optional[str] = None):
    data = load_db()
    t = (
        next((x for x in data["tournaments"] if x["id"] == tournament_id), None)
        if tournament_id
        else next((x for x in data["tournaments"] if x.get("is_active")), None)
    )
    if not t:
        return []
    regs = [r for r in data["registrations"] if r.get("tournament_id") == t["id"] and r.get("status") == "approved"]

    def get_sort_key(x):
        r = x.get('tournament_rank', 0)
        return (-1 if r == 0 else r, -x.get('kills', 0))

    return sorted(
        [{"player_name": r.get("player_name"), "team_name": r.get("team_name"),
          "kills": r.get("kills", 0), "tournament_rank": r.get("tournament_rank", 0),
          "total_prize": r.get("total_prize", 0.0)} for r in regs],
        key=get_sort_key
    )

@api_router.get("/slots")
async def get_slots(tournament_id: Optional[str] = None):
    data = load_db()
    t = (
        next((x for x in data["tournaments"] if x["id"] == tournament_id), None)
        if tournament_id
        else next((x for x in data["tournaments"] if x.get("is_active")), None)
    )
    if not t:
        if not data["tournaments"]:
            doc = Tournament(name="Free Fire Tournament", date="2026-12-31T23:59", max_slots=50, is_active=True).model_dump()
            doc['created_at'] = doc['created_at'].isoformat()
            async with data_lock:
                data = load_db()
                data["tournaments"].append(doc)
                save_db(data)
            t = doc
        else:
            return []
    try:
        max_slots = int(t.get("max_slots", 50))
    except:
        max_slots = 50

    regs = [r for r in data["registrations"] if str(r.get("tournament_id")) == str(t["id"]) and r.get("status") in ["pending", "approved"]]
    
    occupied = {}
    for r in regs:
        try:
            s_num = int(r.get("slot_number"))
            occupied[s_num] = r.get("status")
        except:
            continue
            
    slots = []
    for i in range(1, max_slots + 1):
        if i in occupied:
            slots.append({"slot_number": i, "status": occupied[i]})
        else:
            slots.append({"slot_number": i, "status": "available"})
    return slots

@api_router.put("/registrations/{registration_id}/kills")
async def update_kills(registration_id: str, input: UpdateKills, payload: dict = Depends(verify_jwt_token)):
    async with data_lock:
        data = load_db()
        reg = next((r for r in data["registrations"] if r["id"] == registration_id), None)
        if not reg:
            raise HTTPException(status_code=404, detail="Not found")
        rank_prizes = {1: 120, 2: 60, 3: 30}
        rank_prize = rank_prizes.get(reg.get('tournament_rank', 0), 0)
        reg["kills"] = input.kills
        reg["total_prize"] = (input.kills * 10) + rank_prize
        save_db(data)
    return {"message": "Success"}

@api_router.put("/registrations/{registration_id}/rank")
async def update_rank(registration_id: str, input: UpdateRank, payload: dict = Depends(verify_jwt_token)):
    async with data_lock:
        data = load_db()
        reg = next((r for r in data["registrations"] if r["id"] == registration_id), None)
        if not reg:
            raise HTTPException(status_code=404, detail="Not found")
        rank_prizes = {1: 120, 2: 60, 3: 30}
        reg["tournament_rank"] = input.rank
        reg["total_prize"] = (reg.get('kills', 0) * 10) + rank_prizes.get(input.rank, 0)
        save_db(data)
    return {"message": "Success"}

@api_router.put("/registrations/{registration_id}/status")
async def update_status(registration_id: str, input: UpdateStatus, background_tasks: BackgroundTasks, payload: dict = Depends(verify_jwt_token)):
    async with data_lock:
        data = load_db()
        reg = next((r for r in data["registrations"] if r["id"] == registration_id), None)
        if not reg:
            raise HTTPException(status_code=404, detail="Not found")
        old_status = reg.get("status")
        reg["status"] = input.status
        save_db(data)

    if input.status == "approved" and old_status != "approved":
        t = next((t for t in data["tournaments"] if t["id"] == reg.get("tournament_id")), None)
        background_tasks.add_task(send_approval_email, reg.get("email"), reg.get("player_name"), t["name"] if t else "Tournament")
    return {"message": "Success"}

@api_router.get("/stats", response_model=TournamentStats)
async def get_stats(tournament_id: Optional[str] = None, payload: dict = Depends(verify_jwt_token)):
    data = load_db()
    t = (
        next((x for x in data["tournaments"] if x["id"] == tournament_id), None)
        if tournament_id
        else next((x for x in data["tournaments"] if x.get("is_active")), None)
    )
    if not t:
        return TournamentStats(total_registrations=0, max_registrations=50, total_prize_pool=0, pending_count=0, approved_count=0)
    regs = [r for r in data["registrations"] if r.get("tournament_id") == t["id"]]
    return TournamentStats(
        total_registrations=len(regs),
        max_registrations=t.get("max_slots", 50),
        total_prize_pool=sum(r.get('total_prize', 0.0) for r in regs),
        pending_count=sum(1 for r in regs if r.get("status") == "pending"),
        approved_count=sum(1 for r in regs if r.get("status") == "approved")
    )

# ─────────────────────────────────────────────────────────────────────────────

app.include_router(api_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/", StaticFiles(directory=str(ROOT_DIR.parent), html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    print("Starting GameArenaX backend...")
    if USE_JSONBIN:
        print("✅ Storage: JSONBin.io (Cloud - Data is PERSISTENT!)")
    else:
        print("⚠️  Storage: Local JSON (data.json) — data WILL BE LOST on Render restart!")
        print("   → To fix: Add JSONBIN_API_KEY and JSONBIN_BIN_ID to Render Environment Variables")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
