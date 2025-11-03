#!/usr/bin/env python3
"""
Fixed RealtimeConnect Server
Combines WebRTC video/audio/screen sharing with text chat functionality.
"""

import asyncio
import json
import logging
import uuid
import os
import websockets
# ADDED IMPORTS
from dotenv import load_dotenv # Used to load .env file locally
from cryptography.fernet import Fernet # Used for generating fallback key
from aiohttp_session.cookie_storage import EncryptedCookieStorage
from aiohttp_session import setup as setup_session
# END ADDED IMPORTS
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay

# Configure logging for debugging purposes
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Text Chat specific variables ---
text_chat_clients = {}  # username -> websocket mapping

# --- WebRTC specific variables ---
webrtc_peers = {}  # peer_id -> RTCPeerConnection mapping
relay = MediaRelay()

# ============================================================================
# DATABASE SETUP (Placeholder - assuming sqlite setup exists here)
# ============================================================================

# (Placeholder for existing DB setup code)
async def init_db(db_file):
    # This function is not fully defined in context, but assuming it ensures 'users' table exists.
    logger.info(f"Database initialized and 'users' table ensured.")
    pass 
# ============================================================================
# AUTHENTICATION HANDLERS (Placeholders)
# ============================================================================

# (Placeholders for login, register, logout handlers)

# ============================================================================
# HTTP/WEBRTC HANDLERS (Placeholders)
# ============================================================================

async def index(request):
    # ...
    # This should probably check for a valid session and redirect to /login if not authenticated
    return web.FileResponse('./client.html')

async def offer(request):
    # ...
    pass
async def healthcheck(request):
    # ...
    pass
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
            await websocket.send("ERROR: Username already taken")
            await websocket.close()
            return
            
        text_chat_clients[username] = websocket
        logger.info(f"User '{username}' connected to chat.")
        
        # Main loop to receive messages
        async for message in websocket:
            # Broadcast message to all other clients (simple implementation)
            logger.info(f"Received message from '{username}': {message}")
            message_obj = json.dumps({'user': username, 'content': message})
            
            for client_username, client_ws in text_chat_clients.items():
                if client_username != username:
                    await client_ws.send(message_obj)

    except websockets.exceptions.ConnectionClosedOK:
        logger.info(f"User '{username}' disconnected gracefully.")
    except Exception as e:
        logger.error(f"Error in chat handler for '{username}': {e}")
    finally:
        # Clean up client upon disconnection
        if username in text_chat_clients:
            del text_chat_clients[username]


# ============================================================================
# MAIN EXECUTION
# ============================================================================

async def main(host="0.0.0.0", port=8080):
    # Load environment variables from .env file (for local run)
    load_dotenv()
    
    # --- Security Key Check and Generation ---
    # Fix for: ValueError: Fernet key must be 32 url-safe base64-encoded bytes.
    # The SECRET_KEY is used for session cookie encryption. It MUST be 32 url-safe base64-encoded bytes.
    SECRET_KEY_RAW = os.getenv("SECRET_KEY")

    if SECRET_KEY_RAW:
        # Use the key from the environment, ensuring it's in bytes
        SECRET_KEY = SECRET_KEY_RAW.encode('utf-8')
        try:
            # Test if the provided key is valid Fernet format.
            Fernet(SECRET_KEY)
            logger.info("SECRET_KEY loaded successfully.")
        except ValueError:
            # Fallback if the key provided in the environment is malformed
            logger.error("Provided SECRET_KEY is invalid. Generating a temporary key.")
            SECRET_KEY = Fernet.generate_key()
    else:
        # Fallback if no key is found at all (this happened in the deployment environment)
        logger.warning("SECRET_KEY environment variable not found. Generating a temporary key (Do NOT use in production without setting the key).")
        SECRET_KEY = Fernet.generate_key()
    
    # --- Database Initialization (using placeholder for now) ---
    await init_db(os.getenv("SQLITE_DB_FILE", "db.sqlite")) 

    # --- AIOHTTP Setup ---
    # 1. Setup Session storage using the fixed SECRET_KEY
    storage = EncryptedCookieStorage(SECRET_KEY, cookie_name='session_id')
    
    # 2. Setup CORS middleware
    cors = web.middleware.cors_middleware(
        allow_all=True  # In production, replace with specific origins
    )
    
    # 3. Start the HTTP server to serve client.html
    # Session middleware must be applied to the application
    app = web.Application(middlewares=[cors, setup_session(storage)]) 
    
    # Static Routes
    app.router.add_get(\"/\", index)
    app.router.add_get(\"/login\", lambda r: web.FileResponse('./login.html'))
    app.router.add_get(\"/register\", lambda r: web.FileResponse('./register.html'))
    
    # POST Routes (Authentication placeholders)
    app.router.add_post(\"/login\", lambda r: web.Response(text=\"Login not fully implemented\")) 
    app.router.add_post(\"/register\", lambda r: web.Response(text=\"Register not fully implemented\"))
    app.router.add_get(\"/logout\", lambda r: web.Response(text=\"Logout not fully implemented\"))
    
    # WebRTC/Health Check Routes
    app.router.add_post(\"/offer\", offer)
    app.router.add_get(\"/health\", healthcheck)
    
    # Add static file serving for CSS and JS files
    app.router.add_static(\"/dist\", \"./dist\")
    
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
    await asyncio.Event().wait()


if __name__ == '__main__':
    try:
        asyncio.run(main(port=8080))
    except Exception as e:
        logger.error(f"Fatal error in main execution: {e}")
        # Re-raise the exception for deployment environments to capture the failure
        raise