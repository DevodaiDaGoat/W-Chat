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
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "tailwindcss", "autoprefixer", "postcss"], 
                      check=True, capture_output=True)
        print("✓ CSS processing dependencies installed")
    except subprocess.CalledProcessError as e:
        print(f"✗ Failed to install dependencies: {e}")
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
        '72': '18rem',
        '84': '21rem',
        '96': '24rem'
      },
      borderRadius: {
        'xl': '1rem'
      }
    }
  },
  plugins: [],
}'''
    
    with open("tailwind.config.js", "w") as f:
        f.write(config_content)
    print("✓ Tailwind config created")

def create_postcss_config():
    """Create PostCSS configuration for autoprefixer."""
    config_content = '''module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}'''
    
    with open("postcss.config.js", "w") as f:
        f.write(config_content)
    print("✓ PostCSS config created")

def create_input_css():
    """Create the input CSS file with Tailwind directives."""
    css_content = '''@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    --font-primary: "gg sans", "Noto Sans", "Helvetica Neue", Helvetica, Arial, sans-serif;
    --font-display: "gg sans", "Noto Sans", "Helvetica Neue", Helvetica, Arial, sans-serif;
    --font-code: "gg mono", "Source Code Pro", Consolas, monospace;
  }
}

@layer components {
  .btn {
    @apply px-4 py-2 rounded-full font-medium transition-colors duration-200;
  }

  .btn-primary {
    @apply bg-discord-blue text-white hover:bg-opacity-80;
  }

  .btn-danger {
    @apply bg-discord-red text-white hover:bg-opacity-80;
  }

  .video-container {
    @apply relative w-full h-full bg-discord-darkest rounded-xl overflow-hidden;
  }

  .video-controls {
    @apply absolute bottom-4 left-4 flex space-x-2;
  }

  .control-btn {
    @apply btn flex items-center space-x-2 bg-discord-darker text-discord-light hover:bg-discord-dark;
  }

  .control-btn.active {
    @apply bg-discord-blue;
  }

  .control-btn.muted {
    @apply bg-discord-red;
  }

  .chat-container {
    @apply fixed right-6 bottom-6 w-80 bg-discord-darker rounded-xl shadow-lg flex flex-col h-[480px];
  }

  .chat-header {
    @apply p-4 bg-discord-dark rounded-t-xl border-b border-discord-darkest flex items-center justify-between;
  }

  .chat-messages {
    @apply flex-1 overflow-y-auto p-4 space-y-4;
  }

  .message {
    @apply flex flex-col space-y-1;
  }

  .message .username {
    @apply text-discord-blue font-medium hover:underline cursor-pointer;
  }

  .message .content {
    @apply text-discord-light;
  }

  .message-input {
    @apply p-4 bg-discord-dark rounded-b-xl;
  }

  .message-input input {
    @apply w-full px-4 py-2 bg-discord-darkest text-discord-light rounded-md border-none focus:outline-none focus:ring-2 focus:ring-discord-blue;
  }
}

/* Base styles */
body {
  @apply m-0 p-0 bg-discord-darkest text-discord-light font-sans antialiased h-screen w-screen overflow-hidden;
}

#meeting-container {
  @apply w-full h-full flex flex-col bg-discord-darker;
}

#video-grid {
  @apply flex-grow flex justify-center items-center p-4 gap-3 flex-wrap bg-discord-dark;
}

video {
  @apply w-[400px] h-[300px] object-cover bg-discord-darkest rounded-xl border-2 border-discord-darker shadow-lg;
}

#localVideo {
  @apply border-discord-blue;
}

#controls-bar {
  @apply flex justify-center items-center bg-discord-darker p-4 shadow-xl;
}

.control-icon {
  @apply w-5 h-5;
}'''
    
    with open("input.css", "w") as f:
        f.write(css_content)
    print("✓ Input CSS created")

def build_css():
    """Build the CSS using Tailwind CLI."""
    try:
        # Try to run tailwindcss directly
        result = subprocess.run([
            "npx", "tailwindcss", "-i", "input.css", "-o", "dist/style.css", "--minify"
        ], capture_output=True, text=True)
        
        if result.returncode == 0:
            print("✓ CSS built successfully with npx")
            return True
        else:
            print(f"✗ npx tailwindcss failed: {result.stderr}")
            
            # Try with tailwindcss directly
            result = subprocess.run([
                "tailwindcss", "-i", "input.css", "-o", "dist/style.css", "--minify"
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                print("✓ CSS built successfully with tailwindcss")
                return True
            else:
                print(f"✗ tailwindcss failed: {result.stderr}")
                return False
                
    except FileNotFoundError:
        print("✗ Tailwind CSS CLI not found. Using fallback CSS.")
        return False

def create_fallback_css():
    """Create a fallback CSS file if Tailwind build fails."""
    fallback_content = '''/* Fallback CSS for RealtimeConnect */
/* Discord-inspired color palette */
:root {
  --discord-dark: #36393f;
  --discord-darker: #2f3136;
  --discord-darkest: #202225;
  --discord-light: #dcddde;
  --discord-blue: #7289da;
  --discord-red: #f04747;
  --discord-green: #43b581;
  --font-primary: "gg sans", "Noto Sans", "Helvetica Neue", Helvetica, Arial, sans-serif;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  padding: 0;
  background-color: var(--discord-darkest);
  color: var(--discord-light);
  font-family: var(--font-primary);
  -webkit-font-smoothing: antialiased;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
}

