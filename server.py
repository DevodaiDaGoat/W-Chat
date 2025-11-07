#!/usr/bin/env python3
"""
Fixed RealtimeConnect Server
Combines WebRTC video/audio/screen sharing with text chat functionality.
Adds robust session management and placeholder authentication routes.
"""

import asyncio
import json
import logging
import uuid
import os
import websockets
import aiosqlite
import bcrypt
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
from aiohttp_session import setup as setup_session, get_session
from aiohttp_session.cookie_storage import EncryptedCookieStorage
from cryptography.fernet import Fernet
from dotenv import load_dotenv

# Configure logging for debugging purposes
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Text Chat specific variables ---
# Maps username to the active WebSocket connection for chat
text_chat_clients = {}  

# --- WebRTC specific variables ---
# Maps peer_id (UUID) to the active RTCPeerConnection
webrtc_peers = {}  
# MediaRelay handles stream synchronization for screen sharing
relay = MediaRelay()

# --- Database and Auth ---
DB_PATH = 'users.db'

async def db_init():
    """Initializes the SQLite database and creates the users table."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL
                )
            """)
            await db.commit()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")

async def get_user_by_username(db, username):
    """Retrieves a user from the database by username."""
    async with db.execute("SELECT id, username, password_hash FROM users WHERE username = ?", (username,)) as cursor:
        return await cursor.fetchone()

async def create_user(db, username, password):
    """Creates a new user with a hashed password."""
    # Hash the password
    salt = bcrypt.gensalt()
    password_hash = bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
    
    try:
        async with db.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, password_hash)):
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
# MIDDLEWARE AND AUTH DECORATOR
# ============================================================================

async def requires_auth(request):
    """A simple decorator/check for authenticated access."""
    session = await get_session(request)
    if 'user_id' not in session:
        # Redirect to login page if not authenticated
        raise web.HTTPFound('/login')
    # Store the username in the request object for easy access
    request['username'] = session.get('username')
    return True

# ============================================================================
# HTTP HANDLERS (AIOHTTP)
# ============================================================================

async def index(request):
    """Serves the main meeting client page if authenticated."""
    await requires_auth(request)
    # Read and return the client.html file
    with open("client.html", "r") as f:
        return web.Response(text=f.read(), content_type="text/html")

async def login_page(request):
    """Serves the login page."""
    with open("login.html", "r") as f:
        return web.Response(text=f.read(), content_type="text/html")

async def register_page(request):
    """Serves the register page."""
    with open("register.html", "r") as f:
        return web.Response(text=f.read(), content_type="text/html")

async def handle_login(request):
    """Handles POST request for user login."""
    data = await request.post()
    username = data.get('username')
    password = data.get('password')
    
    db = request.app['db']
    user_data = await get_user_by_username(db, username)
    
    if user_data and check_password(password, user_data[2]): # user_data[2] is password_hash
        session = await get_session(request)
        session['user_id'] = user_data[0]
        session['username'] = user_data[1]
        logger.info(f"User logged in: {username}")
        # Redirect to the main application page
        raise web.HTTPFound('/')
    else:
        # Simple redirect back to login on failure (in a real app, use flash messages)
        raise web.HTTPFound('/login?error=1')

async def handle_register(request):
    """Handles POST request for user registration."""
    data = await request.post()
    username = data.get('username')
    password = data.get('password')
    
    db = request.app['db']

    if not username or not password:
        raise web.HTTPFound('/register?error=1')
        
    if await create_user(db, username, password):
        # Auto-login after successful registration
        user_data = await get_user_by_username(db, username)
        session = await get_session(request)
        session['user_id'] = user_data[0]
        session['username'] = user_data[1]
        raise web.HTTPFound('/')
    else:
        # Username already exists
        raise web.HTTPFound('/register?error=2')
    
async def handle_logout(request):
    """Handles user logout by clearing the session."""
    session = await get_session(request)
    username = session.pop('username', 'Unknown')
    session.pop('user_id', None)
    logger.info(f"User logged out: {username}")
    raise web.HTTPFound('/login')

