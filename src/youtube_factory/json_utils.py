import re
import json


def clean_llm_response(text):
    clean = text.strip()
    if clean.startswith("```"):
        lines = clean.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines[-1].startswith("```"):
            lines = lines[:-1]
        clean = "\n".join(lines).strip()
    clean = re.sub(r',\s*}', '}', clean)
    clean = re.sub(r',\s*\]', ']', clean)
    return clean


def repair_json(text, required_keys=None):
    if required_keys is None:
        required_keys = ["title", "seed"]

    cleaned = clean_llm_response(text)
    if not cleaned:
        raise json.JSONDecodeError("Empty response after cleaning", "", 0)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    try:
        start = cleaned.index("[")
        end = cleaned.rindex("]") + 1
        return json.loads(cleaned[start:end])
    except (ValueError, json.JSONDecodeError):
        pass

    results = []
    depth = 0
    obj_start = None
    for i, ch in enumerate(cleaned):
        if ch == '{':
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and obj_start is not None:
                try:
                    obj = json.loads(cleaned[obj_start:i + 1])
                    if all(k in obj for k in required_keys):
                        results.append(obj)
                except json.JSONDecodeError:
                    pass
                obj_start = None

    if results:
        return results

    try:
        start = cleaned.index("{")
        end = cleaned.rindex("}") + 1
        return json.loads(cleaned[start:end])
    except (ValueError, json.JSONDecodeError):
        pass

    raise json.JSONDecodeError("Could not parse LLM response as JSON", cleaned, 0)
