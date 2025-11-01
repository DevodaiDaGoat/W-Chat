import asyncio
import json
import logging
import uuid
import os
import websockets

from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay, MediaPlayer

# Configure logging for debugging purposes
logging.basicConfig(level=logging.INFO)

# --- Text Chat specific variables ---
text_chat_clients = {}

# --- WebRTC specific variables ---
webrtc_peers = {}
relay = MediaRelay()


async def text_chat_handler(websocket):
    """Handles WebSocket connections for text chat."""
    try:
        username = await websocket.recv()
        text_chat_clients[username] = websocket
        logging.info(f'Text chat connection opened for {username}')
        await broadcast_text_message(f"User {username} has joined the chat.")

        async for message in websocket:
            if message.startswith('/w '):
                parts = message.split(' ', 2)
                if len(parts) >= 3:
                    _, recipient, dm_content = parts
                    await send_direct_text_message(username, recipient, dm_content)
                else:
                    await send_direct_text_message('server', username, "Invalid private message format.")
            else:
                await broadcast_text_message(f"{username}: {message}")

    except websockets.exceptions.ConnectionClosed:
        logging.info(f'Text chat connection closed for {username}')
        if username in text_chat_clients:
            del text_chat_clients[username]
            await broadcast_text_message(f"User {username} has left the chat.")
    finally:
        if username in text_chat_clients:
            del text_chat_clients[username]


async def broadcast_text_message(message):
    """Sends a message to all connected text clients."""
    if text_chat_clients:
        await asyncio.gather(*[client.send(message) for client in text_chat_clients.values()])

async def send_direct_text_message(sender, recipient, message):
    """Sends a private message to a specific user."""
    if recipient in text_chat_clients:
        await text_chat_clients[recipient].send(f"[DM from {sender}]: {message}")
        if sender != 'server':
            await text_chat_clients[sender].send(f"[DM to {recipient}]: {message}")
    elif sender != 'server' and sender in text_chat_clients:
        await text_chat_clients[sender].send(f"User {recipient} is not online.")

# --- aiohttp and WebRTC Handlers ---
async def index(request):
    """Serves the client.html file."""
    with open("client.html") as f:
        return web.Response(text=f.read(), content_type="text/html")

async def offer(request):
    """
    Handles WebRTC signaling (offers and answers).
    """
    params = await request.json()
    offer_description = RTCSessionDescription(
        sdp=params["sdp"], type=params["type"]
    )
    
    peer_connection = RTCPeerConnection()
    peer_id = str(uuid.uuid4())
    webrtc_peers[peer_id] = peer_connection
    
    # Store peer ID in request for easier cleanup
    request.transport_peer_id = peer_id

    @peer_connection.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        logging.info(
            "ICE connection state is %s for peer %s",
            peer_connection.iceConnectionState,
            peer_id,
        )
        if peer_connection.iceConnectionState == "closed":
            await peer_connection.close()
            webrtc_peers.pop(peer_id, None)

    @peer_connection.on("track")
    def on_track(track):
        logging.info("Track %s received from peer %s", track.kind, peer_id)
        # Add logic here to broadcast track to all other peers if necessary
    
    await peer_connection.setRemoteDescription(offer_description)
    await peer_connection.setLocalDescription(await peer_connection.createAnswer())

    return web.json_response(
        {"sdp": peer_connection.localDescription.sdp, "type": peer_connection.localDescription.type, "id": peer_id}
    )

async def start_server():
    """Starts both the HTTP server for the client page and the WebSocket server."""
    
    # Use PORT from environment or default to 3000 (common cloud platform default)
    port = int(os.environ.get("PORT", 3000))
    # Use HOST from environment or default to localhost for development
    host = os.environ.get("HOST", "0.0.0.0")
    
    # Configure CORS for production
    cors = web.middleware.cors_middleware(
        allow_all=True  # In production, replace with specific origins
    )
    
    # Start the HTTP server to serve client.html
    app = web.Application(middlewares=[cors])
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)
    
    # Add health check endpoint for cloud platforms
    async def healthcheck(request):
        return web.Response(text="OK", status=200)
    app.router.add_get("/health", healthcheck)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logging.info(f"HTTP server started on http://{host}:{port}")
    
    ws_port = port + 1
    websocket_server = await websockets.serve(text_chat_handler, host, ws_port)
    logging.info(f"WebSocket server for text chat started on ws://{host}:{ws_port}")
    
    logging.info(f"Server is ready. Access locally at:")
    logging.info(f"- HTTP: http://localhost:{port}")
    logging.info(f"- WebSocket: ws://localhost:{ws_port}")
    
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(start_server())
