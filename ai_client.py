import fastapi_poe as fp
from config import POE_API_KEY, IMAGE_BOT_MODELS
from typing import Dict, List, Any

class PoeChatClient:
    async def chat(self, model: str, messages: list[dict]) -> Dict[str, Any]:
        protocol_messages = []
        for msg in messages:
            role = "bot" if msg["role"] == "assistant" else msg["role"]
            attachments = msg.get("attachments", [])
            parameters = msg.get("parameters")
            
            # Logic Change: Only include parameters if it is NOT an image bot
            if parameters is not None and model not in IMAGE_BOT_MODELS:
                protocol_messages.append(
                    fp.ProtocolMessage(role=role, content=msg["content"], attachments=attachments, parameters=parameters)
                )
            else:
                protocol_messages.append(
                    fp.ProtocolMessage(role=role, content=msg["content"], attachments=attachments)
                )

        if model in IMAGE_BOT_MODELS:
            text_response = ""
            final_attachments = []

            async for partial in fp.get_bot_response(
                messages=protocol_messages,
                bot_name=model,
                api_key=POE_API_KEY,
            ):
                if isinstance(partial, fp.PartialResponse):
                    if partial.text:
                        text_response += partial.text
                    if partial.attachment:
                        final_attachments.append(partial.attachment)
            
            return {"text": text_response, "attachments": final_attachments}
        else:
            request = fp.QueryRequest(
                query=protocol_messages,
                user_id="",
                conversation_id="",
                message_id="",
                version="1.2",
                type="query",
            )
            full_response_text = await fp.get_final_response(
                request=request,
                bot_name=model,
                api_key=POE_API_KEY
            )
            return {"text": full_response_text, "attachments": []}