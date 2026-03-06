from fastapi import FastAPI, APIRouter, HTTPException, Depends, UploadFile, File, Form, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
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

import urllib.parse
import re

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

def get_safe_mongo_url(raw_url: str) -> str:
    if not raw_url: return raw_url
    match = re.match(r"^(mongodb(?:\+srv)?://)([^:]+):(.*)@([^@/]+)(/.*)?$", raw_url)
    if match:
        scheme, user, password, cluster, rest = match.groups()
        # Decode first in case user already URL-encoded parts of it, to avoid double-encoding
        password = urllib.parse.unquote_plus(password)
        user = urllib.parse.unquote_plus(user)
        # Safely re-encode special characters like @, #, !
        safe_pass = urllib.parse.quote_plus(password)
        safe_user = urllib.parse.quote_plus(user)
        rest = rest or ""
        return f"{scheme}{safe_user}:{safe_pass}@{cluster}{rest}"
    return raw_url

mongo_url = get_safe_mongo_url(os.environ.get('MONGO_URL', ''))
db_name = os.environ.get('DB_NAME', 'test_database')

if not mongo_url:
    print("CRITICAL ERROR: MONGO_URL is missing from environment variables!")
    print("Please add MONGO_URL to your Render Environment Variables.")

try:
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
except Exception as e:
    print(f"CRITICAL ERROR: Failed to connect to MongoDB: {e}")

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
        print("WARNING: Email not sent. SMTP_USER and SMTP_PASS are missing in .env")
        logger.warning(f"SMTP not configured. Skipping confirmation email.")
        return
    try:
        print(f"Attempting to send approval email to {player_email}...")
        msg = EmailMessage()
        msg['Subject'] = f"Registration Approved - {tournament_name}"
        msg['From'] = smtp_user
        msg['To'] = player_email
        msg.set_content(f"Hello {player_name},\n\nYour registration for {tournament_name} has been APPROVED! Your slot is now confirmed.\n\nGood luck!")
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        print("Approval email sent successfully!")
    except Exception as e:
        print(f"FAILED TO SEND EMAIL. Google Error: {e}")
        print("Make sure you are using a 16-character Google 'App Password', NOT your actual Gmail password. (Search Google for 'Create App Password')")
        logger.error(f"Email failed: {e}")

def send_registration_email(player_email: str, player_name: str, tournament_name: str, slot_number: int):
    smtp_user = os.environ.get('SMTP_USER')
    smtp_pass = os.environ.get('SMTP_PASS')
    if not smtp_user or not smtp_pass:
        print("WARNING: Email not sent. SMTP_USER and SMTP_PASS are missing in .env")
        return
    try:
        print(f"Attempting to send registration email to {player_email}...")
        msg = EmailMessage()
        msg['Subject'] = f"Registration Received - {tournament_name}"
        msg['From'] = smtp_user
        msg['To'] = player_email
        msg.set_content(f"Hello {player_name},\n\nWe have received your registration and payment screenshot for {tournament_name} (Requested Slot #{slot_number}).\n\nYour registration is currently PENDING. You will receive another email once an admin verifies your payment and approves your slot.\n\nThank you!")
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        print("Registration email sent successfully!")
    except Exception as e:
        print(f"FAILED TO SEND EMAIL. Google Error: {e}")
        print("Make sure you are using a 16-character Google 'App Password', NOT your actual Gmail password. (Search Google for 'Create App Password')")
        logger.error(f"Email failed: {e}")

@api_router.get("/")
async def root(): return {"message": "Free Fire API"}

@api_router.post("/public/contact")
async def create_contact(input: ContactForm):
    doc = input.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    await db.contacts.insert_one(doc)
    return {"message": "Contact form submitted"}

@api_router.get("/contacts")
async def get_contacts(payload: dict = Depends(verify_jwt_token)):
    contacts = await db.contacts.find({}, {"_id": 0}).sort("created_at", -1).to_list(100)
    for c in contacts:
        if isinstance(c.get('created_at'), str):
            c['created_at'] = datetime.fromisoformat(c['created_at'])
    return contacts

@api_router.put("/contacts/{contact_id}/read")
async def mark_contact_read(contact_id: str, payload: dict = Depends(verify_jwt_token)):
    await db.contacts.update_one({"id": contact_id}, {"$set": {"is_read": True}})
    return {"message": "Success"}

@api_router.get("/settings")
async def get_settings():
    t = await db.tournaments.find_one({"is_active": True}, {"_id": 0})
    if not t:
        count = await db.tournaments.count_documents({})
        if count == 0:
            doc = Tournament(name="Free Fire Tournament", date="2026-12-31T23:59", max_slots=50, is_active=True).model_dump()
            doc['created_at'] = doc['created_at'].isoformat()
            await db.tournaments.insert_one(doc)
            t = doc
        else:
            return {"tournament_name": "Free Fire Tournament", "tournament_date": "TBD", "max_slots": 50, "id": None}
    return {"tournament_name": t["name"], "tournament_date": t["date"], "max_slots": t["max_slots"], "id": t["id"]}

