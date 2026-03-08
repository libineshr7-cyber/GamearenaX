from fastapi import FastAPI, APIRouter, HTTPException, Depends, UploadFile, File, Form, BackgroundTasks
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

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# --- LOCAL DATABASE SETUP ---
DATA_FILE = ROOT_DIR / "data.json"
data_lock = asyncio.Lock()

def load_data():
    if not DATA_FILE.exists():
        return {"tournaments": [], "registrations": [], "contacts": []}
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except:
        return {"tournaments": [], "registrations": [], "contacts": []}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

db = load_data()
# ----------------------------

JWT_SECRET = os.environ.get('JWT_SECRET', 'your-secret-key-change-in-production')
JWT_ALGORITHM = 'HS256'
security = HTTPBearer()

app = FastAPI()
api_router = APIRouter(prefix="/api")

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

def create_jwt_token(data: dict):
    to_encode = data.copy()
    to_encode.update({"exp": datetime.now(timezone.utc) + timedelta(hours=24)})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)

def verify_jwt_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        return jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except:
        raise HTTPException(status_code=401, detail="Invalid token")

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
        msg.set_content(f"Hello {player_name},\n\nWe have received your registration and payment screenshot for {tournament_name} (Requested Slot #{slot_number}).\n\nYour registration is currently PENDING. You will receive another email once an admin verifies your payment and approves your slot.\n\nThank you!")
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
   - Players must register before the match start time.
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
    <!-- Header -->
    <div style="background:linear-gradient(135deg,#1a1a00,#2a2000);padding:32px 32px 24px;border-bottom:2px solid #FFD700;text-align:center;">
      <div style="font-size:13px;font-weight:700;letter-spacing:4px;color:#FFD700;text-transform:uppercase;margin-bottom:8px;">GameArenaX</div>
      <h1 style="margin:0;font-size:28px;font-weight:800;color:#FFD700;text-transform:uppercase;letter-spacing:2px;">Registration Confirmed!</h1>
      <p style="margin:8px 0 0;color:#a0a0a0;font-size:14px;">You're officially in the arena.</p>
    </div>

    <!-- Slot Card -->
    <div style="padding:28px 32px;">
      <p style="color:#d0d0d0;font-size:15px;margin:0 0 20px;">Hello <strong style="color:#ffffff;">{player_name}</strong>,</p>
      <p style="color:#a0a0a0;font-size:14px;margin:0 0 24px;">Your registration has been confirmed. Here are your match details:</p>

      <div style="background:#1a1a1a;border:1px solid #FFD700;border-radius:10px;padding:20px;margin-bottom:20px;text-align:center;">
        <div style="font-size:13px;color:#a0a0a0;letter-spacing:3px;text-transform:uppercase;margin-bottom:6px;">Your Slot Number</div>
        <div style="font-size:52px;font-weight:800;color:#FFD700;line-height:1;">#{slot_number}</div>
      </div>

      <div style="background:#161616;border-radius:10px;padding:16px 20px;margin-bottom:24px;">
        <table style="width:100%;border-collapse:collapse;">
          <tr>
            <td style="padding:8px 0;color:#808080;font-size:13px;text-transform:uppercase;letter-spacing:1px;">Tournament</td>
            <td style="padding:8px 0;color:#ffffff;font-weight:600;text-align:right;">{tournament_name}</td>
          </tr>
          <tr style="border-top:1px solid #222;">
            <td style="padding:8px 0;color:#808080;font-size:13px;text-transform:uppercase;letter-spacing:1px;">Entry Fee</td>
            <td style="padding:8px 0;color:#00FFFF;font-weight:600;text-align:right;">&#8377;20 / slot</td>
          </tr>
          <tr style="border-top:1px solid #222;">
            <td style="padding:8px 0;color:#808080;font-size:13px;text-transform:uppercase;letter-spacing:1px;">1st Prize</td>
            <td style="padding:8px 0;color:#FFD700;font-weight:700;text-align:right;">&#8377;120</td>
          </tr>
          <tr style="border-top:1px solid #222;">
            <td style="padding:8px 0;color:#808080;font-size:13px;text-transform:uppercase;letter-spacing:1px;">Per Kill Bonus</td>
            <td style="padding:8px 0;color:#ff6b6b;font-weight:600;text-align:right;">&#8377;10</td>
          </tr>
        </table>
      </div>

      <!-- Important Note -->
      <div style="background:#1a0000;border:1px solid #ff4444;border-radius:8px;padding:12px 16px;margin-bottom:24px;">
        <div style="font-size:12px;font-weight:700;color:#ff4444;text-transform:uppercase;letter-spacing:2px;margin-bottom:4px;">⚠️ Important</div>
        <p style="margin:0;font-size:13px;color:#ffaaaa;">Join the match room at least <strong>15 minutes</strong> before start time. Late players will not be accommodated.</p>
      </div>
    </div>

    <!-- Rules -->
    <div style="padding:0 32px 28px;">
      <div style="border-top:1px solid #222;padding-top:24px;">
        <h2 style="margin:0 0 16px;font-size:16px;font-weight:700;color:#FFD700;text-transform:uppercase;letter-spacing:2px;">Tournament Rules</h2>

        {"".join([f'<div style="margin-bottom:12px;"><div style="font-size:11px;font-weight:700;color:#FFD700;text-transform:uppercase;letter-spacing:2px;margin-bottom:4px;">{title}</div><div style="font-size:13px;color:#a0a0a0;line-height:1.6;">{body}</div></div>' for title, body in [
            ("1. Registration", "Register before match start time. Provide correct in-game ID. Entry fee required to confirm slot."),
            ("2. Payment", "Entry fee is non-refundable. Payment screenshot must be valid. Fake proofs = permanent disqualification."),
            ("3. Match Timing", "Match starts exactly on schedule. Join the room 15 minutes early. No delays for late players."),
            ("4. Gameplay", "Hacks, cheats, and mods are strictly banned. Unfair play = immediate ban."),
            ("5. Results &amp; Disputes", "Winners decided by kills + placement. Admin decisions are final."),
            ("6. Prize Distribution", "Prize sent within 24–48 hrs of result. Winners must share correct payment details."),
            ("7. Behaviour", "No abuse, harassment, or toxicity. Scamming or exploiting = permanent ban."),
            ("8. Technical", "Organizers are not liable for lag or crashes. Ensure stable internet before joining."),
            ("9. Account", "You are responsible for your own account security."),
            ("10. Admin Authority", "Admin can change rules, cancel matches, or make final decisions at any time."),
        ]])}
      </div>
    </div>

    <!-- Footer -->
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

