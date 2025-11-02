# Installation Guide - RealtimeConnect Fixed Version

## Quick Start

### 1. Install Dependencies
```bash
# Install Python dependencies
pip install -r requirements.txt

# For CSS processing (optional but recommended)
npm install
```

### 2. Build CSS
```bash
# Build the CSS files
python3 build_css.py
```

### 3. Run the Server
```bash
# Start the server
python3 server.py
```

### 4. Access the Application
- Open your browser to: `http://localhost:3000`
- The WebSocket server runs on: `ws://localhost:3001`

## Detailed Installation

### Python Dependencies
The server requires the following Python packages:
- `aiohttp>=3.8.0` - Async web framework
- `aiortc>=1.4.0` - WebRTC implementation
- `websockets>=11.0.0` - WebSocket support
- `aiohttp-cors>=0.7.0` - CORS middleware

Install them with:
```bash
pip install aiohttp aiortc websockets aiohttp-cors
```

### Node.js Dependencies (Optional)
For advanced CSS processing with Tailwind CSS:
```bash
npm install tailwindcss autoprefixer postcss postcss-cli
```

## Troubleshooting

### Module Not Found Errors
If you get "No module named 'aiohttp'" or similar errors:
```bash
pip install aiohttp
pip install aiortc
pip install websockets
```

### Port Already in Use
If port 3000 is already in use, set a different port:
```bash
export PORT=3002
python3 server.py
```

### CSS Not Loading
If styles don't appear correctly:
```bash
python3 build_css.py
```

## Testing the Installation

Run the test script to verify everything is working:
```bash
python3 test_server.py
```

Expected output should show:
- ✓ Module imports
- ✓ Syntax check
- ✓ Async functions
- ✓ CSS files

## Running the Server

### Development Mode
```bash
python3 server.py
```

### Production Mode
```bash
# Using Gunicorn (install with: pip install gunicorn)
gunicorn server_fixed:start_server --worker-class aiohttp.GunicornWebWorker --bind 0.0.0.0:3000
```

### Environment Variables
- `PORT`: HTTP server port (default: 3000)
- `HOST`: Server bind address (default: 0.0.0.0)
- `LOG_LEVEL`: Logging level (default: INFO)

## Verification Steps

1. **Start the server** - Should show "HTTP server started" and "WebSocket server started"
2. **Open browser** - Navigate to `http://localhost:3000`
3. **Test video** - Click video button to enable camera
4. **Test chat** - Enter username and send messages
5. **Test multiple users** - Open multiple browser tabs with different usernames

## Common Issues and Solutions

### Issue: "await allowed only within async function"
**Solution**: This is fixed in `server.py`. Use the fixed version.

### Issue: "Unknown at rule @tailwind"
**Solution**: The CSS files have been fixed to use proper CSS syntax.

### Issue: "Module not found"
**Solution**: Install missing dependencies with pip:
```bash
pip install aiohttp aiortc websockets
```

### Issue: "Port already in use"
**Solution**: Change the port:
```bash
export PORT=3002
python3 server.py
```

## File Locations

After installation, your directory should contain:
- `server.py` - The fixed server application
- `client.html` - Main web interface
- `dist/style.css` - Generated CSS file
- `requirements.txt` - Python dependencies

## Next Steps

1. Test video conferencing between multiple browsers
2. Test text chat functionality
3. Configure for production deployment
4. Add authentication if needed
5. Configure TURN/STUN servers for better connectivity

## Support

If you encounter issues:
1. Check the server logs for error messages
2. Run the test script: `python3 test_server.py`
3. Verify all dependencies are installed
4. Check that ports 3000 and 3001 are available
