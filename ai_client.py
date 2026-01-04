import aiohttp
import json
from config import POE_API_KEY, POE_BASE_URL
from typing import Dict, Any, List

class PoeChatClient:
    async def chat(self, model: str, messages: list[dict]) -> Dict[str, Any]:
        openai_messages = []
        
        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")
            attachments = msg.get("attachments", [])
            
            if not attachments:
                openai_messages.append({"role": role, "content": content})
                continue
            
            # Construct content list for multimodal input
            content_parts = []
            if content:
                content_parts.append({"type": "text", "text": content})
            
            for att in attachments:
                # att is expected to be a dict with keys: filename, content_type, data_base64
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

        # Prepare parameters
        # Note: The doc says 'stream' is fully supported. We use stream=False for simplicity to get usage stats easily.
        # 'extra_body' can be used for custom parameters if needed, but we stick to standard fields for now.
        payload = {
            "model": model,
            "messages": openai_messages,
            "stream": False
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{POE_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {POE_API_KEY}",
                    "Content-Type": "application/json"
                },
                json=payload
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise Exception(f"Poe API Error {resp.status}: {error_text}")
                
                data = await resp.json()
                
                # Extract text
                choices = data.get("choices", [])
                if not choices:
                    text_response = ""
                else:
                    text_response = choices[0].get("message", {}).get("content", "")
                
                # Extract usage
                usage = data.get("usage", {})
                
                # OpenAI API on Poe usually embeds image links in text or returns them.
                # We return empty attachments list as the text likely contains the links.
                return {
                    "text": text_response,
                    "attachments": [],
                    "usage": usage,
                    "id": data.get("id"),
                    "created": data.get("created")
                }