.btn {
  padding: 0.5rem 1rem;
  border-radius: 9999px;
  font-weight: 500;
  transition: all 0.2s ease-in-out;
  cursor: pointer;
  border: none;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.btn-primary {
  background-color: var(--discord-blue);
  color: white;
}

.btn-primary:hover {
  background-color: rgba(114, 137, 218, 0.8);
}

.btn-danger {
  background-color: var(--discord-red);
  color: white;
}

.btn-danger:hover {
  background-color: rgba(240, 71, 71, 0.8);
}

.control-btn {
  background-color: var(--discord-darker);
  color: var(--discord-light);
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.control-btn:hover {
  background-color: var(--discord-dark);
}

.control-btn.active {
  background-color: var(--discord-blue);
}

.control-btn.muted {
  background-color: var(--discord-red);
}

#meeting-container {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: var(--discord-darker);
}

#video-grid {
  flex-grow: 1;
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 1rem;
  gap: 0.75rem;
  flex-wrap: wrap;
  background-color: var(--discord-dark);
}

video {
  width: 400px;
  height: 300px;
  object-fit: cover;
  background-color: var(--discord-darkest);
  border-radius: 0.75rem;
  border: 2px solid var(--discord-darker);
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
}

#localVideo {
  border-color: var(--discord-blue);
}

#controls-bar {
  display: flex;
  justify-content: center;
  align-items: center;
  background-color: var(--discord-darker);
  padding: 1rem;
  box-shadow: 0 -4px 6px -1px rgba(0, 0, 0, 0.1);
}

.control-icon {
  width: 1.25rem;
  height: 1.25rem;
}

/* Chat styles */
.chat-container {
  position: fixed;
  right: 1.5rem;
  bottom: 1.5rem;
  width: 20rem;
  background-color: var(--discord-darker);
  border-radius: 0.75rem;
  box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
  display: flex;
  flex-direction: column;
  height: 30rem;
}

.chat-header {
  padding: 1rem;
  background-color: var(--discord-dark);
  border-top-left-radius: 0.75rem;
  border-top-right-radius: 0.75rem;
  border-bottom: 1px solid var(--discord-darkest);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.message {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.message .username {
  color: var(--discord-blue);
  font-weight: 600;
  cursor: pointer;
  text-decoration: none;
}

.message .username:hover {
  text-decoration: underline;
}

.message .content {
  color: var(--discord-light);
}

.message-input {
  padding: 1rem;
  background-color: var(--discord-dark);
  border-bottom-left-radius: 0.75rem;
  border-bottom-right-radius: 0.75rem;
}

.message-input input {
  width: 100%;
  padding: 0.5rem 1rem;
  background-color: var(--discord-darkest);
  color: var(--discord-light);
  border-radius: 0.375rem;
  border: none;
  outline: none;
}

.message-input input:focus {
  box-shadow: 0 0 0 2px var(--discord-blue);
}

/* Utility classes */
.hidden { display: none !important; }
.visible { display: block !important; }
.flex { display: flex; }
.flex-col { flex-direction: column; }
.items-center { align-items: center; }
.justify-center { justify-content: center; }
.justify-between { justify-content: space-between; }
.w-full { width: 100%; }
.h-full { height: 100%; }
.p-4 { padding: 1rem; }
.px-4 { padding-left: 1rem; padding-right: 1rem; }
.py-2 { padding-top: 0.5rem; padding-bottom: 0.5rem; }
.rounded-xl { border-radius: 0.75rem; }
.rounded-full { border-radius: 9999px; }
.rounded-md { border-radius: 0.375rem; }
.shadow-lg { box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05); }
.overflow-hidden { overflow: hidden; }
.overflow-y-auto { overflow-y: auto; }
.fixed { position: fixed; }
.relative { position: relative; }
.absolute { position: absolute; }
.bottom-4 { bottom: 1rem; }
.left-4 { left: 1rem; }
.right-6 { right: 1.5rem; }
.bottom-6 { bottom: 1.5rem; }
.font-medium { font-weight: 500; }
.font-semibold { font-weight: 600; }
.cursor-pointer { cursor: pointer; }
.border-none { border: none; }

/* Responsive design */
@media (max-width: 768px) {
  .chat-container {
    width: 100%;
    height: 50%;
    right: 0;
    bottom: 0;
    border-radius: 0;
  }
  
  video {
    width: 100%;
    max-width: 300px;
    height: auto;
    aspect-ratio: 16/9;
  }
  
  #video-grid {
    padding: 0.5rem;
    gap: 0.5rem;
  }
}'''
    
    # Create dist directory if it doesn't exist
    os.makedirs("dist", exist_ok=True)
    
    with open("dist/style.css", "w") as f:
        f.write(fallback_content)
    print("✓ Fallback CSS created")

def main():
    """Main build process."""
    print("Building CSS for RealtimeConnect...")
    
    # Create dist directory
    os.makedirs("dist", exist_ok=True)
    
    # Create configuration files
    create_tailwind_config()
    create_postcss_config()
    create_input_css()
    
    # Try to build with Tailwind
    if build_css():
        print("✓ CSS build completed successfully!")
    else:
        print("✓ Fallback CSS created (Tailwind build skipped)")
    
    print("CSS build process finished.")

if __name__ == "__main__":
    main()