import asyncio
import json
import logging
import uuid
import os
# websockets package not used for production HTTP WebSocket handling (using aiohttp instead)

from aiohttp import web
import aiohttp_cors
import sqlite3
import bcrypt
import aiosqlite
from aiohttp_session import setup as session_setup, get_session
from aiohttp_session.cookie_storage import EncryptedCookieStorage
from cryptography.fernet import Fernet
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay, MediaPlayer

# Configure logging for debugging purposes
logging.basicConfig(level=logging.INFO)

# --- Text Chat specific variables ---
text_chat_clients = {}

# --- WebRTC specific variables ---
webrtc_peers = {}
relay = MediaRelay()
published_tracks = []  # list of tracks published by peers (Relay subscriptions stored as original tracks)


# Use aiohttp WebSocket for text chat so the app can run on a single port
async def websocket_handler(request):
    """Handles WebSocket connections for text chat using aiohttp.

    This handler enforces unique usernames by appending a short suffix when
    a collision is detected. It also ensures we only remove a stored
    username if the same WebSocket instance is closing (avoids races).
    """
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    username = None
    try:
        # First message should be the username
        msg = await ws.receive()
        if msg.type == web.WSMsgType.TEXT:
            requested = msg.data.strip()

            # Ensure a unique username to prevent accidental overwrites
            assigned = requested
            if assigned in text_chat_clients:
                suffix = str(uuid.uuid4())[:8]
                assigned = f"{requested}_{suffix}"
                # Inform the client of the assigned username so the UI can update
                await ws.send_str(f"ASSIGNED_USERNAME:{assigned}")

            username = assigned
            text_chat_clients[username] = ws
            logging.info(f'Text chat connection opened for {username}')
            await broadcast_text_message(f"User {username} has joined the chat.")
        else:
            await ws.close()
            return ws

        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                message = msg.data

                # Ignore server-to-client assignment messages if somehow echoed back
                if message.startswith('ASSIGNED_USERNAME:'):
                    continue

                if message.startswith('/w '):
                    parts = message.split(' ', 2)
                    if len(parts) >= 3:
                        _, recipient, dm_content = parts
                        await send_direct_text_message(username, recipient, dm_content)
                    else:
                        await send_direct_text_message('server', username, "Invalid private message format.")
                else:
                    # Broadcast the user message to all connected clients
                    await broadcast_text_message(f"{username}: {message}")
            elif msg.type == web.WSMsgType.ERROR:
                logging.error('WebSocket connection closed with exception %s', ws.exception())

    except Exception:
        logging.exception('Exception in websocket handler for %s', username)

    finally:
        # Only remove the entry if it still points to this WebSocket
        if username and username in text_chat_clients and text_chat_clients[username] is ws:
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
    """Serves the client.html file only for authenticated users."""
    session = await get_session(request)
    if not session or 'username' not in session:
        raise web.HTTPFound('/login')

    with open("client.html") as f:
        return web.Response(text=f.read(), content_type="text/html")


async def login_page(request):
    with open('login.html') as f:
        return web.Response(text=f.read(), content_type='text/html')


async def register_page(request):
    with open('register.html') as f:
        return web.Response(text=f.read(), content_type='text/html')


async def do_register(request):
    data = await request.post()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password:
        return web.Response(text='Missing username or password', status=400)

    async with aiosqlite.connect('users.db') as db:
        await db.execute('CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password_hash BLOB)')
        try:
            pw_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
            await db.execute('INSERT INTO users(username, password_hash) VALUES(?, ?)', (username, pw_hash))
            await db.commit()
        except aiosqlite.IntegrityError:
            return web.Response(text='Username already exists', status=409)

    return web.HTTPFound('/login')


async def do_login(request):
    data = await request.post()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password:
        return web.Response(text='Missing username or password', status=400)

    async with aiosqlite.connect('users.db') as db:
        await db.execute('CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password_hash BLOB)')
        async with db.execute('SELECT password_hash FROM users WHERE username = ?', (username,)) as cur:
            row = await cur.fetchone()
            if not row:
                return web.Response(text='Invalid username or password', status=401)
            stored_hash = row[0]
            if not bcrypt.checkpw(password.encode('utf-8'), stored_hash):
                return web.Response(text='Invalid username or password', status=401)

    session = await get_session(request)
    session['username'] = username
    return web.HTTPFound('/')


