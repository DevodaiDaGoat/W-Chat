#!/usr/bin/env python3
"""
Fixed RealtimeConnect Server
Combines WebRTC, authenticated text chat, and admin roles.
"""

import asyncio
import json
import logging
import uuid
import os
import websockets
import aiosqlite # For asynchronous SQLite access
import bcrypt # For secure password hashing
import base64 # Added missing import for session handling/cookie encryption
from dotenv import load_dotenv # <-- NEW IMPORT

# Load environment variables from .env file (now correctly accessed via Secret Files)
load_dotenv() # <-- NEW LINE

# aiortc imports
from aiohttp import web
from aiohttp_session import setup as setup_session, get_session
from aiohttp_session.cookie_storage import EncryptedCookieStorage
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay

# Configure logging for debugging purposes
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Configuration ---
DB_PATH = "users.db"
# NOTE: SECRET_KEY is now loaded from the .env file
SECRET_KEY = os.environ.get('SECRET_KEY', 'default-insecure-secret-key-change-me').encode('utf-8')

# --- In-memory state ---
text_chat_clients = {}  # username -> websocket mapping
webrtc_peers = {}  # peer_id -> RTCPeerConnection mapping
relay = MediaRelay()


# ============================================================================
# DATABASE SETUP
# ============================================================================

async def init_db():
    """Initializes the SQLite database and creates the users table if it doesn't exist."""
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


# ============================================================================
# AUTHENTICATION HANDLERS (HTTP)
# ============================================================================

async def index(request):
    """Checks session and redirects to login or client HTML."""
    session = await get_session(request)
    if 'username' not in session:
        raise web.HTTPFound('/login')
    
    # Serve the main client application
    with open("client.html", "r") as f:
        return web.Response(text=f.read(), content_type="text/html")


async def login_page(request):
    """Serves the login page HTML."""
    with open("login.html", "r") as f:
        return web.Response(text=f.read(), content_type="text/html")


async def register_page(request):
    """Serves the registration page HTML."""
    with open("register.html", "r") as f:
        return web.Response(text=f.read(), content_type="text/html")


async def handle_login(request):
    """Handles POST request for user login."""
    # Initialize variables outside of the POST block to avoid 'undefined variable' errors
    username = None
    password = None 
    
    try:
        data = await request.post()
        username = data.get('username')
        password = data.get('password')
    except Exception as e:
        logger.error(f"Error parsing login request data: {e}")
        return web.HTTPBadRequest(text="Invalid request data format.")

    if not username or not password:
        return web.HTTPBadRequest(text="Username and password are required.")

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT password_hash, role FROM users WHERE username = ?", (username,))
        user_record = await cursor.fetchone()
        
        if user_record and bcrypt.checkpw(password.encode('utf-8'), user_record[0].encode('utf-8')):
            # Successful login
            session = await get_session(request)
            session['username'] = username
            session['role'] = user_record[1]
            raise web.HTTPFound('/') # Redirect to main client page
        else:
            # Failed login
            return web.Response(text="Login failed: Invalid username or password.", status=401)


async def handle_register(request):
    """Handles POST request for user registration."""
    # Initialize variables outside of the POST block to avoid 'undefined variable' errors
    username = None
    password = None

    try:
        data = await request.post()
        username = data.get('username')
        password = data.get('password')
    except Exception as e:
        logger.error(f"Error parsing register request data: {e}")
        return web.HTTPBadRequest(text="Invalid request data format.")
    
    if not username or not password:
        return web.HTTPBadRequest(text="Username and password are required.")

    # Hash the password securely with bcrypt
    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    # Assign role - all new users are 'user' by default
    role = 'user'
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                (username, hashed_password, role)
            )
            await db.commit()
            
        # Log in the user immediately after registration
        session = await get_session(request)
        session['username'] = username
        session['role'] = role
        raise web.HTTPFound('/') # Redirect to main client page
        
    except aiosqlite.IntegrityError:
        return web.Response(text="Registration failed: Username already exists.", status=400)
    except Exception as e:
        logger.error(f"Registration error: {e}")
        return web.Response(text="Registration failed due to server error.", status=500)


async def handle_logout(request):
    """Handles user logout by clearing the session."""
    session = await get_session(request)
    if 'username' in session:
        del session['username']
        del session['role']
    raise web.HTTPFound('/login')


