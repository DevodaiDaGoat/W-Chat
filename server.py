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
            counter = 1
            base_username = username
            while username in text_chat_clients:
                username = f"{base_username}_{counter}"
                counter += 1
            await websocket.send(f"ASSIGNED_USERNAME:{username}")
        
        text_chat_clients[username] = websocket
        logger.info(f'Text chat connection opened for {username}')
        await broadcast_text_message(f"System: User {username} has joined the chat.")

        async for message in websocket:
            if message.startswith('/w ') or message.startswith('/msg '):
                # Handle private messages
                parts = message.split(' ', 2)
                if len(parts) >= 3:
                    _, recipient, dm_content = parts
                    await send_direct_text_message(username, recipient, dm_content)
                else:
                    await send_direct_text_message('System', username, "Invalid private message format. Use: /msg username message")
            elif message.startswith('/global '):
                # Handle global messages
                content = message[8:]  # Remove '/global ' prefix
                await broadcast_text_message(f"[Global] {username}: {content}")
            elif message.startswith('/r '):
                # Handle reply to last DM (placeholder)
                await send_direct_text_message('System', username, "Reply functionality not yet implemented. Use /msg username message")
            elif message.startswith('/help'):
                # Show help
                help_message = """
Available commands:
/msg <username> <message> - Send private message
/w <username> <message> - Alias for private message  
/global <message> - Send message to all rooms
/help - Show this help message
Click on usernames to start private messages
                """
                await websocket.send(f"System:\n{help_message}")
            else:
                # Regular broadcast message
                await broadcast_text_message(f"{username}: {message}")

    except websockets.exceptions.ConnectionClosed:
        logger.info(f'Text chat connection closed for {username}')
    except Exception as e:
        logger.error(f'Error in text chat handler for {username}: {e}')
    finally:
        if username and username in text_chat_clients:
            del text_chat_clients[username]
            await broadcast_text_message(f"System: User {username} has left the chat.")


async def broadcast_text_message(message):
    """Sends a message to all connected text clients."""
    if text_chat_clients:
        disconnected_clients = []
        
        # Send to all connected clients
        for username, client in text_chat_clients.items():
            try:
                await client.send(message)
            except websockets.exceptions.ConnectionClosed:
                disconnected_clients.append(username)
        
        # Clean up disconnected clients
        for username in disconnected_clients:
            if username in text_chat_clients:
                del text_chat_clients[username]


async def send_direct_text_message(sender, recipient, message):
    """Sends a private message to a specific user."""
    if recipient in text_chat_clients:
        try:
            await text_chat_clients[recipient].send(f"[DM from {sender}]: {message}")
            if sender != 'System' and sender in text_chat_clients:
                await text_chat_clients[sender].send(f"[DM to {recipient}]: {message}")
        except websockets.exceptions.ConnectionClosed:
            # Recipient disconnected
            if recipient in text_chat_clients:
                del text_chat_clients[recipient]
            if sender in text_chat_clients:
                await text_chat_clients[sender].send(f"System: User {recipient} is not online.")
    elif sender != 'System' and sender in text_chat_clients:
        await text_chat_clients[sender].send(f"System: User {recipient} is not online.")


# ============================================================================
# WEBRTC HANDLERS
# ============================================================================

async def index(request):
    """Serves the client.html file."""
    try:
        with open("client.html", "r") as f:
            return web.Response(text=f.read(), content_type="text/html")
    except FileNotFoundError:
        logger.error("client.html not found")
        return web.Response(text="Client file not found", status=404)


async def offer(request):
    """Handles WebRTC signaling (offers and answers)."""
    try:
        params = await request.json()
        logger.info(f"Received WebRTC offer request: {params}")
        
        offer_description = RTCSessionDescription(
            sdp=params["sdp"], 
            type=params["type"]
        )
        
        peer_connection = RTCPeerConnection()
        peer_id = str(uuid.uuid4())
        webrtc_peers[peer_id] = peer_connection
        
        logger.info(f"Created new peer connection: {peer_id}")

        @peer_connection.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            logger.info(
                "ICE connection state is %s for peer %s",
                peer_connection.iceConnectionState,
                peer_id,
            )
            if peer_connection.iceConnectionState == "failed":
                logger.warning(f"ICE connection failed for peer {peer_id}")
                # Don't close immediately, allow for ICE restart
            elif peer_connection.iceConnectionState == "closed":
                logger.info(f"ICE connection closed for peer {peer_id}")
                await peer_connection.close()
                webrtc_peers.pop(peer_id, None)

        @peer_connection.on("track")
        def on_track(track):
            logger.info("Track %s received from peer %s", track.kind, peer_id)
            # Add logic here to broadcast track to all other peers if necessary
            # For now, just log the track reception

        # Set remote description and create answer
        await peer_connection.setRemoteDescription(offer_description)
        answer = await peer_connection.createAnswer()
        await peer_connection.setLocalDescription(answer)

        logger.info(f"Created WebRTC answer for peer {peer_id}")

        return web.json_response({
            "sdp": peer_connection.localDescription.sdp, 
            "type": peer_connection.localDescription.type, 
            "id": peer_id
        })
        
    except Exception as e:
        logger.error(f"Error handling WebRTC offer: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def healthcheck(request):
    """Health check endpoint for cloud platforms."""
    return web.Response(text="OK", status=200)


# ============================================================================
# SERVER STARTUP
# ============================================================================

async def start_server():
    """Starts both the HTTP server for the client page and the WebSocket server."""
    
    # Use PORT from environment or default to 3000 (common cloud platform default)
    port = int(os.environ.get("PORT", 3000))
    # Use HOST from environment or default to localhost for development
    host = os.environ.get("HOST", "0.0.0.0")
    
    logger.info(f"Starting server on {host}:{port}")
    
    # Configure CORS for production
    cors = web.middleware.cors_middleware(
        allow_all=True  # In production, replace with specific origins
    )
    
    # Start the HTTP server to serve client.html
    app = web.Application(middlewares=[cors])
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
        asyncio.run(start_server())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server error: {e}")