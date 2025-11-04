#!/usr/bin/env python3
"""
Fixed RealtimeConnect Server
Combines WebRTC video/audio/screen sharing with text chat functionality,
and includes basic user authentication.
"""

import asyncio
import json
import logging
import uuid
import os
import websockets
import bcrypt
import aiosqlite
from aiohttp import web
from aiohttp_session import setup as session_setup, get_session, session_middleware, new_session
from cryptography import fernet
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# --- Configuration ---
# Set the secret key for session management from environment variable
# If the key is not set (e.g., during local testing without a .env), generate one on the fly.
# IMPORTANT: On Render, this must be set as an Environment Variable named SECRET_KEY
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    print("WARNING: SECRET_KEY not found. Generating a temporary key. USE ENVIRONMENT VARIABLE IN PRODUCTION.")
    SECRET_KEY = fernet.Fernet.generate_key().decode()
    
# Configure logging for debugging purposes
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Session Storage Setup ---
def setup_session_storage():
    """Sets up Fernet-based cookie session storage."""
    # Use the SECRET_KEY from the environment
    secret_key = SECRET_KEY.encode('utf-8')
    return fernet.Fernet.generate_key() # Note: Fernet requires a 32-urlsafe-base64-bytes key, we use Fernet.generate_key() as a convenience, though typically you'd derive from SECRET_KEY

# --- Database Setup ---
DB_PATH = 'users.db'

