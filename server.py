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
import aiohttp_cors # Changed to direct import of the module
from aiohttp_cors import ResourceOptions # Kept ResourceOptions, removed ALL

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
            username = f"{username}_{uuid.uuid4().hex[:4]}"
            
        text_chat_clients[username] = websocket
        logger.info(f"Client '{username}' connected to text chat.")
        
        # Send join message to everyone
        join_message = json.dumps({"type": "chat", "sender": "System", "content": f"'{username}' has joined the chat."})
        await broadcast_chat_message(join_message)

        # Main receive loop
        async for message in websocket:
            try:
                data = json.loads(message)
                if data.get("type") == "chat" and data.get("content"):
                    chat_message = json.dumps({"type": "chat", "sender": username, "content": data["content"]})
                    await broadcast_chat_message(chat_message, current_client=websocket)
            except json.JSONDecodeError:
                logger.error(f"Invalid JSON message from {username}: {message}")
            except Exception as e:
                logger.error(f"Error processing message from {username}: {e}")
                
    except websockets.exceptions.ConnectionClosedOK:
        logger.info(f"Client '{username}' disconnected normally.")
    except Exception as e:
        logger.error(f"Text chat handler error for {username}: {e}")
    finally:
        if username and username in text_chat_clients:
            del text_chat_clients[username]
            logger.info(f"Client '{username}' removed from text chat list.")
            # Send leave message to everyone
            leave_message = json.dumps({"type": "chat", "sender": "System", "content": f"'{username}' has left the chat."})
            # A broadcast here may fail if the server is shutting down.
            asyncio.create_task(broadcast_chat_message(leave_message))

async def broadcast_chat_message(message, current_client=None):
    """Broadcasts a message to all connected chat clients."""
    disconnected_clients = []
    for username, websocket in text_chat_clients.items():
        if websocket.open:
            if websocket != current_client:
                try:
                    await websocket.send(message)
                except websockets.exceptions.ConnectionClosed:
                    disconnected_clients.append(username)
                except Exception as e:
                    logger.error(f"Error sending broadcast to {username}: {e}")
        else:
            disconnected_clients.append(username)
            
    # Clean up disconnected clients (handled by the finally block in text_chat_handler, but good practice)
    for username in disconnected_clients:
        if username in text_chat_clients:
            del text_chat_clients[username]


# ============================================================================
# HTTP AND WEBRTC HANDLERS
# ============================================================================

async def index(request):
    """Serve the main client HTML page."""
    with open("client.html", "r") as f:
        html_content = f.read()
    return web.Response(text=html_content, content_type="text/html")

async def healthcheck(request):
    """Simple health check endpoint."""
    return web.Response(text="OK", content_type="text/plain")

async def offer(request):
    """Handles WebRTC offers from the client."""
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    # Create a new peer connection
    peer_id = str(uuid.uuid4())
    pc = RTCPeerConnection()
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
        logger.info(f"Track {track.kind} received from peer {peer_id}")
        if track.kind == "video":
            # Add the track to a local relay to be forwarded back (simple loopback/forwarding)
            pc.addTrack(relay.subscribe(track))

        @track.on("ended")
        async def on_ended():
            logger.info(f"Track {track.kind} ended for peer {peer_id}")
            # Note: Track ended logic for cleanup is complex, simplified here.

    # Set remote description and create answer
    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.json_response(
        {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type, "peerId": peer_id}
    )

# ============================================================================
# MAIN SERVER LOGIC
# ============================================================================

async def start_http_server(host, port):
    """Initializes and starts the HTTP server with routes and CORS."""
    
    # Start the HTTP server to serve client.html
    # Initialize the app without the old middleware argument
    app = web.Application()

    # Configure CORS using aiohttp_cors.setup() (FIX FOR IMPORTERROR)
    cors = aiohttp_cors.setup(app, defaults={
        # Allow all origins for simplicity (NOT SAFE FOR PROD!)
        "*": ResourceOptions(
            allow_credentials=True,
            allow_headers=("X-Requested-With", "Content-Type", "Authorization"),
            allow_methods="*",
        )
    })
    
    # Setup the routes
    index_route = app.router.add_get("/", index)
    offer_route = app.router.add_post("/offer", offer)
    health_route = app.router.add_get("/health", healthcheck)
    
    # Apply CORS to the routes (This is necessary when using aiohttp_cors.setup)
    cors.add(index_route)
    cors.add(offer_route)
    cors.add(health_route)
    
    # Add static file serving for CSS and JS files
    app.router.add_static("/dist", "./dist")
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"HTTP server started on http://{host}:{port}")
    return app # Return app object for potential cleanup

async def main():
    """Main entry point to start all servers."""
    
    # Configuration
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8080))
    
    logger.info("Starting RealtimeConnect Server...")

    # Start the HTTP server
    http_app = await start_http_server(host, port)
    
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
        logger.info("Server shut down manually.")
    except Exception as e:
        logger.error(f"Fatal server error: {e}")