async def get_user_data(request):
    """API endpoint to get the authenticated user's data for the client."""
    session = await get_session(request)
    if 'username' not in session:
        return web.HTTPUnauthorized(text="Unauthorized")
    
    return web.json_response({
        'username': session['username'],
        'role': session.get('role', 'user')
    })


# ============================================================================
# TEXT CHAT HANDLER (WEBSOCKET)
# ============================================================================

async def broadcast(message):
    """Sends a message to all connected text chat clients."""
    disconnected_clients = []
    for username, websocket in list(text_chat_clients.items()):
        try:
            await websocket.send(message)
        except websockets.exceptions.ConnectionClosed:
            disconnected_clients.append(username)
        except Exception as e:
            logger.error(f"Error broadcasting to {username}: {e}")
            disconnected_clients.append(username)

    for username in disconnected_clients:
        if username in text_chat_clients:
            del text_chat_clients[username]


async def text_chat_handler(websocket, path):
    """Handles WebSocket connections for authenticated text chat."""
    # Initialized here so they are available in the 'finally' block for cleanup
    username = None 
    role = 'user' 
    
    try:
        # The client now sends the auth token from its local session data immediately
        auth_data = await websocket.recv()
        data = json.loads(auth_data)
        username = data.get('username')
        role = data.get('role', 'user')

        if not username:
            await websocket.send("ERROR: Authentication failed. Please log in.")
            return # Exit the try block
            
        username = username.strip()

        # Check for existing connection and close it if found
        if username in text_chat_clients:
            try:
                # Attempt to gracefully close old connection before establishing new one
                await text_chat_clients[username].close()
            except Exception:
                pass
            
        text_chat_clients[username] = websocket
        logger.info(f"Chat client connected: {username} ({role})")

        # Notify everyone that a new user has joined
        await broadcast(f"System: {username} has joined the chat.")

        # Send welcome message to just this user
        await websocket.send("System: Welcome to the chat! Type /help for commands.")
        
        if role == 'admin':
            await websocket.send("System: You have ADMIN privileges. Available commands: /kick <user>, /announce <msg>")


        # === Main Message Loop ===
        async for message in websocket:
            message = message.strip()
            if not message:
                continue

            if message.startswith('/'):
                # Handle commands
                parts = message.split(maxsplit=2)
                command = parts[0].lower()
                
                if command == '/help':
                    help_msg = "Available commands: /who (list users), /quit (disconnect)"
                    if role == 'admin':
                        help_msg += ", /kick <user>, /announce <msg>"
                    await websocket.send(f"System: {help_msg}")

                elif command == '/who':
                    user_list = ", ".join(text_chat_clients.keys())
                    await websocket.send(f"System: Users online: {user_list}")

                elif command == '/quit':
                    break # Exit the loop and disconnect

                elif command == '/kick' and role == 'admin' and len(parts) >= 2:
                    target_user = parts[1]
                    if target_user in text_chat_clients:
                        target_ws = text_chat_clients.pop(target_user)
                        await target_ws.send("System: You have been kicked by an admin.")
                        await target_ws.close()
                        await broadcast(f"System: Admin {username} kicked {target_user}.")
                    else:
                        await websocket.send(f"System: User '{target_user}' not found.")

                elif command == '/announce' and role == 'admin' and len(parts) >= 2:
                    announcement = parts[2] if len(parts) > 2 else parts[1] # Allow multi-word announcements
                    await broadcast(f"Announcement: {announcement}")
                
                else:
                    await websocket.send(f"System: Unknown command or insufficient privileges: {command}")

            else:
                # Regular message
                full_message = f"{username}: {message}"
                await broadcast(full_message)

    except websockets.exceptions.ConnectionClosedOK:
        logger.info(f"Chat client disconnected gracefully: {username}")
    except Exception as e:
        logger.error(f"Chat error for {username}: {e}", exc_info=True)
    finally:
        # Cleanup on disconnect. This block is guaranteed to run.
        if username and username in text_chat_clients:
            del text_chat_clients[username]
            # Must await the broadcast as it's an async operation
            await broadcast(f"System: {username} has left the chat.")


# ============================================================================
# WEBRTC HANDLER (HTTP POST)
# ============================================================================

