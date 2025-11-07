#!/usr/bin/env python3
"""
RealtimeConnect Server
WebRTC video/audio/screen sharing with secure, session-based text chat.
"""

import asyncio
import json
import logging
import uuid
import os
import aiosqlite
import bcrypt
import string
import random
from aiohttp import web
from aiohttp_session import setup as setup_session, get_session, new_session
from aiohttp_session.cookie_storage import EncryptedCookieStorage
from cryptography import fernet
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
import aiohttp_cors
from aiohttp_cors import ResourceOptions
from dotenv import load_dotenv

# --- Configuration & Logging ---
load_dotenv()  # Load .env file for local development

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Constants ---
DB_PATH = 'users.db'
STATIC_DIR = os.path.join(os.getcwd())
DIST_DIR = os.path.join(os.getcwd(), "dist")

# --- Global State ---
webrtc_peers = {}  # peer_id -> RTCPeerConnection
relay = MediaRelay()
# app['ws_clients'] = {username: websocket}


# ============================================================================
# UTILITIES
# ============================================================================

def generate_meeting_id(length=8):
    """Generates a random, URL-safe meeting ID."""
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

async def handle_random_id(request):
    """API endpoint to generate and return a new random meeting ID."""
    new_id = generate_meeting_id()
    return web.json_response({'meeting_id': new_id})


# ============================================================================
# DATABASE & AUTHENTICATION
# (Omitting for brevity as it was not requested to change, but keeping function headers)
# ============================================================================

async def init_db(app):
    """Initializes the SQLite database and creates the users table."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user'
                )
            """)
            await db.commit()
        logger.info("Database initialized and 'users' table ensured.")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")

async def handle_login_page(request):
    """Serves the login.html page."""
    try:
        with open(os.path.join(STATIC_DIR, "login.html"), 'r') as f:
            return web.Response(text=f.read(), content_type="text/html")
    except FileNotFoundError:
        logger.error("login.html not found!")
        return web.Response(text="Login page not found.", status=404)

async def handle_login(request):
    """Handles the POST request from the login form."""
    data = await request.post()
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return web.HTTPFound('/login')

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT password_hash FROM users WHERE username = ?", (username,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    hashed_password = row[0]
                    if bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8')):
                        session = await new_session(request)
                        session['username'] = username
                        logger.info(f"User '{username}' logged in successfully.")
                        return web.HTTPFound('/')
                
                logger.warning(f"Failed login attempt for user '{username}'.")
                return web.HTTPFound('/login')
    except Exception as e:
        logger.error(f"Error during login: {e}")
        return web.Response(text="Server error during login.", status=500)

async def handle_register_page(request):
    """Serves the register.html page."""
    try:
        with open(os.path.join(STATIC_DIR, "register.html"), 'r') as f:
            return web.Response(text=f.read(), content_type="text/html")
    except FileNotFoundError:
        logger.error("register.html not found!")
        return web.Response(text="Registration page not found.", status=404)

async def handle_register(request):
    """Handles the POST request from the registration form."""
    data = await request.post()
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        logger.warning("Registration attempt with empty username or password.")
        return web.HTTPFound('/register')

    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    role = 'user' 

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                (username, hashed_password, role)
            )
            await db.commit()
        logger.info(f"New user '{username}' registered successfully.")
        
        session = await new_session(request)
        session['username'] = username
        return web.HTTPFound('/')

    except aiosqlite.IntegrityError:
        logger.warning(f"Registration failed: Username '{username}' already exists.")
        return web.HTTPFound('/register')
    except Exception as e:
        logger.error(f"Error during registration: {e}")
        return web.Response(text="Server error during registration.", status=500)

async def handle_logout(request):
    """Logs the user out by destroying their session."""
    session = await get_session(request)
    session.invalidate()
    logger.info(f"User '{session.get('username')}' logged out.")
    return web.HTTPFound('/login')

