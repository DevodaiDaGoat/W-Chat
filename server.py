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
# Map username -> {'ws': WebSocketResponse, 'meeting': meeting_id}
text_chat_clients = {}

# --- WebRTC specific variables ---
webrtc_peers = {}  # peer_id -> {pc, username, meeting}
relay = MediaRelay()
published_tracks = {}  # meeting -> {username -> {video, audio, screen}}

# STUN servers for WebRTC connection
STUN_SERVERS = [
    "stun:stun.l.google.com:19302",
    "stun:stun1.l.google.com:19302",
]

# Special admin room configuration
ADMIN_MEETING = "admin-tools"  # Special meeting room with extra privileges
ADMIN_USERNAME = "Devodai"     # Admin username for special privileges
ADMIN_PASSWORD = bcrypt.hashpw("D3vSucks@L0t".encode(), bcrypt.gensalt())

# Free STUN servers - consider adding your own TURN server for production
STUN_SERVERS = [
    "stun:stun.l.google.com:19302",
    "stun:stun1.l.google.com:19302",
]

ADMIN_MEETING = "admin-tools"  # Special meeting room with extra privileges
ADMIN_USERNAME = "Devodai"     # Admin username for special privileges


# Use aiohttp WebSocket for text chat so the app can run on a single port
async def websocket_handler(request):
    """Handles WebSocket connections for text chat using aiohttp.
    Uses authenticated username from session and manages meeting-specific features.
    """
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    session = await get_session(request)
    if 'username' not in session:
        await ws.send_str('ERROR: Not authenticated')
        await ws.close()
        return ws
        
    username = session['username']
    if username in text_chat_clients:
        await ws.send_str('ERROR: Already connected from another window')
        await ws.close()
        return ws

    try:
        # First message should be the meeting ID
        msg = await ws.receive()
        if msg.type != web.WSMsgType.TEXT:
            await ws.close()
            return ws

        requested = msg.data.strip()

        # Ensure a unique username to prevent accidental overwrites
        assigned = requested
        if assigned in text_chat_clients:
            suffix = str(uuid.uuid4())[:8]
            assigned = f"{requested}_{suffix}"
            # Inform the client of the assigned username so the UI can update
            await ws.send_str(f"ASSIGNED_USERNAME:{assigned}")

        username = assigned
        # Temporarily store ws without meeting until client sends meeting id
        text_chat_clients[username] = {'ws': ws, 'meeting': None}
        logging.info(f'Text chat connection opened for {username} (meeting pending)')

        # Expect the client to send a meeting id next as: MEETING:<meetingId>
        meeting_msg = await ws.receive()
        meeting_id = None
        if meeting_msg.type == web.WSMsgType.TEXT and meeting_msg.data.startswith('MEETING:'):
            meeting_id = meeting_msg.data.split(':', 1)[1].strip()
            # Enforce optional meeting prefix if configured
            session = await get_session(request)
            is_admin = session.get('is_admin', False)
            
            # Special handling for admin room
            if meeting_id == ADMIN_MEETING:
                if not is_admin and not await check_video_enabled(username):
                    await ws.send_str("ERROR: Video required for admin room")
                    await ws.close()
                    return ws
                    
            # Check meeting prefix if configured
            prefix = os.environ.get('MEETING_PREFIX')
            if prefix and not meeting_id.startswith(prefix) and meeting_id != ADMIN_MEETING:
                await ws.send_str(f"ERROR: Meeting id must start with prefix {prefix}")
                await ws.close()
                return ws
                
            text_chat_clients[username]['meeting'] = meeting_id
            text_chat_clients[username]['is_admin'] = is_admin
            logging.info(f'User {username} joined meeting {meeting_id} {"(admin)" if is_admin else ""}')
            await broadcast_text_message(f"User {username} has joined the chat.", meeting=meeting_id)
        else:
            # client didn't send meeting id - close
            await ws.send_str('ERROR: Missing meeting id. Send MEETING:<id> after username.')
            await ws.close()
            return ws

        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                message = msg.data

                # Store last DM sender for /r command
                if message.startswith('ASSIGNED_USERNAME:') or message.startswith('MEETING:'):
                    continue

                client_info = text_chat_clients[username]
                meeting = client_info.get('meeting')

                if message.startswith('/msg ') or message.startswith('/w '):
                    parts = message.split(' ', 2)
                    if len(parts) >= 3:
                        _, recipient, dm_content = parts
                        await send_direct_text_message(username, recipient, dm_content)
                        if recipient in text_chat_clients:
                            text_chat_clients[recipient]['last_dm_from'] = username
                    else:
                        await send_direct_text_message('server', username, "Usage: /msg <username> <message>")
                
                elif message.startswith('/r '):
                    # Reply to last DM sender
                    last_sender = client_info.get('last_dm_from')
                    if last_sender:
                        reply_content = message[3:].strip()
                        await send_direct_text_message(username, last_sender, reply_content)
                        if last_sender in text_chat_clients:
                            text_chat_clients[last_sender]['last_dm_from'] = username
                    else:
                        await send_direct_text_message('server', username, "No one to reply to")
                
                elif message.startswith('/global '):
                    # Send message to all users across all meetings
                    global_msg = message[8:].strip()
                    await broadcast_text_message(f"[Global] {username}: {global_msg}")
                
                else:
                    # Broadcast to current meeting by default
                    await broadcast_text_message(f"{username}: {message}", meeting=meeting)
            elif msg.type == web.WSMsgType.ERROR:
                logging.error('WebSocket connection closed with exception %s', ws.exception())

    except Exception:
        logging.exception('Exception in websocket handler for %s', username)

    finally:
        # Only remove the entry if it still points to this WebSocket
        if username and username in text_chat_clients and text_chat_clients[username]['ws'] is ws:
            meeting = text_chat_clients[username]['meeting']
            del text_chat_clients[username]
            logging.info(f'Text chat connection closed for {username}')
            await broadcast_text_message(f"User {username} has left the chat.", meeting=meeting)

    return ws