@api_router.post("/tournaments", response_model=Tournament)
async def create_tournament(input: TournamentCreate, payload: dict = Depends(verify_jwt_token)):
    t = Tournament(**input.model_dump())
    count = await db.tournaments.count_documents({})
    if count == 0: t.is_active = True
    doc = t.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    await db.tournaments.insert_one(doc)
    return t

@api_router.get("/tournaments", response_model=List[Tournament])
async def get_tournaments(payload: dict = Depends(verify_jwt_token)):
    return await db.tournaments.find({}, {"_id": 0}).sort("created_at", -1).to_list(100)

@api_router.get("/public/tournaments", response_model=List[Tournament])
async def get_public_tournaments():
    # Return all tournaments so users can register for any!
    return await db.tournaments.find({}, {"_id": 0}).sort("created_at", -1).to_list(100)

@api_router.put("/tournaments/{t_id}/activate")
async def activate_tournament(t_id: str, payload: dict = Depends(verify_jwt_token)):
    await db.tournaments.update_many({}, {"$set": {"is_active": False}})
    await db.tournaments.update_one({"id": t_id}, {"$set": {"is_active": True}})
    return {"message": "Tournament activated"}

@api_router.delete("/tournaments/{t_id}")
async def delete_tournament(t_id: str, payload: dict = Depends(verify_jwt_token)):
    await db.tournaments.delete_one({"id": t_id})
    await db.registrations.delete_many({"tournament_id": t_id})
    return {"message": "Tournament deleted"}

@api_router.post("/register", response_model=Registration)
async def create_registration(input: RegistrationCreate, background_tasks: BackgroundTasks):
    if input.tournament_id:
        t = await db.tournaments.find_one({"id": input.tournament_id})
    else:
        t = await db.tournaments.find_one({"is_active": True})
    
    if not t: raise HTTPException(status_code=400, detail="Tournament not found or no active tournament to register for.")
    
    count = await db.registrations.count_documents({"tournament_id": t["id"]})
    if count >= t["max_slots"]: raise HTTPException(status_code=400, detail="Tournament full.")
        
    slot_existing = await db.registrations.find_one({
        "tournament_id": t["id"],
        "slot_number": input.slot_number,
        "status": {"$in": ["pending", "approved"]}
    })
    if slot_existing: raise HTTPException(status_code=400, detail="Slot occupied.")
    
    email_existing = await db.registrations.find_one({
        "tournament_id": t["id"],
        "email": input.email
    })
    if email_existing: raise HTTPException(status_code=400, detail="You have already registered for this tournament using this email address.")
    
    registration_dict = input.model_dump()
    registration_dict['tournament_id'] = t["id"]
    registration_obj = Registration(**registration_dict)
    doc = registration_obj.model_dump()
    doc['registered_at'] = doc['registered_at'].isoformat()
    await db.registrations.insert_one(doc)
    
    background_tasks.add_task(send_registration_email, input.email, input.player_name, t["name"] if t else "Tournament", input.slot_number)
    
    return registration_obj

@api_router.post("/admin/login")
async def admin_login(input: AdminLogin):
    if input.password != os.environ.get('ADMIN_PASSWORD', 'admin123'):
        raise HTTPException(status_code=401, detail="Invalid password")
    return {"token": create_jwt_token({"role": "admin"})}

@api_router.get("/registrations")
async def get_registrations(tournament_id: Optional[str] = None, payload: dict = Depends(verify_jwt_token)):
    query = {"tournament_id": tournament_id} if tournament_id else {}
    registrations = await db.registrations.find(query, {"_id": 0}).to_list(1000)
    for reg in registrations:
        if isinstance(reg['registered_at'], str):
            reg['registered_at'] = datetime.fromisoformat(reg['registered_at'])
    return registrations

@api_router.get("/public/leaderboard")
async def get_leaderboard(tournament_id: Optional[str] = None):
    t = await db.tournaments.find_one({"id": tournament_id}) if tournament_id else await db.tournaments.find_one({"is_active": True})
    if not t: return []
    
    query = {"tournament_id": t["id"], "status": "approved"}
    registrations = await db.registrations.find(query, {"_id": 0, "player_name": 1, "team_name": 1, "kills": 1, "tournament_rank": 1, "total_prize": 1}).to_list(100)
    
    # Sort with rank 1 first, then by kills. If rank is 0, they come after ranked players.
    def get_sort_key(x):
        r = x.get('tournament_rank', 0)
        return (-1 if r == 0 else r, -x.get('kills', 0))
    
    registrations.sort(key=get_sort_key)
    return registrations