async def get_user_info(request):
    """API endpoint to get the current user's session data."""
    session = await get_session(request)
    username = session.get('username')
    
    if not username:
        return web.json_response({'error': 'Not authenticated'}, status=401)
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT role FROM users WHERE username = ?", (username,)) as cursor:
                row = await cursor.fetchone()
                role = row[0] if row else 'user'
                return web.json_response({'username': username, 'role': role})
    except Exception as e:
        logger.error(f"Error fetching user info for '{username}': {e}")
        return web.json_response({'error': 'Server error'}, status=500)

# ============================================================================
# WEBSOCKET CHAT HANDLER
# ============================================================================

async def text_chat_handler(request):
    """Handles WebSocket connections for text chat, now integrated with aiohttp."""
    
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    app = request.app
    username = None
    
    try:
        # First message from client MUST be the username (from session)
        username = await ws.receive_str()
        if not username or username.strip() == "":
            await ws.close(code=1003, message=b'Username cannot be empty')
            return ws
        
        username = username.strip()
        
        # Check for duplicate username (e.g., user opened a second tab)
        if username in app['ws_clients']:
            username = f"{username}_{uuid.uuid4().hex[:4]}"
            await ws.send_str(f"System: You are already connected. This is a new session: {username}")
        
        app['ws_clients'][username] = ws
        logger.info(f"Client '{username}' connected to text chat.")
        
        # Send join message to everyone
        await broadcast_chat_message(app, f"System: '{username}' has joined the chat.")

        # Main receive loop
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                await broadcast_chat_message(app, f"{username}: {msg.data}")
            elif msg.type == web.WSMsgType.ERROR:
                logger.error(f"WebSocket connection closed with exception {ws.exception()}")

    except Exception as e:
        logger.error(f"Text chat handler error for {username}: {e}")
    finally:
        if username and username in app['ws_clients']:
            del app['ws_clients'][username]
            logger.info(f"Client '{username}' removed from text chat list.")
            # Send leave message
            await broadcast_chat_message(app, f"System: '{username}' has left the chat.")
            
    logger.info(f"WebSocket connection closed for {username}.")
    return ws

async def broadcast_chat_message(app, message):
    """Broadcasts a message to all connected chat clients."""
    disconnected_clients = []
    for username, ws in app['ws_clients'].items():
        if not ws.closed:
            try:
                await ws.send_str(message)
            except ConnectionResetError:
                logger.warning(f"Connection reset for {username}. Marking for removal.")
                disconnected_clients.append(username)
            except Exception as e:
                logger.error(f"Error sending broadcast to {username}: {e}")
        else:
            disconnected_clients.append(username)
            
    # Clean up disconnected clients
    for username in disconnected_clients:
        if username in app['ws_clients']:
            del app['ws_clients'][username]

# ============================================================================
# HTTP AND WEBRTC HANDLERS
# ============================================================================

async def index(request):
    """Serve the main client HTML page, redirect to login if not authenticated."""
    session = await get_session(request)
    if not session.get('username'):
        logger.info("No session found, redirecting to /login.")
        return web.HTTPFound('/login')
        
    logger.info(f"Serving client.html to user '{session['username']}'.")
    try:
        with open(os.path.join(STATIC_DIR, "client.html"), 'r') as f:
            html_content = f.read()
        return web.Response(text=html_content, content_type="text/html")
    except FileNotFoundError:
        logger.error("client.html not found!")
        return web.Response(text="Client application not found.", status=404)

async def healthcheck(request):
    """Simple health check endpoint."""
    return web.Response(text="OK", content_type="text/plain")

async def offer(request):
    """Handles WebRTC offers from the client."""
    session = await get_session(request)
    if not session.get('username'):
        return web.json_response({'error': 'Not authenticated'}, status=401)

    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection()
    peer_id = str(uuid.uuid4())
    webrtc_peers[peer_id] = pc

    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        logger.info(f"ICE connection state is {pc.iceConnectionState} for peer {peer_id}")
        if pc.iceConnectionState == "failed":
            await pc.close()
            webrtc_peers.pop(peer_id, None)

    @pc.on("track")
    def on_track(track):
        logger.info(f"Track {track.kind} received from peer {peer_id}")
        # Subscribe the track to the global relay so all other peers get it
        if track.kind == "video" or track.kind == "audio":
            pc.addTrack(relay.subscribe(track))
        
        @track.on("ended")
        async def on_ended():
            logger.info(f"Track {track.kind} ended for peer {peer_id}")

    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.json_response(
        {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type, "id": peer_id}
    )