async def offer(request):
    """Handles WebRTC signalling for offer/answer exchange."""
    await requires_auth(request)
    
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    # Create a new peer connection for this client
    pc = RTCPeerConnection()
    peer_id = str(uuid.uuid4())
    webrtc_peers[peer_id] = pc
    
    # Store peer ID and username in the session (optional, for tracking)
    # session = await get_session(request)
    # logger.info(f"New WebRTC connection for user {session.get('username')}, Peer ID: {peer_id}")

    # Set up data channel and event handlers... (omitted for brevity)
    
    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        if pc.iceConnectionState == "failed":
            await pc.close()
            webrtc_peers.pop(peer_id, None)

    # Handle remote track from the client (e.g., their mic/camera)
    @pc.on("track")
    def on_track(track):
        # We don't need to relay every track, but if you want to forward 
        # streams to other peers, this is where you'd handle it.
        logger.info(f"Track {track.kind} received.")
        
    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.json_response(
        {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type, "peerId": peer_id}
    )

async def healthcheck(request):
    """Simple health check endpoint."""
    return web.Response(text="OK")

# ============================================================================
# TEXT CHAT HANDLERS
# ============================================================================

async def text_chat_handler(websocket, path):
    """Handles WebSocket connections for text chat."""
    # NOTE: This WebSocket is separate from the HTTP session. 
    # The client is responsible for sending auth/username info in the first message.
    username = None
    
    try:
        # First message should be username (sent by client.html)
        username = await websocket.recv()
        if not username or username.strip() == "":
            await websocket.send(json.dumps({"type": "error", "message": "Username cannot be empty"}))
            await websocket.close()
            return
            
        username = username.strip()
        
        # Check for duplicate username and adjust if necessary
        original_username = username
        counter = 1
        while username in text_chat_clients:
            username = f"{original_username}_{counter}"
            counter += 1
            
        text_chat_clients[username] = websocket
        logger.info(f"Chat client connected: {username}. Total clients: {len(text_chat_clients)}")

        # Broadcast join message
        await broadcast_message(f"{username} has joined the room.")

        # Main loop to receive messages
        async for message in websocket:
            try:
                # Assuming simple text message
                if isinstance(message, str):
                    await broadcast_message(message, sender=username)
            except Exception as e:
                logger.error(f"Error processing message from {username}: {e}")
                
    except websockets.exceptions.ConnectionClosed:
        logger.info(f"Chat client disconnected (ConnectionClosed): {username}")
    except Exception as e:
        logger.error(f"Unexpected error in chat handler for {username}: {e}")
    finally:
        if username and username in text_chat_clients:
            del text_chat_clients[username]
            logger.info(f"Chat client removed: {username}. Remaining clients: {len(text_chat_clients)}")
            # Broadcast leave message
            if username != original_username and username.startswith(original_username):
                # Only broadcast for the adjusted name if it was adjusted
                await broadcast_message(f"{username} has left the room.")
            elif username == original_username:
                 await broadcast_message(f"{username} has left the room.")
            # Ensure all peers are cleaned up (though WebRTC is handled separately)

async def broadcast_message(message, sender="System"):
    """Sends a chat message to all connected clients."""
    chat_payload = json.dumps({
        "type": "message",
        "sender": sender,
        "content": message
    })
    # Gather all send tasks
    send_tasks = [client.send(chat_payload) for client in text_chat_clients.values()]
    # Run all sends concurrently
    if send_tasks:
        await asyncio.gather(*send_tasks, return_exceptions=True)

# ============================================================================
# SERVER LIFECYCLE
# ============================================================================

async def on_shutdown(app):
    """Cleanup all peer connections on server shutdown."""
    # Close all WebRTC peers
    coros = [pc.close() for pc in webrtc_peers.values()]
    if coros:
        await asyncio.gather(*coros)
    webrtc_peers.clear()
    logger.info("All WebRTC peers closed.")

