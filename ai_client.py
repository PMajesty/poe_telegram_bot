import aiohttp
import json
import logging
from config import POE_API_KEY, POE_BASE_URL
from typing import Dict, Any, List

class PoeChatClient:
    async def chat(self, model: str, messages: list[dict], request_id: str = "N/A") -> Dict[str, Any]:
        openai_messages = []
        
        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")
            attachments = msg.get("attachments", [])
            
            if not attachments:
                openai_messages.append({"role": role, "content": content})
                continue
            
            content_parts = []
            if content:
                content_parts.append({"type": "text", "text": content})
            
            for att in attachments:
                mime = att.get("content_type", "application/octet-stream")
                b64 = att.get("data_base64", "")
                filename = att.get("filename", "file")
                
                data_url = f"data:{mime};base64,{b64}"
                
                if mime.startswith("image/"):
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": data_url}
                    })
                else:
                    content_parts.append({
                        "type": "file",
                        "file": {
                            "filename": filename,
                            "file_data": data_url
                        }
                    })
            
            openai_messages.append({"role": role, "content": content_parts})

        payload = {
            "model": model,
            "messages": openai_messages,
            "stream": False
        }

        logging.info(f"[{request_id}] Sending POST request to Poe API ({POE_BASE_URL}/chat/completions) for model: {model}")
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{POE_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {POE_API_KEY}",
                    "Content-Type": "application/json",
                    "Accept-Encoding": "gzip, deflate"
                },
                json=payload
            ) as resp:
                logging.info(f"[{request_id}] Received response from Poe API. Status: {resp.status}")
                if resp.status != 200:
                    error_text = await resp.text()
                    logging.error(f"[{request_id}] Poe API Error Body: {error_text}")
                    raise Exception(f"Poe API Error {resp.status}: {error_text}")
                
                data = await resp.json()
                
                choices = data.get("choices", [])
                if not choices:
                    text_response = ""
                else:
                    text_response = choices[0].get("message", {}).get("content", "")
                
                usage = data.get("usage", {})
                
                return {
                    "text": text_response,
                    "attachments": [],
                    "usage": usage,
                    "id": data.get("id"),
                    "created": data.get("created")
                }