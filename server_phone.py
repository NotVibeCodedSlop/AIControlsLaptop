import asyncio
import websockets
import json
import subprocess
import logging

# Configure logging to show timestamps and client IPs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

async def handle_client(websocket, *args, **kwargs):
    # This signature handles both older and newer versions of the websockets library
    client_ip = websocket.remote_address[0]
    logging.info(f"Client connected from IP: {client_ip}")
    
    try:
        async for message in websocket:
            logging.info(f"Received command from {client_ip}: {message}")
            try:
                data = json.loads(message)
                msg_type = data.get("type")
                value = data.get("value")

                if msg_type == "text":
                    # Safely execute: hid-keyboard "text"
                    subprocess.run(["hid-keyboard", value], check=True)
                    await websocket.send(json.dumps({"status": "success", "message": f"Typed: {value}"}))
                elif msg_type == "key":
                    # Safely execute: hid-keyboard key_combination (e.g., ctrl+alt+delete)
                    subprocess.run(["hid-keyboard", value], check=True)
                    await websocket.send(json.dumps({"status": "success", "message": f"Pressed: {value}"}))
                else:
                    await websocket.send(json.dumps({"status": "error", "message": "Unknown message type"}))
            except json.JSONDecodeError:
                # Fallback if raw text is sent instead of JSON
                subprocess.run(["hid-keyboard", message], check=True)
                await websocket.send(json.dumps({"status": "success", "message": f"Executed raw: {message}"}))
            except subprocess.CalledProcessError as e:
                logging.error(f"Failed to execute hid-keyboard: {e}")
                await websocket.send(json.dumps({"status": "error", "message": f"Execution failed: {e}"}))
    except websockets.exceptions.ConnectionClosed:
        logging.info(f"Client disconnected: {client_ip}")

async def main():
    # Start the websocket server on port 8765, listening on all network interfaces
    async with websockets.serve(handle_client, "0.0.0.0", 8765):
        logging.info("WebSocket server started on port 8765. Listening on all interfaces...")
        await asyncio.Future() # Keep the server running indefinitely

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Server stopped by user.")