async def main():
    """Main entry point for the server."""
    # Load environment variables from .env file (for local development)
    load_dotenv()
    
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8080))
    
    await db_init()

    # --- Session Secret Key Setup (Fixes Fernet ValueError) ---
    secret_key = os.environ.get("SECRET_KEY")
    secret_key_bytes = None
    
    # 1. Attempt to load and validate the key
    if secret_key:
        try:
            # Fernet requires the key to be 32 URL-safe base64-encoded bytes.
            # Convert the string to bytes for validation.
            secret_key_bytes = secret_key.encode('utf-8')
            # Validation check - this will raise ValueError if invalid
            Fernet(secret_key_bytes)
            logger.info("Loaded valid SECRET_KEY from environment.")
        except ValueError:
            logger.error("Environment SECRET_KEY is invalid for Fernet. Generating a temporary key.")
            secret_key_bytes = Fernet.generate_key()
            logger.warning(f"TEMPORARY SECRET_KEY: {secret_key_bytes.decode('utf-8')}")
            logger.warning("!!! ACTION REQUIRED: Please use this new key to update your environment/config. Session integrity will be lost on server restart. !!!")
    else:
        # 2. Generate key if not present (Development/Testing fallback)
        secret_key_bytes = Fernet.generate_key()
        logger.warning(f"SECRET_KEY not found in environment. Generated temporary key: {secret_key_bytes.decode('utf-8')}")
        logger.warning("!!! ACTION REQUIRED: Please use this new key to update your environment/config. Session integrity will be lost on server restart. !!!")

    # Use the validated/generated key for EncryptedCookieStorage
    storage = EncryptedCookieStorage(secret_key_bytes, cookie_name='session_id')

    # Setup aiohttp app
    app = web.Application()
    
    # Attach DB connection to app
    app['db'] = await aiosqlite.connect(DB_PATH) 
    
    # Setup session middleware
    setup_session(app, storage)

    # Setup CORS middleware
    cors = web.middleware.cors_middleware(
        allow_all=True  # In production, replace with specific origins
    )
    
    # Apply middleware to app
    app = web.Application(middlewares=[cors])
    
    # Re-attach components after creating app with middleware
    app['db'] = await aiosqlite.connect(DB_PATH) 
    setup_session(app, storage)

    # --- HTTP Routes ---
    app.router.add_get("/", index)
    app.router.add_get("/login", login_page)
    app.router.add_post("/login", handle_login)
    app.router.add_get("/register", register_page)
    app.router.add_post("/register", handle_register)
    app.router.add_get("/logout", handle_logout)
    
    app.router.add_post("/offer", offer)
    app.router.add_get("/health", healthcheck)
    
    # Add static file serving for CSS and client JS files
    app.router.add_static("/dist", "./dist")
    app.router.add_static("/script.js", "./script.js")
    
    # Register cleanup handler
    app.on_shutdown.append(on_shutdown)
    
    # Start the HTTP server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"HTTP server started on http://{host}:{port}")
    
    # Start WebSocket server for text chat on port+1
    ws_port = port + 1
    websocket_server = await websockets.serve(
        text_chat_handler, 
        host, 
        ws_port,
        ping_interval=20,  # Send pings every 20 seconds
        ping_timeout=10    # Wait 10 seconds for pong
    )
    logger.info(f"WebSocket server for text chat started on ws://{host}:{ws_port}")
    
    # Print connection info
    logger.info(f"Server is ready. Access locally at:")
    logger.info(f"- HTTP: http://localhost:{port}")
    logger.info(f"- WebSocket: ws://localhost:{ws_port}")
    
    # Keep the server running
    try:
        await asyncio.Event().wait()
    finally:
        # Close database connection on exit
        await app['db'].close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server stopped by user.")
    except Exception as e:
        logger.critical(f"Fatal server error: {e}", exc_info=True)
        # Re-raise the exception for the deployment environment to capture
        raise