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
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay

# --- Added imports for session management and environment loading ---
from dotenv import load_dotenv
from cryptography import fernet
from aiohttp_session import session_middleware
from aiohttp_session.cookie_storage import EncryptedCookieStorage
import aiohttp_cors # Needed for the middleware setup below
# -------------------------------------------------------------------

# Load environment variables from .env file (if available)
load_dotenv()

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
            # This section would contain logic to handle chat connections
            pass
        
        # Placeholder for chat loop
        
    except websockets.exceptions.ConnectionClosedOK:
        logger.info(f"Chat client {username} disconnected normally.")
    except Exception as e:
        logger.error(f"Chat error for {username}: {e}")
    finally:
        if username and username in text_chat_clients:
            del text_chat_clients[username]
            logger.info(f"User {username} removed from chat clients.")
            # Broadcast disconnect message
            disconnect_message = json.dumps({"type": "message", "user": "System", "content": f"User {username} has left the chat."})
            await broadcast_chat_message(disconnect_message)


async def broadcast_chat_message(message: str):
    """Broadcasts a message to all connected chat clients."""
    disconnected_clients = []
    for username, ws in text_chat_clients.items():
        try:
            await ws.send(message)
        except websockets.exceptions.ConnectionClosed:
            disconnected_clients.append(username)
        except Exception as e:
            logger.error(f"Error broadcasting to {username}: {e}")

    for username in disconnected_clients:
        if username in text_chat_clients:
            del text_chat_clients[username]
            logger.info(f"Cleaned up disconnected chat client: {username}")
# ============================================================================
# WEBRTC SIGNALING & MEDIA HANDLERS
# ============================================================================

async def index(request):
    """Serves the main client HTML page (Meeting/Chat UI)."""
    return web.FileResponse('./client.html')

async def healthcheck(request):
    """Simple health check endpoint."""
    return web.Response(text="OK")

# Utility function to get secret key, generating a temporary one if missing
def get_secret_key():
    """Retrieves SECRET_KEY from environment or generates a temporary one."""
    key = os.environ.get('SECRET_KEY')
    if not key:
        logger.warning("SECRET_KEY not found. Generating a temporary key. USE ENVIRONMENT VARIABLE IN PRODUCTION.")
        key = uuid.uuid4().hex
    return key


# Mapping of peer_id to the tracks we have received from them
peer_tracks = {}

async def offer(request):
    """Handles the WebRTC offer/answer signaling."""
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
    
    # Generate a unique ID for the new peer connection
    peer_id = str(uuid.uuid4())
    pc = RTCPeerConnection()
    webrtc_peers[peer_id] = pc
    
    # Store the offerer's ID to keep track of their tracks
    peer_tracks[peer_id] = []
    
    logger.info(f"Created RTCPeerConnection for {peer_id}")

    @pc.on("datachannel")
    def on_datachannel(channel):
        """Handle data channel messages (e.g., text chat over WebRTC, control signals)."""
        @channel.on("message")
        def on_message(message):
            if isinstance(message, str):
                logger.info(f"DataChannel message from {peer_id}: {message}")
                # Placeholder for potential WebRTC-based chat/control messages

    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        """Log changes in ICE connection state."""
        logger.info(f"ICE connection state for {peer_id} is {pc.iceConnectionState}")
        if pc.iceConnectionState == "failed":
            await pc.close()
            if peer_id in webrtc_peers:
                del webrtc_peers[peer_id]
                logger.info(f"Cleaned up failed peer connection: {peer_id}")
            if peer_id in peer_tracks:
                del peer_tracks[peer_id]


    @pc.on("track")
    def on_track(track):
        """Handle incoming media tracks (audio/video/screenshare)."""
        logger.info(f"Track {track.kind} received from {peer_id}")
        
        if track.kind == "audio" or track.kind == "video":
            # The relay is used to send the track back to the peer
            # and potentially to other peers in a full mesh.
            pc.addTrack(relay.subscribe(track))

        @track.on("ended")
        async def on_ended():
            logger.info(f"Track {track.kind} from {peer_id} ended")
            # Cleanup logic here if necessary
            

    # Set the remote description (the offer)
    await pc.setRemoteDescription(offer)

    # Create the answer
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    # Return the answer SDP to the client
    return web.Response(
        content_type="application/json",
        text=json.dumps(
            {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type, "peer_id": peer_id}
        ),
    )

# ============================================================================
# MAIN SERVER SETUP
# ============================================================================

async def main():
    """Initializes and runs both the HTTP/WebRTC and WebSocket servers."""
    
    # Configuration
    host = os.environ.get('HOST', '0.0.0.0')
    try:
        port = int(os.environ.get('PORT', 8080))
    except ValueError:
        port = 8080
        logger.warning(f"Invalid PORT environment variable. Using default: {port}")
    
    SECRET_KEY = get_secret_key()
    
    # --- FIX START ---
    # The fix for: RuntimeError: Expected AbstractStorage got <cryptography.fernet.Fernet object ...>
    
    # 1. Create the Fernet encryption key object
    fernet_key = fernet.Fernet(SECRET_KEY.encode('utf-8'))
    
    # 2. Wrap the Fernet key in the correct storage object (EncryptedCookieStorage)
    storage = EncryptedCookieStorage(fernet_key, cookie_name='rtconnect_session')
    
    # 3. Create the session middleware factory
    session_mw = session_middleware(storage)
    # --- FIX END ---
    
    # Setup CORS middleware
    # Note: 'app' must be defined before it can be passed to aiohttp_cors.setup() 
    # if you intend to use it as middleware later. We'll define the app first.
    
    # Start the HTTP server to serve client.html
    # Pass both session and CORS middlewares to the application
    app = web.Application(middlewares=[session_mw]) 

    # Setup CORS middleware (must be done after the app object exists)
    cors = aiohttp_cors.setup(
        app,
        defaults={
            "*": aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
                allow_methods="*",
            )
        },
    )

    # Apply CORS to all routes
    for route in list(app.router.routes()):
        if route.method != 'OPTIONS':
            cors.add(route)
    
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


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.critical(f"Fatal error in main execution: {e}", exc_info=True)