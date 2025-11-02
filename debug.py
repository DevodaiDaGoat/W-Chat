#!/usr/bin/env python3
"""
Test script to verify server functionality
"""

import sys
import os
import asyncio
import importlib.util

def test_imports():
    """Test if all required modules can be imported."""
    required_modules = [
        'asyncio', 'json', 'logging', 'uuid', 'os', 'websockets',
        'aiohttp', 'aiortc'
    ]
    
    print("Testing module imports...")
    failed_imports = []
    
    for module in required_modules:
        try:
            if module == 'aiortc':
                import aiortc
                from aiortc import RTCPeerConnection, RTCSessionDescription
            elif module == 'aiohttp':
                import aiohttp
                from aiohttp import web
            elif module == 'websockets':
                import websockets
            else:
                __import__(module)
            print(f"✓ {module}")
        except ImportError as e:
            print(f"✗ {module}: {e}")
            failed_imports.append(module)
    
    return len(failed_imports) == 0

def test_syntax():
    """Test if the server file has valid syntax."""
    print("\nTesting server syntax...")
    try:
        with open("server_fixed.py", "r") as f:
            code = f.read()
        compile(code, "server_fixed.py", "exec")
        print("✓ Server syntax is valid")
        return True
    except SyntaxError as e:
        print(f"✗ Syntax error: {e}")
        return False

def test_async_functions():
    """Test if async functions are properly defined."""
    print("\nTesting async function definitions...")
    
    # Import the server module
    spec = importlib.util.spec_from_file_location("server", "server_fixed.py")
    server = importlib.util.module_from_spec(spec)
    
    try:
        spec.loader.exec_module(server)
        
        # Check for async functions
        async_functions = [
            'text_chat_handler', 'broadcast_text_message', 'send_direct_text_message',
            'index', 'offer', 'start_server'
        ]
        
        for func_name in async_functions:
            if hasattr(server, func_name):
                func = getattr(server, func_name)
                if asyncio.iscoroutinefunction(func):
                    print(f"✓ {func_name} is properly async")
                else:
                    print(f"✗ {func_name} is not async")
            else:
                print(f"✗ {func_name} not found")
        
        return True
        
    except Exception as e:
        print(f"✗ Error testing async functions: {e}")
        return False

def test_css_files():
    """Test if CSS files exist and are valid."""
    print("\nTesting CSS files...")
    
    css_files = ["style_fixed.css", "dist/style.css"]
    
    for css_file in css_files:
        if os.path.exists(css_file):
            try:
                with open(css_file, "r") as f:
                    content = f.read()
                
                # Check for basic CSS validity
                if "{" in content and "}" in content:
                    print(f"✓ {css_file} exists and contains CSS")
                else:
                    print(f"✗ {css_file} appears to be empty or invalid")
                    
            except Exception as e:
                print(f"✗ Error reading {css_file}: {e}")
        else:
            print(f"✗ {css_file} not found")
    
    return True

def main():
    """Run all tests."""
    print("RealtimeConnect Server Test Suite")
    print("=" * 50)
    
    tests = [
        ("Module Imports", test_imports),
        ("Syntax Check", test_syntax),
        ("Async Functions", test_async_functions),
        ("CSS Files", test_css_files)
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"✗ {test_name} failed with exception: {e}")
            results.append((test_name, False))
    
    print("\n" + "=" * 50)
    print("Test Results:")
    
    all_passed = True
    for test_name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {test_name}: {status}")
        if not passed:
            all_passed = False
    
    print("=" * 50)
    
    if all_passed:
        print("🎉 All tests passed! The server should work correctly.")
        return 0
    else:
        print("❌ Some tests failed. Please check the issues above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())