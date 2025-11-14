#!/usr/bin/env python3
"""
Fixed RealtimeConnect Server
Combines WebRTC, Authentication, and WebSockets into a SINGLE aiohttp application.
"""

import asyncio
import json
import logging
import uuid
import os
import aiosqlite
import bcrypt
import random
import string
from aiohttp import web
from aiohttp_session import setup as setup_session, get_session, new_session
from aiohttp_session.cookie_storage import EncryptedCookieStorage
from cryptography.fernet import Fernet
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
import aiohttp_cors
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
# app['ws_clients'] will be initialized in on_startup


# ============================================================================
# UTILITIES
# ============================================================================

def generate_binary_code(num_chars, bits_per_char=8):
    """Generates a random binary string, grouped for readability."""
    binary_string = ""
    for _ in range(num_chars):
        char_binary = ''.join(random.choice('01') for _ in range(bits_per_char))
        binary_string += char_binary + " "  # Grouping with a space
    return binary_string.strip()

def binary_to_alphabet(binary_code):
    """Converts a grouped binary string to an alphabet-based message using ASCII."""
    decoded_message = ""
    binary_groups = binary_code.split()
    for group in binary_groups:
        try:
            decimal_value = int(group, 2)
            # We only want URL-safe characters for an ID
            if 48 <= decimal_value <= 57 or 65 <= decimal_value <= 90 or 97 <= decimal_value <= 122:
                decoded_message += chr(decimal_value)
            else:
                decoded_message += random.choice(string.ascii_letters + string.digits) # Fallback
        except ValueError:
            decoded_message += "?"  # Handle invalid binary groups
    return decoded_message

async def handle_random_id(request):
    """API endpoint to generate and return a new random meeting ID."""
    random_binary = generate_binary_code(8) # 8-character random ID
    decoded_id = binary_to_alphabet(random_binary)
    return web.json_response({'meeting_id': decoded_id})

# ============================================================================
# DATABASE & AUTHENTICATION
# ============================================================================

async def init_db(app):
    """Initializes the SQLite database and creates the users table."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user'
                )
            """)
            await db.commit()
        app['db'] = await aiosqlite.connect(DB_PATH)
        logger.info("Database initialized and 'users' table ensured.")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

async def close_db(app):
    """Closes the database connection."""
    await app['db'].close()
    logger.info("Database connection closed.")


async def get_user_by_username(db, username):
    """Retrieves a user from the database by username."""
    async with db.execute("SELECT id, username, password_hash, role FROM users WHERE username = ?", (username,)) as cursor:
        return await cursor.fetchone()

async def create_user(db, username, password):
    """Creates a new user with a hashed password."""
    salt = bcrypt.gensalt()
    password_hash = bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
    role = 'user' # All new users are 'user' by default
    
    try:
        await db.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)", (username, password_hash, role))
        await db.commit()
        logger.info(f"User created: {username}")
        return True
    except aiosqlite.IntegrityError:
        return False # Username already exists
    except Exception as e:
        logger.error(f"Error creating user: {e}")
        return False

def check_password(password, password_hash):
    """Verifies a password against its hash."""
    return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))

# ============================================================================
# HTTP HANDLERS (AIOHTTP)
# ============================================================================

async def index(request):
    """Serve the main client HTML page, redirect to login if not authenticated."""
    session = await get_session(request)
    if not session.get('username'):
        logger.info("No session, redirecting to /login.")
        return web.HTTPFound('/login')
        
    logger.info(f"Serving client.html to user '{session['username']}'.")
    try:
        with open(os.path.join(STATIC_DIR, "client.html"), 'r') as f:
            html_content = f.read()
        return web.Response(text=html_content, content_type="text/html")
    except FileNotFoundError:
        logger.error("client.html not found!")
        return web.Response(text="Client application not found.", status=404)

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
        return web.HTTPFound('/login?error=1') # 1 = missing fields

    try:
        user_data = await get_user_by_username(request.app['db'], username)
        if user_data:
            hashed_password = user_data[2]
            if check_password(password, hashed_password):
                # Password is correct, create session
                session = await new_session(request)
                session['user_id'] = user_data[0]
                session['username'] = user_data[1]
                logger.info(f"User '{username}' logged in successfully.")
                return web.HTTPFound('/') # Redirect to main chat page
        
        # Invalid username or password
        logger.warning(f"Failed login attempt for user '{username}'.")
        return web.HTTPFound('/login?error=2') # 2 = invalid credentials
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
        return web.Response(text="Registration page not found.", status=44)

async def handle_register(request):
    """Handles the POST request from the registration form."""
    data = await request.post()
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return web.HTTPFound('/register?error=1') # 1 = missing fields

    if await create_user(request.app['db'], username, password):
        logger.info(f"New user '{username}' registered successfully.")
        # After registering, redirect to login page
        return web.HTTPFound('/login?registered=1')
    else:
        logger.warning(f"Registration failed: Username '{username}' already exists.")
        return web.HTTPFound('/register?error=2') # 2 = username taken

async def handle_logout(request):
    """Logs the user out by destroying their session."""
    session = await get_session(request)
    username = session.pop('username', 'Unknown')
    session.pop('user_id', None)
    session.invalidate()
    logger.info(f"User '{username}' logged out.")
    return web.HTTPFound('/login')

async def get_user_info(request):
    """API endpoint to get the current user's session data."""
    session = await get_session(request)
    username = session.get('username')
    
    if not username:
        return web.json_response({'error': 'Not authenticated'}, status=401)
    
    user_data = await get_user_by_username(request.app['db'], username)
    role = user_data[3] if user_data else 'user'
    return web.json_response({'username': username, 'role': role})

