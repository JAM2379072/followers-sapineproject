import os
import json
import shutil
import zipfile
from typing import Dict, Optional, List
from fastapi import FastAPI, HTTPException, UploadFile, File, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from instagrapi import Client
from instagrapi.exceptions import ClientError, LoginRequired
import uvicorn
import threading
from datetime import datetime
from io import BytesIO

app = FastAPI(title="Instagram Bot Pro API", version="3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

clients: Dict[str, Client] = {}
clients_lock = threading.Lock()
accounts: Dict[str, dict] = {}
SESSIONS_DIR = "sessions"
BACKUP_DIR = "backups"
os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

class AddAccount(BaseModel):
    email: str
    password: str
    proxy: Optional[str] = None
    name: Optional[str] = None

class Action(BaseModel):
    account: str
    target: str

class EditProfile(BaseModel):
    account: str
    bio: Optional[str] = None
    username: Optional[str] = None
    full_name: Optional[str] = None

class ProfilePic(BaseModel):
    account: str

def get_client(account_name: str) -> Client:
    with clients_lock:
        if account_name in clients:
            try:
                clients[account_name].get_timeline_feed()
                return clients[account_name]
            except:
                del clients[account_name]
        
        session_path = os.path.join(SESSIONS_DIR, f"{account_name}.json")
        if not os.path.exists(session_path):
            raise HTTPException(404, f"Session {account_name}.json missing")
        
        cl = Client()
        cl.delay_range = [2, 6]
        cl.set_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        
        if account_name in accounts and accounts[account_name].get("proxy"):
            cl.set_proxy(accounts[account_name]["proxy"])
        
        cl.load_settings(session_path)
        clients[account_name] = cl
        return cl

## 🆕 ADD ACCOUNT
@app.post("/accounts/add")
async def add_account(req: AddAccount, background_tasks: BackgroundTasks):
    name = req.name or req.email.split('@')[0].replace('+', '_').replace('.', '_')
    
    def create_session():
        try:
            cl = Client()
            if req.proxy: cl.set_proxy(req.proxy)
            cl.login(req.email, req.password)
            session_path = os.path.join(SESSIONS_DIR, f"{name}.json")
            cl.dump_settings(session_path)
            accounts[name] = {"email": req.email, "proxy": req.proxy, "created": datetime.now().isoformat()}
        except Exception as e:
            print(f"❌ [{name}] Failed: {e}")
    
    background_tasks.add_task(create_session)
    return {"message": f"Creating {name}...", "account_name": name}

## 📋 LIST ALL ACCOUNTS + STATUS
@app.get("/accounts")
def list_accounts():
    sessions = [f.replace('.json', '') for f in os.listdir(SESSIONS_DIR) if f.endswith('.json')]
    status = {}
    for acc in sessions:
        try:
            cl = get_client(acc)
            info = cl.account_info()
            status[acc] = {
                "username": info.username,
                "followers": info.follower_count,
                "following": info.following_count,
                "posts": info.media_count,
                "bio": info.biography[:100] + "..." if info.biography else "",
                "status": "active"
            }
        except:
            status[acc] = {"status": "inactive"}
    return {"total": len(sessions), "accounts": status}

## 🔍 SINGLE ACCOUNT INFO (Detailed)
@app.get("/accounts/{account}/info")
def account_info(account: str):
    cl = get_client(account)
    info = cl.account_info()
    return {
        "username": info.username,
        "full_name": info.full_name,
        "bio": info.biography,
        "followers": info.follower_count,
        "following": info.following_count,
        "posts": info.media_count,
        "is_private": info.is_private,
        "is_verified": info.is_verified,
        "profile_pic": info.profile_pic_url,
        "external_url": info.external_url
    }

## 👥 FOLLOW/UNFOLLOW
@app.post("/follow")
def follow(action: Action):
    cl = get_client(action.account)
    user_id = cl.user_id_from_username(action.target)
    cl.user_follow(user_id)
    return {"success": True, "user_id": user_id}

@app.post("/unfollow")
def unfollow(action: Action):
    cl = get_client(action.account)
    user_id = cl.user_id_from_username(action.target)
    cl.user_unfollow(user_id)
    return {"success": True}

## ❤️ LIKE/COMMENT
@app.post("/like")
def like(action: Action):
    cl = get_client(action.account)
    if "instagram.com/p/" in action.target:
        media_pk = cl.media_pk_from_url(action.target)
    else:
        media_pk = cl.media_pk_from_code(action.target)
    cl.media_like(media_pk)
    return {"success": True}

@app.post("/comment")
def comment(action: Action, text: str):
    cl = get_client(action.account)
    if "instagram.com/p/" in action.target:
        media_pk = cl.media_pk_from_url(action.target)
    else:
        media_pk = cl.media_pk_from_code(action.target)
    cl.media_comment(media_pk, text)
    return {"success": True}

## ✏️ COMPLETE PROFILE MANAGEMENT
@app.post("/profile/edit")
def edit_profile(req: EditProfile):
    cl = get_client(req.account)
    cl.account_edit(
        username=req.username,
        full_name=req.full_name,
        biography=req.bio
    )
    return {"success": True}

## 🖼️ PROFILE PICTURE UPLOAD
@app.post("/profile/pic")
async def change_profile_pic(account: str, image: UploadFile = File(...)):
    cl = get_client(account)
    
    # Save uploaded image temporarily
    img_path = f"/tmp/{image.filename}"
    with open(img_path, "wb") as f:
        shutil.copyfileobj(image.file, f)
    
    try:
        cl.account_update_profile_pic(img_path)
        return {"success": True, "message": "Profile picture updated"}
    finally:
        os.remove(img_path)

## 📊 BULK ACCOUNT STATUS (Specific accounts)
@app.post("/accounts/status")
def bulk_status(accounts: List[str] = Query(...)):
    results = {}
    for acc in accounts:
        try:
            cl = get_client(acc)
            info = cl.account_info()
            results[acc] = {
                "username": info.username,
                "followers": info.follower_count,
                "status": "active"
            }
        except Exception as e:
            results[acc] = {"status": "error", "error": str(e)}
    return results

## 💾 BACKUP/RESTORE (Redeploy Proof)
@app.post("/backup")
def create_backup():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = os.path.join(BACKUP_DIR, f"backup_{timestamp}.zip")
    
    with zipfile.ZipFile(backup_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        for session_file in os.listdir(SESSIONS_DIR):
            zf.write(os.path.join(SESSIONS_DIR, session_file), session_file)
    
    return {"backup": f"backup_{timestamp}.zip", "size": os.path.getsize(backup_file)}

@app.post("/restore")
async def restore_backup(backup_file: UploadFile = File(...)):
    backup_path = os.path.join(BACKUP_DIR, backup_file.filename)
    with open(backup_path, "wb") as f:
        shutil.copyfileobj(backup_file.file, f)
    
    with zipfile.ZipFile(backup_path, 'r') as zf:
        zf.extractall(SESSIONS_DIR)
    
    with clients_lock:
        clients.clear()
    
    restored = [f.replace('.json', '') for f in os.listdir(SESSIONS_DIR) if f.endswith('.json')]
    return {"restored_accounts": restored, "count": len(restored)}

## 🧹 CLEANUP (Remove account)
@app.delete("/accounts/{account}")
def delete_account(account: str):
    session_path = os.path.join(SESSIONS_DIR, f"{account}.json")
    if os.path.exists(session_path):
        os.remove(session_path)
        with clients_lock:
            clients.pop(account, None)
            accounts.pop(account, None)
        return {"success": True, "message": f"{account} deleted"}
    raise HTTPException(404, "Account not found")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