@api_router.post("/registrations/{registration_id}/send-mail")
async def send_registration_confirmation(registration_id: str, background_tasks: BackgroundTasks, payload: dict = Depends(verify_jwt_token)):
    reg = next((r for r in db["registrations"] if r["id"] == registration_id), None)
    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found")
    t = next((t for t in db["tournaments"] if t["id"] == reg.get("tournament_id")), None)
    tournament_name = t["name"] if t else "GameArenaX Tournament"
    background_tasks.add_task(
        send_confirmation_email_with_rules,
        reg["email"], reg["player_name"], tournament_name, reg.get("slot_number", "?")
    )
    return {"message": f"Confirmation email queued for {reg['email']}"}

@api_router.get("/")
async def root(): return {"message": "Free Fire API"}

@api_router.post("/public/contact")
async def create_contact(input: ContactForm):
    doc = input.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    async with data_lock:
        db["contacts"].append(doc)
        save_data(db)
    return {"message": "Contact form submitted"}

@api_router.get("/contacts")
async def get_contacts(payload: dict = Depends(verify_jwt_token)):
    contacts = sorted(db["contacts"], key=lambda x: x.get("created_at", ""), reverse=True)
    for c in contacts:
        if isinstance(c.get('created_at'), str):
            try:
                c['created_at'] = datetime.fromisoformat(c['created_at'])
            except: pass
    return contacts

@api_router.put("/contacts/{contact_id}/read")
async def mark_contact_read(contact_id: str, payload: dict = Depends(verify_jwt_token)):
    async with data_lock:
        for c in db["contacts"]:
            if c["id"] == contact_id:
                c["is_read"] = True
        save_data(db)
    return {"message": "Success"}

@api_router.get("/settings")
async def get_settings():
    t = next((t for t in db["tournaments"] if t.get("is_active")), None)
    if not t:
        if not db["tournaments"]:
            doc = Tournament(name="Free Fire Tournament", date="2026-12-31T23:59", max_slots=50, is_active=True).model_dump()
            doc['created_at'] = doc['created_at'].isoformat()
            async with data_lock:
                db["tournaments"].append(doc)
                save_data(db)
            t = doc
        else:
            return {"tournament_name": "Free Fire Tournament", "tournament_date": "TBD", "max_slots": 50, "id": None}
    return {"tournament_name": t["name"], "tournament_date": t["date"], "max_slots": t["max_slots"], "id": t["id"]}

