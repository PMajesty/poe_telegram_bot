import re
import telegramify_markdown
from telegramify_markdown import customize

MDV2_SPECIALS = r"_*[]()~`>#+-=|{}.!"

def post_process_response_text(text: str) -> str:
    print(text)
    if not text:
        return ""
    lines = text.split("\n")
    processed = []
    in_thinking_block = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if i == 0 and stripped == "*Thinking...*":
            continue
        if stripped.startswith(">") and not in_thinking_block:
            in_thinking_block = True
            continue
        if in_thinking_block:
            if stripped and stripped.startswith(">"):
                continue
            in_thinking_block = False
        if stripped == "---" and i + 1 < len(lines) and lines[i + 1].strip() == "Learn more:":
            processed.append("**Для данного запроса использовался интернет. Источники были скрыты.**")
            break
        line = re.sub(r"\[\[\s*\d+\s*\]\]\s*\([^)]*?\)", "", line)
        processed.append(line)
    processed_text = "\n".join(processed)
    disclaimer = (
        "This response may include content that is harmful, illegal, or inappropriate. "
        "Please proceed with caution and adhere to relevant guidelines and laws. "
        "All information is provided for reference and academic purposes only."
    )
    processed_text = processed_text.replace(disclaimer, "")
    return processed_text.rstrip()

def markdown_normalize(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"```[ \t]*\n[ \t]*```", "", text)
    def fix_headers(line: str) -> str:
        if re.match(r"^#{1,6}\s*$", line):
            return ""
        return line
    lines = [fix_headers(l) for l in text.split("\n")]
    normalized = []
    empty_run = 0
    in_code = False
    for l in lines:
        if l.strip().startswith("```"):
            in_code = not in_code
            normalized.append(l)
            empty_run = 0
            continue
        if in_code:
            normalized.append(l)
            continue
        if l.strip() == "":
            empty_run += 1
            if empty_run <= 1:
                normalized.append(l)
        else:
            empty_run = 0
            normalized.append(l)
    text = "\n".join(normalized)
    def balance(symbol: str, s: str) -> str:
        cnt = s.count(symbol)
        if cnt % 2 == 1:
            idx = s.rfind(symbol)
            if idx != -1:
                s = s[:idx] + s[idx+1:]
        return s
    text = balance("*", text)
    text = balance("_", text)
    text = balance("~", text)
    text = balance("`", text)
    return text

def sanitize_markdown_v2(text: str) -> str:
    if text is None:
        return ""
    code_blocks = []
    def protect_fences(m):
        code_blocks.append(m.group(0))
        return f"{{CODE_BLOCK_{len(code_blocks)-1}}}"
    text = re.sub(r"```[\s\S]*?```", protect_fences, text)
    inline_codes = []
    def protect_inline(m):
        inline_codes.append(m.group(0))
        return f"{{CODE_INLINE_{len(inline_codes)-1}}}"
    text = re.sub(r"`[^`\n]+`", protect_inline, text)
    def esc(s: str) -> str:
        return re.sub(r"([_%s])" % re.escape(MDV2_SPECIALS), r"\\\1", s)
    text = esc(text)
    for i, val in enumerate(inline_codes):
        text = text.replace(f"{{CODE_INLINE_{i}}}", val)
    for i, val in enumerate(code_blocks):
        text = text.replace(f"{{CODE_BLOCK_{i}}}", val)
    return text

def chunk_text(s, limit=4000):
    if not s:
        return [""]
    out = []
    buf = s
    while buf:
        if len(buf) <= limit:
            out.append(buf)
            break
        cut = buf.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        out.append(buf[:cut])
        buf = buf[cut:].lstrip("\n")
    return out

def sanitize_and_chunk_text(input_text: str):
    processed_text = telegramify_markdown.markdownify(
        input_text,
        max_line_length=None,
        normalize_whitespace=False
    )
    
    chunks = []
    while processed_text:
        if len(processed_text) <= 4000:
            chunks.append(processed_text)
            break
        
        chunk_limit = 4000
        
        split_pos = processed_text.rfind('\n', 0, chunk_limit)
        is_newline_split = True

        if split_pos == -1:
            split_pos = chunk_limit
            is_newline_split = False

        was_split_in_code_block = False
        for match in re.finditer(r"```.*?```", processed_text, re.DOTALL):
            start, end = match.span()
            if start < split_pos < end:
                lang_match = re.match(r"```(\w*)", match.group(0))
                lang = lang_match.group(1) if lang_match else ""

                chunk = processed_text[:split_pos] + "\n```"
                
                if is_newline_split:
                    processed_text = "```" + lang + "\n" + processed_text[split_pos + 1:]
                else:
                    processed_text = "```" + lang + "\n" + processed_text[split_pos:]
                
                chunks.append(chunk)
                was_split_in_code_block = True
                break

        if was_split_in_code_block:
            continue

        chunk = processed_text[:split_pos]
        
        if is_newline_split:
            processed_text = processed_text[split_pos + 1:]
        else:
            processed_text = processed_text[split_pos:]
            
        chunks.append(chunk)
    
    return chunks