async def do_logout(request):
    session = await get_session(request)
    if session and 'username' in session:
        del session['username']
    return web.HTTPFound('/login')

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

    try:
        @peer_connection.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            logging.info(
                "ICE connection state is %s for peer %s",
                peer_connection.iceConnectionState,
                peer_id,
            )
            if peer_connection.iceConnectionState == "failed":
                logging.warning(f"ICE connection failed for peer {peer_id}")
                if peer_id in webrtc_peers:
                    await peer_connection.close()
                    del webrtc_peers[peer_id]
            elif peer_connection.iceConnectionState == "closed":
                if peer_id in webrtc_peers:
                    await peer_connection.close()
                    del webrtc_peers[peer_id]
                    logging.info(f"Cleaned up connection for peer {peer_id}")

        @peer_connection.on("track")
        def on_track(track):
            logging.info("Track %s received from peer %s", track.kind, peer_id)
            # Keep track of published tracks for newly joining peers to subscribe to
            published_tracks.append(track)
            # Try to relay this track to existing peers by adding a relay subscription
            for other_id, other_pc in list(webrtc_peers.items()):
                if other_id == peer_id:
                    continue
                try:
                    other_pc.addTrack(relay.subscribe(track))
                except Exception:
                    logging.exception("Failed to add relayed track to peer %s", other_id)

        # Add any already-published tracks to this new peer so they can receive others' streams
        try:
            for t in published_tracks:
                peer_connection.addTrack(relay.subscribe(t))
        except Exception:
            logging.exception("Error adding existing published tracks to new peer %s", peer_id)

        await peer_connection.setRemoteDescription(offer_description)
        await peer_connection.setLocalDescription(await peer_connection.createAnswer())

        return web.json_response({
            "sdp": peer_connection.localDescription.sdp,
            "type": peer_connection.localDescription.type,
            "id": peer_id
        })

    except Exception as e:
        logging.error(f"Error in WebRTC offer handler: {str(e)}")
        if peer_id in webrtc_peers:
            await peer_connection.close()
            del webrtc_peers[peer_id]
        return web.json_response({"error": str(e)}, status=500)

async def start_server():
    """Starts both the HTTP server for the client page and the WebSocket server."""
    
    # Use PORT from environment or default to 3000 (common cloud platform default)
    port = int(os.environ.get("PORT", 3000))
    # Use HOST from environment or default to localhost for development
    host = os.environ.get("HOST", "0.0.0.0")
    
    # Start the HTTP server to serve client.html
    app = web.Application()

    # Setup encrypted cookie session storage
    secret_key = os.environ.get('SESSION_KEY')
    if not secret_key:
        # Generate a key if none provided (volatile across restarts)
        secret_key = Fernet.generate_key()
    else:
        secret_key = secret_key.encode('utf-8')
    session_setup(app, EncryptedCookieStorage(secret_key))
    
    # Configure CORS before adding routes
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods=["GET", "POST", "OPTIONS"]
        )
    })

    # Add and configure routes with CORS
    resource = cors.add(app.router.add_resource("/"))
    cors.add(resource.add_route("GET", index))

    # Authentication pages and endpoints
    app.router.add_get('/login', login_page)
    app.router.add_get('/register', register_page)
    app.router.add_post('/login', do_login)
    app.router.add_post('/register', do_register)
    app.router.add_get('/logout', do_logout)

    resource = cors.add(app.router.add_resource("/offer"))
    cors.add(resource.add_route("POST", offer))
    
    # WebSocket endpoint for text chat (WebSocket doesn't need CORS)
    app.router.add_get("/ws", websocket_handler)

    # Add health check endpoint with CORS
    resource = cors.add(app.router.add_resource("/health"))
    cors.add(resource.add_route("GET", lambda request: web.Response(text="OK", status=200)))
    
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