@api_router.get("/slots")
async def get_slots(tournament_id: Optional[str] = None):
    t = await db.tournaments.find_one({"id": tournament_id}) if tournament_id else await db.tournaments.find_one({"is_active": True})
    if not t:
        count = await db.tournaments.count_documents({})
        if count == 0:
            doc = Tournament(name="Free Fire Tournament", date="2026-12-31T23:59", max_slots=50, is_active=True).model_dump()
            doc['created_at'] = doc['created_at'].isoformat()
            await db.tournaments.insert_one(doc)
            t = doc
        else:
            return []
            
    max_slots = t.get("max_slots", 50)
    
    registrations = await db.registrations.find({"tournament_id": t["id"], "status": {"$in": ["pending", "approved"]}}).to_list(100)
    occupied = {r["slot_number"]: r for r in registrations if "slot_number" in r}
    slots = []
    for i in range(1, max_slots + 1):
        if i in occupied: slots.append({"slot_number": i, "status": occupied[i]["status"]})
        else: slots.append({"slot_number": i, "status": "available"})
    return slots

@api_router.put("/registrations/{registration_id}/kills")
async def update_kills(registration_id: str, input: UpdateKills, payload: dict = Depends(verify_jwt_token)):
    reg = await db.registrations.find_one({"id": registration_id})
    if not reg: raise HTTPException(status_code=404, detail="Not found")
    
    # Prize pool logic
    rank_prizes = {1: 120, 2: 60, 3: 30}
    rank_prize = rank_prizes.get(reg.get('tournament_rank', 0), 0)
    total_prize = (input.kills * 10) + rank_prize
    
    await db.registrations.update_one({"id": registration_id}, {"$set": {"kills": input.kills, "total_prize": total_prize}})
    return {"message": "Success"}

@api_router.put("/registrations/{registration_id}/rank")
async def update_rank(registration_id: str, input: UpdateRank, payload: dict = Depends(verify_jwt_token)):
    reg = await db.registrations.find_one({"id": registration_id})
    if not reg: raise HTTPException(status_code=404, detail="Not found")
    
    rank_prizes = {1: 120, 2: 60, 3: 30}
    rank_prize = rank_prizes.get(input.rank, 0)
    total_prize = (reg.get('kills', 0) * 10) + rank_prize
    
    await db.registrations.update_one({"id": registration_id}, {"$set": {"tournament_rank": input.rank, "total_prize": total_prize}})
    return {"message": "Success"}

@api_router.put("/registrations/{registration_id}/status")
async def update_status(registration_id: str, input: UpdateStatus, background_tasks: BackgroundTasks, payload: dict = Depends(verify_jwt_token)):
    reg = await db.registrations.find_one({"id": registration_id})
    if not reg: raise HTTPException(status_code=404, detail="Not found")
    
    await db.registrations.update_one({"id": registration_id}, {"$set": {"status": input.status}})
    
    if input.status == "approved" and reg.get("status") != "approved":
        t = await db.tournaments.find_one({"id": reg.get("tournament_id")})
        background_tasks.add_task(send_approval_email, reg["email"], reg["player_name"], t["name"] if t else "Tournament")
        
    return {"message": "Success"}

@api_router.get("/stats", response_model=TournamentStats)
async def get_stats(tournament_id: Optional[str] = None, payload: dict = Depends(verify_jwt_token)):
    t = await db.tournaments.find_one({"id": tournament_id}) if tournament_id else await db.tournaments.find_one({"is_active": True})
    if not t: return TournamentStats(total_registrations=0, max_registrations=50, total_prize_pool=0, pending_count=0, approved_count=0)
    
    query = {"tournament_id": t["id"]}
    total = await db.registrations.count_documents(query)
    pending = await db.registrations.count_documents({**query, "status": "pending"})
    approved = await db.registrations.count_documents({**query, "status": "approved"})
    
    registrations = await db.registrations.find(query, {"_id": 0}).to_list(1000)
    total_prize = sum(reg.get('total_prize', 0) for reg in registrations)
    
    return TournamentStats(total_registrations=total, max_registrations=t.get("max_slots", 50), total_prize_pool=total_prize, pending_count=pending, approved_count=approved)

app.include_router(api_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_credentials=False,  # Wildcard '*' is not allowed with allow_credentials=True
    allow_methods=["*"],
    allow_headers=["*"],
)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Mount static frontend files for mobile access directly from backend port!
app.mount("/", StaticFiles(directory=str(ROOT_DIR.parent), html=True), name="frontend")

@app.on_event("shutdown")
async def shutdown_db_client(): client.close()

if __name__ == "__main__":
    import uvicorn
    print("Starting Free Fire backend server on port 8000...")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