async def offer(request):
    """Handles WebRTC SDP Offer from client and returns an Answer."""
    session = await get_session(request)
    if 'username' not in session:
        return web.HTTPUnauthorized()

    try:
        params = await request.json()
        offer_description = RTCSessionDescription(
            sdp=params["sdp"], 
            type=params["type"]
        )
        
        peer_connection = RTCPeerConnection()
        # Use username and a UUID to create a unique peer identifier
        peer_id = f"{session['username']}_{uuid.uuid4()}" 

        # Add existing tracks (senders) from other peers to this new peer
        all_relayed_tracks = []
        for other_peer in webrtc_peers.values():
            for sender in other_peer.getSenders():
                if sender.track:
                    # Subscribe to the track (MediaRelay allows multiple subscribers)
                    all_relayed_tracks.append(relay.subscribe(sender.track))
        
        # Add all currently active relayed tracks to the new peer connection
        for track in all_relayed_tracks:
            peer_connection.addTrack(track)
        
        webrtc_peers[peer_id] = peer_connection
        
        logger.info(f"Created new WebRTC peer connection: {peer_id}")

        @peer_connection.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            logger.info("ICE connection state is %s for peer %s", 
                        peer_connection.iceConnectionState, peer_id)
            if peer_connection.iceConnectionState == "failed":
                await peer_connection.close()
                webrtc_peers.pop(peer_id, None)
            elif peer_connection.iceConnectionState == "closed":
                webrtc_peers.pop(peer_id, None)

        @peer_connection.on("track")
        def on_track(track):
            """
            Called when a new media track (audio/video) is received from the client.
            We relay this track to all other connected peers.
            """
            logger.info("Track %s received from peer %s", track.kind, peer_id)
            
            # Subscribe the new track to the relay
            relayed_track = relay.subscribe(track)
            
            # Add this new track to all *other* existing peers
            for other_peer_id, other_peer in webrtc_peers.items():
                if other_peer_id != peer_id:
                    try:
                        # Add the relayed track to the other peer's connection
                        other_peer.addTrack(relayed_track)
                        logger.info(f"Relaying new track from {peer_id} to {other_peer_id}")
                    except Exception as e:
                        logger.warning(f"Error relaying track to {other_peer_id}: {e}")

        # Set remote description (the offer) and create answer
        await peer_connection.setRemoteDescription(offer_description)
        answer = await peer_connection.createAnswer()
        await peer_connection.setLocalDescription(answer)

        return web.json_response({
            "sdp": peer_connection.localDescription.sdp,
            "type": peer_connection.localDescription.type,
        })
        
    except Exception as e:
        logger.error("WebRTC offer handler error: %s", e, exc_info=True)
        return web.Response(text=f"Error processing offer: {e}", status=500)


async def healthcheck(request):
    """Simple endpoint to check server status."""
    return web.Response(text="OK")


# ============================================================================
# MAIN APPLICATION SETUP
# ============================================================================

async def main(host='0.0.0.0', port=8080):
    """Sets up and starts the HTTP and WebSocket servers."""
    
    # 1. Initialize Database
    await init_db()

    # 2. Setup aiohttp session middleware (encrypted cookies)
    # The SECRET_KEY is used to encrypt the session data (cookie)
    storage = EncryptedCookieStorage(SECRET_KEY, cookie_name='session_id')
    
    # 3. Setup CORS middleware
    cors = web.middleware.cors_middleware(
        allow_all=True  # In production, replace with specific origins
    )
    
    # 4. Setup Application and Routes
    app = web.Application(middlewares=[cors, setup_session(storage)])
    
    # Authentication routes
    app.router.add_get("/login", login_page)
    app.router.add_post("/login", handle_login)
    app.router.add_get("/register", register_page)
    app.router.add_post("/register", handle_register)
    app.router.add_get("/logout", handle_logout)
    
    # Main application and API routes
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer) # WebRTC signaling
    app.router.add_get("/user_data", get_user_data) # Get user info for client
    app.router.add_get("/health", healthcheck)
    
    # Static file serving for CSS and JS
    app.router.add_static("/dist", "./dist")
    
    # 5. Start HTTP Server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"HTTP server started on http://{host}:{port}")
    
    # 6. Start WebSocket Server for text chat
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
    logger.info(f"- App: http://localhost:{port}")
    logger.info(f"- WebSocket: ws://localhost:{ws_port}")
    
    # Keep the server running
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        # Default ports for WebRTC (8080) and Chat WS (8081)
        asyncio.run(main(port=8080))
    except KeyboardInterrupt:
        logger.info("Server shut down.")
    except Exception as e:
        logger.error(f"Fatal error in main execution: {e}", exc_info=True)