# ============================================================================
# WEBSOCKET CHAT HANDLER (Integrated into aiohttp)
# ============================================================================

async def text_chat_handler(request):
    """Handles WebSocket connections on the /ws route."""
    
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    app = request.app
    username = None
    
    try:
        # First message from client MUST be the username
        username = await ws.receive_str(timeout=10.0)
        if not username or username.strip() == "":
            await ws.close(code=1003, message=b'Username cannot be empty')
            return ws
        
        username = username.strip()
        
        # Handle duplicate usernames
        original_username = username
        counter = 1
        while username in app['ws_clients']:
            username = f"{original_username}_{counter}"
            counter += 1
        
        app['ws_clients'][username] = ws
        logger.info(f"Client '{username}' connected to text chat. Total: {len(app['ws_clients'])}")
        
        # Send join message to everyone
        await broadcast_chat_message(app, f"System: '{username}' has joined the chat.")

        # Main receive loop
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                await broadcast_chat_message(app, f"{username}: {msg.data}")
            elif msg.type == web.WSMsgType.ERROR:
                logger.error(f"WebSocket connection closed with exception {ws.exception()}")

    except asyncio.TimeoutError:
        logger.warning("WebSocket client failed to send username in time.")
    except Exception as e:
        logger.error(f"Text chat handler error for {username}: {e}")
    finally:
        if username and username in app['ws_clients']:
            del app['ws_clients'][username]
            logger.info(f"Client '{username}' removed. Total: {len(app['ws_clients'])}")
            await broadcast_chat_message(app, f"System: '{username}' has left the chat.")
            
    logger.info(f"WebSocket connection closed for {username}.")
    return ws

async def broadcast_chat_message(app, message):
    """Broadcasts a message to all connected chat clients."""
    disconnected_clients = []
    
    # Create JSON payload for structured messaging
    payload = json.dumps({"type": "message", "sender": "System", "content": message})
    if ": " in message:
        parts = message.split(': ', 1)
        payload = json.dumps({"type": "message", "sender": parts[0], "content": parts[1]})

    for username, ws in app['ws_clients'].items():
        if not ws.closed:
            try:
                await ws.send_str(payload)
            except Exception:
                disconnected_clients.append(username)
        else:
            disconnected_clients.append(username)
            
    # Clean up disconnected clients
    for username in disconnected_clients:
        if username in app['ws_clients']:
            del app['ws_clients'][username]

# ============================================================================
# WEBRTC HANDLERS
# ============================================================================

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
        # When a track is received, add it to the global relay
        # so other peers can subscribe to it.
        relayed_track = relay.subscribe(track)
        
        # Add this new track to all *other* existing peer connections
        for other_peer_id, other_pc in webrtc_peers.items():
            if other_peer_id != peer_id:
                try:
                    other_pc.addTrack(relayed_track)
                except Exception as e:
                    logger.warning(f"Error relaying track to {other_peer_id}: {e}")

        @track.on("ended")
        async def on_ended():
            logger.info(f"Track {track.kind} ended for peer {peer_id}")

    # Add tracks from the relay (from *other* peers) to this *new* peer
    for other_peer_pc in webrtc_peers.values():
        if other_peer_pc != pc:
            for sender in other_peer_pc.getSenders():
                if sender.track:
                    pc.addTrack(relay.subscribe(sender.track))

    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.json_response(
        {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type, "id": peer_id}
    )

async def healthcheck(request):
    """Simple health check endpoint."""
    return web.Response(text="OK", content_type="text/plain")

# ============================================================================
# SERVER LIFECYCLE
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
    
    await close_db(app)

async def main():
    """Main entry point to start the single aiohttp server."""
    
    # --- Secret Key Setup (ROBUST FIX) ---
    secret_key_str = os.environ.get("SECRET_KEY")
    storage = None

    if secret_key_str:
        try:
            # Try to use the key from the environment
            key_from_env_bytes = secret_key_str.encode('utf-8')
            # This is the validation: try to initialize EncryptedCookieStorage directly.
            # This will fail if the key is not 32-byte base64-encoded.
            storage = EncryptedCookieStorage(key_from_env_bytes, cookie_name='session_id')
            logger.info("Loaded valid SECRET_KEY and initialized storage.")
        except (ValueError, TypeError) as e:
            # This block will catch the "Fernet key must be..." error
            logger.error(f"Invalid SECRET_KEY in environment: {e}. Generating temporary key.")
    
    if not storage:
        # Fallback if key is missing OR was invalid
        logger.warning("SECRET_KEY not found or invalid. Generating a temporary key for this session.")
        logger.warning("DO NOT USE THIS IN PRODUCTION. Set a permanent SECRET_KEY env variable.")
        secret_key_bytes = Fernet.generate_key() # This returns valid bytes
        storage = EncryptedCookieStorage(secret_key_bytes, cookie_name='session_id')

    # --- App Initialization ---
    app = web.Application()
    
    # Setup session middleware
    setup_session(app, storage)
    
    # Setup CORS
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
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
    app.router.add_get("/api/random_id", handle_random_id) # Added random ID route
    app.router.add_get("/health", healthcheck)
    app.router.add_post("/offer", offer)
    
    # Add WebSocket route (FIX: Now part of the same server)
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
    port = int(os.environ.get("PORT", 8080)) # Render provides this port
    
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
        logger.critical(f"Fatal server error: {e}", exc_info=True)
        raise # Re-raise to ensure Render sees the failure