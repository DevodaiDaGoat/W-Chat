#!/usr/bin/env python3
"""
Setup script for RealtimeConnect
Installs dependencies and sets up the environment.
"""

import subprocess
import sys
import os
import shutil

def install_python_dependencies():
    """Install Python dependencies."""
    print("Installing Python dependencies...")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], 
                      check=True, capture_output=True)
        print("✓ Python dependencies installed")
        return True
    except subprocess.CalledProcessError as e:
        print(f"✗ Failed to install Python dependencies: {e}")
        return False

def check_nodejs():
    """Check if Node.js is available."""
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
        print("✓ Node.js is available")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("✗ Node.js not found (needed for Tailwind CSS)")
        return False

def install_node_dependencies():
    """Install Node.js dependencies for CSS processing."""
    if not check_nodejs():
        return False
        
    print("Installing Node.js dependencies...")
    try:
        # Create package.json
        package_json = '''{
  "name": "realtimeconnect-css",
  "version": "1.0.0",
  "description": "CSS build tools for RealtimeConnect",
  "devDependencies": {
    "tailwindcss": "^3.3.0",
    "autoprefixer": "^10.4.0",
    "postcss": "^8.4.0",
    "postcss-cli": "^10.1.0"
  },
  "scripts": {
    "build-css": "tailwindcss -i input.css -o dist/style.css --minify",
    "watch-css": "tailwindcss -i input.css -o dist/style.css --watch"
  }
}'''
        
        with open("package.json", "w") as f:
            f.write(package_json)
            
        # Install dependencies
        subprocess.run(["npm", "install"], check=True, capture_output=True)
        print("✓ Node.js dependencies installed")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"✗ Failed to install Node.js dependencies: {e}")
        return False

def create_directory_structure():
    """Create necessary directories."""
    directories = ["dist", "logs", "static"]
    for directory in directories:
        os.makedirs(directory, exist_ok=True)
    print("✓ Directory structure created")

def copy_client_files():
    """Copy client HTML files to output directory."""
    try:
        # Copy client files from upload directory
        upload_dir = "/mnt/okcomputer/upload"
        if os.path.exists(upload_dir):
            for filename in ["client.html", "login.html", "register.html"]:
                src = os.path.join(upload_dir, filename)
                if os.path.exists(src):
                    shutil.copy2(src, ".")
                    print(f"✓ Copied {filename}")
        
        print("✓ Client files copied")
        return True
    except Exception as e:
        print(f"✗ Failed to copy client files: {e}")
        return False

def create_run_script():
    """Create a run script for easy server startup."""
    run_script = '''#!/bin/bash
# Run script for RealtimeConnect

echo "Starting RealtimeConnect Server..."
echo "Building CSS..."
python3 build_css.py

echo "Starting server..."
python3 server_fixed.py
'''
    
    with open("run.sh", "w") as f:
        f.write(run_script)
    
    # Make it executable
    os.chmod("run.sh", 0o755)
    print("✓ Run script created")

def create_environment_file():
    """Create a sample environment file."""
    env_content = '''# RealtimeConnect Environment Configuration
# Copy this file to .env and modify as needed

# Server Configuration
PORT=3000
HOST=0.0.0.0

# WebRTC Configuration
# Add your TURN/STUN servers here if needed
# TURN_SERVER=your-turn-server.com
# TURN_USERNAME=username
# TURN_PASSWORD=password

# Logging
LOG_LEVEL=INFO

# Security
# Add your secret key for session management
# SECRET_KEY=your-secret-key-here
'''
    
    with open(".env.example", "w") as f:
        f.write(env_content)
    print("✓ Environment file template created")

def main():
    """Main setup process."""
    print("Setting up RealtimeConnect...")
    print("=" * 50)
    
    # Create directory structure
    create_directory_structure()
    
    # Copy client files
    copy_client_files()
    
    # Install Python dependencies
    if not install_python_dependencies():
        print("⚠ Warning: Some Python dependencies may be missing")
    
    # Install Node.js dependencies for CSS
    if not install_node_dependencies():
        print("⚠ Warning: Node.js not available, will use fallback CSS")
    
    # Create additional files
    create_run_script()
    create_environment_file()
    
    print("=" * 50)
    print("Setup complete!")
    print("")
    print("To start the server:")
    print("1. Run: ./run.sh")
    print("Or manually:")
    print("   python3 build_css.py")
    print("   python3 server_fixed.py")
    print("")
    print("The server will be available at:")
    print("- HTTP: http://localhost:3000")
    print("- WebSocket: ws://localhost:3001")

if __name__ == "__main__":
    main()