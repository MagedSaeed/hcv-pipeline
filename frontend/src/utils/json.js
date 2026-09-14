// JSON (optionally inside a ```json fence, or with raw line breaks inside
// strings, as models often send) re-indented for reading; null when the text
// is not a JSON object or array, so it is shown untouched.
export function formatJsonLike(text) {
  let body = (text ?? '').trim();
  if (!body) return null;
  const fence = /^```[a-zA-Z]*\n([\s\S]*?)\n?```$/.exec(body);
  if (fence) body = fence[1].trim();
  if (!body.startsWith('{') && !body.startsWith('[')) return null;
  for (const candidate of [body, escapeControlCharsInStrings(body)]) {
    try {
      const value = JSON.parse(candidate);
      if (value !== null && typeof value === 'object') return JSON.stringify(value, null, 2);
    } catch {
      // try the next candidate
    }
  }
  return null;
}

function escapeControlCharsInStrings(body) {
  let out = '';
  let inString = false;
  let escaped = false;
  for (const char of body) {
    if (inString) {
      if (escaped) escaped = false;
      else if (char === '\\') escaped = true;
      else if (char === '"') inString = false;
      else if (char === '\n') { out += '\\n'; continue; }
      else if (char === '\r') { out += '\\r'; continue; }
      else if (char === '\t') { out += '\\t'; continue; }
    } else if (char === '"') {
      inString = true;
    }
    out += char;
  }
  return out;
}
