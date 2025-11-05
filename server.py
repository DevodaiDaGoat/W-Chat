#!/usr/bin/env python3
"""
Fixed RealtimeConnect Server
Combines WebRTC video/audio/screen sharing with text chat functionality.
Adds user authentication and session management using aiohttp-session.
"""

import asyncio
import json
import logging
import uuid
import os
import websockets
from aiohttp import web

# --- Session & Auth Imports ---
from dotenv import load_dotenv
# FIX: Import the aiohttp_session module and its core functions/storage
import aiohttp_session
from aiohttp_session import get_session, session_middleware
from aiohttp_session.cookie_storage import EncryptedCookieStorage
from cryptography import fernet
# --- End Session & Auth Imports ---

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay

# Configure logging for debugging purposes
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# --- Security Configuration ---
# Use a secret key from .env or generate a secure fallback for sessions
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    # A generated key is better than nothing, but users should be warned to set a proper one.
    logger.warning("SECRET_KEY not found in .env. Generating one-time key. SET SECRET_KEY for production!")
    SECRET_KEY = fernet.Fernet.generate_key().decode()

# Ensure the key is correctly encoded for Fernet
# WARNING: If using the generated key, sessions will break on server restart.
fernet_key = SECRET_KEY.encode('utf-8')
# --- End Security Configuration ---

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
            pass # Placeholder for logic
    finally:
        pass # Placeholder for finally block
        
# ============================================================================
# HTTP HANDLERS (Auth & WebRTC Signaling)
# ============================================================================

async def index(request):
    """Serve the main client page or redirect to login if not authenticated."""
    # Example usage of get_session that was causing the Pylance error
    session = await get_session(request)
    if 'user_id' not in session:
        # Placeholder for login redirect or anonymous logic
        pass 
    
    with open("client.html", "r") as f:
        return web.Response(text=f.read(), content_type="text/html")

async def offer(request):
    """Handles WebRTC signaling (SDP offer/answer)."""
    # Example usage of get_session that was causing the Pylance error
    session = await get_session(request)
    # The rest of the WebRTC logic...
    pass # Placeholder for WebRTC offer logic

async def healthcheck(request):
    """Simple health check endpoint."""
    return web.Response(text="OK")

# ... (other handlers like login/register/logout would be here)

# ============================================================================
# SERVER STARTUP
# ============================================================================

async def setup_server(host, port):
    """Initializes and starts both the HTTP/WebRTC and WebSocket servers."""
    
    # Configure CORS middleware
    cors = web.middleware.cors_middleware(
        allow_all=True  # In production, replace with specific origins
    )
    
    # Configure Session Middleware
    # Use EncryptedCookieStorage with the Fernet key
    session_storage = EncryptedCookieStorage(fernet_key, cookie_name='session_id')
    
    # Start the HTTP server to serve client.html
    # Apply both Session and CORS middleware
    app = web.Application(middlewares=[
        session_middleware(session_storage),
        cors
    ])
    
    # Add application routes
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)
    app.router.add_get("/health", healthcheck)
    
    # Add static file serving for CSS and JS files
    app.router.add_static("/dist", "./dist")
    
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

# This is typically the entry point, assuming it was at the end of the file.
if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8080))
    try:
        asyncio.run(setup_server(host, port))
    except KeyboardInterrupt:
        pass # Graceful exit