async def init_db():
    """Initializes the SQLite database and creates the users table."""
    db = await aiosqlite.connect(DB_PATH)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    """)
    await db.commit()
    await db.close()

# --- Text Chat specific variables ---
text_chat_clients = {}  # username -> websocket mapping

# --- WebRTC specific variables ---
webrtc_peers = {}  # peer_id -> RTCPeerConnection mapping
relay = MediaRelay()

# ============================================================================
# TEXT CHAT HANDLERS
# ============================================================================

async def text_chat_handler(websocket, path):
    """Handles WebSocket connections for text chat."""
    username = None
    try:
        # First message should be username
        username = await websocket.recv()
        if not username or username.strip() == "":
            await websocket.send("ERROR: Username cannot be empty")
            await websocket.close()
            return
            
        username = username.strip()
        
        # Check for duplicate username
        if username in text_chat_clients:
            # Generate unique username
            username += "-" + str(uuid.uuid4())[:4] 
            
        text_chat_clients[username] = websocket
        
        logger.info(f"Chat connected: {username}")
        
        # Send join message to everyone
        join_message = f"📢 User {username} joined the chat."
        await broadcast_chat_message(join_message, username="System")

        # Listen for chat messages
        async for message in websocket:
            await broadcast_chat_message(message, username=username)

    except websockets.exceptions.ConnectionClosedOK:
        logger.info(f"Chat disconnected gracefully: {username}")
    except Exception as e:
        logger.error(f"Chat connection error for {username}: {e}")
    finally:
        if username and username in text_chat_clients:
            del text_chat_clients[username]
            # Send leave message to everyone
            leave_message = f"📢 User {username} left the chat."
            await broadcast_chat_message(leave_message, username="System")


async def broadcast_chat_message(message, username="System"):
    """Sends a message to all connected text chat clients."""
    # Construct the message payload (JSON is better for structure)
    payload = json.dumps({"username": username, "message": message})
    
    # Send to all clients
    # Use list() comprehension to safely iterate over clients while allowing disconnects
    closed_clients = []
    for user, ws in list(text_chat_clients.items()):
        if ws.closed:
            closed_clients.append(user)
            continue
        try:
            await ws.send(payload)
        except Exception as e:
            logger.error(f"Error sending message to {user}: {e}")
            closed_clients.append(user)
            
    # Clean up closed clients
    for user in closed_clients:
        if user in text_chat_clients:
            del text_chat_clients[user]

# ============================================================================
# WEB APP HANDLERS (AIOHTTP)
# ============================================================================

async def is_authenticated(request):
    """Checks if the user has an active session."""
    session = await get_session(request)
    return 'username' in session

# --- Authentication Handlers ---

async def register_page(request):
    """Serve the registration HTML page."""
    if await is_authenticated(request):
        return web.HTTPFound('/')
    with open('register.html', 'r') as f:
        return web.Response(text=f.read(), content_type='text/html')

async def register_handler(request):
    """Handles registration form submission."""
    data = await request.post()
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return web.Response(text="Username and password required", status=400)

    # Hash the password
    password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    try:
        db = await aiosqlite.connect(DB_PATH)
        await db.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, password_hash))
        await db.commit()
        await db.close()
        
        logger.info(f"User registered: {username}")
        # Automatically log in the user after registration
        session = await new_session(request)
        session['username'] = username
        return web.HTTPFound('/') # Redirect to the main application

    except aiosqlite.IntegrityError:
        return web.Response(text="Username already exists", status=400)
    except Exception as e:
        logger.error(f"Registration error: {e}")
        return web.Response(text="Internal server error during registration", status=500)


async def login_page(request):
    """Serve the login HTML page."""
    if await is_authenticated(request):
        return web.HTTPFound('/')
    with open('login.html', 'r') as f:
        return web.Response(text=f.read(), content_type='text/html')

async def login_handler(request):
    """Handles login form submission."""
    data = await request.post()
    username = data.get('username')
    password = data.get('password')
    
    db = await aiosqlite.connect(DB_PATH)
    cursor = await db.execute("SELECT password_hash FROM users WHERE username = ?", (username,))
    row = await cursor.fetchone()
    await db.close()

    if row and bcrypt.checkpw(password.encode('utf-8'), row[0].encode('utf-8')):
        session = await new_session(request)
        session['username'] = username
        logger.info(f"User logged in: {username}")
        return web.HTTPFound('/')
    else:
        return web.Response(text="Invalid username or password", status=401)

async def logout_handler(request):
    """Handles user logout."""
    session = await get_session(request)
    username = session.pop('username', 'Unknown')
    logger.info(f"User logged out: {username}")
    # Clear session and redirect to login
    return web.HTTPFound('/login')

# --- Main App Handler (Requires Auth) ---

async def index(request):
    """Serve the main client HTML page, protected by authentication."""
    if not await is_authenticated(request):
        return web.HTTPFound('/login')
    
    # Pass the username to the client
    session = await get_session(request)
    username = session.get('username', 'Guest')
    
    with open('client.html', 'r') as f:
        content = f.read()
        # Inject the username into the HTML for client-side use
        content = content.replace(
            "<!-- USERNAME_INJECTION_POINT -->", 
            f"<script>window.USERNAME = '{username}';</script>"
        )
        return web.Response(text=content, content_type='text/html')

# --- WebRTC Signaling Handler ---

async def offer(request):
    """Handles the SDP Offer from a WebRTC client."""
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    # Create a new Peer Connection
    pc = RTCPeerConnection()
    peer_id = str(uuid.uuid4())
    webrtc_peers[peer_id] = pc
    logger.info(f"Created RTCPeerConnection for {peer_id}")

    # Handle ice candidate
    @pc.on("icecandidate")
    async def on_icecandidate(candidate):
        if candidate:
            logger.info(f"ICE candidate for {peer_id}: {candidate.sdp}")
            # In a real app, you would send this to the remote peer via a WebSocket

    # Handle track event (receiving media)
    @pc.on("track")
    def on_track(track):
        logger.info(f"Track {track.kind} received from {peer_id}")

        # The relay helps re-broadcast the media to other connected users
        # For simplicity, we are not implementing a full mesh/SFU rebroadcast here,
        # but the track event shows the connection is established.

    # Handle connection state changes
    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info(f"Connection state for {peer_id}: {pc.connectionState}")
        if pc.connectionState == "failed":
            await pc.close()
            if peer_id in webrtc_peers:
                del webrtc_peers[peer_id]
                logger.info(f"Closed failed connection for {peer_id}")

    # Set the remote offer and create an answer
    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    # Return the SDP answer and the peer ID
    return web.Response(
        content_type="application/json",
        text=json.dumps(
            {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type, "peerId": peer_id}
        ),
    )

# --- Health Check ---

async def healthcheck(request):
    """Simple health check endpoint."""
    return web.Response(text="OK")

# ============================================================================
# MAIN SERVER STARTUP
# ============================================================================

async def main():
    """Initializes and starts both the HTTP and WebSocket servers."""
    
    # 1. Initialize Database
    await init_db()
    
    # 2. Configure HTTP server
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8080))
    
    # Configure CORS middleware
    cors = aiohttp_cors.setup(web.Application(), defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            allow_headers=("X-Requested-With", "Content-Type"),
            allow_methods="*",
        )
    })
    
    # Set up session middleware using Fernet for encryption
    # We use the SECRET_KEY for security
    app = web.Application(middlewares=[
        session_middleware(fernet.Fernet(SECRET_KEY.encode('utf-8')))
    ])

    # Public Routes
    # REMOVED ESCAPING BACKSLASHES (Fix for SyntaxError)
    app.router.add_get("/login", login_page)
    app.router.add_post("/login", login_handler)
    app.router.add_get("/register", register_page)
    app.router.add_post("/register", register_handler)
    app.router.add_get("/logout", logout_handler)
    
    # Main Application Route (protected)
    app.router.add_get("/", index)
    
    # WebRTC Signaling Routes
    app.router.add_post("/offer", offer)
    
    # Health check
    app.router.add_get("/health", healthcheck)
    
    # Add static file serving for CSS and JS files
    app.router.add_static("/dist", "./dist")
    app.router.add_static("/", ".") # Serve other root files like client.html, login.html

    # Apply CORS to all routes
    for route in list(app.router.routes()):
        cors.add(route)
        
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"HTTP server started on http://{host}:{port}")
    
    # 3. Start WebSocket server for text chat on port+1
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
    await asyncio.Event().wait()


if __name__ == "__main__":
    # Import aiohttp_cors after loading dotenv to avoid import error
    import aiohttp_cors
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server shut down.")