@api_router.post("/tournaments", response_model=Tournament)
async def create_tournament(input: TournamentCreate, payload: dict = Depends(verify_jwt_token)):
    t = Tournament(**input.model_dump())
    if not db["tournaments"]: t.is_active = True
    doc = t.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    async with data_lock:
        db["tournaments"].append(doc)
        save_data(db)
    return t

@api_router.get("/tournaments", response_model=List[Tournament])
async def get_tournaments(payload: dict = Depends(verify_jwt_token)):
    return sorted(db["tournaments"], key=lambda x: x.get("created_at", ""), reverse=True)

@api_router.get("/public/tournaments", response_model=List[Tournament])
async def get_public_tournaments():
    # Return all tournaments so users can register for any!
    return sorted(db["tournaments"], key=lambda x: x.get("created_at", ""), reverse=True)

@api_router.put("/tournaments/{t_id}/activate")
async def activate_tournament(t_id: str, payload: dict = Depends(verify_jwt_token)):
    async with data_lock:
        for t in db["tournaments"]:
            t["is_active"] = (t["id"] == t_id)
        save_data(db)
    return {"message": "Tournament activated"}

@api_router.delete("/tournaments/{t_id}")
async def delete_tournament(t_id: str, payload: dict = Depends(verify_jwt_token)):
    async with data_lock:
        db["tournaments"] = [t for t in db["tournaments"] if t["id"] != t_id]
        db["registrations"] = [r for r in db["registrations"] if r.get("tournament_id") != t_id]
        save_data(db)
    return {"message": "Tournament deleted"}

@api_router.post("/register", response_model=Registration)
async def create_registration(input: RegistrationCreate, background_tasks: BackgroundTasks):
    t = None
    if input.tournament_id:
        t = next((t for t in db["tournaments"] if t["id"] == input.tournament_id), None)
    else:
        t = next((t for t in db["tournaments"] if t.get("is_active")), None)
    
    if not t: raise HTTPException(status_code=400, detail="Tournament not found or no active tournament to register for.")
    
    regs = [r for r in db["registrations"] if r.get("tournament_id") == t["id"]]
    if len(regs) >= t.get("max_slots", 50): raise HTTPException(status_code=400, detail="Tournament full.")
        
    slot_existing = next((r for r in regs if r.get("slot_number") == input.slot_number and r.get("status") in ["pending", "approved"]), None)
    if slot_existing: raise HTTPException(status_code=400, detail="Slot occupied.")

    
    registration_dict = input.model_dump()
    registration_dict['tournament_id'] = t["id"]
    registration_obj = Registration(**registration_dict)
    doc = registration_obj.model_dump()
    doc['registered_at'] = doc['registered_at'].isoformat()
    
    async with data_lock:
        db["registrations"].append(doc)
        save_data(db)
    
    background_tasks.add_task(send_registration_email, input.email, input.player_name, t["name"], input.slot_number)
    
    return registration_obj

@api_router.post("/admin/login")
async def admin_login(input: AdminLogin):
    if input.password != os.environ.get('ADMIN_PASSWORD', 'admin123'):
        raise HTTPException(status_code=401, detail="Invalid password")
    return {"token": create_jwt_token({"role": "admin"})}

@api_router.get("/registrations")
async def get_registrations(tournament_id: Optional[str] = None, payload: dict = Depends(verify_jwt_token)):
    regs = [r for r in db["registrations"] if (not tournament_id or r.get("tournament_id") == tournament_id)]
    for reg in regs:
        if isinstance(reg.get('registered_at'), str):
            try:
                reg['registered_at'] = datetime.fromisoformat(reg['registered_at'])
            except: pass
    return regs

@api_router.get("/public/leaderboard")
async def get_leaderboard(tournament_id: Optional[str] = None):
    t = next((t for t in db["tournaments"] if t["id"] == tournament_id), None) if tournament_id else next((t for t in db["tournaments"] if t.get("is_active")), None)
    if not t: return []
    
    regs = [r for r in db["registrations"] if r.get("tournament_id") == t["id"] and r.get("status") == "approved"]
    
    def get_sort_key(x):
        r = x.get('tournament_rank', 0)
        return (-1 if r == 0 else r, -x.get('kills', 0))
    
    return sorted([{"player_name": r.get("player_name"), "team_name": r.get("team_name"), "kills": r.get("kills", 0), "tournament_rank": r.get("tournament_rank", 0), "total_prize": r.get("total_prize", 0.0)} for r in regs], key=get_sort_key)