# ============================================================================
# MAIN SERVER LOGIC
# ============================================================================

async def on_startup(app):
    """Run on application startup."""
    app['ws_clients'] = {}
    await init_db(app)

async def on_cleanup(app):
    """Run on application cleanup."""
    logger.info("Cleaning up WebSocket connections...")
    for ws in app['ws_clients'].values():
        await ws.close(code=1001, message=b'Server shutdown')
    
    logger.info("Cleaning up WebRTC peer connections...")
    for pc in webrtc_peers.values():
        await pc.close()
    webrtc_peers.clear()

async def main():
    """Main entry point to start all servers."""
    
    # --- Secret Key Setup (FIXED LOGIC) ---
    load_dotenv() # Load .env file
    secret_key_bytes = None
    secret_key_str = os.environ.get("SECRET_KEY")

    # 1. Check if SECRET_KEY exists in environment
    if secret_key_str:
        try:
            # Attempt to decode and check if it's a valid Fernet key
            key_candidate = secret_key_str.encode('utf-8')
            
            # Check if key is exactly 32 bytes (which is 44 base64 chars)
            # This is a small helper, Fernet will ultimately check the base64 format.
            if len(key_candidate) == 44:
                fernet.Fernet(key_candidate) 
                secret_key_bytes = key_candidate
                logger.info("Loaded valid SECRET_KEY from environment.")
            else:
                raise ValueError("Key length is incorrect for Fernet.")
                
        except Exception as e:
            # If any exception occurs (like ValueError from Fernet or my length check), log and fall back.
            logger.error(f"Invalid SECRET_KEY found in environment: {e}. Generating temporary key.")
    
    # 2. Fallback to generating a key if no valid key was found
    if secret_key_bytes is None:
        logger.warning("SECRET_KEY not found or invalid. Generating a temporary key.")
        logger.warning("DO NOT USE THIS IN PRODUCTION. Set a permanent SECRET_KEY env variable.")
        secret_key_bytes = fernet.Fernet.generate_key() # Generate valid bytes directly

    # --- App Initialization ---
    app = web.Application()
    
    # Setup session middleware (This will now use a guaranteed valid key)
    storage = EncryptedCookieStorage(secret_key_bytes, cookie_name='session_id')
    setup_session(app, storage)
    
    # Setup CORS
    cors = aiohttp_cors.setup(app, defaults={
        "*": ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods="*",
        )
    })

    # --- Add Routes ---
    app.router.add_get("/", index)
    app.router.add_get("/login", handle_login_page)
    app.router.add_post("/login", handle_login)
    app.router.add_get("/register", handle_register_page)
    app.router.add_post("/register", handle_register)
    app.router.add_get("/logout", handle_logout)
    app.router.add_get("/api/userinfo", get_user_info)
    app.router.add_get("/api/random_id", handle_random_id) # New API route
    app.router.add_get("/health", healthcheck)
    app.router.add_post("/offer", offer)
    
    # Add WebSocket route
    app.router.add_get("/ws", text_chat_handler) 
    
    # Add static file serving
    app.router.add_static("/dist", DIST_DIR)
    
    # Apply CORS to all routes
    for route in list(app.router.routes()):
        cors.add(route)
        
    # Add startup/cleanup tasks
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    
    # --- Run Server ---
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8080))
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    
    logger.info(f"Server is ready. Access at: http://{host}:{port}")
    await asyncio.Event().wait() # Keep server running

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server shut down manually.")
    except Exception as e:
        logger.error(f"Fatal server error: {e}", exc_info=True)