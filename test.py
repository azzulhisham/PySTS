import asyncio
import websockets

async def handler(websocket):
    async for message in websocket:
        print(f'{message}')
        await websocket.send(message)

async def main():
    async with websockets.serve(handler, "127.0.0.1", 38381):
        await asyncio.Future()  # run forever

asyncio.run(main())
