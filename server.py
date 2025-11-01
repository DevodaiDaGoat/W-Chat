import asyncio
import json
import logging
import uuid
import os
# websockets package not used for production HTTP WebSocket handling (using aiohttp instead)

from aiohttp import web
import aiohttp_cors
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay, MediaPlayer

# Configure logging for debugging purposes
logging.basicConfig(level=logging.INFO)

# --- Text Chat specific variables ---
text_chat_clients = {}

# --- WebRTC specific variables ---
webrtc_peers = {}
relay = MediaRelay()


# Use aiohttp WebSocket for text chat so the app can run on a single port
async def websocket_handler(request):
    """Handles WebSocket connections for text chat using aiohttp."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    username = None
    try:
        # First message should be the username
        msg = await ws.receive()
        if msg.type == web.WSMsgType.TEXT:
            username = msg.data.strip()
            text_chat_clients[username] = ws
            logging.info(f'Text chat connection opened for {username}')
            await broadcast_text_message(f"User {username} has joined the chat.")
        else:
            await ws.close()
            return ws

        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                message = msg.data
                if message.startswith('/w '):
                    parts = message.split(' ', 2)
                    if len(parts) >= 3:
                        _, recipient, dm_content = parts
                        await send_direct_text_message(username, recipient, dm_content)
                    else:
                        await send_direct_text_message('server', username, "Invalid private message format.")
                else:
                    await broadcast_text_message(f"{username}: {message}")
            elif msg.type == web.WSMsgType.ERROR:
                logging.error('WebSocket connection closed with exception %s', ws.exception())

    finally:
        if username and username in text_chat_clients:
            del text_chat_clients[username]
            logging.info(f'Text chat connection closed for {username}')
            await broadcast_text_message(f"User {username} has left the chat.")

    return ws


async def broadcast_text_message(message):
    """Sends a message to all connected text clients (aiohttp WebSocket)."""
    if text_chat_clients:
        await asyncio.gather(*[client.send_str(message) for client in text_chat_clients.values()])

async def send_direct_text_message(sender, recipient, message):
    """Sends a private message to a specific user (aiohttp WebSocket)."""
    if recipient in text_chat_clients:
        await text_chat_clients[recipient].send_str(f"[DM from {sender}]: {message}")
        if sender != 'server' and sender in text_chat_clients:
            await text_chat_clients[sender].send_str(f"[DM to {recipient}]: {message}")
    elif sender != 'server' and sender in text_chat_clients:
        await text_chat_clients[sender].send_str(f"User {recipient} is not online.")

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
    
    # Start the HTTP server to serve client.html
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)
    # WebSocket endpoint for text chat
    app.router.add_get("/ws", websocket_handler)

    # Add health check endpoint for cloud platforms
    async def healthcheck(request):
        return web.Response(text="OK", status=200)
    app.router.add_get("/health", healthcheck)

    # Configure CORS for production using aiohttp_cors
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
        )
    })
    # Enable CORS on all routes
    for route in list(app.router.routes()):
        cors.add(route)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logging.info(f"HTTP server started on http://{host}:{port}")
    
    logging.info(f"Server is ready. Access locally at:")
    logging.info(f"- HTTP: http://localhost:{port}")
    logging.info(f"- WebSocket: ws://localhost:{port}/ws")
    
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(start_server())
