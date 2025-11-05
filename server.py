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
from aiohttp_cors import setup, ResourceOptions, ALL # FIXED: Imported 'setup' instead of 'cors_middleware'

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
            await websocket.send(f"WARNING: Username '{original_username}' taken. Assigned you: '{username}'")

        text_chat_clients[username] = websocket
        logger.info(f"Text chat connected: {username}. Total clients: {len(text_chat_clients)}")
        
        # Broadcast connection message
        await broadcast_chat_message("SYSTEM", f"{username} has joined the chat.")
        
        # Listen for chat messages
        async for message in websocket:
            await broadcast_chat_message(username, message)
            
    except websockets.exceptions.ConnectionClosedOK:
        logger.info(f"Text chat disconnected gracefully: {username}")
    except Exception as e:
        logger.error(f"Text chat error for {username}: {e}", exc_info=True)
    finally:
        # Clean up client connection
        if username in text_chat_clients:
            del text_chat_clients[username]
            logger.info(f"Text chat client removed: {username}. Remaining: {len(text_chat_clients)}")
            asyncio.create_task(broadcast_chat_message("SYSTEM", f"{username} has left the chat."))

async def broadcast_chat_message(sender, content):
    """Sends a chat message to all connected clients."""
    if not text_chat_clients:
        return

    message = json.dumps({"sender": sender, "content": content})
    
    # Create a list of send tasks
    send_tasks = [client.send(message) for client in text_chat_clients.values()]
    
    # Wait for all sends to complete, gathering exceptions
    await asyncio.gather(*send_tasks, return_exceptions=True)

# ============================================================================\
# WEB-RTC HANDLERS
# ============================================================================\

async def index(request):
    """Serve the main client HTML page."""
    # This server does not handle authentication logic, it redirects to client for now
    with open(os.path.join(os.getcwd(), "client.html"), 'r') as f:
        html_content = f.read()
    return web.Response(text=html_content, content_type="text/html")

async def offer(request):
    """Handle the WebRTC offer/answer negotiation."""
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    # Create a new PeerConnection for the incoming offer
    pc = RTCPeerConnection()
    peer_id = str(uuid.uuid4())
    webrtc_peers[peer_id] = pc

    # Event handler for connection state changes
    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info(f"PeerConnection {peer_id} state is {pc.connectionState}")
        if pc.connectionState == "failed":
            await pc.close()
            webrtc_peers.pop(peer_id, None)
        elif pc.connectionState == "closed":
            webrtc_peers.pop(peer_id, None)
            logger.info(f"PeerConnection {peer_id} removed.")

    # Event handler for new tracks added from the remote peer
    @pc.on("track")
    def on_track(track):
        # We don't need to do anything with remote tracks yet, but we log it
        logger.info(f"Track {track.kind} received from {peer_id}")

    # Set the remote description (the offer)
    await pc.setRemoteDescription(offer)

    # Create the answer
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    # Return the answer SDP
    return web.Response(
        content_type="application/json",
        text=json.dumps({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}),
    )

async def healthcheck(request):
    """Basic health check endpoint."""
    return web.Response(text="OK")


# ============================================================================
# MAIN APPLICATION SETUP
# ============================================================================

async def main(host, port):
    """Main entry point to start the aiohttp and websockets servers."""
    
    # Start the HTTP server to serve client.html
    # Removed 'middlewares=[cors]' here to use the modern setup pattern
    app = web.Application() 

    # Add routes first
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)
    app.router.add_get("/health", healthcheck)
    
    # Add static file serving for CSS and JS files
    app.router.add_static("/dist", "./dist")

    # Configure CORS using the modern aiohttp-cors setup pattern
    cors = setup(app, defaults={
        "*": ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods="*",
        )
    })

    # Apply CORS to all routes
    for route in list(app.router.routes()):
        cors.add(route)
        
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
    HOST = os.getenv("HOST", "0.0.0.0")
    # For Render/Heroku/etc., PORT is usually provided as an environment variable
    # Fallback to 8080 for local development if not provided
    PORT = int(os.getenv("PORT", 8080))
    
    try:
        asyncio.run(main(HOST, PORT))
    except KeyboardInterrupt:
        print("\nServer shutting down gracefully.")
    except Exception as e:
        logger.error(f"A fatal error occurred: {e}", exc_info=True)