@api_router.get("/slots")
async def get_slots(tournament_id: Optional[str] = None):
    t = next((t for t in db["tournaments"] if t["id"] == tournament_id), None) if tournament_id else next((t for t in db["tournaments"] if t.get("is_active")), None)
    if not t:
        if not db["tournaments"]:
            doc = Tournament(name="Free Fire Tournament", date="2026-12-31T23:59", max_slots=50, is_active=True).model_dump()
            doc['created_at'] = doc['created_at'].isoformat()
            async with data_lock:
                db["tournaments"].append(doc)
                save_data(db)
            t = doc
        else:
            return []
            
    max_slots = t.get("max_slots", 50)
    regs = [r for r in db["registrations"] if r.get("tournament_id") == t["id"] and r.get("status") in ["pending", "approved"]]
    occupied = {r.get("slot_number"): r for r in regs if "slot_number" in r}
    
    slots = []
    for i in range(1, max_slots + 1):
        if i in occupied: slots.append({"slot_number": i, "status": occupied[i].get("status")})
        else: slots.append({"slot_number": i, "status": "available"})
    return slots

@api_router.put("/registrations/{registration_id}/kills")
async def update_kills(registration_id: str, input: UpdateKills, payload: dict = Depends(verify_jwt_token)):
    reg = next((r for r in db["registrations"] if r["id"] == registration_id), None)
    if not reg: raise HTTPException(status_code=404, detail="Not found")
    
    rank_prizes = {1: 120, 2: 60, 3: 30}
    rank_prize = rank_prizes.get(reg.get('tournament_rank', 0), 0)
    total_prize = (input.kills * 10) + rank_prize
    
    async with data_lock:
        reg["kills"] = input.kills
        reg["total_prize"] = total_prize
        save_data(db)
    return {"message": "Success"}

@api_router.put("/registrations/{registration_id}/rank")
async def update_rank(registration_id: str, input: UpdateRank, payload: dict = Depends(verify_jwt_token)):
    reg = next((r for r in db["registrations"] if r["id"] == registration_id), None)
    if not reg: raise HTTPException(status_code=404, detail="Not found")
    
    rank_prizes = {1: 120, 2: 60, 3: 30}
    rank_prize = rank_prizes.get(input.rank, 0)
    total_prize = (reg.get('kills', 0) * 10) + rank_prize
    
    async with data_lock:
        reg["tournament_rank"] = input.rank
        reg["total_prize"] = total_prize
        save_data(db)
    return {"message": "Success"}

@api_router.put("/registrations/{registration_id}/status")
async def update_status(registration_id: str, input: UpdateStatus, background_tasks: BackgroundTasks, payload: dict = Depends(verify_jwt_token)):
    reg = next((r for r in db["registrations"] if r["id"] == registration_id), None)
    if not reg: raise HTTPException(status_code=404, detail="Not found")
    
    old_status = reg.get("status")
    async with data_lock:
        reg["status"] = input.status
        save_data(db)
    
    if input.status == "approved" and old_status != "approved":
        t = next((t for t in db["tournaments"] if t["id"] == reg.get("tournament_id")), None)
        background_tasks.add_task(send_approval_email, reg.get("email"), reg.get("player_name"), t["name"] if t else "Tournament")
        
    return {"message": "Success"}

@api_router.get("/stats", response_model=TournamentStats)
async def get_stats(tournament_id: Optional[str] = None, payload: dict = Depends(verify_jwt_token)):
    t = next((t for t in db["tournaments"] if t["id"] == tournament_id), None) if tournament_id else next((t for t in db["tournaments"] if t.get("is_active")), None)
    if not t: return TournamentStats(total_registrations=0, max_registrations=50, total_prize_pool=0, pending_count=0, approved_count=0)
    
    regs = [r for r in db["registrations"] if r.get("tournament_id") == t["id"]]
    total = len(regs)
    pending = sum(1 for r in regs if r.get("status") == "pending")
    approved = sum(1 for r in regs if r.get("status") == "approved")
    total_prize = sum(r.get('total_prize', 0.0) for r in regs)
    
    return TournamentStats(total_registrations=total, max_registrations=t.get("max_slots", 50), total_prize_pool=total_prize, pending_count=pending, approved_count=approved)

app.include_router(api_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app.mount("/", StaticFiles(directory=str(ROOT_DIR.parent), html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    print("Starting Free Fire backend server on port 8000 using local JSON data...")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