async def broadcast_text_message(message, meeting=None):
    """Sends a message to all connected text clients (aiohttp WebSocket).

    If meeting is provided, only clients in that meeting receive the message.
    """
    if not text_chat_clients:
        return

    async def _send(entry):
        try:
            await entry['ws'].send_str(message)
        except Exception:
            logging.exception('Failed to send text message to a client')

    if meeting is None:
        await asyncio.gather(*[_send(entry) for entry in text_chat_clients.values()])
    else:
        await asyncio.gather(*[_send(entry) for entry in text_chat_clients.values() if entry.get('meeting') == meeting])

async def send_direct_text_message(sender, recipient, message):
    """Sends a private message to a specific user (aiohttp WebSocket) within the same meeting."""
    sender_entry = text_chat_clients.get(sender)
    recipient_entry = text_chat_clients.get(recipient)
    if recipient_entry and sender_entry and recipient_entry.get('meeting') == sender_entry.get('meeting'):
        try:
            await recipient_entry['ws'].send_str(f"[DM from {sender}]: {message}")
        except Exception:
            logging.exception('Failed to send DM to %s', recipient)
        if sender != 'server':
            try:
                await sender_entry['ws'].send_str(f"[DM to {recipient}]: {message}")
            except Exception:
                logging.exception('Failed to send DM confirmation to %s', sender)
    elif sender_entry:
        try:
            await sender_entry['ws'].send_str(f"User {recipient} is not online or not in your meeting.")
        except Exception:
            logging.exception('Failed to notify sender %s about unavailable recipient', sender)

# Helper functions for WebRTC and admin features
async def check_video_enabled(username):
    """Check if a user has video enabled in their WebRTC connection."""
    for peer_info in webrtc_peers.values():
        if (peer_info['username'] == username and 
            peer_info['meeting'] == ADMIN_MEETING):
            tracks = published_tracks.get(ADMIN_MEETING, {}).get(username, {})
            return bool(tracks.get('video'))
    return False

async def cleanup_peer(peer_id):
    """Clean up peer's resources and tracks."""
    if peer_id in webrtc_peers:
        peer_info = webrtc_peers[peer_id]
        meeting = peer_info['meeting']
        username = peer_info['username']
        
        # Remove published tracks
        if meeting in published_tracks and username in published_tracks[meeting]:
            del published_tracks[meeting][username]
            if not published_tracks[meeting]:
                del published_tracks[meeting]
                
        await peer_info['pc'].close()
        del webrtc_peers[peer_id]
        logging.info(f"Cleaned up peer {username} from {meeting}")

