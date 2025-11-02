#!/usr/bin/env python3
"""
CSS Build Script for RealtimeConnect
Processes Tailwind CSS and creates a production-ready CSS file.
"""

import subprocess
import os
import sys

def install_dependencies():
    """Install required dependencies for CSS processing."""
    print("Installing Node.js dependencies (Tailwind, PostCSS, Autoprefixer)...")
    try:
        # Check if npm is available
        subprocess.run(["npm", "--version"], check=True, capture_output=True)
        
        # Install dependencies locally
        subprocess.run(["npm", "install", "tailwindcss", "autoprefixer", "postcss"], 
                      check=True, capture_output=True)
        print("✓ CSS processing dependencies installed")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("✗ Node.js or npm not found. Skipping Tailwind compilation.")
        # Fallback: create a basic CSS file
        create_fallback_css()
        return False
    return True

def create_tailwind_config():
    """Create a Tailwind CSS configuration file."""
    config_content = '''module.exports = {
  content: [
    "./client.html",
    "./login.html", 
    "./register.html",
    "./script.js"
  ],
  theme: {
    extend: {
      colors: {
        'discord-dark': '#36393f',
        'discord-darker': '#2f3136',
        'discord-darkest': '#202225',
        'discord-light': '#dcddde',
        'discord-blue': '#7289da',
        'discord-red': '#f04747',
        'discord-green': '#43b581'
      },
      spacing: {
        '18': '4.5rem',
      },
    },
  },
  plugins: [],
}'''
    with open("tailwind.config.js", "w") as f:
        f.write(config_content)
    print("✓ Tailwind configuration file created")

def create_input_css():
    """Create the input CSS file with Tailwind directives."""
    input_content = """
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    --font-sans: "Inter", sans-serif;
    /* Custom Discord-like color variables for easier theming */
    --background: 220 7.7% 12.5%; /* #202225 */
    --foreground: 220 14.3% 95.9%; /* #dcddde */
    --primary: 232 50% 65.5%; /* #7289da */
    --primary-hover: 232 50% 55.5%; 
    --danger: 350 70% 60.2%; /* #f04747 */
    --sidebar: 220 8.3% 18.2%; /* #2f3136 */
    --sidebar-border: 220 10% 8%; /* darker border */
    --chat-bubble: 220 7.7% 12.5%;
  }
}

@layer components {
  .btn {
    @apply px-4 py-2 rounded-lg font-semibold transition-colors duration-200 shadow-md;
  }

  .btn-primary {
    @apply bg-[hsl(var(--primary))] text-white hover:bg-[hsl(var(--primary-hover))];
  }

  .btn-danger {
    @apply bg-[hsl(var(--danger))] text-white hover:bg-opacity-80;
  }

  .video-box {
    @apply relative bg-[hsl(var(--background))] rounded-xl shadow-lg overflow-hidden flex flex-col;
    aspect-ratio: 16/9;
  }

  .video-box video {
    @apply w-full h-full object-cover;
  }

  .video-controls {
    @apply absolute bottom-2 left-2 right-2 p-2 bg-black bg-opacity-50 text-white text-xs rounded-lg font-medium text-center;
  }
}
"""
    with open("style.css", "w") as f:
        f.write(input_content)
    print("✓ Input CSS file created")

def compile_css():
    """Compile the CSS using Tailwind and PostCSS."""
    print("Compiling Tailwind CSS...")
    try:
        # Use npx to run Tailwind CSS command
        subprocess.run([
            "npx", "tailwindcss", 
            "-i", "./style.css", 
            "-o", "./dist/style.css", 
            "--minify"
        ], check=True, capture_output=True)
        print("✓ Tailwind CSS compiled successfully to dist/style.css")
        return True
    except subprocess.CalledProcessError as e:
        print(f"✗ Tailwind compilation failed: {e.stderr.decode()}")
        create_fallback_css()
        return False
    except FileNotFoundError:
        print("✗ npx not found. Falling back to simple CSS.")
        create_fallback_css()
        return False

def create_fallback_css():
    """Create a minimal CSS file if Tailwind compilation fails."""
    fallback_content = '''
/* Fallback CSS */
body { font-family: sans-serif; background: #2f3136; color: #dcdfe4; }
#video-grid { display: flex; flex-wrap: wrap; gap: 1rem; padding: 1rem; justify-content: center; }
.video-box { border: 2px solid #7289da; border-radius: 8px; overflow: hidden; position: relative; width: 320px; height: 180px; }
.video-box video { width: 100%; height: 100%; object-fit: cover; }
.video-controls { position: absolute; bottom: 5px; left: 5px; background: rgba(0,0,0,0.5); color: white; padding: 2px 8px; border-radius: 4px; font-size: 12px; }
#chat-sidebar { position: fixed; right: 0; top: 0; height: 100%; width: 320px; background: #2f3136; border-left: 1px solid #444; }
#chat-messages { padding: 10px; overflow-y: auto; height: calc(100% - 120px); }
#messageInputContainer { padding: 10px; }
#messageInput { width: 100%; padding: 8px; }
.message .username { color: #7289da; font-weight: bold; }
'''
    os.makedirs("dist", exist_ok=True)
    with open("dist/style.css", "w") as f:
        f.write(fallback_content)
    print("✓ Fallback CSS file created in dist/style.css")


def main():
    """Main setup process for CSS."""
    print("Starting CSS Build Process...")
    
    # 1. Install dependencies (Node.js/npm)
    if not install_dependencies():
        print("CSS compilation skipped. Using fallback styles.")
        return
        
    # 2. Create config
    create_tailwind_config()
    
    # 3. Create input CSS
    create_input_css()
    
    # 4. Compile CSS
    compile_css()
    
    print("CSS Build Process finished.")

if __name__ == "__main__":
    main()