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
# FIX 1: Import aiohttp_cors components directly
from aiohttp_cors import cors_middleware, ResourceOptions, ALL

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
            original_username = username
            counter = 1
            while username in text_chat_clients:
                username = f"{original_username}#{counter}"
                counter += 1
            
            await websocket.send(f"WARNING: Username {original_username} taken. Joined as {username}")

        text_chat_clients[username] = websocket
        logger.info(f"Chat client connected: {username}")
        
        # Send a welcome message to the new user and broadcast join message
        await websocket.send(f"Welcome to the chat, {username}!")
        await broadcast_chat_message(f"**{username}** has joined the room.")
        
        # Main message loop
        async for message in websocket:
            await broadcast_chat_message(f"**{username}**: {message}")
            
    except websockets.exceptions.ConnectionClosedOK:
        logger.info(f"Chat client disconnected (normal): {username}")
    except Exception as e:
        logger.error(f"Chat client disconnected (error): {username} - {e}")
    finally:
        if username in text_chat_clients:
            del text_chat_clients[username]
            # Broadcast the leave message only if they were successfully registered
            if username:
                 await broadcast_chat_message(f"**{username}** has left the room.")

async def broadcast_chat_message(message):
    """Broadcasts a message to all connected chat clients."""
    # Ensure all broadcast messages are JSON strings for consistency (though currently only text is used)
    # This prepares for future structured message support.
    
    # We will send the message as a raw string for simplicity matching the client implementation.
    disconnected_clients = []
    
    # Create a list of send tasks
    send_tasks = []
    for username, ws in text_chat_clients.items():
        send_tasks.append(ws.send(message))

    # Execute all send tasks concurrently
    if send_tasks:
        results = await asyncio.gather(*send_tasks, return_exceptions=True)
        
        # Check results for connection errors
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                # An error occurred (e.g., connection closed)
                client_username = list(text_chat_clients.keys())[i]
                disconnected_clients.append(client_username)
                logger.warning(f"Failed to send message to {client_username}: {result}")
    
    # Clean up disconnected clients
    for username in disconnected_clients:
        if username in text_chat_clients:
            del text_chat_clients[username]
            await broadcast_chat_message(f"**{username}** was disconnected.")
            logger.info(f"Cleaned up disconnected chat client: {username}")

# ============================================================================
# WEBRTC HANDLERS
# ============================================================================

async def index(request):
    """Serve the main client HTML page."""
    with open('client.html', 'r') as f:
        return web.Response(text=f.read(), content_type='text/html')

async def healthcheck(request):
    """Basic health check endpoint."""
    return web.Response(text="OK", content_type='text/plain')

async def offer(request):
    """Handle WebRTC SDP offers."""
    params = await request.json()
    offer = RTCSessionDescription(
        sdp=params["sdp"],
        type=params["type"])

    pc = RTCPeerConnection()
    peer_id = str(uuid.uuid4())
    webrtc_peers[peer_id] = pc

    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        logger.info(f"ICE connection state is now {pc.iceConnectionState} for peer {peer_id}")
        if pc.iceConnectionState == "failed":
            await pc.close()
            if peer_id in webrtc_peers:
                del webrtc_peers[peer_id]

    @pc.on("track")
    def on_track(track):
        logger.info(f"Track {track.kind} received for peer {peer_id}")
        
        # Handle media tracks here if you intend to process or re-stream them.
        # For a simple mesh call, this is where you might relay the tracks
        # or just acknowledge them.

        if track.kind == "audio":
            # For audio, you might not do anything specific here for a simple conference
            pass
        elif track.kind == "video":
            # For video, you might relay it if you were building an SFU/MCU, 
            # but for a simple mesh, the client handles it.
            pass

        @track.on("ended")
        async def on_ended():
            logger.info(f"Track {track.kind} ended for peer {peer_id}")
            # Clean up logic for track end
    
    # Set the remote offer and create an answer
    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.Response(
        content_type="application/json",
        text=json.dumps({
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type,
            "peer_id": peer_id
        }),
    )

async def setup_server(host, port):
    """Set up the aiohttp HTTP and websockets chat server."""
    
    # FIX 2: Correct usage of cors_middleware by calling it directly.
    # We define the CORS settings here. Since no specific origins are configured,
    # we allow all origins for development/testing, but this should be restricted
    # in a real production environment.
    cors = cors_middleware(
        defaults={
            # Allow all origins (replace with specific origins for production)
            "*": ResourceOptions(
                allow_headers=("*",),
                allow_methods=("GET", "POST", "OPTIONS"),
                allow_credentials=True,
                max_age=3600
            )
        }
    )
    
    # Start the HTTP server to serve client.html
    app = web.Application(middlewares=[cors])
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)
    app.router.add_get("/health", healthcheck)
    
    # Add static file serving for CSS and JS files
    app.router.add_static("/dist", "./dist")
    app.router.add_static("/", ".") # Serve root static files like script.js

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
    # Load environment variables (like PORT)
    from dotenv import load_dotenv
    load_dotenv()

    # Determine host and port from environment or use defaults
    host = os.environ.get("HOST", "0.0.0.0")
    # For Render, the server must bind to the port defined by the PORT environment variable
    # We default to 8080 if not found, but Render will set this automatically.
    port_str = os.environ.get("PORT", "8080") 
    
    try:
        port = int(port_str)
    except ValueError:
        logger.error(f"Invalid PORT environment variable: {port_str}. Using default 8080.")
        port = 8080
    
    # Check for a SECRET_KEY for production use
    if os.environ.get("SECRET_KEY") is None:
        logger.warning("SECRET_KEY not found in .env. Generating one-time key. SET SECRET_KEY for production!")
        # Generate a random 32-byte key
        os.environ["SECRET_KEY"] = uuid.uuid4().hex

    try:
        # Check if the server is running in a Render environment
        if os.environ.get("RENDER"):
            # Render needs the server to be non-blocking on start and rely on the platform
            logger.info("Running in Render environment. Using provided PORT.")

        # The chat WebSocket port will be port + 1, assuming the environment allows this.
        # In single-port environments like Render, this may require specific configuration 
        # or a separate service, but for local testing, this is fine.
        asyncio.run(setup_server(host, port))

    except KeyboardInterrupt:
        logger.info("Server stopped by user (Ctrl+C)")
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")