# --- aiohttp and WebRTC Handlers ---
async def index(request):
    """Serves the client.html file only for authenticated users."""
    session = await get_session(request)
    if not session or 'username' not in session:
        raise web.HTTPFound('/login')

    with open("client.html") as f:
        return web.Response(text=f.read(), content_type="text/html")


async def login_page(request):
    """Handle login requests with special handling for admin credentials."""
    if request.method == "POST":
        data = await request.post()
        username = data.get("username")
        password = data.get("password")
        
        if not username or not password:
            raise web.HTTPBadRequest(text="Missing username or password")
            
        # Special admin login check
        if username == ADMIN_USERNAME:
            if bcrypt.checkpw(password.encode(), ADMIN_PASSWORD):
                session = await get_session(request)
                session['username'] = username
                session['is_admin'] = True
                raise web.HTTPFound('/')
            else:
                raise web.HTTPUnauthorized(text="Invalid credentials")
        
        # Regular user login
        async with aiosqlite.connect("users.db") as db:
            async with db.execute(
                "SELECT password FROM users WHERE username = ?", (username,)
            ) as cursor:
                row = await cursor.fetchone()
                if row and bcrypt.checkpw(password.encode(), row[0]):
                    session = await get_session(request)
                    session['username'] = username
                    raise web.HTTPFound('/')
                else:
                    raise web.HTTPUnauthorized(text="Invalid credentials")
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
    Handles WebRTC signaling (offers and answers) with special handling for admin room.
    """
    session = await get_session(request)
    if 'username' not in session:
        raise web.HTTPUnauthorized(text='Not authenticated')
        
    params = await request.json()
    meeting = params.get('meeting')
    username = session['username']
    is_admin = session.get('is_admin', False)
    
    # Enforce video requirements for admin room
    if meeting == ADMIN_MEETING:
        if username != ADMIN_USERNAME and not params.get('hasVideo'):
            raise web.HTTPForbidden(text='Video required for this room')
            
        if not is_admin and not params.get('hasVideo'):
            raise web.HTTPForbidden(text='Video required for admin room')
    
    offer = RTCSessionDescription(
        sdp=params["sdp"],
        type=params["type"]
    )
    
    # Configure peer connection with STUN servers
    pc = RTCPeerConnection({
        "iceServers": [{"urls": server} for server in STUN_SERVERS]
    })
    
    peer_id = str(uuid.uuid4())
    request.app['webrtc_peers'] = webrtc_peers  # Store in app for cleanup
    webrtc_peers[peer_id] = {
        'pc': pc,
        'username': username,
        'meeting': meeting,
        'is_admin': is_admin
    }
    
    request.transport_peer_id = peer_id
    
    # Handle incoming media tracks
    @pc.on("track")
    def on_track(track):
        logging.info(f"Track {track.kind} received from {username}")
        if meeting not in published_tracks:
            published_tracks[meeting] = {}
        if username not in published_tracks[meeting]:
            published_tracks[meeting][username] = {}
            
        # Relay the track
        relayed = relay.subscribe(track)
        published_tracks[meeting][username][track.kind] = relayed
        
        # Special handling for admin room
        if meeting == ADMIN_MEETING:
            # Admin tracks are sent to everyone
            if username == ADMIN_USERNAME:
                for pid, peer_info in webrtc_peers.items():
                    if pid != peer_id and peer_info['meeting'] == meeting:
                        peer_info['pc'].addTrack(relayed)
            # Regular user tracks only go to admin
            else:
                for pid, peer_info in webrtc_peers.items():
                    if (pid != peer_id and peer_info['meeting'] == meeting and 
                        peer_info['username'] == ADMIN_USERNAME):
                        peer_info['pc'].addTrack(relayed)
        else:
            # Normal room behavior - send to all peers in meeting
            for pid, peer_info in webrtc_peers.items():
                if pid != peer_id and peer_info['meeting'] == meeting:
                    peer_info['pc'].addTrack(relayed)
                    
        @track.on("ended")
        async def on_ended():
            logging.info(f"Track {track.kind} ended from {username}")
            if (meeting in published_tracks and 
                username in published_tracks[meeting] and 
                track.kind in published_tracks[meeting][username]):
                del published_tracks[meeting][username][track.kind]
    
    # Add existing tracks based on room rules
    if meeting in published_tracks:
        for user, tracks in published_tracks[meeting].items():
            if user != username:
                if meeting == ADMIN_MEETING:
                    # In admin room, regular users only get admin's tracks
                    if (user == ADMIN_USERNAME or 
                        (username == ADMIN_USERNAME and not is_admin)):
                        for track in tracks.values():
                            pc.addTrack(track)
                else:
                    # Normal room - get all tracks
                    for track in tracks.values():
                        pc.addTrack(track)
    
    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        logging.info(
            "ICE connection state is %s for peer %s",
            pc.iceConnectionState,
            peer_id,
        )
        if pc.iceConnectionState == "failed":
            logging.warning(f"ICE connection failed for peer {peer_id}")
            await cleanup_peer(peer_id)
        elif pc.iceConnectionState == "closed":
            await cleanup_peer(peer_id)
            
    try:
        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        
        return web.json_response({
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type,
        })
    except Exception as e:
        logging.error(f"Error in WebRTC offer handler for {username}: {str(e)}")
        if peer_id in webrtc_peers:
            await cleanup_peer(peer_id)
        return web.json_response({"error": str(e)}, status=500)

async def cleanup_peer(peer_id):
    """Clean up a peer's resources including published tracks"""
    if peer_id in webrtc_peers:
        peer_info = webrtc_peers[peer_id]
        meeting = peer_info['meeting']
        username = peer_info['username']
        
        # Remove published tracks
        if meeting in published_tracks and username in published_tracks[meeting]:
            del published_tracks[meeting][username]
            if not published_tracks[meeting]:
                del published_tracks[meeting]
                
        await peer_info['pc'].close()
        del webrtc_peers[peer_id]

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
    # Accept either a valid Fernet key in SESSION_KEY (base64 urlsafe 32 bytes),
    # or derive one from SESSION_PASSPHRASE (convenient) using PBKDF2.
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.backends import default_backend
    import base64

    session_key_env = os.environ.get('SESSION_KEY')
    passphrase = os.environ.get('SESSION_PASSPHRASE')

    def _use_key(key_bytes: bytes):
        # validate by attempting to construct a Fernet instance
        try:
            Fernet(key_bytes)
            return key_bytes
        except Exception:
            return None

    secret_key = None
    if session_key_env:
        # try direct use (user likely provided Fernet.generate_key())
        try:
            candidate = session_key_env.encode('utf-8')
            if _use_key(candidate):
                secret_key = candidate
            else:
                # maybe user provided the base64 string without bytes; try raw base64 decode
                try:
                    decoded = base64.urlsafe_b64decode(session_key_env)
                    # re-encode into the exact form Fernet expects
                    encoded = base64.urlsafe_b64encode(decoded)
                    if _use_key(encoded):
                        secret_key = encoded
                except Exception:
                    secret_key = None
        except Exception:
            secret_key = None

    if secret_key is None and passphrase:
        # Derive a Fernet key from passphrase using PBKDF2 (stable across restarts if same passphrase)
        salt = os.environ.get('SESSION_SALT', 'static_salt_for_demo')
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt.encode('utf-8'),
            iterations=390000,
            backend=default_backend(),
        )
        key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode('utf-8')))
        if _use_key(key):
            secret_key = key

    if secret_key is None:
        # Last resort: generate a volatile key (sessions will not survive restarts)
        logging.warning('No valid SESSION_KEY or SESSION_PASSPHRASE provided; generating a volatile session key. Set SESSION_KEY or SESSION_PASSPHRASE in env to make sessions persistent.')
        secret_key = Fernet.generate_key()

    try:
        session_setup(app, EncryptedCookieStorage(secret_key))
    except Exception as e:
        logging.exception('Failed to initialize EncryptedCookieStorage with provided key: %s', e)
        # Try regenerating a fresh Fernet key and retry (volatile)
        try:
            fallback_key = Fernet.generate_key()
            session_setup(app, EncryptedCookieStorage(fallback_key))
            logging.warning('Using a newly-generated volatile Fernet key for sessions (will not persist across restarts).')
        except Exception:
            logging.exception('Failed to initialize EncryptedCookieStorage even with a generated key. Falling back to SimpleCookieStorage (no encryption).')
            try:
                from aiohttp_session import SimpleCookieStorage
                session_setup(app, SimpleCookieStorage())
            except Exception:
                logging.exception('Failed to initialize any session storage. Exiting.')
                raise

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